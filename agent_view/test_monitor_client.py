#!/usr/bin/env python3
"""Offline tests for monitor_client — the door to the VPS monitor's MCP API.

No network: monitor_client.urlopen is monkeypatched to return canned MCP
envelopes with the REAL response shapes, or to raise HTTPError / URLError. The
things that matter:
  * overview() unwraps result.content[0].text into system + containers,
  * the ~5s cache serves a second call with NO second HTTP hit,
  * a 401 / URLError / bad-JSON each become a clean MonitorError,
  * NO test failure or error message ever contains the token,
  * is_configured() reflects env / file presence,
  * config() env overrides win.
A FAKE token is used throughout — never a real credential.
Run:  python test_monitor_client.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import monitor_client  # noqa: E402

_results = []
FAKE_TOKEN = "FAKE-TESTONLY-TOKEN-0000"
FAKE_URL = "https://monitor.example.invalid/mcp/"

# REAL response shapes (trimmed but structurally identical to production).
SYSTEM = {
    "timestamp": "2026-08-10T00:00:00",
    "cpu": {"percent": 4.1, "count": 5, "count_logical": 5,
            "freq": {"current": 3092.6, "min": 0, "max": 0},
            "per_cpu": [22.2, 10.0, 3.0, 1.0, 0.0], "load_avg": [0.76, 0.88, 0.85]},
    "memory": {"virtual": {"total": 28371181568, "available": 19388608512,
                           "used": 8982573056, "free": 922791936, "percent": 31.7},
               "swap": {"total": 0, "used": 0, "free": 0, "percent": 0.0}},
    "disk": {"usage": {"/": {"total": 84423864320, "used": 52235935744,
                             "free": 27876462592, "percent": 65.2}}},
    "network": {},
}
CONTAINERS = [
    {"id": "0fdb13c85fd3", "name": "monitor_web", "status": "running",
     "image": "monitor-web:latest",
     "ports": {"8001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8001"}]},
     "stats": None},
    {"id": "aa11bb22cc33", "name": "acme_web", "status": "running",
     "image": "acme:latest", "ports": {}, "stats": None},
]


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


def envelope(payload) -> bytes:
    """Wrap a payload dict the way the MCP server does: the real data is a JSON
    STRING inside result.content[0].text."""
    return json.dumps({"result": {"content": [
        {"type": "text", "text": json.dumps(payload)}]}}).encode("utf-8")


class FakeResp:
    """A minimal urlopen return value: a context manager exposing .read()."""
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Recorder:
    """A fake urlopen that records each outbound request and returns a canned
    envelope keyed by the MCP tool name, or raises a configured error."""
    def __init__(self, raise_exc=None, system=SYSTEM, containers=CONTAINERS,
                 raw_override=None, health=None):
        self.calls = []
        self.raise_exc = raise_exc
        self.system = system
        self.containers = containers
        self.raw_override = raw_override
        self.health = health if health is not None else [
            {"app_id": "ods", "name": "ODS", "url": "https://docs.example.com/health/",
             "status": "up", "http_status": 200, "response_ms": 42},
        ]

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        tool = body.get("params", {}).get("name")
        self.calls.append({
            "tool": tool,
            "auth": req.get_header("Authorization"),
            "url": req.full_url,
            "timeout": timeout,
        })
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.raw_override is not None:
            return FakeResp(self.raw_override)
        if tool == "get_system_stats":
            return FakeResp(envelope(self.system))
        if tool == "get_docker_containers":
            return FakeResp(envelope({"containers": self.containers}))
        if tool == "get_app_health":
            return FakeResp(envelope({"apps": self.health}))
        raise AssertionError("unexpected tool: " + str(tool))


#: A path that never exists, used to blank out a config source in a test — for
#: BOTH the old per-machine file and the tracked file, so a real credential
#: sitting in either of THIS machine's actual config files can never leak into
#: a test result (the hard rule: tests never touch the real local files).
_NOWHERE = str(HERE / "does-not-exist.json")


def _configured_env():
    os.environ["MONITOR_URL"] = FAKE_URL
    os.environ["MONITOR_TOKEN"] = FAKE_TOKEN
    os.environ.pop("AGENT_VIEW_MONITOR_CONFIG", None)
    os.environ["AGENT_VIEW_TRACKED_CONFIG"] = _NOWHERE


def _no_config_env():
    os.environ.pop("MONITOR_URL", None)
    os.environ.pop("MONITOR_TOKEN", None)
    # Point BOTH file paths at something that does not exist so neither this
    # machine's real gitignored file nor its real tracked config can make this
    # look configured.
    os.environ["AGENT_VIEW_MONITOR_CONFIG"] = _NOWHERE
    os.environ["AGENT_VIEW_TRACKED_CONFIG"] = _NOWHERE


def _no_token(exc) -> bool:
    return FAKE_TOKEN not in str(exc)


# --------------------------------------------------------------------------- #
def test_overview_parses_system_and_containers():
    _configured_env()
    monitor_client.reset_cache()
    rec = Recorder()
    orig = monitor_client.urlopen
    monitor_client.urlopen = rec
    try:
        out = monitor_client.overview()
        check("overview: system dict unwrapped from content[0].text",
              out.get("system", {}).get("cpu", {}).get("percent") == 4.1, str(out.get("system")))
        check("overview: memory nested value survives the unwrap",
              out["system"]["memory"]["virtual"]["percent"] == 31.7, str(out["system"].get("memory")))
        check("overview: containers is the inner list (unwrapped from {containers:[...]})",
              isinstance(out.get("containers"), list) and len(out["containers"]) == 2, str(out.get("containers")))
        check("overview: a container's fields are intact",
              out["containers"][0]["name"] == "monitor_web" and out["containers"][0]["status"] == "running",
              str(out["containers"][0]))
        check("overview: it made exactly three HTTP calls (system + docker + health)",
              len(rec.calls) == 3, str([c["tool"] for c in rec.calls]))
        check("overview: the token rides on the Authorization header as a Bearer",
              all(c["auth"] == "Bearer " + FAKE_TOKEN for c in rec.calls),
              "auth header not set to Bearer <token>")
        check("overview: the token is NEVER placed in the URL",
              all(FAKE_TOKEN not in c["url"] for c in rec.calls), "token leaked into URL")
        check("overview: the 20s timeout is passed to urlopen",
              all(c["timeout"] == monitor_client.TIMEOUT_S for c in rec.calls), str(rec.calls))
    finally:
        monitor_client.urlopen = orig


def test_cache_serves_second_call_without_http():
    _configured_env()
    monitor_client.reset_cache()
    rec = Recorder()
    orig = monitor_client.urlopen
    monitor_client.urlopen = rec
    try:
        first = monitor_client.overview()
        n_after_first = len(rec.calls)
        second = monitor_client.overview()
        check("cache: first call hit the network three times (system+docker+health)",
              n_after_first == 3, str(n_after_first))
        check("cache: second call added NO HTTP hit (served from ~5s cache)",
              len(rec.calls) == 3, str(len(rec.calls)))
        check("cache: cached payload is identical", first == second, "cached copy diverged")
        # reset_cache forces a fresh fetch.
        monitor_client.reset_cache()
        monitor_client.overview()
        check("cache: reset_cache re-enables a network fetch", len(rec.calls) == 6, str(len(rec.calls)))
    finally:
        monitor_client.urlopen = orig


def test_401_becomes_clean_error_without_token():
    _configured_env()
    monitor_client.reset_cache()
    err = urllib.error.HTTPError(FAKE_URL, 401, "Unauthorized", {}, None)
    rec = Recorder(raise_exc=err)
    orig = monitor_client.urlopen
    monitor_client.urlopen = rec
    try:
        raised = None
        try:
            monitor_client.overview()
        except Exception as exc:
            raised = exc
        check("401: raises MonitorError", raised is not None and raised.__class__.__name__ == "MonitorError",
              type(raised).__name__ if raised else "none")
        check("401: message is the curated 'unauthorized'", "unauthorized" in str(raised), str(raised))
        check("401: message carries NO token", _no_token(raised), "token leaked in error")
    finally:
        monitor_client.urlopen = orig


def test_other_http_code_becomes_clean_error():
    _configured_env()
    monitor_client.reset_cache()
    err = urllib.error.HTTPError(FAKE_URL, 500, "Server Error", {}, None)
    rec = Recorder(raise_exc=err)
    orig = monitor_client.urlopen
    monitor_client.urlopen = rec
    try:
        raised = None
        try:
            monitor_client.overview()
        except Exception as exc:
            raised = exc
        check("http500: raises MonitorError with the numeric code",
              raised is not None and "500" in str(raised), str(raised))
        check("http500: message carries NO token", _no_token(raised), "token leaked in error")
    finally:
        monitor_client.urlopen = orig


def test_urlerror_becomes_clean_error_without_token():
    _configured_env()
    monitor_client.reset_cache()
    rec = Recorder(raise_exc=urllib.error.URLError("getaddrinfo failed for monitor.example.invalid"))
    orig = monitor_client.urlopen
    monitor_client.urlopen = rec
    try:
        raised = None
        try:
            monitor_client.overview()
        except Exception as exc:
            raised = exc
        check("urlerror: raises MonitorError", raised is not None and raised.__class__.__name__ == "MonitorError",
              type(raised).__name__ if raised else "none")
        check("urlerror: message is the curated 'unreachable'", "unreachable" in str(raised), str(raised))
        check("urlerror: message carries NO token", _no_token(raised), "token leaked in error")
    finally:
        monitor_client.urlopen = orig


def test_bad_outer_json_becomes_clean_error():
    _configured_env()
    monitor_client.reset_cache()
    rec = Recorder(raw_override=b"this is not json <html>500</html>")
    orig = monitor_client.urlopen
    monitor_client.urlopen = rec
    try:
        raised = None
        try:
            monitor_client.overview()
        except Exception as exc:
            raised = exc
        check("badjson-outer: raises MonitorError",
              raised is not None and raised.__class__.__name__ == "MonitorError", str(raised))
        check("badjson-outer: message is 'malformed data'", "malformed" in str(raised), str(raised))
        check("badjson-outer: message carries NO token", _no_token(raised), "token leaked in error")
    finally:
        monitor_client.urlopen = orig


def test_bad_inner_text_json_becomes_clean_error():
    # The envelope is valid JSON, but content[0].text is NOT valid JSON.
    _configured_env()
    monitor_client.reset_cache()
    bad = json.dumps({"result": {"content": [{"type": "text", "text": "{not: valid"}]}}).encode()
    rec = Recorder(raw_override=bad)
    orig = monitor_client.urlopen
    monitor_client.urlopen = rec
    try:
        raised = None
        try:
            monitor_client.overview()
        except Exception as exc:
            raised = exc
        check("badjson-inner: raises MonitorError on non-JSON content text",
              raised is not None and raised.__class__.__name__ == "MonitorError", str(raised))
        check("badjson-inner: message carries NO token", _no_token(raised), "token leaked in error")
    finally:
        monitor_client.urlopen = orig


def test_jsonrpc_error_object_becomes_clean_error():
    _configured_env()
    monitor_client.reset_cache()
    rpc_err = json.dumps({"jsonrpc": "2.0", "id": 1,
                          "error": {"code": -32000, "message": "tool denied"}}).encode()
    rec = Recorder(raw_override=rpc_err)
    orig = monitor_client.urlopen
    monitor_client.urlopen = rec
    try:
        raised = None
        try:
            monitor_client.overview()
        except Exception as exc:
            raised = exc
        check("rpc-error: raises MonitorError", raised is not None and raised.__class__.__name__ == "MonitorError",
              str(raised))
        check("rpc-error: echoes the numeric code, not the upstream message",
              "-32000" in str(raised) and "tool denied" not in str(raised), str(raised))
        check("rpc-error: message carries NO token", _no_token(raised), "token leaked in error")
    finally:
        monitor_client.urlopen = orig


def test_not_configured_call_raises_without_touching_network():
    _no_config_env()
    monitor_client.reset_cache()
    called = {"n": 0}
    orig = monitor_client.urlopen
    monitor_client.urlopen = lambda req, timeout=None: called.__setitem__("n", called["n"] + 1)
    try:
        check("not-configured: is_configured() is False", monitor_client.is_configured() is False, "")
        raised = None
        try:
            monitor_client.overview()
        except Exception as exc:
            raised = exc
        check("not-configured: overview raises MonitorError",
              raised is not None and raised.__class__.__name__ == "MonitorError", str(raised))
        check("not-configured: network was NEVER touched", called["n"] == 0, str(called["n"]))
    finally:
        monitor_client.urlopen = orig
        os.environ.pop("AGENT_VIEW_MONITOR_CONFIG", None)


def test_is_configured_and_config_env_override():
    _configured_env()
    check("config: is_configured True with url+token in env", monitor_client.is_configured() is True, "")
    cfg = monitor_client.config()
    check("config: env url wins", cfg.get("url") == FAKE_URL, str(cfg.get("url")))
    check("config: env token wins", cfg.get("token") == FAKE_TOKEN, "token mismatch")

    # Only a URL, no token -> not configured (a call needs both).
    os.environ["MONITOR_URL"] = FAKE_URL
    os.environ.pop("MONITOR_TOKEN", None)
    os.environ["AGENT_VIEW_MONITOR_CONFIG"] = str(HERE / "does-not-exist.json")
    try:
        check("config: url present but token missing -> not configured",
              monitor_client.is_configured() is False, str(monitor_client.config()))
    finally:
        os.environ.pop("AGENT_VIEW_MONITOR_CONFIG", None)


def test_missing_config_file_is_empty_not_raise():
    _no_config_env()
    try:
        check("config: absent file -> config() is empty {}", monitor_client.config() == {}, str(monitor_client.config()))
    finally:
        os.environ.pop("AGENT_VIEW_MONITOR_CONFIG", None)
        os.environ.pop("AGENT_VIEW_TRACKED_CONFIG", None)


# --------------------------------------------------------------------------- #
#  F5 -- the tracked agent_view.config.json ("monitor": {url, token}) as the
#  middle rung of the resolution chain: env > tracked config > old local file.
# --------------------------------------------------------------------------- #
def test_tracked_config_provides_monitor_when_no_env():
    os.environ.pop("MONITOR_URL", None)
    os.environ.pop("MONITOR_TOKEN", None)
    os.environ["AGENT_VIEW_MONITOR_CONFIG"] = _NOWHERE
    tracked = HERE / "does-not-exist-tracked.json"
    tracked.write_text(json.dumps({"monitor": {"url": FAKE_URL, "token": FAKE_TOKEN}}),
                       encoding="utf-8")
    os.environ["AGENT_VIEW_TRACKED_CONFIG"] = str(tracked)
    try:
        cfg = monitor_client.config()
        check("tracked config: url read from the tracked file", cfg.get("url") == FAKE_URL, str(cfg))
        check("tracked config: token read from the tracked file", cfg.get("token") == FAKE_TOKEN, str(cfg))
        check("tracked config: is_configured() True", monitor_client.is_configured() is True)
    finally:
        tracked.unlink(missing_ok=True)
        os.environ.pop("AGENT_VIEW_MONITOR_CONFIG", None)
        os.environ.pop("AGENT_VIEW_TRACKED_CONFIG", None)


def test_env_overrides_tracked_config():
    tracked = HERE / "does-not-exist-tracked.json"
    tracked.write_text(json.dumps({"monitor": {"url": "https://tracked.example.invalid/mcp/",
                                               "token": "TRACKED-TOKEN"}}),  # release-check: fixture
                       encoding="utf-8")
    os.environ["AGENT_VIEW_TRACKED_CONFIG"] = str(tracked)
    os.environ["MONITOR_URL"] = FAKE_URL
    os.environ["MONITOR_TOKEN"] = FAKE_TOKEN
    os.environ["AGENT_VIEW_MONITOR_CONFIG"] = _NOWHERE
    try:
        cfg = monitor_client.config()
        check("env over tracked: env url wins", cfg.get("url") == FAKE_URL, str(cfg))
        check("env over tracked: env token wins", cfg.get("token") == FAKE_TOKEN, str(cfg))
    finally:
        tracked.unlink(missing_ok=True)
        os.environ.pop("AGENT_VIEW_MONITOR_CONFIG", None)
        os.environ.pop("AGENT_VIEW_TRACKED_CONFIG", None)


def test_tracked_config_overrides_old_local_file():
    legacy = HERE / "does-not-exist-legacy.json"
    legacy.write_text(json.dumps({"monitor_url": "https://legacy.example.invalid/mcp/",
                                  "monitor_token": "LEGACY-TOKEN"}), encoding="utf-8")  # release-check: fixture
    tracked = HERE / "does-not-exist-tracked.json"
    tracked.write_text(json.dumps({"monitor": {"url": FAKE_URL, "token": FAKE_TOKEN}}),
                       encoding="utf-8")
    os.environ.pop("MONITOR_URL", None)
    os.environ.pop("MONITOR_TOKEN", None)
    os.environ["AGENT_VIEW_MONITOR_CONFIG"] = str(legacy)
    os.environ["AGENT_VIEW_TRACKED_CONFIG"] = str(tracked)
    try:
        cfg = monitor_client.config()
        check("tracked over legacy: tracked url wins", cfg.get("url") == FAKE_URL, str(cfg))
        check("tracked over legacy: tracked token wins", cfg.get("token") == FAKE_TOKEN, str(cfg))
    finally:
        legacy.unlink(missing_ok=True)
        tracked.unlink(missing_ok=True)
        os.environ.pop("AGENT_VIEW_MONITOR_CONFIG", None)
        os.environ.pop("AGENT_VIEW_TRACKED_CONFIG", None)


def test_malformed_tracked_config_degrades_to_not_configured():
    tracked = HERE / "does-not-exist-tracked.json"
    tracked.write_text("{not valid json", encoding="utf-8")
    os.environ.pop("MONITOR_URL", None)
    os.environ.pop("MONITOR_TOKEN", None)
    os.environ["AGENT_VIEW_MONITOR_CONFIG"] = _NOWHERE
    os.environ["AGENT_VIEW_TRACKED_CONFIG"] = str(tracked)
    try:
        check("malformed tracked config: config() is empty {}, not raise",
              monitor_client.config() == {}, str(monitor_client.config()))
    finally:
        tracked.unlink(missing_ok=True)
        os.environ.pop("AGENT_VIEW_MONITOR_CONFIG", None)
        os.environ.pop("AGENT_VIEW_TRACKED_CONFIG", None)


def main():
    for fn in (test_overview_parses_system_and_containers,
               test_cache_serves_second_call_without_http,
               test_401_becomes_clean_error_without_token,
               test_other_http_code_becomes_clean_error,
               test_urlerror_becomes_clean_error_without_token,
               test_bad_outer_json_becomes_clean_error,
               test_bad_inner_text_json_becomes_clean_error,
               test_jsonrpc_error_object_becomes_clean_error,
               test_not_configured_call_raises_without_touching_network,
               test_is_configured_and_config_env_override,
               test_missing_config_file_is_empty_not_raise,
               test_tracked_config_provides_monitor_when_no_env,
               test_env_overrides_tracked_config,
               test_tracked_config_overrides_old_local_file,
               test_malformed_tracked_config_degrades_to_not_configured):
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    # Leave the environment clean for the next module.
    for k in ("MONITOR_URL", "MONITOR_TOKEN", "AGENT_VIEW_MONITOR_CONFIG", "AGENT_VIEW_TRACKED_CONFIG"):
        os.environ.pop(k, None)
    monitor_client.reset_cache()
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
