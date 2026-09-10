#!/usr/bin/env python3
"""The attribution gate denies exactly when an agent wrote the code, and is
silent every other time.

The silence matters more than the denial. Most commits here are main-context
work that attributes to nobody, so a gate that spoke for those would be the
fourteen-reminders-zero-skills failure again, wearing a deny instead of a nudge.

  python scripts/brain/test_attribute_gate.py
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attribute_gate as G  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def run(cmd: str, *, tool: str = "Bash", cwd: str = ".",
        who: tuple[str, str] | None = ("backend-senior", "craft-code, tickets")) -> str:
    """Returns what the hook printed: '' means it stayed out of the way."""
    G.trailers_for = lambda _cwd: who                            # noqa: ARG005
    payload = {"tool_name": tool, "tool_input": {"command": cmd}, "cwd": cwd}
    buf = io.StringIO()
    stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
    try:
        with redirect_stdout(buf):
            G.main()
    finally:
        sys.stdin = stdin
    return buf.getvalue()


def main() -> int:
    real = G.trailers_for
    try:
        print("it denies when an agent wrote the code")
        out = run('git commit -m "feat: thing"')
        ok(out.strip() != "", "a bare commit after an agent ran is denied")
        d = json.loads(out)["hookSpecificOutput"]
        ok(d["permissionDecision"] == "deny", f"decision is deny: {d['permissionDecision']}")
        r = d["permissionDecisionReason"]
        ok("Brain-Agents: backend-senior" in r, "the reason carries the EXACT line to paste")
        ok("Brain-Skills: craft-code, tickets" in r, "and the skills line")

        print("it is silent when there is nobody to attribute")
        ok(run('git commit -m "x"', who=None) == "",
           "no agent ran -> silent, which is most commits here")

        print("it is silent once the message already says it")
        ok(run('git commit -m "x\n\nBrain-Agents: scout"') == "",
           "trailer inline -> silent")
        ok(run("git commit -F - <<'MSG'\nx\n\nBrain-Agents: scout\nMSG") == "",
           "trailer inside a heredoc -> silent (the body is part of the command)")

        print("-F <file> is read, because that message is not in the command")
        with tempfile.TemporaryDirectory() as td:
            msg = Path(td) / "m.txt"
            msg.write_text("subject\n\nBrain-Agents: scout\n", encoding="utf-8")
            ok(run(f'git commit -F {msg}') == "", "file carries the trailer -> silent")
            msg.write_text("subject only\n", encoding="utf-8")
            ok(run(f'git commit -F {msg}') != "", "file without it -> denied")
            ok(run(f'git commit -F {Path(td) / "gone.txt"}') != "",
               "an unreadable -F file does not count as attributed")

        print("it never touches anything that is not a commit")
        ok(run("git status") == "", "git status -> silent")
        ok(run("python scripts/brain/tests.py --fast") == "", "a test run -> silent")
        ok(run('git commit -m "x"', tool="Edit") == "", "a non-shell tool -> silent")

        print("it fails OPEN")
        def boom(_cwd):                                          # noqa: ANN001
            raise RuntimeError("transcript gone")
        G.trailers_for = boom
        payload = {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "x"'},
                   "cwd": "."}
        buf = io.StringIO()
        stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
        try:
            with redirect_stdout(buf):
                rc = G.main()
        finally:
            sys.stdin = stdin
        ok(rc == 0 and buf.getvalue() == "",
           "a raise inside means the commit goes through, never blocked by our bug")
    finally:
        G.trailers_for = real

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
