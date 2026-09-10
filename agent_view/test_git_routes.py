#!/usr/bin/env python3
"""Offline tests for the git-viz HTTP routes in server.py.

The server is started on 127.0.0.1:0 and driven over loopback; gitviz's data
functions are monkeypatched so no real git runs. The security-critical checks:
  * an unknown/traversal `path` -> 400 and git is NEVER executed,
  * the routes are loopback-gated (a non-local peer -> 403),
  * sensitive reads carry no CORS header (cors=False).
Run:  python test_git_routes.py
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server      # noqa: E402
import gitviz      # noqa: E402

_results = []


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
def test_repos_overview_happy_path_no_cors():
    orig = gitviz.repos_overview
    gitviz.repos_overview = lambda cwds, watch=(), full=False: {"repos": [
        {"path": "r", "name": "r", "active": True, "pinned": False, "branch": "main",
         "dirty": 0, "ahead": 0, "behind": 0, "conflicts": 0}]}
    httpd, port = _start()
    try:
        code, headers, body = _get(port, "/api/git/repos")
        out = json.loads(body.decode() or "{}")
        check("repos: -> 200 json", code == 200, str(code))
        check("repos: payload is {repos:[...]}",
              isinstance(out.get("repos"), list) and out["repos"][0]["name"] == "r", str(out))
        check("repos: no CORS header on a sensitive read (cors=False)",
              "Access-Control-Allow-Origin" not in headers, str(headers.get("Access-Control-Allow-Origin")))
    finally:
        gitviz.repos_overview = orig
        httpd.shutdown()
        httpd.server_close()


def test_repos_overview_params_threaded():
    # The route's ONLY job over the data function is to parse the query string:
    # ?full=1 -> full True, ?watch=a,b -> ["a","b"] (parse_qs url-decodes), and a
    # bare call -> the poll-cheap default (full False, empty watch). Capture the
    # args repos_overview is invoked with to prove each shape.
    orig = gitviz.repos_overview
    seen = {}
    gitviz.repos_overview = lambda cwds, watch=(), full=False: (
        seen.update(watch=list(watch), full=full) or {"repos": []})
    httpd, port = _start()
    try:
        _get(port, "/api/git/repos")
        check("params: bare call defaults to full=False (poll-cheap)", seen.get("full") is False, str(seen))
        check("params: bare call sends no watch set", seen.get("watch") == [], str(seen))

        _get(port, "/api/git/repos?full=1")
        check("params: ?full=1 -> full True (initial load stats all repos)", seen.get("full") is True, str(seen))
        check("params: ?full=1 alone -> empty watch", seen.get("watch") == [], str(seen))

        # a url-encoded, comma-separated pair of Windows-ish paths
        _get(port, "/api/git/repos?watch=" + quote("C:/a repo,D:/b") + "&full=0")
        check("params: ?full=0 -> full False", seen.get("full") is False, str(seen))
        check("params: ?watch=a,b -> url-decoded, comma-split list",
              seen.get("watch") == ["C:/a repo", "D:/b"], str(seen))

        _get(port, "/api/git/repos?watch=")   # empty watch value -> no entries
        check("params: an empty ?watch= yields no entries", seen.get("watch") == [], str(seen))
    finally:
        gitviz.repos_overview = orig
        httpd.shutdown()
        httpd.server_close()


def test_repos_overview_foreign_watch_never_stats():
    # End-to-end through the route with the REAL repos_overview: a ?watch pointing
    # at a path that is NOT a discovered repo must be dropped by the whitelist, so
    # git is never run on it. We build a config with two known repos, record every
    # repo_status call, and pass a foreign watch path + no active sessions + no
    # full -> repo_status must never fire (nothing is legitimately watched/active).
    import os
    import shutil
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="gitroute_"))
    for name in ("alpha", "beta"):
        (root / name / ".git").mkdir(parents=True)
    foreign = str(root / "gamma")   # a real dir, but NOT a repo -> not discovered
    orig_config = gitviz._config
    orig_status = gitviz.repo_status
    orig_snapshot = server.HUB.snapshot
    stat_calls = []
    gitviz._config = lambda: {"git_roots": [str(root)], "git_repos": []}
    gitviz.reset_caches()
    gitviz.repo_status = lambda p: stat_calls.append(p) or {
        "branch": "main", "dirty": 0, "ahead": 0, "behind": 0, "conflicts": 0}
    server.HUB.snapshot = lambda: []            # no live sessions -> nothing active
    httpd, port = _start()
    try:
        code, _h, body = _get(port, "/api/git/repos?watch=" + quote(foreign))
        out = json.loads(body.decode() or "{}")
        names = sorted(r["name"] for r in out.get("repos", []))
        check("foreign-watch: -> 200 with both discovered repos", code == 200 and names == ["alpha", "beta"], str(out))
        check("foreign-watch: the foreign path earns NO git call", stat_calls == [], str(stat_calls))
        check("foreign-watch: rows carry no status fields (git never ran)",
              all("branch" not in r for r in out.get("repos", [])), str(out))
    finally:
        gitviz._config = orig_config
        gitviz.repo_status = orig_status
        server.HUB.snapshot = orig_snapshot
        gitviz.reset_caches()
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(root, ignore_errors=True)


def test_unknown_path_is_400_and_never_execs():
    # is_known_repo returns False for any path here, so the route must 400 BEFORE
    # touching git. We prove git is never reached by recording _git + repo_detail.
    orig_known = gitviz.is_known_repo
    orig_detail = gitviz.repo_detail
    orig_git = gitviz._git
    orig_github = gitviz.github
    git_calls = []
    detail_calls = []
    gitviz.is_known_repo = lambda p: False
    gitviz._git = lambda *a, **k: git_calls.append(a) or ""
    gitviz.repo_detail = lambda p: detail_calls.append(p) or {}
    gitviz.github = lambda p: detail_calls.append(p) or {}
    httpd, port = _start()
    try:
        for route in ("/api/git/repo?path=C:\\Windows",
                      "/api/git/github?path=..%2f..%2fetc%2fpasswd"):
            code, _h, body = _get(port, route)
            out = json.loads(body.decode() or "{}")
            check(f"unknown: {route.split('?')[0]} -> 400", code == 400, str(code))
            check(f"unknown: {route.split('?')[0]} returns an error", "error" in out, str(out))
        check("unknown: git was NEVER executed", git_calls == [], str(git_calls))
        check("unknown: neither repo_detail nor github was reached", detail_calls == [], str(detail_calls))
    finally:
        gitviz.is_known_repo = orig_known
        gitviz.repo_detail = orig_detail
        gitviz._git = orig_git
        gitviz.github = orig_github
        httpd.shutdown()
        httpd.server_close()


def test_known_path_reaches_detail():
    # With the whitelist satisfied, the route calls repo_detail with the path.
    orig_known = gitviz.is_known_repo
    orig_detail = gitviz.repo_detail
    seen = []
    gitviz.is_known_repo = lambda p: True
    gitviz.repo_detail = lambda p: seen.append(p) or {"name": "x", "branch": "main",
                                                      "head": "", "commits": [], "maxCol": 0,
                                                      "branches": 1, "metrics": {}, "status": {}}
    httpd, port = _start()
    try:
        code, _h, body = _get(port, "/api/git/repo?path=whatever")
        out = json.loads(body.decode() or "{}")
        check("known: -> 200 with the detail shape", code == 200 and out.get("name") == "x", str(out))
        check("known: repo_detail reached once with the path", seen == ["whatever"], str(seen))
    finally:
        gitviz.is_known_repo = orig_known
        gitviz.repo_detail = orig_detail
        httpd.shutdown()
        httpd.server_close()


def test_routes_are_loopback_gated():
    # Force the loopback gate to fail (as it would for a LAN peer): every git route
    # (including the aggregate heatmap) must 403 before any handler logic runs.
    orig_gate = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    httpd, port = _start()
    try:
        for route in ("/api/git/repos", "/api/git/repo?path=x",
                      "/api/git/github?path=x", "/api/git/insights?path=x",
                      "/api/git/heatmap-all"):
            code, _h, _b = _get(port, route)
            check(f"gate: {route.split('?')[0]} -> 403 for a non-local peer", code == 403, str(code))
    finally:
        server.Handler._client_is_local = orig_gate
        httpd.shutdown()
        httpd.server_close()


def test_heatmap_all_happy_path_no_cors():
    # The aggregate heatmap route takes NO ?path (it enumerates the whitelist itself),
    # calls gitviz.heatmap_all(), and returns {repos,days} with no CORS header.
    orig = gitviz.heatmap_all
    calls = []
    gitviz.heatmap_all = lambda: calls.append(1) or {
        "repos": [{"name": "alpha", "color": "#38e6ff", "path": "a"}],
        "days": {"2026-08-09": {"alpha": 3}}}
    httpd, port = _start()
    try:
        code, headers, body = _get(port, "/api/git/heatmap-all")
        out = json.loads(body.decode() or "{}")
        check("heatmap-all: -> 200 json", code == 200, str(code))
        check("heatmap-all: payload is {repos,days}",
              set(out) == {"repos", "days"} and out["days"]["2026-08-09"]["alpha"] == 3, str(out))
        check("heatmap-all: heatmap_all() reached once", calls == [1], str(calls))
        check("heatmap-all: no CORS header on a sensitive read (cors=False)",
              "Access-Control-Allow-Origin" not in headers,
              str(headers.get("Access-Control-Allow-Origin")))
    finally:
        gitviz.heatmap_all = orig
        httpd.shutdown()
        httpd.server_close()


def test_insights_unknown_path_is_400_and_never_execs():
    # The insights route must validate ?path with is_known_repo BEFORE any git — an
    # unknown/traversal path is a 400 and insights() is never reached.
    orig_known = gitviz.is_known_repo
    orig_insights = gitviz.insights
    orig_git = gitviz._git
    git_calls = []
    insight_calls = []
    gitviz.is_known_repo = lambda p: False
    gitviz._git = lambda *a, **k: git_calls.append(a) or ""
    gitviz.insights = lambda p, config_service=None: insight_calls.append(p) or {}
    httpd, port = _start()
    try:
        for route in ("/api/git/insights?path=C:\\Windows",
                      "/api/git/insights?path=..%2f..%2fetc%2fpasswd"):
            code, _h, body = _get(port, route)
            out = json.loads(body.decode() or "{}")
            check(f"insights-unknown: {route} -> 400", code == 400, str(code))
            check(f"insights-unknown: {route} returns an error", "error" in out, str(out))
        check("insights-unknown: git was NEVER executed", git_calls == [], str(git_calls))
        check("insights-unknown: insights() was never reached", insight_calls == [], str(insight_calls))
    finally:
        gitviz.is_known_repo = orig_known
        gitviz.insights = orig_insights
        gitviz._git = orig_git
        httpd.shutdown()
        httpd.server_close()


def test_insights_known_path_reaches_insights_no_cors():
    # With the whitelist satisfied the route calls insights(path) and returns the
    # panel dict with no CORS header (cors=False, a sensitive local read).
    orig_known = gitviz.is_known_repo
    orig_insights = gitviz.insights
    seen = []
    gitviz.is_known_repo = lambda p: True
    gitviz.insights = lambda p, config_service=None: seen.append(p) or {
        "heatmap": [], "contributors": [], "churn": [], "hotspots": [], "filetypes": []}
    httpd, port = _start()
    try:
        code, headers, body = _get(port, "/api/git/insights?path=whatever")
        out = json.loads(body.decode() or "{}")
        check("insights: -> 200 with the five-panel shape",
              code == 200 and set(out) == {"heatmap", "contributors", "churn",
                                           "hotspots", "filetypes"}, str(out))
        check("insights: insights() reached once with the path", seen == ["whatever"], str(seen))
        check("insights: no CORS header on a sensitive read (cors=False)",
              "Access-Control-Allow-Origin" not in headers,
              str(headers.get("Access-Control-Allow-Origin")))
    finally:
        gitviz.is_known_repo = orig_known
        gitviz.insights = orig_insights
        httpd.shutdown()
        httpd.server_close()


def test_git_routes_wired_in_get_not_post():
    import inspect
    get_src = inspect.getsource(server.Handler.do_GET)
    post_src = inspect.getsource(server.Handler.do_POST)
    for r in ('"/api/git/repos"', '"/api/git/repo"', '"/api/git/github"',
              '"/api/git/insights"', '"/api/git/heatmap-all"'):
        check(f"wiring: {r} routed in do_GET", r in get_src, "")
        check(f"wiring: {r} is NOT a mutation route", r not in post_src, "")
    check("wiring: the git GET block is behind the loopback gate",
          "_client_is_local" in get_src and "/api/git/repos" in get_src, "")


def main():
    for fn in (test_repos_overview_happy_path_no_cors, test_repos_overview_params_threaded,
               test_repos_overview_foreign_watch_never_stats,
               test_unknown_path_is_400_and_never_execs,
               test_known_path_reaches_detail, test_routes_are_loopback_gated,
               test_heatmap_all_happy_path_no_cors,
               test_insights_unknown_path_is_400_and_never_execs,
               test_insights_known_path_reaches_insights_no_cors,
               test_git_routes_wired_in_get_not_post):
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
