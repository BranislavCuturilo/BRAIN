#!/usr/bin/env python3
"""Offline tests for the Production (VPS monitor) HTTP routes in server.py.

The server runs on 127.0.0.1:0 over loopback; monitor_client's overview() /
is_configured() are monkeypatched so no real call to the VPS happens. What matters:
  * /api/prod/status returns {"configured": bool},
  * /api/prod/overview returns {"ok":true,...} on success and a graceful
    {"ok":false,"configured":bool,"error":...} on failure — with NO token,
  * the not-configured path answers WITHOUT calling overview() (no call-out),
  * the routes are loopback-gated (a non-local peer -> 403),
  * sensitive reads carry no CORS header (cors=False).
Run:  python test_prod_routes.py
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server          # noqa: E402
import monitor_client  # noqa: E402

_results = []
FAKE_TOKEN = "FAKE-TESTONLY-TOKEN-0000"

SYSTEM = {"cpu": {"percent": 4.1}, "memory": {"virtual": {"percent": 31.7}}}
CONTAINERS = [{"id": "0fdb13c85fd3", "name": "monitor_web", "status": "running"}]


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


def _start():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# --------------------------------------------------------------------------- #
def test_status_reports_configured_bool():
    orig = monitor_client.is_configured
    monitor_client.is_configured = lambda: True
    httpd, port = _start()
    try:
        code, headers, body = _get(port, "/api/prod/status")
        out = json.loads(body.decode() or "{}")
        check("status: -> 200 json", code == 200, str(code))
        check("status: reports configured True", out == {"configured": True}, str(out))
        check("status: no CORS header on a sensitive read (cors=False)",
              "Access-Control-Allow-Origin" not in headers, str(headers.get("Access-Control-Allow-Origin")))
        monitor_client.is_configured = lambda: False
        _c, _h, body2 = _get(port, "/api/prod/status")
        check("status: reports configured False when unconfigured",
              json.loads(body2.decode()) == {"configured": False}, body2.decode())
    finally:
        monitor_client.is_configured = orig
        httpd.shutdown()
        httpd.server_close()


def test_overview_happy_path_shape_no_cors():
    orig_cfg = monitor_client.is_configured
    orig_ov = monitor_client.overview
    monitor_client.is_configured = lambda: True
    monitor_client.overview = lambda: {"system": SYSTEM, "containers": CONTAINERS}
    httpd, port = _start()
    try:
        code, headers, body = _get(port, "/api/prod/overview")
        out = json.loads(body.decode() or "{}")
        check("overview: -> 200 json", code == 200, str(code))
        check("overview: ok true", out.get("ok") is True, str(out))
        check("overview: system passed through", out.get("system", {}).get("cpu", {}).get("percent") == 4.1, str(out.get("system")))
        check("overview: containers passed through",
              isinstance(out.get("containers"), list) and out["containers"][0]["name"] == "monitor_web", str(out.get("containers")))
        check("overview: no CORS header (cors=False)",
              "Access-Control-Allow-Origin" not in headers, str(headers.get("Access-Control-Allow-Origin")))
    finally:
        monitor_client.is_configured = orig_cfg
        monitor_client.overview = orig_ov
        httpd.shutdown()
        httpd.server_close()


def test_overview_not_configured_does_not_call_out():
    orig_cfg = monitor_client.is_configured
    orig_ov = monitor_client.overview
    ov_calls = []
    monitor_client.is_configured = lambda: False
    monitor_client.overview = lambda: ov_calls.append(1) or {"system": {}, "containers": []}
    httpd, port = _start()
    try:
        code, _h, body = _get(port, "/api/prod/overview")
        out = json.loads(body.decode() or "{}")
        check("not-configured: -> 200", code == 200, str(code))
        check("not-configured: ok false, configured false", out.get("ok") is False and out.get("configured") is False, str(out))
        check("not-configured: carries an error string", isinstance(out.get("error"), str) and out["error"], str(out))
        check("not-configured: overview() was NEVER called (no call-out)", ov_calls == [], str(ov_calls))
    finally:
        monitor_client.is_configured = orig_cfg
        monitor_client.overview = orig_ov
        httpd.shutdown()
        httpd.server_close()


def test_overview_monitor_error_is_graceful_and_tokenless():
    orig_cfg = monitor_client.is_configured
    orig_ov = monitor_client.overview

    def _boom():
        # The message deliberately embeds the fake token to prove _monitor_err
        # would NOT be the leak path — but MonitorError is the curated class, and
        # the route echoes str(exc). So this test also asserts that a token placed
        # in a MonitorError message DOES surface, which is why monitor_client never
        # puts it there. Here we simulate a *curated* message (no token) and check
        # the graceful shape; the tokenless guarantee for real errors lives in
        # test_monitor_client.
        raise monitor_client.MonitorError("monitor unreachable")

    monitor_client.is_configured = lambda: True
    monitor_client.overview = _boom
    httpd, port = _start()
    try:
        code, _h, body = _get(port, "/api/prod/overview")
        out = json.loads(body.decode() or "{}")
        check("error: -> 200 (graceful in-band failure, not an HTTP error)", code == 200, str(code))
        check("error: ok false, configured true", out.get("ok") is False and out.get("configured") is True, str(out))
        check("error: carries the curated MonitorError text", out.get("error") == "monitor unreachable", str(out))
        check("error: no token anywhere in the response body", FAKE_TOKEN not in body.decode(), "token leaked")
    finally:
        monitor_client.is_configured = orig_cfg
        monitor_client.overview = orig_ov
        httpd.shutdown()
        httpd.server_close()


def test_unknown_exception_collapses_to_generic():
    # A NON-MonitorError bug must not leak its text; _monitor_err collapses it.
    orig_cfg = monitor_client.is_configured
    orig_ov = monitor_client.overview
    monitor_client.is_configured = lambda: True

    def _bug():
        raise RuntimeError("secret path C:/Users/you/token " + FAKE_TOKEN)

    monitor_client.overview = _bug
    httpd, port = _start()
    try:
        code, _h, body = _get(port, "/api/prod/overview")
        out = json.loads(body.decode() or "{}")
        check("bug: -> 200 graceful", code == 200, str(code))
        check("bug: generic error, not the exception text", out.get("error") == "monitor backend error", str(out))
        check("bug: no token, no path in the body", FAKE_TOKEN not in body.decode() and "C:/Users" not in body.decode(), "leak")
    finally:
        monitor_client.is_configured = orig_cfg
        monitor_client.overview = orig_ov
        httpd.shutdown()
        httpd.server_close()


def test_routes_are_loopback_gated():
    orig_gate = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    httpd, port = _start()
    try:
        for route in ("/api/prod/status", "/api/prod/overview"):
            code, _h, _b = _get(port, route)
            check(f"gate: {route} -> 403 for a non-local peer", code == 403, str(code))
    finally:
        server.Handler._client_is_local = orig_gate
        httpd.shutdown()
        httpd.server_close()


def test_prod_routes_wired_in_get_not_post():
    import inspect
    get_src = inspect.getsource(server.Handler.do_GET)
    post_src = inspect.getsource(server.Handler.do_POST)
    for r in ('"/api/prod/status"', '"/api/prod/overview"'):
        check(f"wiring: {r} routed in do_GET", r in get_src, "")
        check(f"wiring: {r} is NOT a mutation route", r not in post_src, "")
    check("wiring: the prod GET block is behind the loopback gate",
          "_client_is_local" in get_src and "/api/prod/overview" in get_src, "")


def main():
    for fn in (test_status_reports_configured_bool,
               test_overview_happy_path_shape_no_cors,
               test_overview_not_configured_does_not_call_out,
               test_overview_monitor_error_is_graceful_and_tokenless,
               test_unknown_exception_collapses_to_generic,
               test_routes_are_loopback_gated,
               test_prod_routes_wired_in_get_not_post):
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
