#!/usr/bin/env python3
"""Offline tests for the manual "sync all folders" route: POST /api/mail/sync.

The route reuses mailstore's own background driver (start_sync), which spawns a
daemon thread that runs sync_all and swallows its errors. Here sync_all is faked
to record the thread it runs on, proving the route (a) returns immediately, (b)
runs sync_all OFF the request thread, and (c) is gated loopback + same-origin. The
mail cache/config are throwaway; no IMAP, no network beyond loopback. Run:
python test_mail_sync.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_TMP = Path(tempfile.mkdtemp(prefix="mailsync_"))
os.environ["AGENT_VIEW_MAIL_CACHE"] = str(_TMP / "mail_cache.sqlite")
os.environ["AGENT_VIEW_MAIL_CONFIG"] = str(_TMP / "mail.config.json")
os.environ["AGENT_VIEW_TRACKED_CONFIG"] = str(_TMP / "agent_view.config.json")

import server        # noqa: E402
import mailstore     # noqa: E402  (the same module object server._mailstore imports)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


def _start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _post(port, payload, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/mail/sync",
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


class _SyncRecorder:
    """Replaces mailstore.sync_all: records the acct and the thread it ran on, and
    signals an Event so the test can wait for the background thread deterministically."""

    def __init__(self):
        self.acct = None
        self.thread = None
        self.event = threading.Event()

    def __call__(self, acct_id, prefetch_bodies=None):
        self.acct = acct_id
        self.thread = threading.current_thread()
        self.event.set()


def _install_recorder():
    mailstore._syncing.clear()                 # ensure start_sync is not deduped away
    rec = _SyncRecorder()
    mailstore.sync_all = rec                    # start_sync's thread calls this
    return rec


# --------------------------------------------------------------------------- #
def test_route_spawns_sync_all_on_a_thread():
    rec = _install_recorder()
    httpd, port = _start_server()
    try:
        code, out = _post(port, {"acct": "acc1"})            # loopback, no Origin
        check("sync: loopback POST -> 200", code == 200, str(code))
        check("sync: ok:true in the response", out.get("ok") is True, str(out))
        check("sync: status is started/already-running",
              out.get("status") in ("started", "already-running"), str(out.get("status")))
        ran = rec.event.wait(timeout=5)
        check("sync: sync_all was invoked in the background", ran, "event not set")
        check("sync: sync_all got the posted acct", rec.acct == "acc1", str(rec.acct))
        check("sync: sync_all ran OFF the main/request thread",
              rec.thread is not None and rec.thread is not threading.main_thread(),
              str(rec.thread))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_route_cross_origin_rejected_and_no_sync():
    rec = _install_recorder()
    httpd, port = _start_server()
    try:
        code, _ = _post(port, {"acct": "acc1"}, origin="http://evil.example:1234")
        check("sync: cross-origin POST -> 403 (CSRF gate)", code == 403, str(code))
        fired = rec.event.wait(timeout=0.5)
        check("sync: sync_all NEVER invoked on a rejected cross-origin POST",
              not fired, "event was set")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_route_503_when_backend_unavailable():
    # When the mail backend cannot load, the route is a clean 503, not a crash.
    orig = server.Handler._mailstore
    server.Handler._mailstore = lambda self: None
    httpd, port = _start_server()
    try:
        code, out = _post(port, {"acct": "acc1"})
        check("sync: backend unavailable -> 503", code == 503, str(code))
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.Handler._mailstore = orig


def test_route_is_in_the_mutation_gate():
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    check("gate: /api/mail/sync is in the mutation tuple _MUT",
          '"/api/mail/sync"' in src)


def test_non_loopback_peer_is_not_local():
    # The mutation gate depends on _client_is_local; a LAN peer must read as remote.
    h = server.Handler.__new__(server.Handler)
    h.client_address = ("192.168.1.50", 5555)
    check("gate: LAN peer is NOT local", server.Handler._client_is_local(h) is False)
    h.client_address = ("127.0.0.1", 5555)
    check("gate: loopback peer IS local", server.Handler._client_is_local(h) is True)


def main():
    for fn in (test_route_spawns_sync_all_on_a_thread,
               test_route_cross_origin_rejected_and_no_sync,
               test_route_503_when_backend_unavailable,
               test_route_is_in_the_mutation_gate,
               test_non_loopback_peer_is_not_local):
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
