#!/usr/bin/env python3
"""Sloj 2 — the streaming test wrapper that feeds live N/M to the Agent View HUD.

    python testrun.py -- python manage.py test appname
    python testrun.py -- pytest -q

Runs the child command with stderr merged into stdout and line-buffered, TEES
every line to this process's real stdout (so Claude sees the output exactly as
if it had run the command itself), and in parallel feeds each line to
testparse.parse_progress -> a throttled (<= ~2/s) POST /testrun
{phase:"progress"}. A `start` is posted at launch and a final `end` (from
parse_final over the whole captured text, with the child's exit code breaking a
tie parse_final can't) at exit. Exits with the child's own exit code.

Every POST is loopback + fire-and-forget: if the HUD is down, the test still
runs and prints normally. run_id + the port come from hook.py (one owner for
both), so a run started here matches one the hook would compute.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hook          # noqa: E402  — run_id_for / normalize_cmd / _endpoint / port
import testparse     # noqa: E402

POST_TIMEOUT_S = 0.6
THROTTLE_S = 0.5     # a progress POST at most ~twice a second


def _post(ev: dict) -> None:
    """Fire-and-forget POST /testrun. The HUD being down must never disturb the
    test run, so every failure is swallowed."""
    try:
        body = json.dumps(ev).encode()
        req = urllib.request.Request(hook._endpoint("/testrun"), data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=POST_TIMEOUT_S).read()
    except Exception:
        pass


def _session() -> str:
    for k in ("CLAUDE_SESSION_ID", "CLAUDE_SESSION", "AGENT_VIEW_SESSION"):
        v = os.environ.get(k)
        if v:
            return v
    return "-"


def _child_argv(argv: list) -> list:
    """Everything after `--` is the child command; without a `--` the whole
    argument list is taken as the command (lenient)."""
    return argv[argv.index("--") + 1:] if "--" in argv else argv


def _tee(line: str) -> None:
    """Write a child line to our stdout WITHOUT ever crashing the wrapper — a
    č/ć/š/ž/đ or a ✓/✕ on a Windows cp1252 console would otherwise raise
    UnicodeEncodeError mid-run and kill the tee (the console-encoding trap)."""
    try:
        sys.stdout.write(line)
        sys.stdout.flush()
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        try:
            sys.stdout.buffer.write(line.encode(enc, "replace"))
            sys.stdout.flush()
        except Exception:
            pass
    except Exception:
        pass


def main() -> int:
    try:                                          # prefer utf-8 on our own stdout
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    child = _child_argv(sys.argv[1:])
    if not child:
        sys.stderr.write("usage: python testrun.py -- <test command ...>\n")
        return 2

    session = _session()
    cmd_str = hook.normalize_cmd(" ".join(child))
    run_id = hook.run_id_for(session, cmd_str)
    ident = {"run_id": run_id, "session": session, "cmd": cmd_str}

    _post(dict(ident, phase="start"))

    agg = {"total": 0, "done": 0, "passed": 0, "failed": 0}
    captured: list = []
    last_post = 0.0

    try:
        proc = subprocess.Popen(
            child, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, universal_newlines=True, encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        sys.stderr.write("testrun: cannot launch: %s\n" % exc)
        _post(dict(ident, phase="end", status="error"))
        return 127

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _tee(line)                            # Claude sees the output verbatim
            captured.append(line)
            try:
                p = testparse.parse_progress(line)
            except Exception:
                p = None
            if not p:
                continue
            if p.get("total") is not None:
                agg["total"] = p["total"]         # absolute
            for k in ("done", "passed", "failed"):
                if p.get(k):
                    agg[k] += p[k]                # increments
            now = time.time()
            if now - last_post >= THROTTLE_S:
                last_post = now
                _post(dict(ident, phase="progress", **agg))
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass
        code = proc.wait()

    final = testparse.parse_final("".join(captured))
    status = final.get("status", "unknown")
    if status not in ("passed", "failed"):
        # parse_final couldn't decide (an unrecognised runner, empty output) —
        # the child's real exit code is authoritative for pass/fail.
        status = "passed" if code == 0 else "failed"
    _post(dict(ident, phase="end", status=status,
               total=final.get("total", agg["total"]) or agg["total"],
               passed=final.get("passed", agg["passed"]),
               failed=final.get("failed", agg["failed"]),
               fails=final.get("fails", [])))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
