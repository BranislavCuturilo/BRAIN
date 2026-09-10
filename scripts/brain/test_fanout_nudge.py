#!/usr/bin/env python3
"""Proof that the fan-out nudge fires on a queue and stays quiet otherwise.

Both halves matter and they fail in opposite directions. A nudge that never
fires is the thing it was built to replace -- the brain already measured 269
solo launches with nothing said. A nudge that fires on every launch is the
fourteen reminder hooks that produced zero skill invocations in a day.

  python scripts/brain/test_fanout_nudge.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "fanout_nudge.py"

failures: list[str] = []


def ck(label: str, ok: bool) -> None:
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)


def transcript(widths: list[int], extra: str = "") -> str:
    """A fake transcript whose assistant messages carry `widths` Agent calls."""
    lines = []
    for n in widths:
        blocks = [{"type": "tool_use", "name": "Agent",
                   "input": {"subagent_type": "brain:scout"}} for _ in range(n)]
        lines.append(json.dumps({"type": "assistant", "isSidechain": False,
                                 "message": {"content": blocks}}))
    return "\n".join(lines) + ("\n" + extra if extra else "") + "\n"


def run(widths: list[int] | None, *, path: str | None = None,
        extra: str = "") -> str:
    """Drive the hook through its real stdin/stdout contract; return stdout."""
    with tempfile.TemporaryDirectory() as tmp:
        if path is None:
            t = Path(tmp) / "t.jsonl"
            t.write_text(transcript(widths or [], extra), encoding="utf-8")
            path = str(t)
        payload = {"tool_name": "Agent", "transcript_path": path}
        proc = subprocess.run([sys.executable, str(HOOK)],
                              input=json.dumps(payload), capture_output=True,
                              text=True, timeout=30, encoding="utf-8",
                              errors="replace")
    return (proc.stdout or "").strip()


def fired(out: str) -> bool:
    if not out:
        return False
    try:
        return bool((json.loads(out).get("hookSpecificOutput") or {})
                    .get("additionalContext"))
    except ValueError:
        return False


def main() -> int:
    # --- it stays quiet while solo launches are just work -----------------
    ck("0 launches: silent", not fired(run([])))
    ck("1 solo launch: silent", not fired(run([1])))
    ck("2 solo launches: silent", not fired(run([1, 1])))

    # --- it fires once the queue IS the shape of the session --------------
    ck("3 solo launches: FIRES", fired(run([1, 1, 1])))

    # --- and then only every third, so it does not become wallpaper -------
    ck("4 solo: silent (not every launch)", not fired(run([1, 1, 1, 1])))
    ck("5 solo: silent", not fired(run([1, 1, 1, 1, 1])))
    ck("6 solo: FIRES again", fired(run([1] * 6)))
    ck("9 solo: FIRES again", fired(run([1] * 9)))

    # --- one batch anywhere means the mechanic is known -------------------
    ck("a width-2 launch present: silent even at 6",
       not fired(run([1, 1, 2, 1, 1, 1])))
    ck("batch FIRST, then many solo: still silent",
       not fired(run([3, 1, 1, 1, 1, 1])))

    # --- fail open, always ------------------------------------------------
    ck("no transcript on disk: silent",
       not fired(run(None, path="/nonexistent/transcript.jsonl")))
    ck("unparseable lines: silent rather than crashing",
       not fired(run([1, 1], extra="{not json at all")))

    # A subagent's own transcript must not be counted: it would make every
    # delegated run look like a queue of its own.
    side = json.dumps({"type": "assistant", "isSidechain": True,
                       "message": {"content": [
                           {"type": "tool_use", "name": "Agent",
                            "input": {"subagent_type": "brain:scout"}}]}})
    ck("sidechain messages are not counted",
       not fired(run([1, 1], extra="\n".join([side] * 4))))

    # --- prove the check can fail ----------------------------------------
    # If the threshold logic were removed and it fired unconditionally, the
    # "2 solo launches: silent" case above would go red. Verify the detector
    # itself can distinguish, rather than trusting that it can.
    ck("the detector distinguishes fired from silent",
       fired(run([1, 1, 1])) and not fired(run([1, 1])))

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("ok - the nudge fires on a queue, and only on a queue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
