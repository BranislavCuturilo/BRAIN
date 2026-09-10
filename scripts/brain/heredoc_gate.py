#!/usr/bin/env python3
"""PreToolUse on Bash: refuse a heredoc that carries backslashes to an interpreter.

**The rule this enforces already existed and was still broken.**
`craft-code/references/change-safety.md` has said *"use the file-editing tool
for file edits"* since 2026-08-31, after a patch written as `re.sub(r"\\b(...)")`
landed in the file as a literal **backspace byte** -- a regex that reads
correctly, prints back correctly, and matches nothing, ever. It was reconfirmed
2026-09-07. It was then broken **twice on 2026-09-10**, in the same session, by
someone who read the rule only afterwards. Once the damage was again a backspace
in a `\\b`; the gate that word boundary belonged to silently matched nothing and
the router it lived in routed on the wrong terms.

Three statements of the same sentence did not stop it. A fourth would not
either, which is why this is a gate and not a rewording: **the constitution's
own point is that recall is not the same as the rule being in front of the line
you are about to write.**

**The exact mechanism, from a controlled probe run 2026-09-10.** Three shapes
sent through one heredoc, and the bytes that reached the file:

    r"\\b"    typed with ONE backslash   ->  [92, 98]   survives
    "\\\\b"   typed with TWO backslashes ->  [8]        BACKSPACE
    "\\b"     typed with ONE backslash   ->  [8]        backspace, as Python does

One level of escaping is lost on the way. A single backslash has no level to
lose and arrives intact; a **doubled** one arrives single, and a non-raw string
literal then reads `\\b` as the backspace character. So the damage needs both
halves: the shell halves the pair, and the language interprets what is left.

**That is why this gate looks for `\\\\`, not for any backslash.** The first
version of it fired on one, which would have denied `grep '\\bTOP\\b'` and a raw
string regex -- both of which are correct and were verified working. A gate that
refuses correct commands is the fourteen-reminder wallpaper this brain has
already paid for, so it denies only the shape that has actually broken things
twice: two or more consecutive backslashes in a heredoc body fed to an
interpreter.

**Fail OPEN on anything unexpected**, the same rule `require_skill.py` follows:
a gate that blocks work because it could not parse something is worse than no
gate.
"""
from __future__ import annotations

import json
import re
import sys

#: `cat <<EOF > file` is a file write, not code being interpreted, and the
#: escaping damage there is the caller's own text. These are the ones that turn
#: a damaged payload into behaviour.
INTERPRETERS = ("python", "python3", "py", "node", "perl", "ruby", "php",
                "bash", "sh", "zsh", "psql", "sqlite3", "awk", "sed")

#: `<<'EOF'`, `<<"EOF"`, `<<EOF`, `<<-EOF`. The quoting does not save you here,
#: which is the whole finding: the level is lost before the shell sees it.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def bodies(command: str) -> list[tuple[str, str]]:
    """[(delimiter, body)] for every heredoc in the command."""
    out: list[tuple[str, str]] = []
    for m in HEREDOC.finditer(command):
        delim = m.group(2)
        rest = command[m.end():]
        end = re.search(rf"^\s*{re.escape(delim)}\s*$", rest, re.MULTILINE)
        out.append((delim, rest[:end.start()] if end else rest))
    return out


def offending(command: str) -> tuple[str, str] | None:
    """(delimiter, the first backslash span) when this command is the bad shape."""
    if "<<" not in command:
        return None
    head = command.split("<<", 1)[0]
    if not any(re.search(rf"\b{re.escape(name)}\b", head) for name in INTERPRETERS):
        return None
    for delim, body in bodies(command):
        # Two or more consecutive backslashes: the pair that gets halved. A
        # lone backslash arrives intact and is none of this gate's business.
        m = re.search(r".{0,26}\\{2,}.{0,26}", body, re.DOTALL)
        if m:
            return delim, " ".join(m.group(0).split())[:90]
    return None


def main() -> int:
    try:
        raw = sys.stdin.read(400_000)
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:                                           # noqa: BLE001
        return 0                                                # fail open

    if str(payload.get("tool_name") or "") not in ("Bash", "PowerShell"):
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    hit = offending(command)
    if not hit:
        return 0

    delim, span = hit
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"This pipes a heredoc (<<{delim}) containing a backslash into an "
            f"interpreter, near: {span!r}. One level of escaping is lost on the "
            f"way, so the file gets something you did not type -- a `\\b` "
            f"becomes a literal backspace byte, which every editor and every "
            f"diff renders as nothing. Measured twice on 2026-09-10 and once on "
            f"2026-08-31.\n\n"
            f"Use Write or Edit for file edits. If a script really must do the "
            f"patching, Write the script to a file and run that file -- same "
            f"script, no shell in the path of the payload. "
            f"(/brain:craft-code -> references/change-safety.md)"),
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
