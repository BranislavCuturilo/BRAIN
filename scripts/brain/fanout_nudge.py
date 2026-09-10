#!/usr/bin/env python3
"""PostToolUse on Agent: notice that this session is a queue, while it still is.

**The hole this fills.** `prompt_router.py` fires before the first tool call and
can name the agents; it cannot see what actually happened afterwards. The queue
pattern is only visible *between* launches -- one agent goes out, its result
comes back, and the next launch is decided from inside the reading of that
result, which is the worst moment to be deciding it.

**The bug this shipped with, for its whole life.** It counted `tool_use` blocks
per transcript RECORD, and Claude Code writes one record per block. `any(w > 1)`
-- the test that was supposed to keep it quiet in a session that had batched --
was therefore unreachable, so it announced "none launched together" in every
session it ever ran, including the ones that did batch. The 269-of-269 and
326-of-326 figures it and `delegation.py` reported were facts about the file
format, not about the work: regrouped by `message.id` the same transcripts hold
six real agent batches, one of them five researchers at once.

A nudge that fires on correct behaviour is worse than no nudge, because it
teaches the main loop to skip reading it. Grouping now lives in
`transcript.py`; never count blocks per record again.

**What it does NOT do.** It cannot deny, and it does not fire on every launch --
a solo launch is frequently correct, and a hook that objects to all of them is
the fourteen-reminder wallpaper again. It fires only once a RUN of solo launches
has established that this is the shape of the session, and then only every third
one after that.

Reads the transcript rather than keeping state: the transcript is the honest
record, a state file would drift, and hooks are separate processes anyway.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript as tx                                          # noqa: E402

#: Below this, solo launches are just work. At it, they are a pattern.
THRESHOLD = 3
#: Then every Nth, so it stays a signal rather than a running commentary.
REPEAT = 3


def widths(path: str) -> list[int]:
    """Agent calls per assistant MESSAGE, in order. [1,1,1] is a queue.

    Grouped by `message.id`, because one message is many records. See
    `transcript.py` for what counting per record did to this hook.
    """
    return tx.widths(path)


def main() -> int:
    try:
        raw = sys.stdin.read(200_000)
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:                                           # noqa: BLE001
        return 0                                                # fail silent

    path = payload.get("transcript_path") or ""
    if not path:
        return 0

    seen = widths(path)
    if len(seen) < THRESHOLD:
        return 0

    # Only a session that has NEVER batched is worth interrupting. One wide
    # launch anywhere means the mechanic is known and being used deliberately.
    if any(w > 1 for w in seen):
        return 0
    if (len(seen) - THRESHOLD) % REPEAT != 0:
        return 0

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            f"BRAIN: that is {len(seen)} agents launched one at a time in this "
            f"session, and none launched together. Agent calls in the SAME "
            f"assistant message run concurrently; in separate messages they run "
            f"in sequence and each result lands here before the next starts. "
            f"Before the next launch, name every unit that does not need a "
            f"previous agent's OUTPUT and emit them in one message. If the next "
            f"one genuinely depends on what just came back, this is a real "
            f"sequence -- say so in one line and carry on. "
            f"Recipes: /brain:ops-delegation, references/fan-out.md."),
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
