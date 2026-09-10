#!/usr/bin/env python3
"""Who wrote this code -- which agents, under which skills.

The reverse of `attribute.py`. A user reports that something is broken or badly
built; this answers *which agent produced it and what it was working from*, so
the fix can go into the agent's brief or the skill's rules instead of only into
the code. A bug fixed only in the code will be written again by the same agent.

  blame.py stocktaking/views.py:412     the line -- via git blame
  blame.py stocktaking/views.py         every commit that touched the file
  blame.py --agent dj-list-view         everything that agent worked on
  blame.py --skill craft-security       everything written under that skill

Two halves arrive together. Attribution -- *who wrote it* -- lives in commit
trailers (`Brain-Agents:`, `Brain-Skills:`) written by `attribute.py`. Rationale
-- *why it is like this* -- lives in the decision records a file points at with
`# decision: docs/decisions/NNNN-slug.md`, and those are printed too, because
"this was implemented badly" is usually a question about the second one.

Git history is the store for provenance because it is the only record that
survives -- transcripts are cleaned up, and a hand-maintained ledger drifts from
the code it claims to describe.

A commit with no trailers is reported as UNATTRIBUTED, never as "no agents".
The two are entirely different findings and must never print the same.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRAILER = re.compile(r"^Brain-(Agents|Skills):\s*(.+)$", re.MULTILINE)
FILES = re.compile(r"^Brain-Files:\s*(.+)$", re.MULTILINE)


def git(*args: str, repo: str = ".") -> str:
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def attribution(sha: str, repo: str = ".") -> dict:
    """Trailers plus enough context to judge the commit, or {} if unattributed."""
    body = git("show", "-s", "--format=%B", sha, repo=repo)
    out: dict = {"agents": [], "skills": [], "files": {}}
    for kind, names in TRAILER.findall(body):
        out[kind.lower()] = [n.strip() for n in names.split(",") if n.strip()]
    # Brain-Files: path=agent, path=agent+agent -- the precise answer. Without
    # it a commit touching 12 files names all its agents for every one of them.
    for pair in FILES.findall(body):
        for item in pair.split(","):
            if "=" in item:
                path, who = item.split("=", 1)
                out["files"][path.strip()] = [w for w in who.strip().split("+") if w]
    meta = git("show", "-s", "--format=%ad|%an|%s", "--date=short", sha, repo=repo).strip()
    parts = meta.split("|", 2)
    out["date"], out["author"], out["subject"] = (parts + ["", "", ""])[:3]
    return out


def show(sha: str, repo: str, prefix: str = "", path: str | None = None) -> None:
    a = attribution(sha, repo)
    print(f"{prefix}{sha[:9]}  {a['date']}  {a['subject'][:66]}")
    exact = a["files"].get(path.replace("\\", "/")) if path else None
    if a["agents"] or a["skills"]:
        if exact:
            # The precise answer for THIS file beats the commit-wide list.
            print(f"{prefix}          wrote this file: {', '.join(exact)}")
            others = [x for x in a["agents"] if x not in exact]
            if others:
                print(f"{prefix}          also in commit: {', '.join(others)}")
        elif a["agents"]:
            print(f"{prefix}          agents: {', '.join(a['agents'])}"
                  + ("  (commit-wide -- no per-file record)" if a["files"] else ""))
        if a["skills"]:
            print(f"{prefix}          skills: {', '.join(a['skills'])}")
    else:
        print(f"{prefix}          UNATTRIBUTED -- written before attribution, "
              f"or committed without it")


def decisions_in(repo: str, path: str) -> list[str]:
    """Decision records the file points at.

    Git answers *who wrote this*; a decision record answers *why it is like
    this*, which is usually the question behind "this was implemented badly".
    Surfacing both together is the whole point -- provenance without rationale
    just names someone to blame.
    """
    try:
        text = (Path(repo) / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return sorted({m for m in re.findall(r"decision:\s*(\S+\.md)", text)})


def show_decisions(repo: str, path: str, prefix: str = "") -> None:
    found = decisions_in(repo, path)
    if not found:
        return
    print(f"\n{prefix}why it is like this:")
    for rec in found:
        status = ""
        try:
            body = (Path(repo) / rec).read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^Status:\s*(.+)$", body, re.MULTILINE)
            title = body.lstrip().splitlines()[0].lstrip("# ").strip()
            status = f"  [{m.group(1).strip()}]" if m else ""
            print(f"{prefix}  {rec} - {title}{status}")
        except (OSError, IndexError):
            print(f"{prefix}  {rec}  (referenced but missing)")


def blame_line(repo: str, path: str, line: int) -> int:
    out = git("blame", "-L", f"{line},{line}", "--porcelain", "--", path, repo=repo)
    if not out:
        print(f"could not blame {path}:{line} -- is it tracked, and does the line exist?")
        return 1
    sha = out.split()[0]
    print(f"{path}:{line}\n")
    show(sha, repo, path=path)
    show_decisions(repo, path)
    print("\nIf this is a defect, the question is whether the fault is in the code,")
    print("in the agent's brief, or in a skill's rules. Fix the code AND the source,")
    print("or the same agent writes it again:  score.py record agent <name> hindered \"...\"")
    return 0


def blame_file(repo: str, path: str, limit: int) -> int:
    log = git("log", f"-{limit}", "--format=%H", "--", path, repo=repo).split()
    if not log:
        print(f"no history for {path}")
        return 1
    print(f"{path} -- {len(log)} most recent commit(s)\n")
    show_decisions(repo, path)
    print()
    agents: dict[str, int] = {}
    unattributed = 0
    for sha in log:
        show(sha, repo, path=path)
        a = attribution(sha, repo)
        if not a["agents"] and not a["skills"]:
            unattributed += 1
        for name in a["agents"]:
            agents[name] = agents.get(name, 0) + 1
    if agents:
        top = ", ".join(f"{n} ({c})" for n, c in sorted(agents.items(), key=lambda kv: -kv[1]))
        print(f"\nagents across these commits: {top}")
    if unattributed:
        print(f"{unattributed}/{len(log)} commit(s) carry no attribution.")
    return 0


def by_name(repo: str, kind: str, name: str, limit: int) -> int:
    """Everything a given agent or skill worked on."""
    trailer = "Brain-Agents" if kind == "agent" else "Brain-Skills"
    shas = git("log", f"-{limit}", "--format=%H", "-E",
               f"--grep={trailer}:.*(^|[ ,]){re.escape(name)}([,]|$)",
               repo=repo).split()
    if not shas:
        print(f"no commits attributed to {kind} {name}")
        print("(either it has not shipped code yet, or those commits carry no trailers)")
        return 1
    print(f"{kind} {name} -- {len(shas)} commit(s)\n")
    files: dict[str, int] = {}
    for sha in shas:
        show(sha, repo)
        for f in git("show", "--name-only", "--format=", sha, repo=repo).split("\n"):
            if f.strip():
                files[f.strip()] = files.get(f.strip(), 0) + 1
    print(f"\ntouched {len(files)} file(s); most often:")
    for f, c in sorted(files.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {c:>3}x  {f}")
    print("\nA defect pattern repeating across these files is a defect in the")
    print(f"{kind}, not in any one of them.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="path, or path:line")
    ap.add_argument("--agent")
    ap.add_argument("--skill")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    if not git("rev-parse", "--git-dir", repo=args.repo):
        print(f"{args.repo} is not a git repository")
        return 2

    if args.agent:
        return by_name(args.repo, "agent", args.agent, args.limit)
    if args.skill:
        return by_name(args.repo, "skill", args.skill, args.limit)
    if not args.target:
        ap.print_help()
        return 2

    target = args.target
    # path:line, but a Windows drive letter also contains a colon.
    if ":" in target and target.rsplit(":", 1)[-1].isdigit():
        path, line = target.rsplit(":", 1)
        if Path(path).name:
            return blame_line(args.repo, path, int(line))
    return blame_file(args.repo, target, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
