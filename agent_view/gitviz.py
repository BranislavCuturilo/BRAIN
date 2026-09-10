#!/usr/bin/env python3
"""Git/repo visualization backend for the Agent View — standard library only.

Every function here runs LOCAL git (and optionally the `gh` CLI) from an HTTP
request, so the security model is the load-bearing part:

  * Every endpoint that takes a client `path` validates it against the
    DISCOVERED/configured repo set with `is_known_repo()` (realpath + normcase
    compare) BEFORE any git runs. An arbitrary client path is a 400 and never
    reaches subprocess. The route in server.py enforces this; the public data
    functions here re-check it too, so a mistaken internal caller cannot run git
    on an unknown path either (defence in depth).
  * Every git/gh call is `subprocess.run` with an argv LIST, shell=False,
    cwd=<validated repo>, a timeout, stdin=DEVNULL (so shortlog never blocks on a
    tty), and READ-ONLY verbs only (a whitelist rejects anything else, and
    `remote` is restricted to `get-url`). No mutating verb is reachable.
  * Everything is capped: commits <= MAX_COMMITS, discovery depth <= DISCOVER_DEPTH
    and repo count <= MAX_REPOS, node_modules/.venv/vendor skipped when scanning.
  * The github_token is never logged.

The tricky graph layout (lane/column assignment) is centralized in build_graph so
the front end only draws. Parsing is split from IO so it is testable offline.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
#  Caps — every one of these is a DoS / cost boundary, named so it is checkable.
# --------------------------------------------------------------------------- #
GIT_TIMEOUT = 10                 # seconds, every git/gh subprocess
# On Windows the server runs console-less (start.py launches it via pythonw +
# CREATE_NO_WINDOW), so a child git/gh WOULD pop its own console window on every
# call — and the Git tab polls dozens of git calls every few seconds, which showed
# up as an endless storm of terminal windows. This suppresses the child console.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # 0 on non-Windows
MAX_COMMITS = 200                # hard ceiling on graph size
DEFAULT_GRAPH_LIMIT = 120        # default commit_graph window
DISCOVER_DEPTH = 3               # how deep under a git_root to scan for repos
MAX_REPOS = 50                   # hard ceiling on discovered repos
DISCOVER_TTL = 5.0               # discover() cache lifetime
GITHUB_TTL = 20.0                # github() cache lifetime (rate limits)
GH_LIST_LIMIT = 10               # runs / PRs pulled from gh or the REST API
INSIGHTS_TTL = 60.0              # insights() cache lifetime (per repo)
HEATMAP_ALL_TTL = 60.0           # heatmap_all() cache lifetime (aggregate, all repos)
INSIGHTS_DAYS = 180              # heatmap / hotspots / contributors window
CHURN_WEEKS = 12                 # churn window (weeks)
TOP_CONTRIBUTORS = 8             # contributors panel bound
TOP_HOTSPOTS = 15                # hotspots panel bound
TOP_FILETYPES = 10               # filetypes panel bound
SKIP_DIRS = {"node_modules", ".venv", "venv", "vendor", "__pycache__",
             ".tox", "dist", "build", ".mypy_cache", ".pytest_cache"}

#: The git subcommands that may run — READ-ONLY only. `remote` is further
#: restricted to `get-url` in _git. No mutating verb is in the set, ever.
_ALLOWED_GIT = {"log", "status", "rev-parse", "rev-list", "for-each-ref",
                "shortlog", "diff", "remote", "ls-files"}

#: git-log field separator (git emits %x1f as this byte); parse_log splits on it.
_US = "\x1f"
_LOG_FORMAT = "%H%x1f%P%x1f%an%x1f%aI%x1f%s%x1f%D"

GITHUB_SETUP_HINT = "run gh auth login or set github_token"

_RUNS_ARGS = ["run", "list", "--limit", str(GH_LIST_LIMIT),
              "--json", "name,displayTitle,status,conclusion,createdAt"]
_PRS_ARGS = ["pr", "list", "--limit", str(GH_LIST_LIMIT),
             "--json", "number,title,additions,deletions,statusCheckRollup"]


class GitError(Exception):
    """A curated, path-free error safe to surface to the client (like MailError).
    Raised only for an unknown repo or a disallowed verb — never carries a path."""


# --------------------------------------------------------------------------- #
#  Config / path normalisation
# --------------------------------------------------------------------------- #
def _config() -> dict:
    """The agent_view config — reuse server.load_config so there is ONE reader of
    the config file. Falls back to reading the file directly if server can't be
    imported (it always can in-process, but tests may import gitviz first)."""
    try:
        import server
        return server.load_config()
    except Exception:
        try:
            return json.loads((HERE / "agent_view.config.json").read_text(encoding="utf-8"))
        except Exception:
            return {}


def _norm(p) -> str:
    """realpath + normcase, the canonical form used for EVERY path comparison.
    On Windows this lower-cases and folds / to \\, so two spellings of the same
    repo compare equal. Empty string on any failure (a non-path is never known)."""
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
#  Discovery — bounded scan of git_roots + explicit git_repos, deduped, cached.
# --------------------------------------------------------------------------- #
_discover_lock = threading.Lock()
_discover_cache: dict = {"at": 0.0, "data": None}
_gh_lock = threading.Lock()
_gh_cache: dict = {}             # normcase(path) -> {"at":, "data":}
_insights_lock = threading.Lock()
_insights_cache: dict = {}       # normcase(path) -> {"at":, "data":}
_heatmap_all_lock = threading.Lock()
_heatmap_all_cache: dict = {"at": 0.0, "data": None}   # the ONE aggregate, not per-repo

#: A small fixed HUD palette assigned round-robin (discovery order) to repos in
#: the aggregate heatmap, so each repo gets a distinct, theme-independent hue. The
#: front end colours each day cell by its dominant repo using this same list, so
#: keep it in sync with the JS lane palette family (cyan/violet/amber/green/pink…).
HEATMAP_PALETTE = ("#38e6ff", "#a78bfa", "#fbbf24", "#4ade80", "#f472b6",
                   "#38bdf8", "#2dd4bf", "#fb7185", "#c084fc", "#facc15",
                   "#60a5fa", "#34d399")


def reset_caches() -> None:
    """Drop the discover + github + insights + aggregate-heatmap caches. Used by
    tests that swap the config."""
    with _discover_lock:
        _discover_cache["at"] = 0.0
        _discover_cache["data"] = None
    with _gh_lock:
        _gh_cache.clear()
    with _insights_lock:
        _insights_cache.clear()
    with _heatmap_all_lock:
        _heatmap_all_cache["at"] = 0.0
        _heatmap_all_cache["data"] = None


def _looks_like_repo(rp: str) -> bool:
    try:
        return os.path.isdir(rp) and os.path.exists(os.path.join(rp, ".git"))
    except OSError:
        return False


def _scan_root(root: str, out: list, seen: set) -> None:
    """DFS under one root for directories containing a `.git`, bounded by
    DISCOVER_DEPTH and MAX_REPOS, pruning SKIP_DIRS and dot-dirs. A found repo is
    recorded and NOT descended into (its own subdirs are not separate repos here)."""
    if not os.path.isdir(root):
        return
    stack = [(root, 0)]
    while stack and len(out) < MAX_REPOS:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except (OSError, PermissionError):
            continue
        if any(e.name == ".git" for e in entries):
            rp = os.path.realpath(d)
            key = os.path.normcase(rp)
            if key not in seen:
                seen.add(key)
                out.append(rp)
            continue                          # a repo is a leaf for this scan
        if depth >= DISCOVER_DEPTH:
            continue
        for e in entries:
            try:
                if (e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS
                        and not e.name.startswith(".")):
                    stack.append((e.path, depth + 1))
            except OSError:
                continue


def _discover_scan() -> list:
    cfg = _config()
    roots = list(cfg.get("git_roots")) if isinstance(cfg.get("git_roots"), list) else []
    # The config is tracked and shared across machines; PROJECTS_ROOT is the
    # per-machine location of the projects (C:/projects vs E:/POSAO) — scan it too.
    pr = (os.environ.get("PROJECTS_ROOT") or "").strip()
    if pr and pr not in roots:
        roots.append(pr)
    explicit = cfg.get("git_repos") if isinstance(cfg.get("git_repos"), list) else []
    out: list = []
    seen: set = set()
    for p in explicit:
        if len(out) >= MAX_REPOS:
            break
        rp = os.path.realpath(str(p))
        key = os.path.normcase(rp)
        if key not in seen and _looks_like_repo(rp):
            seen.add(key)
            out.append(rp)
    for root in roots:
        if len(out) >= MAX_REPOS:
            break
        try:
            _scan_root(os.path.realpath(str(root)), out, seen)
        except OSError:
            continue
    return out[:MAX_REPOS]


def discover() -> list:
    """Realpathed, deduped repo paths from git_roots (bounded scan) + git_repos.
    Cached ~DISCOVER_TTL so a page refresh does not re-walk the tree each time."""
    now = time.time()
    with _discover_lock:
        if _discover_cache["data"] is not None and now - _discover_cache["at"] < DISCOVER_TTL:
            return list(_discover_cache["data"])
    data = _discover_scan()
    with _discover_lock:
        _discover_cache["at"] = time.time()
        _discover_cache["data"] = data
    return list(data)


def is_known_repo(path) -> bool:
    """The whitelist gate: True only when `path` normalises to a discovered repo.
    Every route that takes a client `path` calls this BEFORE any git runs."""
    if not isinstance(path, str) or not path.strip():
        return False
    target = _norm(path)
    if not target:
        return False
    return target in {os.path.normcase(r) for r in discover()}


# --------------------------------------------------------------------------- #
#  The one git door — argv list, shell=False, read-only verb, cwd=<repo>, capped.
# --------------------------------------------------------------------------- #
def _git(path: str, args: list, timeout: float = GIT_TIMEOUT):
    """Run one read-only git command in `path`, returning stdout (str) on success
    or None on any failure/non-zero. A non-zero exit is normal for some reads
    (e.g. rev-list @{u}... with no upstream), so callers default from None. Raises
    GitError for a disallowed verb — a guard against a future mutating caller."""
    if not args or args[0] not in _ALLOWED_GIT:
        raise GitError("disallowed git verb")
    if args[0] == "remote" and (len(args) < 2 or args[1] != "get-url"):
        raise GitError("disallowed git verb")
    try:
        proc = subprocess.run(
            ["git", *args], cwd=path, shell=False,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


# --------------------------------------------------------------------------- #
#  Small pure parsers — split out so status/metrics are testable on fixtures.
# --------------------------------------------------------------------------- #
def _count_lines(text) -> int:
    if not text:
        return 0
    return sum(1 for ln in text.splitlines() if ln.strip())


def _parse_ahead_behind(text):
    """`rev-list --count --left-right @{u}...HEAD` -> "<behind>\\t<ahead>"; the
    left side is @{u} (commits we are behind), the right side is HEAD (ahead)."""
    if not text:
        return (0, 0)
    parts = text.split()
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return (int(parts[0]), int(parts[1]))     # (behind, ahead)
    return (0, 0)


def _sum_numstat(text):
    """Sum added/deleted over `log --numstat` output. Binary files show `-\\t-` and
    are skipped."""
    add = dele = 0
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            add += int(parts[0])
            dele += int(parts[1])
    return add, dele


def _bucket_daily(iso_dates, today, days: int = 14) -> list:
    """A `days`-long daily commit-count array for a sparkline, oldest->newest:
    index 0 is (today-(days-1)), index days-1 is today. `iso_dates` are
    'YYYY-MM-DD' strings; anything outside the window is ignored."""
    from datetime import timedelta
    counts = [0] * days
    index_of = {}
    for i in range(days):
        d = today - timedelta(days=(days - 1 - i))
        index_of[d.isoformat()] = i
    for s in iso_dates or []:
        i = index_of.get((s or "").strip())
        if i is not None:
            counts[i] += 1
    return counts


# --------------------------------------------------------------------------- #
#  Status + metrics
# --------------------------------------------------------------------------- #
#: The status fields repo_status carries beyond path/name. repos_overview omits
#: exactly these on a repo it does not stat, so "was git run?" == "are these set?".
_STATUS_FIELDS = ("branch", "dirty", "ahead", "behind", "conflicts")


def _repo_name(path: str) -> str:
    """The display name for a repo path (its final path component). One writer so
    repo_status and repos_overview cannot drift on how a repo is labelled."""
    return os.path.basename(path.rstrip("\\/")) or path


def repo_status(path: str) -> dict:
    if not is_known_repo(path):
        raise GitError("unknown repo")
    branch = (_git(path, ["rev-parse", "--abbrev-ref", "HEAD"]) or "").strip()
    dirty = _count_lines(_git(path, ["status", "--porcelain"]))
    behind, ahead = _parse_ahead_behind(
        _git(path, ["rev-list", "--count", "--left-right", "@{u}...HEAD"]))
    conflicts = _count_lines(_git(path, ["diff", "--name-only", "--diff-filter=U"]))
    return {"path": path, "name": _repo_name(path),
            "branch": branch, "dirty": dirty, "ahead": ahead, "behind": behind,
            "conflicts": conflicts}


def metrics(path: str) -> dict:
    if not is_known_repo(path):
        raise GitError("unknown repo")
    commits7d = _count_lines(
        _git(path, ["log", "--since=7 days ago", "--pretty=format:%H"]))
    daily_dates = [ln.strip() for ln in (
        _git(path, ["log", "--since=14 days ago",
                    "--date=format:%Y-%m-%d", "--pretty=format:%cd"]) or ""
    ).splitlines() if ln.strip()]
    daily14 = _bucket_daily(daily_dates, date.today(), 14)
    churn_add, churn_del = _sum_numstat(
        _git(path, ["log", "--since=7 days ago", "--numstat", "--pretty=format:"]))
    contributors = _count_lines(_git(path, ["shortlog", "-sne", "--all"]))
    branches = _count_lines(
        _git(path, ["for-each-ref", "--format=%(refname:short)", "refs/heads"]))
    last_commit = (_git(path, ["log", "-1", "--pretty=format:%cI"]) or "").strip()
    return {"commits7d": commits7d, "daily14": daily14, "churnAdd": churn_add,
            "churnDel": churn_del, "contributors": contributors,
            "branches": branches, "lastCommit": last_commit}


# --------------------------------------------------------------------------- #
#  Commit graph — parse + lane/column layout (the part the front end can't do).
# --------------------------------------------------------------------------- #
def parse_log(text: str) -> list:
    """Parse `git log --format=<_LOG_FORMAT>` into commit dicts. Each line is
    hash US parents US author US dateISO US subject US refs; parents/refs are
    space- and comma-separated respectively."""
    commits = []
    for line in (text or "").split("\n"):
        if not line.strip():
            continue
        f = line.split(_US)
        if len(f) < 6:
            f = f + [""] * (6 - len(f))
        h, parents_s, author, date_s, subject, refs_s = f[0], f[1], f[2], f[3], f[4], f[5]
        commits.append({
            "hash": h.strip(),
            "parents": [p for p in parents_s.split() if p],
            "author": author,
            "date": date_s.strip(),
            "subject": subject,
            "refs": [r.strip() for r in refs_s.split(",") if r.strip()],
        })
    return commits


def _first_free(lanes: list) -> int:
    for i, x in enumerate(lanes):
        if x is None:
            return i
    return len(lanes)


def _is_head_ref(refs) -> bool:
    return any(r == "HEAD" or r.startswith("HEAD ->") for r in (refs or []))


def build_graph(commits: list) -> dict:
    """Assign a column per commit and resolve parent edge columns, newest->oldest.

    Each lane holds the hash of the commit expected to occupy it next. A commit
    takes the leftmost lane expecting it (a new lane if it is a tip); other lanes
    expecting the same hash CONVERGE and are freed (that is a lane closing). The
    first parent continues the commit's own column; each extra parent reuses an
    existing lane already heading to it or OPENS a new lane (a merge fanning out).
    A root commit (no parents) frees its lane."""
    lanes: list = []
    rows: list = []
    head = ""
    for idx, c in enumerate(commits):
        h = c["hash"]
        parents = c["parents"]
        expecting = [i for i, x in enumerate(lanes) if x == h]
        if expecting:
            col = expecting[0]
            for j in expecting[1:]:
                lanes[j] = None                    # converged lanes close
        else:
            col = _first_free(lanes)
            if col == len(lanes):
                lanes.append(None)
        parent_cols = []
        if parents:
            lanes[col] = parents[0]                 # first parent continues the column
            parent_cols.append({"hash": parents[0], "col": col})
            for p in parents[1:]:
                existing = next((i for i, x in enumerate(lanes) if x == p), None)
                if existing is None:
                    existing = _first_free(lanes)
                    if existing == len(lanes):
                        lanes.append(None)
                    lanes[existing] = p             # a new lane opens for this parent
                parent_cols.append({"hash": p, "col": existing})
        else:
            lanes[col] = None                       # root: lane closes
        is_head = _is_head_ref(c["refs"])
        if is_head:
            head = h
        rows.append({
            "hash": h, "short": h[:7], "col": col, "row": idx,
            "parents": parent_cols, "subject": c["subject"],
            "author": c["author"], "date": c["date"], "refs": c["refs"],
            "isHead": is_head,
        })
    max_col = max((r["col"] for r in rows), default=0)
    for r in rows:
        for pc in r["parents"]:
            if pc["col"] > max_col:
                max_col = pc["col"]
    return {"commits": rows, "maxCol": max_col, "head": head}


def commit_graph(path: str, limit: int = DEFAULT_GRAPH_LIMIT) -> dict:
    if not is_known_repo(path):
        raise GitError("unknown repo")
    try:
        lim = min(max(int(limit), 1), MAX_COMMITS)
    except (TypeError, ValueError):
        lim = DEFAULT_GRAPH_LIMIT
    text = _git(path, ["log", "--topo-order", "--format=" + _LOG_FORMAT,
                       "-n", str(lim), "--all"])
    return build_graph(parse_log(text or ""))


# --------------------------------------------------------------------------- #
#  GitHub — prefer the `gh` CLI (no stored token), else a token, else disabled.
#  build_github is PURE over already-fetched inputs so it is testable offline;
#  _github_io does the IO and github() caches per-repo (rate limits).
# --------------------------------------------------------------------------- #
def _parse_github_remote(url):
    """(owner, repo) from an origin URL, or None if it is not a github remote.
    Handles https://github.com/o/r(.git), git@github.com:o/r(.git),
    ssh://git@github.com/o/r(.git)."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    low = u.lower()
    if "github.com" not in low:
        return None
    rest = None
    if low.startswith("git@github.com:"):
        rest = u[len("git@github.com:"):]
    else:
        marker = "github.com/"
        i = low.find(marker)
        if i != -1:
            rest = u[i + len(marker):]
    if not rest:
        return None
    rest = rest.strip("/")
    if rest.endswith(".git"):
        rest = rest[:-4]
    parts = [p for p in rest.split("/") if p]
    if len(parts) < 2:
        return None
    return (parts[0], parts[1])


