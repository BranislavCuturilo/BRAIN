#!/usr/bin/env python3
"""Offline tests for gitviz: the commit-graph lane algorithm, the status/metrics
parsers, the github assembly (fabricated gh JSON + the no-gh/no-token path), and
the security whitelist (discover / is_known_repo / active_repos).

Everything is exercised on FABRICATED git/gh output or a locally-constructed temp
repo — no external network, no credentials. One end-to-end integration test uses
a real temp `git` repo if git is on PATH (skipped otherwise). Run:
  python test_gitviz.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gitviz      # noqa: E402

_US = gitviz._US
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Graph lane algorithm — a fabricated history with a branch + merge:
#      A (root) -> B (main)        merged at E
#             \--> C (feature) ---/
#  Newest first, topo-ordered: E, B, C, A.  E has HEAD.
# --------------------------------------------------------------------------- #
def _log(rows):
    return "\n".join(_US.join(r) for r in rows) + "\n"


GRAPH_SAMPLE = _log([
    ["E", "B C", "Alice", "2026-08-09T10:00:00+00:00", "Merge feature into main", "HEAD -> main"],
    ["B", "A", "Alice", "2026-08-08T10:00:00+00:00", "work on main", ""],
    ["C", "A", "Bob", "2026-08-08T09:00:00+00:00", "feature work", "feature"],
    ["A", "", "Alice", "2026-08-07T10:00:00+00:00", "initial commit", ""],
])


def test_graph_lane_algorithm():
    commits = gitviz.parse_log(GRAPH_SAMPLE)
    check("parse_log: 4 commits parsed", len(commits) == 4, str(len(commits)))
    check("parse_log: merge commit has two parents",
          commits[0]["parents"] == ["B", "C"], str(commits[0]["parents"]))
    check("parse_log: root commit has no parents", commits[3]["parents"] == [], str(commits[3]))

    g = gitviz.build_graph(commits)
    by = {r["hash"]: r for r in g["commits"]}
    check("graph: HEAD is flagged and reported", g["head"] == "E" and by["E"]["isHead"] is True,
          f'head={g["head"]}')
    check("graph: non-HEAD commits are not flagged",
          not any(by[h]["isHead"] for h in ("B", "C", "A")), "")

    # First-parent-continues invariant: every commit's own column carries its
    # first parent's edge — that is the straight line down the lane.
    fp_ok = all(r["parents"][0]["col"] == r["col"] for r in g["commits"] if r["parents"])
    check("graph: first parent continues the commit's own column", fp_ok, "")

    # The merge OPENS a lane: E's two parent edges sit on two distinct columns.
    e_cols = sorted(pc["col"] for pc in by["E"]["parents"])
    check("graph: merge opens a second lane (parents on distinct columns)",
          e_cols == [0, 1], str(e_cols))
    check("graph: feature commit C sits in the branch lane (col 1)", by["C"]["col"] == 1,
          str(by["C"]["col"]))

    # The merge CLOSES a lane: B and C both point to A, so two lanes (cols 0 and
    # 1) converge into A, which lands in the leftmost (col 0) as a root.
    a_edges = sorted(pc["col"] for r in g["commits"] for pc in r["parents"] if pc["hash"] == "A")
    check("graph: two lanes converge into A (cols 0 and 1 close into it)",
          a_edges == [0, 1], str(a_edges))
    check("graph: root A lands leftmost and closes its lane (no parents)",
          by["A"]["col"] == 0 and by["A"]["parents"] == [], str(by["A"]))

    check("graph: maxCol is 1", g["maxCol"] == 1, str(g["maxCol"]))
    cols_ok = all(0 <= r["col"] <= g["maxCol"] for r in g["commits"])
    edges_ok = all(0 <= pc["col"] <= g["maxCol"] for r in g["commits"] for pc in r["parents"])
    check("graph: every column and edge is within [0, maxCol]", cols_ok and edges_ok, "")
    check("graph: rows carry row index and short hash",
          by["E"]["row"] == 0 and by["E"]["short"] == "E", str(by["E"]))


def test_graph_empty():
    g = gitviz.build_graph(gitviz.parse_log(""))
    check("graph(empty): no commits, maxCol 0, head ''",
          g["commits"] == [] and g["maxCol"] == 0 and g["head"] == "", str(g))


# --------------------------------------------------------------------------- #
#  Status / metrics parsers (pure, on fabricated git output).
# --------------------------------------------------------------------------- #
def test_status_parsers():
    check("count_lines: empty -> 0", gitviz._count_lines("") == 0)
    check("count_lines: two porcelain rows -> 2",
          gitviz._count_lines(" M a.py\n?? b.py\n") == 2, "")
    check("ahead_behind: '3\\t2' -> behind 3, ahead 2",
          gitviz._parse_ahead_behind("3\t2") == (3, 2), "")
    check("ahead_behind: no upstream / empty -> (0,0)",
          gitviz._parse_ahead_behind("") == (0, 0) and gitviz._parse_ahead_behind("x") == (0, 0), "")
    add, dele = gitviz._sum_numstat("1\t2\tf.py\n10\t3\tg.py\n-\t-\timg.png\n")
    check("sum_numstat: adds/dels summed, binary '-' skipped", (add, dele) == (11, 5), f"{add},{dele}")


def test_bucket_daily():
    today = date(2026, 8, 9)
    dates = ["2026-08-09", "2026-08-09", "2026-08-08", "2026-07-27", "2026-07-20"]
    counts = gitviz._bucket_daily(dates, today, 14)
    check("bucket_daily: 14-element array", len(counts) == 14, str(len(counts)))
    check("bucket_daily: today (index 13) counted twice", counts[13] == 2, str(counts))
    check("bucket_daily: yesterday (index 12) once", counts[12] == 1, str(counts))
    check("bucket_daily: oldest in-window day (index 0 = today-13) once", counts[0] == 1, str(counts))
    check("bucket_daily: a date older than the window is ignored", sum(counts) == 4, str(sum(counts)))


# --------------------------------------------------------------------------- #
#  GitHub assembly — fabricated gh JSON, the token path, and the disabled paths.
# --------------------------------------------------------------------------- #
def test_parse_github_remote():
    cases = {
        "https://github.com/owner/repo.git": ("owner", "repo"),
        "https://github.com/owner/repo": ("owner", "repo"),
        "git@github.com:owner/repo.git": ("owner", "repo"),
        "ssh://git@github.com/owner/repo.git": ("owner", "repo"),
    }
    for url, exp in cases.items():
        check(f"remote: {url} -> {exp}", gitviz._parse_github_remote(url) == exp,
              str(gitviz._parse_github_remote(url)))
    for url in ("https://gitlab.com/o/r.git", "", None, "not a url"):
        check(f"remote: non-github {url!r} -> None", gitviz._parse_github_remote(url) is None, "")


def test_summarize_checks():
    rollup = [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}, {"state": "SUCCESS"}]
    check("checks: 2 of 3 pass -> '2/3'", gitviz._summarize_checks(rollup) == "2/3",
          gitviz._summarize_checks(rollup))
    check("checks: no checks -> ''", gitviz._summarize_checks([]) == "", "")


def test_build_github_gh_path():
    runs = [{"name": "CI", "status": "completed", "conclusion": "success",
             "createdAt": "2026-08-09T10:00:00Z"}]
    prs = [{"number": 7, "title": "Add x", "additions": 10, "deletions": 2,
            "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]}]
    out = gitviz.build_github("git@github.com:owner/repo.git", True, runs, prs, "")
    check("github(gh): enabled + source gh",
          out.get("enabled") is True and out.get("source") == "gh", str(out))
    check("github(gh): owner/repo parsed", out.get("owner") == "owner" and out.get("repo") == "repo", str(out))
    r0 = (out.get("runs") or [{}])[0]
    check("github(gh): run maps conclusion->status, name, when",
          r0.get("name") == "CI" and r0.get("status") == "success" and r0.get("when"), str(r0))
    p0 = (out.get("prs") or [{}])[0]
    check("github(gh): pr maps number/additions/deletions/checks",
          p0.get("number") == 7 and p0.get("additions") == 10
          and p0.get("deletions") == 2 and p0.get("checks") == "1/2", str(p0))


def test_build_github_token_path():
    def fake_rest(owner, repo, token):
        check("github(token): token threaded to rest fetch", token == "tok", token)
        return ([{"name": "n", "status": "ok", "when": "w"}],
                [{"number": 1, "title": "p", "additions": 0, "deletions": 0, "checks": ""}])
    out = gitviz.build_github("https://github.com/o/r.git", False, None, None, "tok",
                              rest_fetch=fake_rest)
    check("github(token): enabled + source token",
          out.get("enabled") is True and out.get("source") == "token", str(out))
    check("github(token): runs/prs from the rest fetch",
          out.get("runs") and out.get("prs"), str(out))


def test_build_github_disabled_paths():
    out1 = gitviz.build_github("https://github.com/o/r.git", False, None, None, "")
    check("github(off): no gh + no token -> enabled false + setup hint",
          out1.get("enabled") is False and out1.get("reason") == gitviz.GITHUB_SETUP_HINT, str(out1))
    out2 = gitviz.build_github("https://github.com/o/r.git", True, None, None, "")
    check("github(off): gh present but unauthed (runs None) + no token -> disabled",
          out2.get("enabled") is False and out2.get("reason") == gitviz.GITHUB_SETUP_HINT, str(out2))
    out3 = gitviz.build_github("https://gitlab.com/o/r.git", True, [], [], "tok")
    check("github(off): non-github remote -> enabled false + 'not a github remote'",
          out3.get("enabled") is False and out3.get("reason") == "not a github remote", str(out3))


# --------------------------------------------------------------------------- #
#  Security: discover / is_known_repo / active_repos on a fabricated tree.
# --------------------------------------------------------------------------- #
def _mktree():
    """A temp tree: alpha/ and beta/ are repos (have .git); gamma/ is plain;
    node_modules/pkg/ has a .git that must be SKIPPED; deep/l2/l3/l4/ has a .git
    below the depth cap that must NOT be discovered."""
    root = Path(tempfile.mkdtemp(prefix="gitviz_tree_"))
    for name in ("alpha", "beta"):
        (root / name / ".git").mkdir(parents=True)
    (root / "gamma").mkdir()
    (root / "node_modules" / "pkg" / ".git").mkdir(parents=True)
    (root / "deep" / "l2" / "l3" / "l4" / ".git").mkdir(parents=True)
    return root


def _with_config(cfg):
    gitviz._config = lambda: cfg
    gitviz.reset_caches()


def test_discover_and_whitelist():
    root = _mktree()
    orig_config = gitviz._config
    try:
        _with_config({"git_roots": [str(root)], "git_repos": []})
        repos = gitviz.discover()
        normed = {os.path.normcase(r) for r in repos}
        alpha = os.path.normcase(os.path.realpath(str(root / "alpha")))
        beta = os.path.normcase(os.path.realpath(str(root / "beta")))
        gamma = str(root / "gamma")
        deep_repo = str(root / "deep" / "l2" / "l3" / "l4")
        nm_repo = str(root / "node_modules" / "pkg")

        check("discover: finds alpha and beta", alpha in normed and beta in normed, str(repos))
        check("discover: plain gamma is not a repo",
              os.path.normcase(os.path.realpath(gamma)) not in normed, str(repos))
        check("discover: node_modules is skipped (its .git never discovered)",
              os.path.normcase(os.path.realpath(nm_repo)) not in normed, str(repos))
        check("discover: a repo below the depth cap is not discovered",
              os.path.normcase(os.path.realpath(deep_repo)) not in normed, str(repos))

        check("is_known_repo: a discovered repo IS known", gitviz.is_known_repo(str(root / "alpha")))
        check("is_known_repo: the scan root itself is NOT a known repo",
              gitviz.is_known_repo(str(root)) is False, "")
        check("is_known_repo: a plain sibling dir is NOT known",
              gitviz.is_known_repo(gamma) is False, "")
        # traversal that ESCAPES a repo resolves to a non-repo -> rejected
        escape = os.path.join(str(root / "alpha"), "..")
        check("is_known_repo: '<repo>/..' escapes to a non-repo and is rejected",
              gitviz.is_known_repo(escape) is False, escape)
        check("is_known_repo: a wholly-unrelated system path is rejected",
              gitviz.is_known_repo(os.path.join(str(root), "..", "..", "Windows")) is False, "")
        for bad in ("", None, 12345, "   "):
            check(f"is_known_repo: {bad!r} rejected", gitviz.is_known_repo(bad) is False, "")
    finally:
        gitviz._config = orig_config
        gitviz.reset_caches()
        shutil.rmtree(root, ignore_errors=True)


def test_active_repos():
    base = Path(tempfile.mkdtemp(prefix="gitviz_active_"))
    alpha = str(base / "alpha")
    beta = str(base / "beta")
    os.makedirs(alpha)
    os.makedirs(beta)
    n = gitviz._norm
    # a cwd DEEP inside alpha marks alpha active; a cwd under base-but-no-repo does not
    a1 = gitviz.active_repos([os.path.join(alpha, "sub", "dir"), os.path.join(str(base), "gamma")],
                             repos=[alpha, beta])
    check("active: a cwd inside alpha marks alpha active", n(alpha) in a1, str(a1))
    check("active: alpha-only (beta not active)", n(beta) not in a1, str(a1))
    check("active: a non-repo cwd marks nothing", len(a1) == 1, str(a1))
    a2 = gitviz.active_repos([beta], repos=[alpha, beta])
    check("active: a cwd equal to the repo root marks it active", a2 == {n(beta)}, str(a2))
    a3 = gitviz.active_repos([None, "", 123], repos=[alpha, beta])
    check("active: junk cwds are ignored", a3 == set(), str(a3))
    shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  repos_overview: the poll-cost boundary. Git must run on ONLY the repos that
#  matter (full / active / pinned) — never on all discovered repos. We record
#  every repo_status call to prove the unwatched repos are never stat'd.
# --------------------------------------------------------------------------- #
def test_repos_overview_selective_status():
    root = Path(tempfile.mkdtemp(prefix="gitviz_overview_"))
    for name in ("alpha", "beta", "delta"):
        (root / name / ".git").mkdir(parents=True)
    (root / "gamma").mkdir()                       # a plain dir, never discovered
    n = gitviz._norm
    alpha, beta, delta = (str(root / x) for x in ("alpha", "beta", "delta"))
    gamma = str(root / "gamma")

    orig_config = gitviz._config
    orig_status = gitviz.repo_status
    stat_calls = []

    def fake_status(p):
        # stand-in for the 4-git-call real status; records that git WOULD have run
        stat_calls.append(n(p))
        return {"path": p, "name": gitviz._repo_name(p), "branch": "main",
                "dirty": 1, "ahead": 0, "behind": 0, "conflicts": 0}
    try:
        _with_config({"git_roots": [str(root)], "git_repos": []})
        gitviz.repo_status = fake_status

        # --- full=True: the one-time initial load stats EVERY discovered repo ---
        stat_calls.clear()
        out = gitviz.repos_overview([], watch=[], full=True)
        rows = {r["name"]: r for r in out["repos"]}
        check("overview(full): all three repos present",
              set(rows) == {"alpha", "beta", "delta"}, str(sorted(rows)))
        check("overview(full): every repo carries the status fields",
              all(all(k in rows[x] for k in gitviz._STATUS_FIELDS) for x in rows), str(out))
        check("overview(full): git ran on all three",
              set(stat_calls) == {n(alpha), n(beta), n(delta)}, str(stat_calls))

        # --- a POLL: full=False, watch=[alpha], a live session sits inside beta.
        #     ONLY alpha (pinned) + beta (active) get status; delta carries none
        #     and repo_status is NEVER called on it — the whole point of the change.
        stat_calls.clear()
        out = gitviz.repos_overview([beta], watch=[alpha], full=False)
        rows = {r["name"]: r for r in out["repos"]}
        check("overview(poll): alpha is pinned and carries status",
              rows["alpha"]["pinned"] is True and "branch" in rows["alpha"], str(rows["alpha"]))
        check("overview(poll): beta is active and carries status",
              rows["beta"]["active"] is True and "branch" in rows["beta"], str(rows["beta"]))
        check("overview(poll): delta carries NO status fields",
              all(k not in rows["delta"] for k in gitviz._STATUS_FIELDS), str(rows["delta"]))
        check("overview(poll): delta is neither active nor pinned",
              rows["delta"]["active"] is False and rows["delta"]["pinned"] is False, str(rows["delta"]))
        check("overview(poll): git ran on ONLY alpha + beta",
              set(stat_calls) == {n(alpha), n(beta)}, str(stat_calls))
        check("overview(poll): delta was never stat'd (git not run on it)",
              n(delta) not in stat_calls, str(stat_calls))

        # --- an unknown/foreign watch path is dropped by the whitelist: not
        #     pinned, and no git runs anywhere (nothing else active/watched/full).
        stat_calls.clear()
        out = gitviz.repos_overview([], watch=[gamma], full=False)
        rows = {r["name"]: r for r in out["repos"]}
        check("overview(foreign watch): a non-repo path pins nothing",
              not any(rows[x]["pinned"] for x in rows), str(out))
        check("overview(foreign watch): git ran on NOTHING", stat_calls == [], str(stat_calls))

        # --- a bare poll with nothing active and nothing watched runs git on 0 ---
        stat_calls.clear()
        gitviz.repos_overview([], watch=[], full=False)
        check("overview(bare poll): git runs on ZERO repos", stat_calls == [], str(stat_calls))

        # --- stable order: active first, then pinned, then the rest by name ---
        out = gitviz.repos_overview([delta], watch=[beta], full=False)
        order = [r["name"] for r in out["repos"]]
        check("overview(order): active(delta), then pinned(beta), then alpha by name",
              order == ["delta", "beta", "alpha"], str(order))
    finally:
        gitviz._config = orig_config
        gitviz.repo_status = orig_status
        gitviz.reset_caches()
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Integration: a REAL temp git repo with a branch + merge (skipped if no git).
# --------------------------------------------------------------------------- #
def _git_cli(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=Tester",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def _build_real_repo():
    repo = Path(tempfile.mkdtemp(prefix="gitviz_real_"))
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                   capture_output=True, text=True)
    (repo / "f.txt").write_text("1\n", encoding="utf-8")
    _git_cli(repo, "add", "f.txt")
    _git_cli(repo, "commit", "-m", "initial")
    (repo / "f.txt").write_text("1\n2\n", encoding="utf-8")
    _git_cli(repo, "commit", "-am", "main work")
    _git_cli(repo, "checkout", "-b", "feature")
    (repo / "g.txt").write_text("x\n", encoding="utf-8")
    _git_cli(repo, "add", "g.txt")
    _git_cli(repo, "commit", "-m", "feature work")
    _git_cli(repo, "checkout", "main")
    _git_cli(repo, "merge", "--no-ff", "-m", "merge feature", "feature")
    return repo


def test_real_repo_integration():
    if not shutil.which("git"):
        check("integration: (skipped, git not on PATH)", True)
        return
    orig_config = gitviz._config
    repo = None
    try:
        repo = _build_real_repo()
    except Exception as exc:
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
        gitviz._config = orig_config
        gitviz.reset_caches()
        check("integration: (skipped, repo setup failed)", True, f"{type(exc).__name__}: {exc}")
        return
    try:
        _with_config({"git_roots": [], "git_repos": [str(repo)]})
        check("integration: repo is discovered + known", gitviz.is_known_repo(str(repo)), "")
        st = gitviz.repo_status(str(repo))
        check("integration: branch is main", st["branch"] == "main", st["branch"])
        check("integration: clean tree is not dirty", st["dirty"] == 0, str(st))
        check("integration: no upstream -> ahead/behind 0", st["ahead"] == 0 and st["behind"] == 0, str(st))

        g = gitviz.commit_graph(str(repo))
        check("integration: graph has >= 4 commits", len(g["commits"]) >= 4, str(len(g["commits"])))
        check("integration: HEAD is flagged in the real graph", bool(g["head"]), str(g["head"]))
        check("integration: a branch+merge widens the graph (maxCol >= 1)", g["maxCol"] >= 1, str(g["maxCol"]))
        merge_rows = [r for r in g["commits"] if len(r["parents"]) == 2]
        check("integration: the merge commit has two parents", len(merge_rows) >= 1, str(len(merge_rows)))

        m = gitviz.metrics(str(repo))
        check("integration: >= 2 branches (main + feature)", m["branches"] >= 2, str(m["branches"]))
        check("integration: recent commits counted", m["commits7d"] >= 4, str(m["commits7d"]))
        check("integration: daily14 is a 14-element array", len(m["daily14"]) == 14, str(len(m["daily14"])))
        check("integration: at least one contributor", m["contributors"] >= 1, str(m["contributors"]))

        detail = gitviz.repo_detail(str(repo))
        check("integration: repo_detail carries the full shape",
              set(detail) == {"name", "branch", "head", "commits", "maxCol", "branches",
                              "metrics", "status"}, str(sorted(detail)))

        ins = gitviz.insights(str(repo))
        check("integration: insights carries all five panels",
              set(ins) == {"heatmap", "contributors", "churn", "hotspots", "filetypes"},
              str(sorted(ins)))
        check("integration: heatmap has at least one day with commits", len(ins["heatmap"]) >= 1, str(ins["heatmap"]))
        check("integration: heatmap rows are {date,count}",
              all(set(r) == {"date", "count"} for r in ins["heatmap"]), str(ins["heatmap"]))
        check("integration: at least one contributor", len(ins["contributors"]) >= 1, str(ins["contributors"]))
        check("integration: churn sums are non-negative ints",
              all(isinstance(w["add"], int) and w["add"] >= 0 and w["del"] >= 0 for w in ins["churn"]),
              str(ins["churn"]))
        check("integration: hotspots include a real file (f.txt or g.txt touched)",
              any(h["path"] in ("f.txt", "g.txt") for h in ins["hotspots"]), str(ins["hotspots"]))
        check("integration: filetypes bucket the tracked .txt files",
              any(f["ext"] == "txt" for f in ins["filetypes"]), str(ins["filetypes"]))
    finally:
        gitviz._config = orig_config
        gitviz.reset_caches()
        if repo:
            shutil.rmtree(repo, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Insights panels — pure parsers over fabricated git output, plus the panel
#  degradation contract (one bad sub-command -> [], never raises).
# --------------------------------------------------------------------------- #
def test_insights_heatmap():
    # Same day appears twice, two other days once; only days WITH commits returned,
    # oldest -> newest. Blank/garbage lines are ignored (never a phantom day).
    text = "2026-08-09\n2026-08-09\n2026-08-08\n\nnot-a-date\n2026-07-20\n"
    hm = gitviz.parse_heatmap(text)
    check("heatmap: only days with commits, sorted asc",
          [d["date"] for d in hm] == ["2026-07-20", "2026-08-08", "2026-08-09"], str(hm))
    by = {d["date"]: d["count"] for d in hm}
    check("heatmap: buckets by day (09 counted twice)", by["2026-08-09"] == 2, str(hm))
    check("heatmap: a single-commit day counts once", by["2026-08-08"] == 1, str(hm))
    check("heatmap: a non-date line is never a phantom day", len(hm) == 3, str(hm))
    check("heatmap: empty input -> []", gitviz.parse_heatmap("") == [], "")


def test_insights_contributors():
    # shortlog -sne: '<count>\t<name> <email>'. Two addresses for Alice aggregate;
    # sorted commits-desc; bounded to `top`.
    text = ("    12\tAlice <a@x>\n"
            "     8\tBob <b@x>\n"
            "     3\tAlice <alice@home>\n"
            "     1\tCarol <c@x>\n")
    con = gitviz.parse_contributors(text, top=8)
    check("contributors: Alice's two emails aggregate to 15",
          con[0] == {"name": "Alice", "commits": 15}, str(con))
    check("contributors: sorted by commits desc",
          [c["name"] for c in con] == ["Alice", "Bob", "Carol"], str(con))
    check("contributors: email stripped from the name", all("<" not in c["name"] for c in con), str(con))
    bounded = gitviz.parse_contributors(text, top=2)
    check("contributors: bounded to top N", len(bounded) == 2, str(bounded))
    check("contributors: a row without a numeric count is skipped",
          gitviz.parse_contributors("garbage line\n   \n") == [], "")


def test_insights_churn():
    # Interleaved: a date line, a blank, then numstat rows; binary rows are '-\t-'.
    # Two commits in ISO week 32 (Aug 3-9 2026), one in week 30 (Jul 20-26).
    text = (
        "2026-08-09\n\n12\t3\tf.py\n-\t-\timg.png\n"     # week 32: +12 -3, binary skipped
        "2026-08-05\n\n4\t1\tg.py\n"                       # week 32: +4 -1
        "2026-07-22\n\n7\t2\th.py\n"                       # week 30: +7 -2
    )
    ch = gitviz.parse_churn(text, weeks=12)
    by = {w["week"]: w for w in ch}
    check("churn: buckets by ISO week", set(by) == {"2026-W30", "2026-W32"}, str(ch))
    check("churn: week 32 sums both commits (+16/-4)",
          by["2026-W32"]["add"] == 16 and by["2026-W32"]["del"] == 4, str(by.get("2026-W32")))
    check("churn: binary '-\\t-' row is NOT counted",
          by["2026-W32"]["add"] == 16, "binary would have added to the sum")
    check("churn: week 30 carries its single commit (+7/-2)",
          by["2026-W30"]["add"] == 7 and by["2026-W30"]["del"] == 2, str(by.get("2026-W30")))
    check("churn: weeks sorted ascending",
          [w["week"] for w in ch] == ["2026-W30", "2026-W32"], str(ch))
    check("churn: bounded to the most recent N weeks",
          len(gitviz.parse_churn(text, weeks=1)) == 1
          and gitviz.parse_churn(text, weeks=1)[0]["week"] == "2026-W32", str(ch))
    check("churn: a numstat row before any date line is dropped",
          gitviz.parse_churn("9\t9\torphan.py\n") == [], "")


def test_insights_hotspots():
    # name-only across commits: frequency == times a file was touched. Blank
    # separators ignored; sorted changes-desc; bounded.
    text = "f.py\ng.txt\n\nf.py\n\nf.py\nh.md\n"
    hs = gitviz.parse_hotspots(text, top=15)
    by = {h["path"]: h["changes"] for h in hs}
    check("hotspots: counts file frequency (f.py touched 3x)", by["f.py"] == 3, str(hs))
    check("hotspots: a once-touched file counts once", by["g.txt"] == 1, str(hs))
    check("hotspots: most-changed file is first", hs[0]["path"] == "f.py", str(hs))
    check("hotspots: blank separator lines are not files", "" not in by, str(hs))
    check("hotspots: bounded to top N", len(gitviz.parse_hotspots(text, top=1)) == 1, "")


def test_insights_filetypes():
    text = "a/b.py\nc.py\nd.js\nMakefile\n.gitignore\ne.PY\n"
    ft = gitviz.parse_filetypes(text, top=10)
    by = {f["ext"]: f["count"] for f in ft}
    check("filetypes: extension lower-cased and dot-stripped (py counts .py + .PY)",
          by.get("py") == 3, str(ft))
    check("filetypes: js counted", by.get("js") == 1, str(ft))
    check("filetypes: no-extension + dotfile bucket as '(none)'", by.get("(none)") == 2, str(ft))
    check("filetypes: sorted count desc (py first)", ft[0]["ext"] == "py", str(ft))
    check("filetypes: bounded to top N", len(gitviz.parse_filetypes(text, top=2)) == 2, "")


def test_insights_panel_degrades_not_raises():
    # The contract: one panel whose git call/parse blows up degrades to [] and the
    # other panels still return. We monkeypatch _git so churn's parser hits bad
    # input that would raise, and confirm the dict still has all five keys.
    root = Path(tempfile.mkdtemp(prefix="gitviz_insights_"))
    (root / "alpha" / ".git").mkdir(parents=True)
    alpha = str(root / "alpha")
    orig_config = gitviz._config
    orig_git = gitviz._git
    try:
        _with_config({"git_roots": [], "git_repos": [alpha]})

        # every git call returns a date-line + numstat that parses fine for churn,
        # but we force ONE panel to raise by handing its parser a non-str via _git.
        def boom_git(path, args, timeout=gitviz.GIT_TIMEOUT):
            if args[:1] == ["ls-files"]:
                return 12345                      # a non-str -> splitlines() raises
            return "2026-08-09\n"
        gitviz._git = boom_git
        gitviz.reset_caches()
        out = gitviz.insights(alpha)
        check("insights: all five panels present even when one blows up",
              set(out) == {"heatmap", "contributors", "churn", "hotspots", "filetypes"},
              str(sorted(out)))
        check("insights: the failing panel degrades to []", out["filetypes"] == [], str(out["filetypes"]))
        check("insights: a healthy panel still returns data",
              out["heatmap"] == [{"date": "2026-08-09", "count": 1}], str(out["heatmap"]))
    finally:
        gitviz._config = orig_config
        gitviz._git = orig_git
        gitviz.reset_caches()
        shutil.rmtree(root, ignore_errors=True)


def test_insights_unknown_repo_rejected():
    _with_config({"git_roots": [], "git_repos": []})
    try:
        raised = False
        try:
            gitviz.insights("C:/definitely/not/a/known/repo")
        except gitviz.GitError:
            raised = True
        check("insights: an unknown repo raises GitError before any git runs", raised, "")
    finally:
        gitviz.reset_caches()


def test_insights_ls_files_whitelisted():
    check("whitelist: ls-files is a permitted read-only verb",
          "ls-files" in gitviz._ALLOWED_GIT, str(sorted(gitviz._ALLOWED_GIT)))


def test_git_verb_whitelist():
    # A mutating verb never runs, even if a future caller passes one.
    for verb in (["checkout", "main"], ["commit", "-m", "x"], ["push"],
                 ["remote", "set-url", "origin", "x"], []):
        try:
            gitviz._git(".", verb)
            raised = False
        except gitviz.GitError:
            raised = True
        except Exception:
            raised = False
        check(f"whitelist: {verb} rejected by GitError", raised, str(verb))


def test_heatmap_all():
    # The aggregate heatmap: one bounded git call per DISCOVERED repo, days keyed by a
    # UNIQUE per-repo label, each repo a distinct palette colour, a failing repo degrades
    # to no days rather than sinking the aggregate.
    root = Path(tempfile.mkdtemp(prefix="gitviz_heatall_"))
    (root / "alpha" / ".git").mkdir(parents=True)
    (root / "sub" / "alpha" / ".git").mkdir(parents=True)   # a name COLLISION with the first alpha
    (root / "beta" / ".git").mkdir(parents=True)
    orig_config = gitviz._config
    orig_git = gitviz._git
    try:
        _with_config({"git_roots": [str(root)], "git_repos": []})

        def fake_git(path, args, timeout=gitviz.GIT_TIMEOUT):
            base = os.path.basename(path.rstrip("\\/"))
            parent = os.path.basename(os.path.dirname(path.rstrip("\\/")))
            if base == "beta":
                raise RuntimeError("boom")               # a repo that fails must NOT sink the aggregate
            if parent == "sub":                          # the second alpha (under sub/)
                return "2026-08-09\n"
            return "2026-08-09\n2026-08-09\n2026-08-08\n"   # the top-level alpha: 2 on 09, 1 on 08
        gitviz._git = fake_git
        gitviz.reset_caches()
        out = gitviz.heatmap_all()

        check("heatmap_all: shape is {repos, days}", set(out) == {"repos", "days"}, str(sorted(out)))
        names = [r["name"] for r in out["repos"]]
        check("heatmap_all: colliding basenames get UNIQUE labels", len(set(names)) == len(names), str(names))
        check("heatmap_all: every repo carries a palette colour",
              all(r["color"] in gitviz.HEATMAP_PALETTE for r in out["repos"]), str(out["repos"]))
        check("heatmap_all: distinct colours for the first repos",
              out["repos"][0]["color"] != out["repos"][1]["color"], str(out["repos"][:2]))
        # both alphas committed on 09 (2 from the top-level one, 1 from sub/) → that day carries
        # BOTH labels summing to 3, one of them at 2 (order-independent: DFS discovery order varies)
        d09 = out["days"].get("2026-08-09", {})
        d08 = out["days"].get("2026-08-08", {})
        check("heatmap_all: two repos on the same day both appear", len(d09) == 2, str(d09))
        check("heatmap_all: per-day per-repo counts are summed (2 on the busy repo)",
              sorted(d09.values()) == [1, 2], str(d09))
        # the repo with 2 on 09 is the top-level alpha; ONLY it has a commit on 08
        top_label = [k for k, v in d09.items() if v == 2][0]
        check("heatmap_all: a second commit-day for a repo is kept",
              d08.get(top_label) == 1 and sum(d08.values()) == 1, str(d08))
        check("heatmap_all: a failing repo contributes no days (never raises)",
              all("beta" not in day for day in out["days"].values()), str(out["days"]))
    finally:
        gitviz._config = orig_config
        gitviz._git = orig_git
        gitviz.reset_caches()
        shutil.rmtree(root, ignore_errors=True)


def test_heatmap_all_unique_labels():
    # _heatmap_all_labels: basename, then parent/name on collision, then ' (n)'.
    labels = gitviz._heatmap_all_labels(
        ["/x/alpha", "/y/alpha", "/z/alpha", "/q/beta"])
    check("labels: all unique", len(set(labels)) == 4, str(labels))
    check("labels: first keeps the bare basename", labels[0] == "alpha", str(labels))
    check("labels: a collision falls back to parent/name", labels[1] == "y/alpha", str(labels))


def main():
    for fn in (test_graph_lane_algorithm, test_graph_empty, test_status_parsers,
               test_bucket_daily, test_parse_github_remote, test_summarize_checks,
               test_build_github_gh_path, test_build_github_token_path,
               test_build_github_disabled_paths, test_discover_and_whitelist,
               test_active_repos, test_repos_overview_selective_status,
               test_insights_heatmap, test_insights_contributors, test_insights_churn,
               test_insights_hotspots, test_insights_filetypes,
               test_insights_panel_degrades_not_raises, test_insights_unknown_repo_rejected,
               test_insights_ls_files_whitelisted,
               test_heatmap_all, test_heatmap_all_unique_labels,
               test_real_repo_integration, test_git_verb_whitelist):
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
