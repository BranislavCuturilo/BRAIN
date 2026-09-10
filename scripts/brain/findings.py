#!/usr/bin/env python3
"""Findings live in the repository they are about, one Markdown file each.

**Why not a store in the brain.** The operator settled it: *"ako nije na gitu
nije urađeno"*, and every project repo is committed and pushed anyway. Three
things follow, and the third is the one that decides it:

1. A finding is a fact about that code. When the code moves, it moves with it.
2. Collaborators on that project can see it. The brain is private to one
   person; `acme-audit/.claude/` is not.
3. **The fix and the finding's closure land in the SAME commit.** Filed
   centrally, a fix would be committed in the client repo while the status
   changed in the brain -- two repositories for one change, and they drift
   the first time one of them is not pushed.

The machinery still lives here (`health.py`, `cadence.py`, the producers);
it READS `<repo>/.claude/findings/`. `store.py` already resolves a repo name
to a path on this machine, which is what made central storage look necessary.

**Markdown with frontmatter, not JSON**, for the same reason `docs/pages` is:
somebody who is not running this tooling has to be able to read it on GitHub
and edit it in an editor.

**This tool NEVER commits.** It writes a file into someone else's repository
and stops. Committing there would put a finding into a working tree whose
other changes are not ours to describe (`craft-git`: stage what you changed).
The file is meant to travel in the same commit as the fix.

  findings.py                      everything open, across every mapped repo
  findings.py --repo acme-audit
  findings.py --all                every status, not just what is open
  findings.py --json
  findings.py add --repo X --what "..." --where "f.py:12" --kind defect \\
                  --severity major --evidence "..." [--question "..."]
  findings.py answer <id> --repo X --answer "..."
  findings.py close  <id> --repo X --resolution "..." [--ticket DEMO#01621]
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "tickets"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIR = ".claude/findings"
STATUSES = ("open", "asked", "answered", "doing", "done", "wontfix")
#: `asked` and `answered` are separate states on purpose. A finding waiting on
#: a DECISION is not a finding waiting on work, and collapsing them is how a
#: question gets asked twice -- the second time by a session that has no memory
#: of the first.
OPEN_STATES = ("open", "asked", "answered", "doing")
KINDS = ("defect", "dead-code", "question", "note", "risk")
SEVERITIES = ("critical", "major", "minor")
SCALAR = ("id", "key", "status", "kind", "severity", "where", "found_by",
          "found_at", "review_by", "origin", "dies_when", "question", "answer",
          "resolution", "ticket")
#: `dies_when` is the OTHER end of a rule's life. We record why one was born --
#: the incident -- and never what would end it, so a rule that exists only to
#: work around a missing capability outlives the gap and nobody notices.
#: `upstream.py` is the reactive half of this and says in its own source that
#: "covered", "partly covered" and "unrelated" look identical to a keyword
#: match. Measured 2026-09-09: judging 23 release entries, 6 looked like they
#: touched our files and 4 of those were coincidence. A stated condition turns
#: that judgement into a comparison.
#:
#: It applies to ONE kind of rule -- the workaround. "Filter by scope in every
#: query" has no expiry and must not be made to invent one. A gate that exists
#: because the tool does not do something yet has an obvious one.
#:
#: It is a REASON TO LOOK, never a licence to delete: the condition can arrive
#: and the rule still be needed for a second reason nobody wrote down.
#: `origin` + `review_by` are what a BORROWED idea carries (CLAUDE.md, the
#: three doors): where it came from, and the date by which it is either earned
#: or dropped. Without a place to put the date the requirement was prose, and
#: a borrowed idea with no expiry quietly becomes law by sitting still.


def slug(text: str, n: int = 48) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return (t[:n].rstrip("-") or "nalaz")


def repos(root: Path | None = None) -> dict:
    """{name: path} for every repo this brain knows, plus the brain itself.
    A repo whose path does not exist on THIS machine is left out rather than
    guessed at -- the same file lists both machines' projects."""
    out = {"brain": str(ROOT)}
    try:
        import store                                            # noqa: PLC0415
        r = Path(root) if root else store.default_store()
        cat = json.loads((r / "modules.json").read_text(encoding="utf-8")).get("modules") or {}
        for _mod, repo in cat.items():
            if not repo:
                continue
            p = store.repo_path(repo)
            if p and Path(p).is_dir():
                out[Path(p).name] = str(p)
    except Exception:                                           # noqa: BLE001
        pass
    return out