def _summarize_checks(rollup) -> str:
    """gh statusCheckRollup -> "<passed>/<total>", or "" when there are no checks."""
    if not isinstance(rollup, list) or not rollup:
        return ""
    total = passed = 0
    for c in rollup:
        if not isinstance(c, dict):
            continue
        total += 1
        st = str(c.get("conclusion") or c.get("state") or "").upper()
        if st in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            passed += 1
    return f"{passed}/{total}" if total else ""


def _map_gh_runs(items) -> list:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({
            "name": it.get("name") or it.get("displayTitle") or "",
            "status": it.get("conclusion") or it.get("status") or "",
            "when": it.get("createdAt") or "",
        })
    return out


def _map_gh_prs(items) -> list:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({
            "number": it.get("number"),
            "title": it.get("title") or "",
            "additions": it.get("additions") or 0,
            "deletions": it.get("deletions") or 0,
            "checks": _summarize_checks(it.get("statusCheckRollup")),
        })
    return out


def build_github(url, gh_present, gh_runs_json, gh_pr_json, token, rest_fetch=None) -> dict:
    """Assemble the github() payload from already-resolved inputs. Pure, so the
    no-gh/no-token and gh-json paths are testable without a network."""
    owner_repo = _parse_github_remote(url) if url else None
    if owner_repo is None:
        return {"enabled": False, "reason": "not a github remote"}
    owner, repo = owner_repo
    gh_ok = bool(gh_present) and gh_runs_json is not None
    if not gh_ok and not token:
        return {"enabled": False, "reason": GITHUB_SETUP_HINT}
    if gh_ok:
        return {"enabled": True, "source": "gh", "owner": owner, "repo": repo,
                "runs": _map_gh_runs(gh_runs_json), "prs": _map_gh_prs(gh_pr_json or [])}
    runs, prs = (rest_fetch or _rest_github)(owner, repo, token)
    return {"enabled": True, "source": "token", "owner": owner, "repo": repo,
            "runs": runs, "prs": prs}


