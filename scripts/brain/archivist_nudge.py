#!/usr/bin/env python3
"""Stop: a day that produced commits and no journal entry is a day forgotten.

**The hole this fills.** `episode.py` already runs on SessionEnd and writes the
RAW MATERIAL -- which tickets, which agents, which files, what failed. It says
plainly why it stops there: *"a script cannot write why a decision was made"*.
The "why" is `archivist`'s job, and `archivist` is an agent.

A hook cannot launch an agent, and SessionEnd is too late to ask for one -- the
session is over and nothing can act. `Stop` is the last moment the main loop is
still alive, which is why this lives here and not next to `episode.py`.

**Measured, 2026-09-10.** Over the last thirty days: 333 commits across 23 days,
and the journal holds **one** dated entry, on a day that was not one of those 23.
So 22 of 23 working days produced code and no record of why. The consequence is
not theoretical: step 3 of `ops-maintain` -- archivist reading the journal back
to find a repeating shape -- had to work from commit messages, because there was
nothing else. One of five steps in the periodic cycle was running on empty.

**What it does not do.** It does not fire per commit, per turn, or on a session
that only read things. It fires once, on a session that committed, on a day with
no journal entry yet -- roughly once per working day. And it blocks exactly once:
`stop_hook_active` is set on the stop a stop hook itself caused, so a second stop
in the same turn goes through. Nagging is how a signal becomes wallpaper, which
this brain has already paid for once in the router.

The judgement stays human. This only says the record is missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
JOURNAL = ROOT / "journal"


def committed_today() -> int:
    """Commits in this repo since midnight. 0 means nothing to write up."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", "--since=midnight", "--pretty=%h"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 0            # no git, no opinion
    if out.returncode != 0:
        return 0
    return len([ln for ln in out.stdout.split() if ln.strip()])


def journalled_today(root: Path = JOURNAL) -> bool:
    """True if a dated narrative entry was written or touched today.

    `journal/episodes/` is deliberately excluded: those are the machine's
    record, written by `episode.py` on every session, and counting them would
    mean this hook silences itself with the very thing it exists to complain
    about.
    """
    if not root.is_dir():
        return False
    today = date.today()
    try:
        for fp in root.rglob("*.md"):
            if "episodes" in fp.parts:
                continue
            if date.fromtimestamp(fp.stat().st_mtime) == today:
                return True
    except OSError:
        return False
    return False


def main() -> int:
    try:
        raw = sys.stdin.read(200_000)
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:                                           # noqa: BLE001
        return 0                                                # fail silent

    if payload.get("stop_hook_active"):
        return 0            # already blocked once this turn; let it end

    n = committed_today()
    if not n:
        return 0            # a reading session owes the journal nothing
    if journalled_today():
        return 0

    print(json.dumps({"decision": "block", "reason": (
        f"BRAIN: {n} commit(s) today and no journal entry. `episode.py` will "
        f"record WHAT happened at SessionEnd; nothing records WHY, and that is "
        f"the half `ops-maintain` step 3 reads back. Measured over 30 days: 333 "
        f"commits across 23 days against ONE dated entry.\n\n"
        f"Launch Agent(brain:archivist) now and let it write today's entry into "
        f"journal/, or say in one line why this session is not worth one -- a "
        f"pure refactor with nothing decided is a fair answer. This blocks once "
        f"and then lets go.")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
