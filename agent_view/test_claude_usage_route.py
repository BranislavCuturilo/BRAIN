#!/usr/bin/env python3
"""Offline test for GET /api/claude/usage in server.py.

The server starts on 127.0.0.1:0; `usage_limit.refresh` is monkeypatched so no
network is touched and no token is read. What is pinned: the route returns
the windows and a one-line summary, carries NOTHING that looks like a token,
sends no CORS header (a sensitive-ish read, like the Gemini budget), asks the
reader for a short timeout so a HUD poll cannot hang, and answers a broken
reader with a JSON dash rather than a 500.

Run:  python test_claude_usage_route.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts" / "brain"))
import server        # noqa: E402
import usage_limit   # noqa: E402  (the same module object the route imports)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


def _start():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, dict(r.headers), r.read()


FAKE = {"state": "ok", "fetched_at": 1_800_000_000.0, "fetched_iso": "2026-09-07T11:29:16+00:00",
        "consecutive_429": 0, "floor_until": 0,
        "windows": {"five_hour": {"label": "5h", "percent": 46, "resets_at": "2026-09-07T15:20:00+00:00"},
                    "seven_day": {"label": "7d", "percent": 33, "resets_at": "2026-09-08T02:00:00+00:00"}}}


def main() -> int:
    httpd, port = _start()
    seen = []
    orig = usage_limit.refresh
    orig_sessions = usage_limit.sessions
    try:
        usage_limit.refresh = lambda **kw: (seen.append(kw), dict(FAKE))[1]
        usage_limit.sessions = lambda *a, **kw: [{"session_id": "abc", "cwd": "C:/projects/x", "model": "Fable 5.1",
                                                 "ctx_pct": 41.2, "ctx_size": 1000000, "cost_usd": 0.42,
                                                 "age_s": 12, "lines_added": 3, "lines_removed": 1}]
        status, headers, body = _get(port, "/api/claude/usage")
        d = json.loads(body)
        check("200 with the windows", status == 200 and d["windows"]["five_hour"]["percent"] == 46
              and d["windows"]["seven_day"]["percent"] == 33, body[:120])
        check("one-line summary present", d.get("line", "").startswith("5h 46%"), d.get("line"))
        check("state passed through", d.get("state") == "ok")
        low = body.decode("utf-8", "replace").lower()
        check("nothing token-like in the body", "token" not in low and "sk-ant" not in low and "bearer" not in low)
        check("no CORS header on a sensitive read", "Access-Control-Allow-Origin" not in headers)
        check("reader asked with a short timeout", seen and seen[0].get("timeout", 99) <= 5, str(seen))
        check("the JSON carries only the summary keys",
              set(d) <= {"state", "windows", "fetched_at", "fetched_iso", "problem", "consecutive_429", "line", "sessions"}, sorted(d))
        check("sessions ride along: model, context, cost", d["sessions"][0]["model"] == "Fable 5.1"
              and d["sessions"][0]["ctx_pct"] == 41.2, str(d.get("sessions"))[:120])

        def boom(**kw):
            raise RuntimeError("reader broke")
        usage_limit.refresh = boom
        status, _h, body = _get(port, "/api/claude/usage")
        d = json.loads(body)
        check("a broken reader is a JSON dash, not a 500", status == 200 and d["state"] == "error" and d["windows"] == {})

        usage_limit.refresh = lambda **kw: {"state": "needs_auth", "problem": "the access token has expired -- run `claude`"}
        status, _h, body = _get(port, "/api/claude/usage")
        d = json.loads(body)
        check("no token -> state and problem, empty line", d["state"] == "needs_auth" and d["line"] == "" and "expired" in d["problem"])

        # A live VS Code session with no status-line record: read from its transcript's
        # tail, through the same jail the work summariser uses.
        usage_limit.refresh = lambda **kw: dict(FAKE)
        usage_limit.sessions = lambda *a, **kw: []
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            tr = root / "e275.jsonl"
            import datetime as _dt
            ts = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
            rec = {"type": "assistant", "sessionId": "e275", "cwd": "C:/projects/acme-audit", "version": "2.1.261",
                   "timestamp": ts, "effort": "high", "entrypoint": "claude-vscode",
                   "message": {"model": "claude-fable-5-1",
                               "usage": {"input_tokens": 32, "cache_read_input_tokens": 572907, "cache_creation_input_tokens": 1586}}}
            tr.write_text(json.dumps(rec) + "\n", encoding="utf-8")
            server.WORK_TRANSCRIPT_ROOTS.append(root)
            orig_snapshot = server.HUB.snapshot
            try:
                server.HUB.snapshot = lambda: [{"id": "e275", "cwd": "C:/projects/acme-audit", "transcript": str(tr)}]
                status, _h, body = _get(port, "/api/claude/usage")
                d = json.loads(body)
                ss = d.get("sessions") or []
                check("a live session with no status line is read from its transcript",
                      len(ss) == 1 and ss[0]["session_id"] == "e275" and ss[0]["source"] == "transcript", str(ss)[:160])
                check("  ...model named, context in tokens, no cost invented",
                      ss and ss[0]["model"] == "Fable 5.1" and ss[0]["ctx_tokens"] == 574525 and ss[0]["cost_usd"] is None
                      and ss[0]["entrypoint"] == "claude-vscode", str(ss)[:160])
                # the jail: a transcript outside the roots is not read
                server.WORK_TRANSCRIPT_ROOTS.remove(root)
                ok_path = server._work_transcript_ok(str(tr))
                check("outside the jail the path is refused", ok_path == "", ok_path)
            finally:
                server.HUB.snapshot = orig_snapshot
                if root in server.WORK_TRANSCRIPT_ROOTS:
                    server.WORK_TRANSCRIPT_ROOTS.remove(root)
    finally:
        usage_limit.refresh = orig
        usage_limit.sessions = orig_sessions
        httpd.shutdown()

    failed = [n for n, ok, _d in _results if not ok]
    print(f"\n{len(_results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