def _github_token() -> str:
    t = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if t:
        return t
    return str(_config().get("github_token") or "").strip()


def _gh_json(path: str, args: list):
    """Run `gh <args>` (a read-only list command) in `path`, returning the parsed
    JSON list or None (gh missing, not authed, non-zero, or non-list)."""
    exe = shutil.which("gh")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, *args], cwd=path, shell=False,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "null")
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, list) else None


def _rest_get(url: str, token: str):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "agent-view-gitviz",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=GIT_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "null")
    except Exception:
        return None                                # the token must never leak via an error


def _rest_github(owner: str, repo: str, token: str):
    """REST fallback when gh is absent. Bounded to GH_LIST_LIMIT; per-PR diff
    stats are not on the list endpoint, so additions/deletions stay 0 here."""
    runs = _rest_get(
        f"https://api.github.com/repos/{owner}/{repo}/actions/runs?per_page={GH_LIST_LIMIT}",
        token)
    run_items = runs.get("workflow_runs") if isinstance(runs, dict) else []
    out_runs = []
    for r in (run_items or [])[:GH_LIST_LIMIT]:
        if not isinstance(r, dict):
            continue
        out_runs.append({"name": r.get("name") or r.get("display_title") or "",
                         "status": r.get("conclusion") or r.get("status") or "",
                         "when": r.get("created_at") or ""})
    prs = _rest_get(
        f"https://api.github.com/repos/{owner}/{repo}/pulls?per_page={GH_LIST_LIMIT}&state=open",
        token)
    out_prs = []
    for p in (prs or [])[:GH_LIST_LIMIT]:
        if not isinstance(p, dict):
            continue
        out_prs.append({"number": p.get("number"), "title": p.get("title") or "",
                        "additions": 0, "deletions": 0, "checks": ""})
    return out_runs, out_prs


