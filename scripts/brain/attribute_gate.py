#!/usr/bin/env python3
"""PreToolUse gate: a commit whose code an AGENT wrote must say which one.

DIES WHEN: Claude Code records the agents that produced a commit itself, or the
Agent tool returns an attribution block the commit path can carry. This gate
exists only because neither happens today.

WHY A GATE AND NOT A REMINDER. `attribute.py` has existed and worked the whole
time -- run it today and it prints the correct trailers on demand. It was asked
for in prose, in `craft-git`, and that is the entire mechanism it ever had.
Measured on 2026-09-09: the last commit carrying `Brain-Agents:` is 71f43d7,
dated 2026-08-21. In the 153 hand-written commits since, not one carries it.
The habit stopped; the code never broke. `require_skill.py` already priced what
a day of reminder hooks is worth -- fourteen reminders, zero skills loaded --
so this denies, the same way the visual and skill gates do.

WHAT IT IS FOR. Months later a customer reports that something is badly built.
`git blame` finds the line. These two trailers say who wrote it and what they
were working from:

    Brain-Agents: backend-senior
    Brain-Skills: craft-code, tickets, ops-integrations

Without them you fix the code, and the same agent writes the same defect again
next time from the same brief. That is the whole argument, and it is in
`craft-git` under "Record who wrote it, when agents did the writing".

WHEN IT FIRES -- all of these, and it fails OPEN on anything else:

  * the command is a `git commit`;
  * agents or skills actually ran SINCE THE LAST COMMIT in this repo. Work done
    in the main context attributes to nobody, and that is most commits -- so
    silence is the normal case and the gate must never speak for it;
  * the message does not already carry `Brain-Agents:` -- checked in the command
    text and, for `-F <file>`, in that file.

IT CANNOT WEDGE. The block is satisfiable by the thing it asks for: add the
lines it printed and the retry passes. It never asks for a command that could
fail, and it never guesses trailers -- they come from the session's own run
records via `attribute.py`, which is the only honest source. `craft-git`:
"Attribute what actually ran, or not at all."
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import attribute as attr                                        # noqa: E402
import usage as usage_mod                                       # noqa: E402

TRAILER = "Brain-Agents:"


def _payload() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return {}


def deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def message_carries_trailer(cmd: str, cwd: str) -> bool:
    """The command text covers `-m "..."` and heredocs alike, because the whole
    command string is what the tool receives. `-F <file>` is the one form whose
    message lives elsewhere, so that file is read."""
    if TRAILER in cmd:
        return True
    m = re.search(r"-F\s+(?!-)([^\s'\"]+)", cmd)
    if m:
        p = Path(m.group(1))
        if not p.is_absolute():
            p = Path(cwd) / p
        try:
            return TRAILER in p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
    return False


def trailers_for(cwd: str) -> tuple[str, str] | None:
    """`(agents_line, skills_line)` from the session's own records, or None when
    nothing ran since the last commit."""
    since = attr.last_commit_time(cwd)
    found = attr.sessions_for(cwd)
    if not found:
        return None                                 # no transcript -> fail open
    session = found[0][0]
    agents = attr.agents_in(session, since)
    invoked = attr.skills_in(session, since)
    if not agents:
        return None                 # skills alone are the main context working
    pre = usage_mod.preloads()
    carried = {s for a in agents for s in pre.get(a, [])}
    skills = sorted(set(invoked) | carried)
    return (", ".join(sorted(agents)), ", ".join(skills))


def main() -> int:
    payload = _payload()
    try:
        if str(payload.get("tool_name") or "") not in ("Bash", "PowerShell"):
            return 0
        cmd = str((payload.get("tool_input") or {}).get("command") or "")
        if "git commit" not in cmd:
            return 0
        cwd = str(payload.get("cwd") or os.getcwd())
        if message_carries_trailer(cmd, cwd):
            return 0
        got = trailers_for(cwd)
        if not got:
            return 0                # nobody to attribute -- the common case
        agents_line, skills_line = got
        lines = f"{TRAILER} {agents_line}"
        if skills_line:
            lines += f"\nBrain-Skills: {skills_line}"
        return deny(
            "This commit contains work an AGENT did, and the message does not say "
            "which one. Add these two lines at the end of the message, after the "
            "Co-Authored-By line, and commit again:\n\n"
            f"{lines}\n\n"
            "They come from this session's own run records, not from memory -- "
            "months from now `git blame` will find the line and these will say who "
            "wrote it and what brief they worked from, which is the only way a "
            "defect gets fixed in the agent instead of just in the code. "
            "If you believe no agent wrote this commit's changes, say so and "
            "commit with the lines omitted deliberately rather than silently.")
    except Exception:                                           # noqa: BLE001
        return 0                                    # fail OPEN, always
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
