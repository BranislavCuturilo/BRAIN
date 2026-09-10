#!/usr/bin/env python3
"""A dead session stays dead, and a subagent knows who launched it.

Two invariants, both learned from the same defect class. On 2026-08-19 a
truncated payload produced a phantom "default" session with 37 tools and no
prompt, and it stayed invisible for weeks because `Hub.ingest` CREATED a session
for any id it did not recognise -- including one that had already ended. The
parent link is the other half: `agent` said which KIND ran, never under whom, so
two concurrent fan-outs of the same kind were indistinguishable.

Both are derived, never guessed: the parent comes out of the transcript path,
which the harness writes as `<parent-session>/subagents/agent-*.jsonl`.

  python agent_view/test_hub_lifecycle.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import server as S  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def parent_of(transcript: str) -> str:
    """The same derivation hook.py does, kept here so the rule is pinned even
    though the hook itself runs out of process."""
    try:
        tp = pathlib.PurePath(transcript)
        return tp.parent.parent.name if tp.parent.name == "subagents" else ""
    except (ValueError, IndexError):
        return ""


def main() -> int:
    print("the parent is derived from the transcript path")
    ok(parent_of("C:/p/proj/e275ad09/subagents/agent-aa03.jsonl") == "e275ad09",
       "a subagent transcript names its parent session")
    ok(parent_of("C:/p/proj/e275ad09.jsonl") == "",
       "a main session has no parent, and that is not an error")
    ok(parent_of("") == "", "an empty path yields nothing rather than raising")
    ok(parent_of("C:/subagents/agent-x.jsonl") == "",
       "`subagents` with no directory above it yields nothing, not a drive letter")

    print("a session that ended is not resurrected")
    hub = S.Hub()
    hub.ingest({"session_id": "S1", "agent": "main", "phase": "pretool", "tool": "Bash"})
    ok("S1" in hub._sessions, "a live session is tracked")

    hub.ingest({"session_id": "S1", "phase": "sessionend"})
    ok("S1" not in hub._sessions, "sessionend drops the tab immediately")

    before = hub._resurrected
    hub.ingest({"session_id": "S1", "agent": "main", "phase": "pretool", "tool": "Read"})
    ok("S1" not in hub._sessions,
       "a LATE event does not recreate the session -- this is the phantom-tab defect")
    ok(hub._resurrected == before + 1,
       f"and it is COUNTED, not swallowed: {before} -> {hub._resurrected}")

    print("a session nobody ended is still created normally")
    hub.ingest({"session_id": "S2", "agent": "main", "phase": "pretool", "tool": "Bash"})
    ok("S2" in hub._sessions, "an unknown id is a new session, exactly as before")

    print("the dead list cannot grow without bound")
    for i in range(260):
        hub.ingest({"session_id": f"T{i}", "phase": "sessionend"})
    ok(len(hub._ended) <= 260, f"pruned rather than accumulating forever: {len(hub._ended)}")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