def _github_io(path: str) -> dict:
    url = _git(path, ["remote", "get-url", "origin"])
    gh_present = bool(shutil.which("gh"))
    gh_runs = _gh_json(path, _RUNS_ARGS) if gh_present else None
    gh_pr = _gh_json(path, _PRS_ARGS) if (gh_present and gh_runs is not None) else None
    return build_github(url, gh_present, gh_runs, gh_pr, _github_token())


def github(path: str) -> dict:
    """{enabled, runs, prs} for a repo's GitHub Actions + PRs, or
    {enabled:false, reason}. Cached ~GITHUB_TTL per repo to spare rate limits."""
    if not is_known_repo(path):
        raise GitError("unknown repo")
    key = _norm(path)
    now = time.time()
    with _gh_lock:
        c = _gh_cache.get(key)
        if c and now - c["at"] < GITHUB_TTL:
            return c["data"]
    data = _github_io(path)
    with _gh_lock:
        _gh_cache[key] = {"at": time.time(), "data": data}
    return data


# --------------------------------------------------------------------------- #
#  Insights — five richer Git-tab panels, each a PURE parser over one bounded
#  read-only git call. The parsers are split from IO so they test on fixtures;
#  insights() caches the whole dict ~INSIGHTS_TTL per repo, and every panel
#  degrades to [] on any failure so one bad/slow sub-command never sinks the rest.
# --------------------------------------------------------------------------- #
def _is_iso_date(s) -> bool:
    """True for a 'YYYY-MM-DD' string (git --date=short). Deliberately strict so a
    numstat path or a filename can never be mistaken for a date row."""
    return (isinstance(s, str) and len(s) == 10 and s[4] == "-" and s[7] == "-"
            and s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit())


