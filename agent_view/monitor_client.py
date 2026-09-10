#!/usr/bin/env python3
"""One door to the VPS monitor's MCP API — the Production tab reads system + Docker
stats from it, and every call goes through here.

The monitor speaks JSON-RPC 2.0 over HTTPS POST at `monitor_url`, authenticated by
a Bearer token. The token is a PRODUCTION credential: it is read ONLY from the
gitignored config file (or the MONITOR_URL / MONITOR_TOKEN env overrides), rides on
the Authorization header of the outbound request, and NEVER appears in a return
value, an exception message, or a log line — this module logs nothing at all, and
MonitorError messages are curated to a status/kind, mirroring gemini_client's
GeminiError.

Each MCP tool result is an envelope:
    {"result": {"content": [{"type": "text", "text": "<JSON string>"}]}}
so the real payload is the JSON string in content[0].text — _call unwraps it.

Config (first found wins per field):
  env MONITOR_URL / MONITOR_TOKEN
  the TRACKED agent_view.config.json next to this file, key "monitor":
    { "url": "...", "token": "..." }               (decision 2026-08-19)
  agent_view.monitor.config.json next to this file (gitignored, OLD per-machine
  file — kept as the last fallback so an unmigrated machine keeps working)
    { "monitor_url": "...", "monitor_token": "..." }
  env AGENT_VIEW_MONITOR_CONFIG overrides the OLD file's PATH (for tests);
  env AGENT_VIEW_TRACKED_CONFIG overrides the TRACKED file's PATH (for tests) —
  shared with mailstore.py, which reads/writes the same tracked file.

The read-only token may call exactly: get_system_stats, get_cpu_stats,
get_memory_stats, get_disk_stats, get_network_stats, get_docker_containers,
get_app_health, get_visit_stats.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The outbound call ceiling. The monitor is remote, so a stall must not wedge a
#: request thread indefinitely — 20s matches the brief.
TIMEOUT_S = 20.0

#: overview() caches its result for this long so a browser polling the Production
#: tab does not hammer the VPS. Short enough that the HUD still feels live.
OVERVIEW_TTL_S = 5.0

#: app_health() keeps its OWN cache, longer than overview's: an up/down probe
#: changes slowly and overview embeds it, so 30s keeps the main poll cheap while
#: the health list still refreshes several times a minute.
HEALTH_TTL_S = 30.0

#: visit_stats() is goaccess-backed and heavy, so it caches ~5min PER site. The
#: Visits panel loads on demand (not every poll), so a longer TTL is fine.
VISITS_TTL_S = 300.0

#: Bound the callable is patched in tests. Referencing the module-level name (not
#: urllib.request.urlopen directly) lets a test swap in a canned MCP envelope.
urlopen = urllib.request.urlopen


class MonitorError(Exception):
    """A monitor call failed. The message carries a status/kind only — never the
    token, the URL, or an upstream stack trace. Like gemini_client.GeminiError."""


def _config_path() -> Path:
    return Path(os.environ.get("AGENT_VIEW_MONITOR_CONFIG")
                or (HERE / "agent_view.monitor.config.json"))


def _tracked_config_path() -> Path:
    return Path(os.environ.get("AGENT_VIEW_TRACKED_CONFIG")
                or (HERE / "agent_view.config.json"))


def _load_file() -> dict:
    """The OLD per-machine monitor config file, or {} when absent/unreadable. A
    broken config for an OPTIONAL tab must degrade to 'not configured' (the tab
    shows a notice), not raise — so json/read errors collapse to {} rather than
    propagating."""
    fp = _config_path()
    if not fp.exists():
        return {}
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _load_tracked() -> dict:
    """The "monitor" key of the TRACKED agent_view.config.json, or {} when the
    file/key is absent or malformed — degrade quietly (an optional tab). Through
    tracked_config, the one loader; no server.py import."""
    try:
        import tracked_config
        d = tracked_config.load_quiet(_tracked_config_path()).get("monitor")
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def config() -> dict:
    """The monitor endpoint + token. Per field, first found wins: env
    MONITOR_URL/MONITOR_TOKEN, then the tracked config's "monitor" key, then the
    OLD per-machine file. Returns {"url": ..., "token": ...} ONLY when BOTH are
    present (a call needs both); otherwise {} — so is_configured() == bool(config())."""
    tracked = _load_tracked()
    legacy = _load_file()
    url = (os.environ.get("MONITOR_URL") or tracked.get("url")
           or legacy.get("monitor_url") or "").strip()
    token = (os.environ.get("MONITOR_TOKEN") or tracked.get("token")
             or legacy.get("monitor_token") or "").strip()
    if url and token:
        return {"url": url, "token": token}
    return {}


def is_configured() -> bool:
    """True when both a URL and a token are available. Cheap: reads config only,
    never calls out — the status route uses it to answer without a round trip."""
    return bool(config())


# --------------------------------------------------------------------------- #
#  The one JSON-RPC call.
# --------------------------------------------------------------------------- #
_id_lock = threading.Lock()
_next_id = 0


def _rpc_id() -> int:
    global _next_id
    with _id_lock:
        _next_id += 1
        return _next_id


def _unwrap(resp: dict, tool: str) -> dict:
    """Turn one MCP JSON-RPC response into the tool's payload dict. A JSON-RPC
    error object, a missing/!text content part, or non-JSON inner text each become
    a curated MonitorError. Everything here is token-free by construction."""
    if not isinstance(resp, dict):
        raise MonitorError("monitor returned malformed data")
    if resp.get("error") is not None:
        # JSON-RPC error object {"code": int, "message": str}. Echo the numeric
        # code only — never the upstream message, which is out of our control.
        code = ""
        if isinstance(resp["error"], dict):
            code = str(resp["error"].get("code", "")).strip()
        raise MonitorError(f"monitor RPC error {code}".strip())
    result = resp.get("result")
    content = result.get("content") if isinstance(result, dict) else None
    text = None
    if isinstance(content, list):
        for part in content:                       # first text part wins
            if isinstance(part, dict) and part.get("type") == "text" and "text" in part:
                text = part["text"]
                break
        if text is None and content and isinstance(content[0], dict):
            text = content[0].get("text")
    if not isinstance(text, str):
        raise MonitorError("monitor returned malformed data")
    try:
        data = json.loads(text)
    except Exception:
        raise MonitorError("monitor returned malformed data") from None
    if not isinstance(data, dict):
        raise MonitorError("monitor returned malformed data")
    return data


def _call(method: str, tool: str, args: dict | None = None) -> dict:
    """POST one JSON-RPC 2.0 request to the monitor and return the tool's unwrapped
    payload dict. `method` is the JSON-RPC method (e.g. "tools/call"), `tool` the
    MCP tool name, `args` its arguments. Raises MonitorError — with a token-free
    message — on not-configured, HTTP error, timeout, unreachable, bad JSON, or a
    JSON-RPC error. The token goes on the Authorization header, never in the URL
    and never in a raised message."""
    cfg = config()
    if not cfg:
        raise MonitorError("monitor not configured")
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": _rpc_id(),
        "method": method,
        "params": {"name": tool, "arguments": args or {}},
    }).encode("utf-8")
    req = urllib.request.Request(cfg["url"], data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", "Bearer " + cfg["token"])   # secret on header only
    try:
        with urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        # 401/403 -> the token is bad or lacks scope; other codes echo the number.
        if exc.code in (401, 403):
            raise MonitorError("monitor unauthorized") from None
        raise MonitorError(f"monitor HTTP {exc.code}") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        # URLError.reason can name a host/socket detail; class name is enough and
        # carries nothing sensitive. Timeout collapses to the same shape.
        kind = "timed out" if isinstance(exc, TimeoutError) else "unreachable"
        raise MonitorError(f"monitor {kind}") from None
    try:
        resp = json.loads(raw.decode("utf-8"))
    except Exception:
        raise MonitorError("monitor returned malformed data") from None
    return _unwrap(resp, tool)


# --------------------------------------------------------------------------- #
#  Cached reads. overview() is the Production tab's main poll (~5s); app_health()
#  and per-site visit_stats() keep their own longer caches. All thread-safe, all
#  built outside their lock so a slow VPS never blocks other request threads.
# --------------------------------------------------------------------------- #
_overview_lock = threading.Lock()
_overview_cache: dict = {"at": 0.0, "data": None}

_health_lock = threading.Lock()
_health_cache: dict = {"at": 0.0, "data": None}

_visits_lock = threading.Lock()
_visits_cache: dict = {}                 # site-key ("" == main log) -> {at, data}


def reset_cache() -> None:
    """Drop every monitor cache (overview, app-health, per-site visits). For
    tests, and any caller that wants a fresh read."""
    with _overview_lock:
        _overview_cache["at"] = 0.0
        _overview_cache["data"] = None
    with _health_lock:
        _health_cache["at"] = 0.0
        _health_cache["data"] = None
    with _visits_lock:
        _visits_cache.clear()


def app_health() -> list:
    """The monitored apps' up/down list from get_app_health (no args) — the
    `apps` array of [{app_id, name, url, status, http_status, response_ms}].

    Cached for HEALTH_TTL_S with its own lock. A non-list `apps` payload collapses
    to []. Raises MonitorError (token-free) on a call failure; a failure is NOT
    cached, so the next call retries. overview() wraps this so a health failure
    never hides the system/container metrics."""
    now = time.time()
    with _health_lock:
        data = _health_cache["data"]
        if data is not None and now - _health_cache["at"] < HEALTH_TTL_S:
            return data
    payload = _call("tools/call", "get_app_health", {})
    apps = payload.get("apps", [])
    if not isinstance(apps, list):
        apps = []
    with _health_lock:
        _health_cache["at"] = time.time()
        _health_cache["data"] = apps
    return apps


def visit_stats(site: str | None = None) -> dict:
    """goaccess-backed visit stats from get_visit_stats for one `site` (or the
    main log when site is None/empty). Returns the monitor's dict AS-IS — general
    counts, status_codes, top_pages, referrers and the optional os/browsers/
    countries breakdowns — INCLUDING a curated {"error": ...} for an unknown site,
    which the monitor already made safe.

    Cached for VISITS_TTL_S PER site (keyed by site, "" == main log) with its own
    lock, because goaccess is heavy and the Visits panel loads on demand rather
    than every poll. Raises MonitorError (token-free) on a call failure; a failure
    is NOT cached, so the next open retries."""
    key = site or ""
    now = time.time()
    with _visits_lock:
        entry = _visits_cache.get(key)
        if entry is not None and now - entry["at"] < VISITS_TTL_S:
            return entry["data"]
    args = {"site": site} if site else {}      # omit for the main log
    data = _call("tools/call", "get_visit_stats", args)
    with _visits_lock:
        _visits_cache[key] = {"at": time.time(), "data": data}
    return data


def overview() -> dict:
    """{"system": <get_system_stats dict>, "containers": [<container dicts>],
        "health": [<app health dicts>]}.

    Result is cached for OVERVIEW_TTL_S so rapid polls of the Production tab do
    not hammer the VPS. Raises MonitorError (token-free) on a system/container
    failure; a failure is NOT cached, so the next poll retries. Built outside the
    lock — matching server.dashboard_data — so a slow VPS never blocks other
    request threads.

    `health` is best-effort: it rides the main poll (cheap, its own 30s cache),
    but a health MonitorError degrades it to [] rather than sinking the whole
    overview, so the tab still shows metrics and containers when only the health
    probe is down."""
    now = time.time()
    with _overview_lock:
        data = _overview_cache["data"]
        if data is not None and now - _overview_cache["at"] < OVERVIEW_TTL_S:
            return data
    system = _call("tools/call", "get_system_stats", {})
    docker = _call("tools/call", "get_docker_containers", {})
    containers = docker.get("containers", [])
    if not isinstance(containers, list):
        containers = []
    try:
        health = app_health()
    except MonitorError:
        health = []            # a health failure must NOT hide system/containers
    out = {"system": system, "containers": containers, "health": health}
    with _overview_lock:
        _overview_cache["at"] = time.time()
        _overview_cache["data"] = out
    return out
