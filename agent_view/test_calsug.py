#!/usr/bin/env python3
"""Offline tests for F7 calendar suggestions (calsug.py) and its HTTP routes.

No network, ever: mail/chat context is faked (calsug._mailstore / _mailcache
overridden, the same seam mail_ai's own tests use) and every Gemini call is
injected via the `gemini_call=` parameter or, for the default-caller path,
by overriding calsug._gemini_call — never a real key, never gemini_client's
network path. calsvc.STORE_PATH is redirected to a temp file per test (the
calsvc-test pattern) and every ticket/worklog/dismissed/cache file lives under
its own temp "tickets_store" directory, never the real one. The HTTP checks
point server.CONFIG at a temp config file (the profiles-toggle-route pattern)
so a route test never touches the real, gitignored agent_view.config.json.
Run:  python test_calsug.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts" / "tickets"))

import calsug     # noqa: E402  (also puts scripts/tickets on sys.path)
import calsvc      # noqa: E402
import worklog       # noqa: E402
import server          # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Temp roots — a fresh tickets_store dir + a fresh calendar store, per test.
# --------------------------------------------------------------------------- #
def _recent_mail_date() -> str:
    """An RFC-2822 date INSIDE calsug.MAIL_DAYS of right now.

    The AI source reads the last `MAIL_DAYS` days of the mail cache relative to
    `now`, so a fixture pinned to a literal August date stops being visible on
    the fourth of September -- the AI is never called, the suggestion list comes
    back empty and three checks fail for a reason that has nothing to do with
    what they test. The route-level tests cannot inject `now` (they go over
    HTTP), so the FIXTURE moves instead. Found on 2026-09-10, the first time
    anything ran these files at all.
    """
    when = datetime.now() - timedelta(days=1)
    return when.strftime("%a, %d %b %Y %H:%M:%S +0200")


def _fresh_roots():
    d = tempfile.mkdtemp(prefix="calsug_test_")
    root = Path(d) / "tickets_store"
    root.mkdir()
    calsvc.STORE_PATH = Path(d) / "calendar_store.json"
    return d, root


def _cleanup(d):
    shutil.rmtree(d, ignore_errors=True)


def _write_module(root, mod, tickets):
    (Path(root) / f"{mod}.json").write_text(
        json.dumps({"project": {"name": mod}, "tickets": tickets, "rev": 1}),
        encoding="utf-8")


ONLY_TICKETS = {"tickets": True, "worklog": False, "deploy": False, "mail": False, "chat": False}
ONLY_WORKLOG = {"tickets": False, "worklog": True, "deploy": False, "mail": False, "chat": False}


# --------------------------------------------------------------------------- #
#  Deterministic sources: ticket deadlines.
# --------------------------------------------------------------------------- #
def test_ticket_deadline_suggestion_and_stable_sid():
    d, root = _fresh_roots()
    try:
        _write_module(root, "INV", {
            "94313": {"title": "Nesto se pokvarilo",
                     "helpdesk": {"is_closed": False, "deadline": "2026-08-25"}},
        })
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        sug = res["suggestions"]
        check("ticket: one suggestion produced", len(sug) == 1, str(sug))
        s = sug[0]
        check("ticket: source/ref/start/allday/title",
              s["source"] == "ticket" and s["ref"] == "INV#94313"
              and s["start"] == "2026-08-25" and s["allday"] is True
              and s["title"] == "Rok: #94313 Nesto se pokvarilo", str(s))
        res2 = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        check("ticket: sid stable across calls", res2["suggestions"][0]["sid"] == s["sid"], "")
    finally:
        _cleanup(d)


def test_ticket_deadline_excludes_inactive_and_undated():
    d, root = _fresh_roots()
    try:
        _write_module(root, "INV", {
            "1": {"title": "Closed", "helpdesk": {"is_closed": True, "deadline": "2026-08-25"}},
            "2": {"title": "Done", "helpdesk": {"is_closed": False, "deadline": "2026-08-25"},
                 "triage": {"state": "done"}},
            "3": {"title": "NoDeadline", "helpdesk": {"is_closed": False, "deadline": ""}},
        })
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        check("ticket: closed/done/undated all excluded", res["suggestions"] == [], str(res["suggestions"]))
    finally:
        _cleanup(d)


def test_window_filtering():
    d, root = _fresh_roots()
    try:
        _write_module(root, "INV", {
            "1": {"title": "InWindow", "helpdesk": {"is_closed": False, "deadline": "2026-08-10"}},
            "2": {"title": "OutWindow", "helpdesk": {"is_closed": False, "deadline": "2026-09-10"}},
        })
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        titles = [s["title"] for s in res["suggestions"]]
        check("window: only the in-range deadline appears", titles == ["Rok: #1 InWindow"], str(titles))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Deterministic source: worklog sessions.
# --------------------------------------------------------------------------- #
def test_worklog_suggestion_stable_sid_and_excludes_incomplete():
    d, root = _fresh_roots()
    try:
        worklog.start(str(root), "w1", [{"module": "INV", "ticket": "77"}], kind="claude")
        worklog.end(str(root), "w1")                                     # complete
        worklog.start(str(root), "w2", [{"module": "INV", "ticket": "78"}], kind="claude")
        worklog.end(str(root), "w2", auto_closed=True)                   # complete but auto -> excluded
        worklog.start(str(root), "w3", [{"module": "INV", "ticket": "79"}], kind="claude")
        # w3 never ended -> open, excluded

        today = datetime.now().date()
        frm = (today - timedelta(days=1)).isoformat()
        to = (today + timedelta(days=1)).isoformat()
        res = calsug.suggestions(frm, to, root=root, sources=ONLY_WORKLOG)
        refs = [s["ref"] for s in res["suggestions"]]
        check("worklog: only the complete, non-auto interval produced a suggestion",
              refs == ["INV#77#w1"], str(refs))
        s = res["suggestions"][0]
        check("worklog: title/source", s["title"] == "Rad: #77" and s["source"] == "worklog"
              and s["allday"] is False, str(s))
        res2 = calsug.suggestions(frm, to, root=root, sources=ONLY_WORKLOG)
        check("worklog: sid stable across calls", res2["suggestions"][0]["sid"] == s["sid"], "")

        res3 = calsug.suggestions("2099-01-01", "2099-01-02", root=root, sources=ONLY_WORKLOG)
        check("worklog: window filtering excludes it", res3["suggestions"] == [], str(res3["suggestions"]))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  accept()/dismiss() — never trust client fields, exclude what was acted on.
# --------------------------------------------------------------------------- #
def test_accept_creates_event_with_source_ref_then_disappears():
    d, root = _fresh_roots()
    try:
        _write_module(root, "INV", {"1": {"title": "Rok1",
                                            "helpdesk": {"is_closed": False, "deadline": "2026-08-15"}}})
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        sid = res["suggestions"][0]["sid"]
        out, code = calsug.accept(sid, "2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        check("accept: -> 200 with an event id", code == 200 and bool(out.get("id")), str(out))
        check("accept: sid echoed back", out.get("sid") == sid, str(out))
        ev = out["event"]
        check("accept: event carries source/ref/title/start from the RECOMPUTED suggestion",
              ev["source"] == "ticket" and ev["ref"] == "INV#1"
              and ev["title"] == "Rok: #1 Rok1" and ev["start"] == "2026-08-15", str(ev))

        res2 = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        check("accept: the accepted suggestion no longer appears (already-accepted dedup)",
              res2["suggestions"] == [], str(res2["suggestions"]))

        _out3, code3 = calsug.accept(sid, "2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        check("accept: accepting the same sid again -> 404, not a duplicate event",
              code3 == 404, str(code3))
    finally:
        _cleanup(d)


def test_accept_unknown_sid_and_empty_sid():
    d, root = _fresh_roots()
    try:
        out, code = calsug.accept("", "2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        check("accept: empty sid -> 400", code == 400, str((out, code)))
        out2, code2 = calsug.accept("deadbeef0000", "2026-08-01", "2026-08-31",
                                    root=root, sources=ONLY_TICKETS)
        check("accept: unknown sid -> 404", code2 == 404, str((out2, code2)))
    finally:
        _cleanup(d)


def test_dismiss_removes_suggestion_and_is_idempotent():
    d, root = _fresh_roots()
    try:
        _write_module(root, "INV", {"1": {"title": "Rok1",
                                            "helpdesk": {"is_closed": False, "deadline": "2026-08-15"}}})
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        sid = res["suggestions"][0]["sid"]
        out, code = calsug.dismiss(sid, root=root)
        check("dismiss: -> 200", code == 200 and out.get("sid") == sid, str(out))
        res2 = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        check("dismiss: the suggestion disappears", res2["suggestions"] == [], str(res2["suggestions"]))
        out2, code2 = calsug.dismiss(sid, root=root)
        check("dismiss: dismissing twice is not an error (idempotent)", code2 == 200, str((out2, code2)))
        _o3, code3 = calsug.dismiss("", root=root)
        check("dismiss: empty sid -> 400", code3 == 400, str(code3))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  AI sources (mail/chat) — fakes only, no network.
# --------------------------------------------------------------------------- #
class _FakeMailstore:
    def __init__(self, accts):
        self._accts = accts

    def accounts_public(self):
        return self._accts


class _FakeMailcache:
    def __init__(self, msgs):
        self._msgs = msgs

    def read_messages(self, acct, folder, limit, q=None):
        return list(self._msgs)


class _CountingGemini:
    """A bare callable (the `gemini_call=` shape), not an object with .call —
    matches how estimate.py/calsvc inject their default caller."""
    def __init__(self, reply):
        self.calls = 0
        self.reply = reply

    def __call__(self, prompt, *, json_out=True, api_key="", model=""):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return dict(self.reply)


ONLY_MAIL = {"tickets": False, "worklog": False, "deploy": False, "mail": True, "chat": False}


def _install_mail(msgs):
    orig_ms, orig_mc = calsug._mailstore, calsug._mailcache
    calsug._mailstore = lambda: _FakeMailstore([{"id": "acct1"}])
    calsug._mailcache = lambda: _FakeMailcache(msgs)
    return orig_ms, orig_mc


def _restore_mail(orig):
    calsug._mailstore, calsug._mailcache = orig


def test_ai_not_called_when_both_toggles_off():
    d, root = _fresh_roots()
    try:
        gem = _CountingGemini({"events": []})
        sources = {"tickets": False, "worklog": False, "deploy": False, "mail": False, "chat": False}
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=sources, gemini_call=gem)
        check("ai: not called when both toggles are off", gem.calls == 0, str(gem.calls))
        check("ai: ai_used False", res["ai_used"] is False, str(res))
    finally:
        _cleanup(d)


def test_ai_called_once_per_day_then_cached():
    d, root = _fresh_roots()
    orig = _install_mail([{"subject": "Sastanak", "from_name": "Ana", "date": "Tue, 18 Aug 2026 10:00:00 +0200", "snippet": "sutra u 10"}])
    try:
        reply = {"events": [{"title": "Sastanak sa Anom", "start": "2026-08-20T10:00:00",
                             "allday": False, "why": "iz maila", "ref": "m1", "from": "mail"}]}
        gem = _CountingGemini(reply)
        now = datetime(2026, 8, 19, 9, 0, 0)
        res1 = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_MAIL,
                                  gemini_call=gem, now=now, allow_ai_call=True)
        check("ai: first call reaches the model once", gem.calls == 1, str(gem.calls))
        check("ai: ai_used True", res1["ai_used"] is True, str(res1))
        check("ai: the reply's event was parsed, tagged source=mail",
              len(res1["suggestions"]) == 1 and res1["suggestions"][0]["title"] == "Sastanak sa Anom"
              and res1["suggestions"][0]["source"] == "mail", str(res1["suggestions"]))

        res2 = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_MAIL,
                                  gemini_call=gem, now=now, allow_ai_call=True)
        check("ai: a SECOND call the same day reuses the cache, no second spend",
              gem.calls == 1, str(gem.calls))
        check("ai: cached reply still parses the same suggestion",
              len(res2["suggestions"]) == 1, str(res2["suggestions"]))

        now2 = now + timedelta(days=1)
        res3 = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_MAIL,
                                  gemini_call=gem, now=now2, allow_ai_call=True)
        check("ai: a new calendar day spends a new call", gem.calls == 2, str(res3))
    finally:
        _restore_mail(orig)
        _cleanup(d)


def test_allow_ai_call_false_never_spends_even_with_context():
    d, root = _fresh_roots()
    orig = _install_mail([{"subject": "X", "from_name": "Y", "date": "Tue, 18 Aug 2026 10:00:00 +0200", "snippet": "z"}])
    try:
        gem = _CountingGemini({"events": []})
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_MAIL,
                                 gemini_call=gem, allow_ai_call=False)
        check("ai: allow_ai_call=False never spends, even with mail context available",
              gem.calls == 0, str(gem.calls))
        check("ai: ai_used False on a cache miss with no call allowed", res["ai_used"] is False, str(res))
    finally:
        _restore_mail(orig)
        _cleanup(d)


def test_ai_tolerant_parse():
    d, root = _fresh_roots()
    orig = _install_mail([{"subject": "X", "from_name": "Y", "date": "Tue, 18 Aug 2026 10:00:00 +0200", "snippet": "z"}])
    try:
        junk_reply = {"events": [
            {"title": "Good", "start": "2026-08-20T09:00:00", "allday": False, "ref": "ok1", "from": "mail"},
            {"start": "2026-08-20T09:00:00"},                     # no title
            {"title": "BadDate", "start": "not-a-date"},          # unparseable start
            {"title": "OutOfWindow", "start": "2099-01-01"},      # outside the requested window
            "not-an-object",
            {"title": "NoStart"},
            42,
        ]}
        gem = _CountingGemini(junk_reply)
        # `now=` like every sibling test: the mail fixture is dated 18 Aug, and
        # without it the AI source sees no mail and is never called.
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_MAIL,
                                 gemini_call=gem, now=datetime(2026, 8, 19, 9, 0, 0),
                                 allow_ai_call=True)
        titles = [s["title"] for s in res["suggestions"]]
        check("ai: only the one well-formed, in-window event survives", titles == ["Good"], str(titles))

        # a non-object top-level reply, and a reply with no "events" list, both parse to [].
        check("ai: _parse_ai_reply tolerates a non-dict reply",
              calsug._parse_ai_reply([1, 2, 3], calsug._parse_date("2026-08-01"),
                                     calsug._parse_date("2026-08-31"), ("mail",)) == [], "")
        check("ai: _parse_ai_reply tolerates a dict with no events list",
              calsug._parse_ai_reply({"nope": True}, calsug._parse_date("2026-08-01"),
                                     calsug._parse_date("2026-08-31"), ("mail",)) == [], "")
    finally:
        _restore_mail(orig)
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  HTTP surface.
# --------------------------------------------------------------------------- #
def _start():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(port, path, obj, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(obj).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class _Cfg:
    """Points server.CONFIG at a temp file carrying {tickets_root, ...} for the
    duration of one test, restored on exit — the same isolation
    test_ticket_reader.py's profiles-toggle-route test uses, so a route test
    never touches the real, gitignored agent_view.config.json."""
    def __init__(self, root, extra=None):
        self.root = str(root)
        self.tmp_cfg = Path(tempfile.mkdtemp(prefix="calsug_cfg_")) / "agent_view.config.json"
        self.extra = extra or {}

    def __enter__(self):
        self.orig = server.CONFIG
        server.CONFIG = self.tmp_cfg
        cfg = {"tickets_root": self.root}
        cfg.update(self.extra)
        self.tmp_cfg.write_text(json.dumps(cfg), encoding="utf-8")
        server._tix_cache["data"] = None
        return self

    def __exit__(self, *exc):
        server.CONFIG = self.orig
        server._tix_cache["data"] = None
        return False


def test_http_suggestions_route_and_get_never_spends_ai():
    d, root = _fresh_roots()
    try:
        _write_module(root, "INV", {"1": {"title": "Rok1",
                                            "helpdesk": {"is_closed": False, "deadline": "2026-08-15"}}})
        sources = {"tickets": True, "worklog": True, "deploy": True, "mail": True, "chat": True}
        with _Cfg(root, {"calendar_sources": sources}):
            orig_default = calsug._gemini_call
            counter = {"n": 0}

            def _counting_default(*_a, **_kw):
                counter["n"] += 1
                return {"events": []}
            calsug._gemini_call = _counting_default
            httpd, port = _start()
            try:
                code, body = _get(port, "/api/calendar/suggestions?from=2026-08-01&to=2026-08-31")
                out = json.loads(body.decode() or "{}")
                check("http: GET suggestions -> 200", code == 200, str(code))
                check("http: the ticket deadline suggestion is present",
                      any(s["ref"] == "INV#1" for s in out.get("suggestions") or []), str(out))
                check("http: GET never spends the default Gemini caller (mail+chat on, cache miss)",
                      counter["n"] == 0, str(counter["n"]))
                check("http: ai_used is False on a GET cache miss", out.get("ai_used") is False, str(out))
            finally:
                httpd.shutdown()
                httpd.server_close()
                calsug._gemini_call = orig_default
    finally:
        _cleanup(d)


def test_http_accept_ignores_client_fields_and_dismiss_removes():
    d, root = _fresh_roots()
    try:
        _write_module(root, "INV", {
            "1": {"title": "Rok1", "helpdesk": {"is_closed": False, "deadline": "2026-08-15"}},
            "2": {"title": "Rok2", "helpdesk": {"is_closed": False, "deadline": "2026-08-16"}},
        })
        with _Cfg(root):
            httpd, port = _start()
            try:
                code, body = _get(port, "/api/calendar/suggestions?from=2026-08-01&to=2026-08-31")
                out = json.loads(body.decode() or "{}")
                sid1 = next(s["sid"] for s in out["suggestions"] if s["ref"] == "INV#1")
                sid2 = next(s["sid"] for s in out["suggestions"] if s["ref"] == "INV#2")

                code, body = _post(port, "/api/calendar/suggestions/accept",
                                   {"sid": sid1, "from": "2026-08-01", "to": "2026-08-31",
                                    "title": "HACKED", "start": "2099-01-01", "source": "manual",
                                    "ref": "NOT#REAL"})
                acc = json.loads(body.decode() or "{}")
                check("http: accept -> 200", code == 200, str((code, acc)))
                check("http: accept ignores every client field except sid/from/to",
                      acc["event"]["title"] == "Rok: #1 Rok1" and acc["event"]["start"] == "2026-08-15"
                      and acc["event"]["source"] == "ticket" and acc["event"]["ref"] == "INV#1",
                      str(acc))

                code2, body2 = _get(port, "/api/calendar/events?from=2026-08-01&to=2026-08-31")
                ev = json.loads(body2.decode() or "{}")
                check("http: the accepted suggestion is now a real calendar event",
                      len(ev.get("events") or []) == 1 and ev["events"][0]["title"] == "Rok: #1 Rok1",
                      str(ev))

                code3, body3 = _post(port, "/api/calendar/suggestions/dismiss", {"sid": sid2})
                check("http: dismiss -> 200", code3 == 200, str((code3, body3)))
                code4, body4 = _get(port, "/api/calendar/suggestions?from=2026-08-01&to=2026-08-31")
                out4 = json.loads(body4.decode() or "{}")
                check("http: dismissed suggestion no longer listed",
                      all(s["ref"] != "INV#2" for s in out4["suggestions"]), str(out4))

                code5, _b5 = _post(port, "/api/calendar/suggestions/accept",
                                   {"sid": sid1, "from": "2026-08-01", "to": "2026-08-31"})
                check("http: accepting an already-accepted sid -> 404",
                      code5 == 404, str(code5))
            finally:
                httpd.shutdown()
                httpd.server_close()
    finally:
        _cleanup(d)


def test_http_sources_route_get_post_default_partial_and_gate():
    d, root = _fresh_roots()
    try:
        with _Cfg(root):
            httpd, port = _start()
            try:
                code, body = _get(port, "/api/calendar/sources")
                out = json.loads(body.decode() or "{}")
                check("sources: GET default shape", out.get("sources") == calsug.DEFAULT_SOURCES, str(out))

                full = {"tickets": True, "worklog": False, "deploy": True, "mail": True, "chat": False}
                code2, body2 = _post(port, "/api/calendar/sources", full)
                out2 = json.loads(body2.decode() or "{}")
                check("sources: POST a full valid shape -> 200, echoes it",
                      code2 == 200 and out2.get("sources") == full, str(out2))

                code3, body3 = _get(port, "/api/calendar/sources")
                out3 = json.loads(body3.decode() or "{}")
                check("sources: GET reflects the POST immediately (no restart)",
                      out3.get("sources") == full, str(out3))

                code4, _b4 = _post(port, "/api/calendar/sources", {"tickets": True})
                check("sources: a partial body -> 400", code4 == 400, str(code4))

                bad = dict(full)
                bad["mail"] = "yes"
                code5, _b5 = _post(port, "/api/calendar/sources", bad)
                check("sources: a non-boolean value -> 400", code5 == 400, str(code5))

                code6, _b6 = _post(port, "/api/calendar/sources", full, origin="http://evil.example:1234")
                check("sources: cross-origin POST -> 403 (same CSRF gate)", code6 == 403, str(code6))
            finally:
                httpd.shutdown()
                httpd.server_close()
    finally:
        _cleanup(d)


def test_suggestion_routes_shape_and_mutation_gate():
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    mut_block = src.split("_MUT = (", 1)[1].split(")", 1)[0]
    for p in ("/api/calendar/suggestions/accept", "/api/calendar/suggestions/dismiss",
              "/api/calendar/sources"):
        check(f"gate: {p} in _MUT", f'"{p}"' in mut_block, "")
    getsrc = inspect.getsource(server.Handler.do_GET)
    check("gate: /api/calendar/suggestions read is a plain GET (not in the mutation gate)",
          '"/api/calendar/suggestions"' in getsrc and '"/api/calendar/suggestions"' not in mut_block, "")



def test_null_ref_ai_suggestion_is_accepted_once_and_concurrent_accepts_do_not_double():
    d, root = _fresh_roots()
    orig = _install_mail([{"subject": "Sastanak", "from_name": "Ana", "date": "Tue, 18 Aug 2026 10:00:00 +0200", "snippet": "sutra u 10"}])
    try:
        reply = {"events": [{"title": "Sastanak sa Anom", "start": "2026-08-20T10:00:00",
                             "allday": False, "why": "iz maila", "ref": None, "from": "mail"}]}
        gem = _CountingGemini(reply)
        now = datetime(2026, 8, 19, 9, 0, 0)
        res = calsug.refresh("2026-08-01", "2026-08-31", root=root, sources=ONLY_MAIL, gemini_call=gem, now=now)
        sid = res["suggestions"][0]["sid"]
        r1, c1 = calsug.accept(sid, "2026-08-01", "2026-08-31", root=root, sources=ONLY_MAIL, gemini_call=gem, now=now)
        r2, c2 = calsug.accept(sid, "2026-08-01", "2026-08-31", root=root, sources=ONLY_MAIL, gemini_call=gem, now=now)
        check("null-ref: first accept creates the event", c1 == 200, str(r1))
        check("null-ref: second accept is a 404 (already accepted, deduped by source+start)", c2 == 404, str(r2))
        evs = calsvc.load_store()["events"]
        check("null-ref: exactly one event exists", len([e for e in evs if e.get("source") == "mail"]) == 1, str(evs))
        _write_module(root, "INV", {"1": {"title": "Rok1", "helpdesk": {"is_closed": False, "deadline": "2026-08-15"}}})
        res = calsug.suggestions("2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
        tsid = res["suggestions"][0]["sid"]
        import threading
        codes = []

        def _go():
            _r, c = calsug.accept(tsid, "2026-08-01", "2026-08-31", root=root, sources=ONLY_TICKETS)
            codes.append(c)
        ts = [threading.Thread(target=_go) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        check("concurrent: exactly one of four accepts wins", sorted(codes) == [200, 404, 404, 404], str(codes))
        check("dismiss: a junk sid is refused (never evicts real dismissals)",
              calsug.dismiss("not-a-sid", root=root)[1] == 400)
    finally:
        _restore_mail(orig)
        _cleanup(d)


def test_http_refresh_route_spends_and_get_is_loopback_only():
    d, root = _fresh_roots()
    orig = _install_mail([{"subject": "Sastanak", "from_name": "Ana", "date": _recent_mail_date(), "snippet": "sutra u 10"}])
    try:
        sources = {"tickets": True, "worklog": False, "deploy": False, "mail": True, "chat": False}
        with _Cfg(root, {"calendar_sources": sources}):
            orig_default = calsug._gemini_call
            counter = {"n": 0}

            def _counting_default(*_a, **_kw):
                counter["n"] += 1
                return {"events": [{"title": "X", "start": "2026-08-20T10:00:00", "allday": False, "why": "w", "ref": "m9", "from": "mail"}]}
            calsug._gemini_call = _counting_default
            httpd, port = _start()
            try:
                origin = f"http://127.0.0.1:{port}"
                code, body = _post(port, "/api/calendar/suggestions/refresh", {"from": "2026-08-01", "to": "2026-08-31"}, origin=origin)
                out = json.loads(body.decode() or "{}")
                check("http: POST refresh -> 200 and spends the day's call once", code == 200 and counter["n"] == 1, f"{code} {counter}")
                check("http: refresh returns the AI row", any(s.get("ref") == "m9" for s in out.get("suggestions") or []), str(out))
                code, body = _post(port, "/api/calendar/suggestions/refresh", {"from": "2026-08-01", "to": "2026-08-31"}, origin=origin)
                check("http: a second refresh the same day is served from the cache", counter["n"] == 1, str(counter))
                code, _b = _post(port, "/api/calendar/suggestions/refresh", {"from": "2026-08-01", "to": "2026-08-31"}, origin="http://evil.example")
                check("http: refresh is origin-gated (403)", code == 403, str(code))
                counter["n"] = 0
                cache = Path(root) / ".calsug_cache.json"
                if cache.exists():
                    cache.unlink()
                code, _b = _post(port, "/api/calendar/suggestions/accept", {"sid": "000000000000", "from": "2026-08-01", "to": "2026-08-31"}, origin=origin)
                check("http: accept never spends the AI call (cache cleared, mail on)", counter["n"] == 0, str(counter))
            finally:
                httpd.shutdown()
                httpd.server_close()
                calsug._gemini_call = orig_default
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        i = src.find('"/api/calendar/suggestions"')
        check("http: GET suggestions is gated loopback-only", "_client_is_local()" in src[i:i + 900], "")
    finally:
        _restore_mail(orig)
        _cleanup(d)

def main():
    tests = (
        test_null_ref_ai_suggestion_is_accepted_once_and_concurrent_accepts_do_not_double,
        test_http_refresh_route_spends_and_get_is_loopback_only,
        test_ticket_deadline_suggestion_and_stable_sid,
        test_ticket_deadline_excludes_inactive_and_undated,
        test_window_filtering,
        test_worklog_suggestion_stable_sid_and_excludes_incomplete,
        test_accept_creates_event_with_source_ref_then_disappears,
        test_accept_unknown_sid_and_empty_sid,
        test_dismiss_removes_suggestion_and_is_idempotent,
        test_ai_not_called_when_both_toggles_off,
        test_ai_called_once_per_day_then_cached,
        test_allow_ai_call_false_never_spends_even_with_context,
        test_ai_tolerant_parse,
        test_http_suggestions_route_and_get_never_spends_ai,
        test_http_accept_ignores_client_fields_and_dismiss_removes,
        test_http_sources_route_get_post_default_partial_and_gate,
        test_suggestion_routes_shape_and_mutation_gate,
    )
    for fn in tests:
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