def _iso_week(datestr: str) -> str:
    """'YYYY-MM-DD' -> ISO 'YYYY-Www' (zero-padded week, so string sort == time
    order within and across years)."""
    cal = date.fromisoformat(datestr).isocalendar()
    return f"{cal[0]}-W{cal[1]:02d}"


def _strip_email(s: str) -> str:
    """'Name <email>' -> 'Name'. shortlog -e appends the email; the panel shows the
    person, and stripping lets two addresses for one name aggregate."""
    i = s.rfind("<")
    if i != -1:
        s = s[:i]
    return s.strip()


def parse_heatmap(text: str) -> list:
    """`log --date=short --format=%ad` -> [{date, count}] for days that HAVE
    commits (gaps are omitted; the front end lays out the grid), oldest->newest."""
    counts: dict = {}
    for line in (text or "").splitlines():
        s = line.strip()
        if _is_iso_date(s):
            counts[s] = counts.get(s, 0) + 1
    return [{"date": d, "count": counts[d]} for d in sorted(counts)]


def parse_contributors(text: str, top: int = TOP_CONTRIBUTORS) -> list:
    """`shortlog -sne` (`<count>\\t<name> <email>`) -> top-N [{name, commits}],
    commits-desc then name. Emails are stripped and their counts aggregated."""
    tally: dict = {}
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split("\t", 1)
        if len(parts) != 2:                       # some builds separate on spaces
            parts = raw.split(None, 1)
        if len(parts) != 2 or not parts[0].strip().isdigit():
            continue
        name = _strip_email(parts[1].strip())
        if not name:
            continue
        tally[name] = tally.get(name, 0) + int(parts[0].strip())
    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [{"name": n, "commits": c} for n, c in ranked[:top]]


