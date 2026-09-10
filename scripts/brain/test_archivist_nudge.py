#!/usr/bin/env python3
"""Proof that the journal nudge fires on the right day and shuts up on the rest.

The trap this pins: `episode.py` writes a machine record into
`journal/episodes/` on EVERY session. If those counted as "the journal has an
entry today", the hook would be silenced by exactly the artefact it exists to
complain about -- it would never once fire, and would look like it was working.

The other trap is the one the router already paid for: a hook that fires on
every stop becomes wallpaper. It must be silent for a reading session, silent
after the day is written up, and silent on its own second stop.

No git, no network: `committed_today` is patched, and the journal is a temp
directory.

  python scripts/brain/test_archivist_nudge.py
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import archivist_nudge as an                                     # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def run(payload: dict, commits: int, journalled: bool) -> dict | None:
    """main() through its real contract: JSON on stdin, JSON or nothing out."""
    buf = io.StringIO()
    with mock.patch.object(an, "committed_today", lambda: commits), \
         mock.patch.object(an, "journalled_today", lambda: journalled), \
         mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
         redirect_stdout(buf):
        an.main()
    out = buf.getvalue().strip()
    return json.loads(out) if out else None


# --- the day it must speak up ---------------------------------------------
r = run({}, commits=4, journalled=False)
ck("commits today and no entry: it blocks", r is not None
   and r.get("decision") == "block")
ck("and the reason names the agent to launch",
   bool(r) and "brain:archivist" in r.get("reason", ""))
ck("and carries the count, not just a complaint",
   bool(r) and "4 commit" in r.get("reason", ""))

# --- every day it must not ------------------------------------------------
ck("a reading session owes nothing", run({}, 0, False) is None)
ck("once the day is written up, silence", run({}, 4, True) is None)
ck("its own second stop goes through",
   run({"stop_hook_active": True}, 4, False) is None)

# --- the trap: episodes must not silence it -------------------------------
tmp = Path(tempfile.mkdtemp()) / "journal"
(tmp / "episodes").mkdir(parents=True)
(tmp / "episodes" / "desktop.md").write_text("machine record", encoding="utf-8")
ck("a file under journal/episodes/ does NOT count as today's entry",
   an.journalled_today(root=tmp) is False)

(tmp / "2026-09").mkdir()
(tmp / "2026-09" / "2026-09-10-why.md").write_text("why", encoding="utf-8")
ck("a dated narrative entry DOES count",
   an.journalled_today(root=tmp) is True)

# --- and it must never be the thing that breaks a turn --------------------
ck("a missing journal directory is False, not an exception",
   an.journalled_today(root=Path("C:/nema/ovoga/nigde")) is False)

buf = io.StringIO()
with mock.patch.object(sys, "stdin", io.StringIO("not json at all")), \
     redirect_stdout(buf):
    rc = an.main()
ck("unparseable stdin exits 0 and prints nothing",
   rc == 0 and buf.getvalue().strip() == "")

# A day is a day: an entry from yesterday must not silence today.
old = tmp / "2026-09" / "2026-09-10-why.md"
import os                                                        # noqa: E402
os.utime(old, (0, 0))
(tmp / "episodes" / "desktop.md").unlink()
ck("yesterday's entry does not count for today",
   an.journalled_today(root=tmp) is False)

print()
print(f"{len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
