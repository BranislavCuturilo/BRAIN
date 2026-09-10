#!/usr/bin/env python3
"""Proof that the gate refuses the shape that broke things and nothing else.

The rule it enforces was written 2026-08-31, reconfirmed 2026-09-07, and broken
twice on 2026-09-10 by someone who had read it. Three statements did not stop
it; that is why it is a gate.

**What exactly breaks, from a probe run 2026-09-10.** One level of backslash
escaping is lost between what is typed and what the heredoc delivers:

    typed r"\\b"    -> [92, 98]  a backslash and a b, intact
    typed "\\\\b"   -> [8]       BACKSPACE: the pair was halved, then the
                                 non-raw literal read what was left
    typed "\\b"     -> [8]       backspace, which is just Python

So a LONE backslash has no level to lose and arrives correct. A DOUBLED one
does not. The gate looks for the pair.

Two ways a gate like this fails, and both look fine from outside:

  * TOO LOOSE -- it misses the command that caused the damage. The first case
    below is that command.
  * TOO TIGHT -- it refuses correct commands and becomes wallpaper. The first
    version of this gate fired on any backslash, which would have denied
    `grep '\\bTOP\\b'` and a raw-string regex in a heredoc -- both verified
    working minutes earlier. The allow cases outnumber the deny cases here on
    purpose.

  python scripts/brain/test_heredoc_gate.py
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import heredoc_gate as hg                                        # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def run(command: str, tool: str = "Bash") -> dict | None:
    payload = {"tool_name": tool, "tool_input": {"command": command}}
    buf = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
         redirect_stdout(buf):
        hg.main()
    out = buf.getvalue().strip()
    return json.loads(out) if out else None


def denied(command: str, tool: str = "Bash") -> bool:
    r = run(command, tool)
    return bool(r) and r["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- REFUSE: the doubled backslash, which is what actually broke -----------
# Verbatim in shape from the patch that wrote a backspace byte into
# prompt_router.py's DJANGO_NOUNS regex, twice, on 2026-09-10.
REAL = (
    "cd ~/.claude/skills/brain\n"
    "python - <<'PY'\n"
    "import io\n"
    "s = io.open('scripts/brain/prompt_router.py').read()\n"
    r'new = """r"(?:\\b(django|model|view)"""' + "\n"
    "io.open('scripts/brain/prompt_router.py','w').write(s.replace(old, new))\n"
    "PY\n")
ck("the command that wrote a backspace byte today is refused", denied(REAL))

r = run(REAL)
ck("and the reason says what to use instead",
   bool(r) and "Write or Edit" in r["hookSpecificOutput"]["permissionDecisionReason"])
ck("and quotes the offending span rather than lecturing",
   bool(r) and "django" in r["hookSpecificOutput"]["permissionDecisionReason"])

ck("an unquoted delimiter is no safer",
   denied("python - <<PY\n" + r'p = "C:\\Users\\you"' + "\nPY\n"))
ck("node counts too",
   denied("node <<'JS'\n" + r'const re = new RegExp("\\d+");' + "\nJS\n"))
ck("<<- counts too",
   denied("\tpython3 - <<-'PY'\n\t" + r'x = "a\\nb"' + "\n\tPY\n"))
ck("two heredocs, one doubled, is still refused",
   denied("python - <<'A'\nclean\nA\npython - <<'B'\n" + r'y = "\\s"' + "\nB\n"))

# --- ALLOW: a lone backslash arrives intact and is not this gate's business -
# This exact command was run successfully minutes before the gate was narrowed;
# the first version would have denied it.
ck("a raw-string regex in a heredoc goes through",
   run("python - <<'PY'\nimport re\n"
       + r'print(re.sub(r"\b(test)\b", "X", "a test here"))' + "\nPY\n") is None)
ck("a heredoc with no backslash goes through",
   run("python - <<'PY'\nprint(sum(range(10)))\nPY\n") is None)
ck("a plain command with a lone backslash is untouched",
   run(r"grep -n '\bTOP\b' scripts/tickets/similar.py") is None)
ck("a Windows path in a heredoc, single-separated, goes through",
   run("python - <<'PY'\n" + r"p = r'C:\Users\you'" + "\nPY\n") is None)

# --- ALLOW: not an interpreter, not a shell, not a heredoc -----------------
ck("writing a file with cat is not an interpreter, doubled or not",
   run("cat <<'EOF' > notes.txt\n" + r"path C:\\Users\\you" + "\nEOF\n") is None)
ck("no heredoc at all", run("git status --porcelain") is None)
ck("an interpreter named only INSIDE the body does not arm it",
   run("cat <<'EOF' > run.sh\n" + r'python x.py "\\d"' + "\nEOF\n") is None)
ck("a tool that is not a shell is not our business",
   run("python - <<'PY'\n" + r'x = "\\b"' + "\nPY\n", tool="Read") is None)

# --- and it must never be the thing that breaks a turn --------------------
buf = io.StringIO()
with mock.patch.object(sys, "stdin", io.StringIO("not json")), \
     redirect_stdout(buf):
    rc = hg.main()
ck("unparseable stdin fails OPEN: exit 0, nothing printed",
   rc == 0 and buf.getvalue().strip() == "")
ck("a missing command key fails open", run("") is None)
ck("an unterminated heredoc is still inspected, not skipped",
   denied("python - <<'PY'\n" + r'x = "\\b"' + "\n"))

print()
print(f"{len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