def parse_churn(text: str, weeks: int = CHURN_WEEKS) -> list:
    """`log --numstat --date=short --format=%ad` -> last-N-weeks [{week, add, del}].
    The stream interleaves a date line, a blank, then `add\\tdel\\tpath` rows; each
    numstat row is summed into the week of the most recent date line. Binary rows
    (`-\\t-\\tpath`) are skipped — they carry no line counts."""
    buckets: dict = {}                            # week -> [add, del]
    current = None
    for line in (text or "").splitlines():
        if "\t" in line:                          # a numstat row
            parts = line.split("\t")
            if (current is not None and len(parts) >= 3
                    and parts[0].isdigit() and parts[1].isdigit()):
                b = buckets.setdefault(current, [0, 0])
                b[0] += int(parts[0])
                b[1] += int(parts[1])
            continue
        s = line.strip()
        if _is_iso_date(s):
            current = _iso_week(s)
    ranked = sorted(buckets.items())[-weeks:]      # keep the most recent N weeks
    return [{"week": w, "add": a, "del": d} for w, (a, d) in ranked]


def parse_hotspots(text: str, top: int = TOP_HOTSPOTS) -> list:
    """`log --name-only --format=` -> top-N most-CHANGED files [{path, changes}]:
    each path appears once per commit that touched it, so frequency == change
    count. Blank separator lines are ignored."""
    counts: dict = {}
    for line in (text or "").splitlines():
        p = line.strip()
        if not p:
            continue
        counts[p] = counts.get(p, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [{"path": p, "changes": c} for p, c in ranked[:top]]


def parse_filetypes(text: str, top: int = TOP_FILETYPES) -> list:
    """`ls-files` -> top-N [{ext, count}] by extension (lower-cased, no dot). A file
    with no extension (including a dotfile like .gitignore) buckets as '(none)'."""
    counts: dict = {}
    for line in (text or "").splitlines():
        p = line.strip()
        if not p:
            continue
        ext = os.path.splitext(p)[1].lower().lstrip(".")
        key = ext if ext else "(none)"
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"ext": e, "count": c} for e, c in ranked[:top]]


