#!/usr/bin/env python3
"""Which agents and skills produced this work -- as commit trailers.

The point is the loop that starts months later: a user reports that something
is broken or badly built, and the question becomes *which agent wrote it, under
which skill, and is the fault in the code or in the agent's brief?* That
question is unanswerable unless the answer was recorded at the time. Transcripts
get deleted; git history does not.

So attribution is stored in the commit itself:

    Brain-Agents: dj-list-view, dj-templates, reviewer
    Brain-Skills: stack-django, ui-bootstrap, craft-security

`blame.py` reads them back from any line of code. This script writes them.

  attribute.py                    trailers for work done SINCE THE LAST COMMIT
  attribute.py --all-session      the whole session instead
  attribute.py --cwd <path>       ...for another directory
  attribute.py --session <id>     ...for a specific session
  attribute.py --list             candidate sessions, most recent first

The window is "since HEAD was committed", because a session usually produces
several commits and whole-session trailers over-attribute every commit after the
first. Work done in the main context rather than by agents produces no trailers
at all -- that is the correct answer, not a failure.

Typical use, at commit time:

  git commit -m "$(printf 'feat: thing\\n\\n%s' "$(python .../attribute.py)")"

The basis for the answer -- which transcript, when it was last written, how many
records were read -- goes to STDERR, so the trailers pipe cleanly while the claim
stays checkable. An attribution nobody can check is worse than none, because it
looks authoritative.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage as usage_mod                                       # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECTS = Path.home() / ".claude" / "projects"


def last_commit_time(repo: str) -> float:
    """When HEAD was committed -- the floor for "what produced THIS commit".

    A session can produce several commits. Without a floor the trailers describe
    the whole session and over-attribute every commit after the first, which is
    exactly the kind of authoritative-looking wrong record this is meant to
    prevent.
    """
    try:
        r = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct"],
                           capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0.0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p.strip()))


def session_cwd(path: Path) -> str | None:
    """The working directory a transcript belongs to.

    Read from the records rather than reconstructed from the directory name:
    the on-disk slug mangles drive letters and separators, and guessing it wrong
    silently attributes the work to the wrong project.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 40:
                    break
                if '"cwd"' not in line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return cwd
    except OSError:
        pass
    return None


def sessions_for(cwd: str) -> list[tuple[Path, float]]:
    """Every transcript for a working directory, most recently written first."""
    want = norm(cwd)
    out = []
    for path in PROJECTS.glob("*/*.jsonl"):
        got = session_cwd(path)
        if got and norm(got) == want:
            try:
                out.append((path, path.stat().st_mtime))
            except OSError:
                continue
    return sorted(out, key=lambda t: -t[1])