def resolve(repo: str, root: Path | None = None) -> Path | None:
    """A repo NAME or a path -> its findings directory. None when unknown."""
    if not repo:
        return None
    p = Path(repo)
    if p.is_dir():
        return p / DIR
    known = repos(root)
    if repo in known:
        return Path(known[repo]) / DIR
    for name, path in known.items():
        if name.lower() == str(repo).lower():
            return Path(path) / DIR
    return None


def parse(text: str) -> dict:
    """Frontmatter + body. Tolerant on purpose: a human edits these by hand,
    and a finding with a typo in one field must still be readable."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", str(text or ""), re.S)
    if not m:
        return {}
    out: dict = {"body": m.group(2).strip()}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        if k in SCALAR:
            out[k] = v.strip().strip('"').strip("'")
    return out


def render(f: dict) -> str:
    lines = ["---"]
    for k in SCALAR:
        v = f.get(k)
        if v not in (None, ""):
            s = str(v).replace("\n", " ").strip()
            lines.append(f"{k}: {s}" if not re.search(r"[:#]", s) else f'{k}: "{s}"')
    lines.append("---")
    lines.append(str(f.get("body") or "").strip())
    return "\n".join(lines).rstrip() + "\n"


def load(repo: str, root: Path | None = None) -> list[dict]:
    """Every finding filed in one repo, newest first."""
    d = resolve(repo, root)
    if d is None or not d.is_dir():
        return []
    out = []
    for fp in sorted(d.glob("*.md")):
        try:
            f = parse(fp.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not f:
            continue
        f.setdefault("id", fp.stem)
        f["file"] = str(fp)
        f["repo"] = repo
        out.append(f)
    out.sort(key=lambda f: f.get("found_at", ""), reverse=True)
    return out


def across(root: Path | None = None) -> list[dict]:
    out = []
    for name in repos(root):
        out += load(name, root)
    return out


def add(repo: str, what: str, *, where: str = "", kind: str = "note",
        severity: str = "minor", evidence: str = "", question: str = "",
        found_by: str = "session", key: str = "", root: Path | None = None,
        today: str = "", origin: str = "", review_by: str = "",
        dies_when: str = "") -> tuple[Path | None, str]:
    """File one finding. Returns (path, note). A finding whose `key` is already
    filed is NOT written again -- `dead.py` reports the same candidates on
    every sweep, and a folder that grows a duplicate per run is a folder
    nobody opens twice."""
    d = resolve(repo, root)
    if d is None:
        return None, f"unknown repo {repo!r} — findings.py --repo <name|path>"
    if kind not in KINDS:
        return None, f"kind must be one of {KINDS}"
    if severity not in SEVERITIES:
        return None, f"severity must be one of {SEVERITIES}"
    if not str(what).strip():
        return None, "a finding needs `what` — one sentence, what is wrong"
    if not str(evidence).strip():
        return None, ("a finding needs `evidence` — how it was proved. "
                      "Without it this is a rumour, and the next reader cannot check it")
    if key:
        for f in load(repo, root):
            if f.get("key") == key:
                return None, f"already filed as {f['id']} ({f.get('status')})"
    when = today or date.today().isoformat()
    fid = f"{when}-{slug(what)}"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{fid}.md"
    n = 2
    while fp.exists():
        fp = d / f"{fid}-{n}.md"
        n += 1
    body = ["## Dokaz", str(evidence).strip()]
    if question:
        body += ["", "## Pitanje", str(question).strip()]
    if origin and not review_by:
        return None, ("a borrowed idea needs `review_by` — a date by which it is "
                      "earned or dropped. Without one it becomes law by sitting still")
    fp.write_text(render({
        "id": fp.stem, "key": key, "status": "asked" if question else "open",
        "kind": kind, "severity": severity, "where": where,
        "found_by": found_by, "found_at": when, "origin": origin,
        "review_by": review_by, "dies_when": dies_when, "question": question,
        "body": "\n".join(body),
    }), encoding="utf-8")
    return fp, ("filed — commit it in that repo, ideally with the fix "
                "(this tool never commits for you)")


def update(repo: str, fid: str, root: Path | None = None, **fields) -> tuple[Path | None, str]:
    for f in load(repo, root):
        # Prefix too: the ids are `2026-09-07-<slug>` and a human refers to one
        # by its date and first few words, not by typing the whole slug.
        if f["id"] == fid or f["id"].startswith(fid) or f["id"].endswith(fid):
            f.update({k: v for k, v in fields.items() if v not in (None, "")})
            st = f.get("status")
            if st and st not in STATUSES:
                return None, f"status must be one of {STATUSES}"
            # `wontfix` is the only status with NOTHING downstream: no question
            # waiting on an answer, no fix to land, no review date to expire. An
            # unexplained one is indistinguishable from a forgotten one six
            # months later, so the reason is the checkpoint.
            if st == "wontfix" and not str(f.get("resolution") or "").strip():
                return None, ("declining a finding needs `resolution` -- why it is being "
                              "declined. It is the only status nothing will ever re-open")
            fp = Path(f["file"])
            fp.write_text(render(f), encoding="utf-8")
            return fp, "updated — commit it in that repo"
    return None, f"no finding {fid!r} in {repo}"


def overdue(f: dict, today: str = "") -> bool:
    """A borrowed idea past its review date. It does not become wrong on that
    day -- it becomes UNEXAMINED, which is the state the marker exists to
    prevent."""
    d = str(f.get("review_by") or "")
    return bool(d) and d < (today or date.today().isoformat())


def summary(rows: list[dict], today: str = "") -> dict:
    by_repo: dict = {}
    for f in rows:
        r = by_repo.setdefault(f["repo"], {"open": 0, "asked": 0, "critical": 0,
                                           "overdue": 0, "declined": 0, "total": 0})
        if f.get("status") == "wontfix":
            r["declined"] += 1
        if f.get("status") in OPEN_STATES and overdue(f, today):
            r["overdue"] += 1
        r["total"] += 1
        if f.get("status") in OPEN_STATES:
            r["open"] += 1
        if f.get("status") == "asked":
            r["asked"] += 1
        if f.get("severity") == "critical" and f.get("status") in OPEN_STATES:
            r["critical"] += 1
    return by_repo


def main() -> int:
    args = sys.argv[1:]

    def opt(name, default=""):
        return args[args.index(f"--{name}") + 1] if f"--{name}" in args and args.index(f"--{name}") + 1 < len(args) else default

    repo = opt("repo")

    if args and args[0] == "add":
        fp, note = add(repo, opt("what"), where=opt("where"), kind=opt("kind", "note"),
                       severity=opt("severity", "minor"), evidence=opt("evidence"),
                       question=opt("question"), found_by=opt("found-by", "session"),
                       key=opt("key"), origin=opt("origin"), review_by=opt("review-by"),
                       dies_when=opt("dies-when"))
        print(f"  {fp if fp else ''} {note}")
        return 0 if fp else 1

    if args and args[0] in ("answer", "close") and len(args) > 1:
        fid = args[1]
        if args[0] == "answer":
            fp, note = update(repo, fid, status="answered", answer=opt("answer"))
        else:
            fp, note = update(repo, fid, status=opt("status", "done"),
                              resolution=opt("resolution"), ticket=opt("ticket"))
        print(f"  {fp if fp else ''} {note}")
        return 0 if fp else 1

    rows = load(repo) if repo else across()
    if "--all" not in args:
        rows = [f for f in rows if f.get("status") in OPEN_STATES]
    if "--json" in args:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("  Nema otvorenih nalaza." if not repo else f"  Nema otvorenih nalaza u {repo}.")
        return 0
    for r, s in sorted(summary(rows).items()):
        head = f"  {r}: {s['open']} otvoreno"
        if s["asked"]:
            head += f", {s['asked']} ČEKA TVOJ ODGOVOR"
        if s["critical"]:
            head += f", {s['critical']} critical"
        if s["overdue"]:
            head += f", {s['overdue']} PROŠAO ROK REVIZIJE"
        print(head)
        for f in [x for x in rows if x["repo"] == r]:
            mark = ("⏰" if overdue(f) else
                    "?" if f.get("status") == "asked" else
                    "!" if f.get("severity") == "critical" else " ")
            print(f"    {mark} [{f.get('kind','?'):9}] {f['id']}")
            if f.get("where"):
                print(f"        {f['where']}")
            if f.get("origin"):
                print(f"        pozajmljeno: {f['origin'][:70]}"
                      + (f" · revizija do {f['review_by']}" if f.get("review_by") else ""))
            if f.get("question"):
                print(f"        PITANJE: {f['question'][:100]}")
    print("\n  Odgovori:  findings.py answer <id> --repo <repo> --answer \"...\"")
    print("  Zatvori:   findings.py close  <id> --repo <repo> --resolution \"...\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