#: Each panel: (key, argv, parser). The argv is bounded by --since / a top-N slice
#: in the parser; the parser is pure so every panel is tested on fabricated output.
_INSIGHT_PANELS = (
    ("heatmap", ["log", f"--since={INSIGHTS_DAYS}.days", "--date=short",
                 "--format=%ad"], parse_heatmap),
    ("contributors", ["shortlog", "-sne", "--all",
                      f"--since={INSIGHTS_DAYS}.days"], parse_contributors),
    ("churn", ["log", f"--since={CHURN_WEEKS}.weeks", "--numstat",
               "--date=short", "--format=%ad"], parse_churn),
    ("hotspots", ["log", f"--since={INSIGHTS_DAYS}.days", "--name-only",
                  "--format="], parse_hotspots),
    ("filetypes", ["ls-files"], parse_filetypes),
)


def _insights_io(path: str) -> dict:
    """Run each panel's git call and parse it, independently. A single panel that
    raises (or whose git call fails) degrades to [] and never sinks the others."""
    out: dict = {}
    for key, args, parser in _INSIGHT_PANELS:
        try:
            out[key] = parser(_git(path, args) or "")
        except Exception:
            out[key] = []                          # one bad panel, not one bad tab
    return out


def insights(path: str, config_service=None) -> dict:
    """{heatmap, contributors, churn, hotspots, filetypes} for a repo, each from one
    bounded read-only git call. Cached ~INSIGHTS_TTL per repo (the Git tab polls).
    `config_service` is accepted for caller symmetry; the panels are pure git and
    read no config."""
    if not is_known_repo(path):
        raise GitError("unknown repo")
    key = _norm(path)
    now = time.time()
    with _insights_lock:
        c = _insights_cache.get(key)
        if c and now - c["at"] < INSIGHTS_TTL:
            return c["data"]
    data = _insights_io(path)
    with _insights_lock:
        _insights_cache[key] = {"at": time.time(), "data": data}
    return data


# --------------------------------------------------------------------------- #
#  Aggregate commit heatmap — daily commit counts across ALL known repos, so the
#  Git tab's calendar reflects the whole machine, not just the selected repo. It
#  reuses the heatmap panel's git call + parse_heatmap per repo; a repo that fails
#  degrades to zero days and never sinks the aggregate. Cached ~HEATMAP_ALL_TTL.
# --------------------------------------------------------------------------- #
def _heatmap_all_labels(paths: list) -> list:
    """One UNIQUE display label per repo path, preserving order. Basename first;
    on a collision fall back to '<parent>/<name>', then an ' (n)' suffix — because
    the aggregate keys each day's breakdown by label, and two repos sharing a
    basename must not silently merge their counts into one hue."""
    used: set = set()
    labels: list = []
    for p in paths:
        base = _repo_name(p)
        label = base
        if label in used:
            parent = os.path.basename(os.path.dirname(p.rstrip("\\/")))
            label = (parent + "/" + base) if parent else base
        i = 2
        while label in used:
            label = f"{base} ({i})"
            i += 1
        used.add(label)
        labels.append(label)
    return labels