def agents_in(session: Path, since: float = 0.0) -> dict[str, int]:
    """Agent runs recorded for one session, at any spawn depth.

    Reads the run records, not the transcript: an agent that delegated to three
    juniors shows one Task call and four runs, and the juniors are the ones who
    wrote the code.
    """
    found: dict[str, int] = {}
    root = session.parent / session.stem / "subagents"
    for meta in root.glob("**/agent-*.meta.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        kind = str(info.get("agentType", "")).split(":")[-1]
        if not kind or kind == "workflow-subagent":
            continue
        try:
            if since and meta.stat().st_mtime < since:
                continue
        except OSError:
            continue
        found[kind] = found.get(kind, 0) + 1
    return found


WRITE_TOOLS = ("Write", "Edit", "NotebookEdit", "MultiEdit")


def files_by_agent(session: Path, repo: str, since: float = 0.0) -> dict[str, set[str]]:
    """Repo-relative path -> the agents that WROTE it.

    A commit can touch 12 files written by 4 agents; a trailer naming all four
    tells you nothing about which one wrote the file in front of you. Each
    subagent's transcript records every Write/Edit with its file_path, so this
    mapping is DERIVED -- it costs no maintenance and cannot drift from the code,
    which is exactly why it does not belong in hand-written comments.

    Reads only writes. An agent that read a file did not produce it.
    """
    out: dict[str, set[str]] = {}
    root = session.parent / session.stem / "subagents"
    repo_norm = norm(repo)
    for meta in root.glob("**/agent-*.meta.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if since and meta.stat().st_mtime < since:
                continue
        except (OSError, ValueError):
            continue
        kind = str(info.get("agentType", "")).split(":")[-1]
        if not kind or kind == "workflow-subagent":
            continue
        transcript = Path(str(meta).replace(".meta.json", ".jsonl"))
        try:
            with transcript.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"tool_use"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    content = (rec.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if (not isinstance(block, dict) or block.get("type") != "tool_use"
                                or block.get("name") not in WRITE_TOOLS):
                            continue
                        fp = (block.get("input") or {}).get("file_path")
                        if not fp:
                            continue
                        if not norm(fp).startswith(repo_norm):
                            continue      # agent memory, scratchpad, another repo
                        rel = os.path.relpath(fp, repo).replace("\\", "/")
                        out.setdefault(rel, set()).add(kind)
        except OSError:
            continue
    return out


def skills_in(session: Path, since: float = 0.0) -> set[str]:
    """Skills explicitly invoked with the Skill tool, after `since`."""
    floor = datetime.fromtimestamp(since, timezone.utc).isoformat() if since else ""
    out: set[str] = set()
    try:
        with session.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"Skill"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                if floor and (rec.get("timestamp") or "") < floor:
                    continue
                for block in content:
                    if (isinstance(block, dict) and block.get("type") == "tool_use"
                            and block.get("name") == "Skill"):
                        s = (block.get("input") or {}).get("skill")
                        if s:
                            out.add(str(s).split(":")[-1])
    except OSError:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--session")
    ap.add_argument("--list", action="store_true", dest="as_list")
    ap.add_argument("--all-session", action="store_true",
                    help="the whole session, not just since the last commit")
    args = ap.parse_args()

    since = 0.0 if args.as_list or args.all_session else last_commit_time(args.cwd)

    found = sessions_for(args.cwd)
    if args.session:
        found = [(p, m) for p, m in found if p.stem.startswith(args.session)] or \
                [(p, p.stat().st_mtime) for p in PROJECTS.glob(f"*/{args.session}*.jsonl")]

    if not found:
        print(f"no session found for {args.cwd}", file=sys.stderr)
        print("(the transcript may have been cleaned up -- attribute by hand or omit)",
              file=sys.stderr)
        return 1

    if args.as_list:
        for path, mtime in found[:15]:
            when = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"{path.stem}  {when}  {len(agents_in(path))} agent kind(s)")
        return 0

    session, mtime = found[0]
    agents = agents_in(session, since)
    invoked = skills_in(session, since)

    # A preloaded skill shaped the code just as much as an invoked one -- more,
    # usually, since it is what the writing agent was actually working from.
    pre = usage_mod.preloads()
    carried = {s for a in agents for s in pre.get(a, [])}

    if not agents and not invoked:
        print("no agents or skills ran since the last commit -- this work was done "
              "in the main context, so there is nothing to attribute", file=sys.stderr)
        print("(--all-session widens the window to the whole session)", file=sys.stderr)
        return 1

    when = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    window = ("whole session" if not since else
              "since " + datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print(f"basis: {session.name} (last written {when}), {window}; "
          f"{sum(agents.values())} agent run(s), "
          f"{len(invoked)} skill(s) invoked, {len(carried)} preloaded",
          file=sys.stderr)

    if agents:
        ordered = sorted(agents, key=lambda a: (-agents[a], a))
        print("Brain-Agents: " + ", ".join(ordered))
    skills = sorted(invoked | carried)
    if skills:
        print("Brain-Skills: " + ", ".join(skills))
    # Per-file, because commit-level attribution is too coarse to act on: it
    # names every agent in the commit for every line in it.
    per_file = files_by_agent(session, args.cwd, since)
    if per_file:
        pairs = [f"{path}={'+'.join(sorted(who))}" for path, who in sorted(per_file.items())]
        print("Brain-Files: " + ", ".join(pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