def _heatmap_all_io() -> dict:
    """`{repos:[{name,color,path}], days:{'YYYY-MM-DD':{'<label>':count,...}}}`
    over every discovered repo. One bounded read-only git call per repo (the same
    one the per-repo heatmap panel uses), parsed with parse_heatmap; a failing repo
    contributes no days rather than raising."""
    repos = discover()
    labels = _heatmap_all_labels(repos)
    out_repos: list = []
    days: dict = {}
    for idx, path in enumerate(repos):
        label = labels[idx]
        color = HEATMAP_PALETTE[idx % len(HEATMAP_PALETTE)]
        out_repos.append({"name": label, "color": color, "path": path})
        try:
            text = _git(path, ["log", f"--since={INSIGHTS_DAYS}.days",
                               "--date=short", "--format=%ad"])
            parsed = parse_heatmap(text or "")     # [{date,count}], commit-days only
        except Exception:
            parsed = []                            # one bad repo, not one bad aggregate
        for row in parsed:
            day = days.setdefault(row["date"], {})
            day[label] = day.get(label, 0) + row["count"]
    return {"repos": out_repos, "days": days}


def heatmap_all() -> dict:
    """The aggregate daily commit heatmap across ALL known repos. Takes no path arg
    (it enumerates the discovered/whitelisted set itself), so unlike the per-repo
    reads it needs no is_known_repo gate. Cached ~HEATMAP_ALL_TTL (the Git tab
    fetches it once per open + on manual refresh, never on the fast repos poll)."""
    now = time.time()
    with _heatmap_all_lock:
        c = _heatmap_all_cache
        if c["data"] is not None and now - c["at"] < HEATMAP_ALL_TTL:
            return c["data"]
    data = _heatmap_all_io()
    with _heatmap_all_lock:
        _heatmap_all_cache["at"] = time.time()
        _heatmap_all_cache["data"] = data
    return data


# --------------------------------------------------------------------------- #
#  Active-repo detection + route aggregators (thin: the route just calls these).
# --------------------------------------------------------------------------- #
def active_repos(session_cwds, repos=None) -> set:
    """The normcased repo paths that CONTAIN a live session's cwd (== the repo, or
    a descendant of it). The server passes the cwds it already tracks."""
    if repos is None:
        repos = discover()
    normed = {_norm(r) for r in repos}
    active = set()
    for cwd in session_cwds or []:
        if not cwd or not isinstance(cwd, str):
            continue
        c = _norm(cwd)
        if not c:
            continue
        for nr in normed:
            if c == nr or c.startswith(nr + os.sep):
                active.add(nr)
    return active


def repos_overview(session_cwds, watch=(), full=False) -> dict:
    """`{repos:[...]}` — one row per discovered repo, but git runs on only the
    repos that MATTER, not all of them. This is the whole point: the Git tab polls
    every few seconds, and shelling out to `git status` on every discovered repo
    each time was wasteful (25 repos -> 100 git calls per poll).

    Base fields are CHEAP and touch no git: `{path, name, active, pinned}`, where
    `active` means a live session's cwd is inside the repo (pure path match) and
    `pinned` means the repo is in `watch`. The full status (`_STATUS_FIELDS`:
    branch/dirty/ahead/behind/conflicts) is computed ONLY when `full` is True, or
    the repo is active, or the repo is pinned; every other repo OMITS those fields
    and git is never run on it.

    `watch` is a client-supplied list of repo paths (the page's pinned+selected
    set, re-sent each poll). Each entry is validated with is_known_repo, so an
    unknown/foreign path is dropped and never earns a git call — the same whitelist
    gate every other route uses. `full=True` is the one-time initial load; a poll
    passes `full=False` plus the small `watch` set, so it stats only active+pinned.

    Order is stable and useful to the front end: active repos first, then pinned,
    then the rest by name (so gitRepos[0] is the one you are working in)."""
    repos = discover()
    active = active_repos(session_cwds, repos)
    watched = {_norm(w) for w in (watch or ()) if is_known_repo(w)}
    out = []
    for r in repos:
        nr = _norm(r)
        is_active = nr in active
        is_pinned = nr in watched
        row = {"path": r, "name": _repo_name(r),
               "active": is_active, "pinned": is_pinned}
        if full or is_active or is_pinned:
            try:
                st = repo_status(r)
            except (GitError, OSError):
                st = {k: ("" if k == "branch" else 0) for k in _STATUS_FIELDS}
            for k in _STATUS_FIELDS:
                row[k] = st[k]
        out.append(row)
    out.sort(key=lambda x: (0 if x["active"] else 1 if x["pinned"] else 2,
                            (x["name"] or "").lower(), x["path"]))
    return {"repos": out}


def repo_detail(path: str) -> dict:
    """`{name,branch,head,commits,maxCol,branches,metrics,status}` for one repo."""
    st = repo_status(path)
    graph = commit_graph(path)
    m = metrics(path)
    return {"name": st["name"], "branch": st["branch"], "head": graph["head"],
            "commits": graph["commits"], "maxCol": graph["maxCol"],
            "branches": m["branches"], "metrics": m, "status": st}
