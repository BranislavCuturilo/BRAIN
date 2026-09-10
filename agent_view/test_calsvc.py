#!/usr/bin/env python3
"""Offline tests for the Calendar service (calsvc.py) and its HTTP routes.

calsvc.STORE_PATH is redirected to a temp file per test (the focus-test pattern),
so nothing touches the real calendar_store.json. Recurrence is delegated to the
sibling `rrule` module; since rrule.py is built in parallel and may not be on disk
yet, we install a DETERMINISTIC daily-expander STUB into sys.modules['rrule']
before any expansion runs. That keeps these tests self-contained and about
calsvc's OWN logic (subtract exdates, apply overrides, count, window cap), not
about rrule's grammar. calsvc imports rrule lazily, so the stub is picked up.

The HTTP checks start the server on 127.0.0.1:0 and drive it over loopback (so the
mutation gate — loopback + same-origin — is satisfied). Run:  python test_calsvc.py
"""
from __future__ import annotations

import json
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


# --------------------------------------------------------------------------- #
#  Deterministic rrule stub — a DAILY expander, installed BEFORE calsvc first
#  imports rrule. One occurrence per day at dtstart's time, within [win,win].
# --------------------------------------------------------------------------- #
class _StubRrule:
    @staticmethod
    def expand(dtstart, rrule_str, win_start, win_end, exdates=None):
        out = []
        cur = dtstart
        while cur < win_start:                 # advance into the window
            cur += timedelta(days=1)
        while cur <= win_end:
            out.append(cur)
            cur += timedelta(days=1)
        return out


sys.modules["rrule"] = _StubRrule             # shadow any on-disk rrule.py, deterministically

import calsvc      # noqa: E402  (after the stub is in place)
import server      # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


def _fresh_store():
    """Point calsvc at an empty temp store and return its dir (for cleanup)."""
    d = tempfile.mkdtemp(prefix="cal_test_")
    calsvc.STORE_PATH = Path(d) / "calendar_store.json"
    return d


def _cleanup(d):
    import shutil
    shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Service-level logic (deterministic, no HTTP)
# --------------------------------------------------------------------------- #
def test_create_then_list():
    d = _fresh_store()
    try:
        res, code = calsvc.create_event({"title": "Dentist", "start": "2026-08-10T09:00",
                                          "end": "2026-08-10T10:00", "cat": "health"})
        check("create: -> 200 with id", code == 200 and bool(res.get("id")), str(res))
        eid = res.get("id")
        out, code = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("list: -> 200", code == 200, str(code))
        evs = out.get("events") or []
        check("list: single event appears once", len(evs) == 1, str(evs))
        o = evs[0]
        check("list: occ_id == master_id for a single", o["occ_id"] == eid and o["master_id"] == eid,
              str(o))
        check("list: is_recurring False for a single", o["is_recurring"] is False, str(o))
        check("list: occ_date is the start date", o["occ_date"] == "2026-08-10", str(o))
        check("list: carries title/cat/start/end/allday/notes",
              o["title"] == "Dentist" and o["cat"] == "health"
              and o["start"] == "2026-08-10T09:00:00" and o["end"] == "2026-08-10T10:00:00"
              and o["allday"] is False and o["notes"] == "", str(o))
        # Outside the window -> absent.
        out2, _ = calsvc.list_occurrences("2026-09-01", "2026-09-30")
        check("list: out-of-window event absent", (out2.get("events") or []) == [], str(out2))
    finally:
        _cleanup(d)


def test_create_validation():
    d = _fresh_store()
    try:
        _r, code = calsvc.create_event({"start": "2026-08-10"})       # no title
        check("create: missing title -> 400", code == 400, str(code))
        _r, code = calsvc.create_event({"title": "x", "start": "not-a-date"})
        check("create: bad start -> 400", code == 400, str(code))
        _r, code = calsvc.create_event({"title": "x", "start": "2026-08-10T10:00",
                                        "end": "2026-08-10T09:00"})    # end before start
        check("create: end before start -> 400", code == 400, str(code))
        _r, code = calsvc.create_event({"title": "x", "start": "2026-08-10", "rrule": 5})
        check("create: non-string rrule -> 400", code == 400, str(code))
        out, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("create: nothing persisted on validation failure",
              (out.get("events") or []) == [], str(out))
    finally:
        _cleanup(d)


def test_recurring_expands_in_range():
    d = _fresh_store()
    try:
        res, _ = calsvc.create_event({"title": "Standup", "start": "2026-08-03T09:30",
                                      "end": "2026-08-03T09:45", "rrule": "FREQ=DAILY"})
        eid = res["id"]
        out, _ = calsvc.list_occurrences("2026-08-03", "2026-08-09")   # 7 inclusive days
        evs = out.get("events") or []
        check("recurring: 7 daily occurrences in a 7-day window", len(evs) == 7, str(len(evs)))
        check("recurring: is_recurring True", all(o["is_recurring"] for o in evs), "")
        check("recurring: occ_id is master#date",
              evs[0]["occ_id"] == eid + "#2026-08-03"
              and evs[-1]["occ_id"] == eid + "#2026-08-09", str([evs[0]["occ_id"], evs[-1]["occ_id"]]))
        check("recurring: each occurrence keeps the master's 15-min duration",
              evs[0]["start"] == "2026-08-03T09:30:00" and evs[0]["end"] == "2026-08-03T09:45:00",
              str(evs[0]))
        check("recurring: occurrences sorted by start",
              [o["start"] for o in evs] == sorted(o["start"] for o in evs), "")
    finally:
        _cleanup(d)


def test_edit_this_override():
    d = _fresh_store()
    try:
        res, _ = calsvc.create_event({"title": "Standup", "start": "2026-08-03T09:30",
                                      "end": "2026-08-03T09:45", "rrule": "FREQ=DAILY"})
        eid = res["id"]
        _r, code = calsvc.update_event({"id": eid, "scope": "this", "occ_date": "2026-08-05",
                                        "title": "Standup (moved)", "start": "2026-08-05T11:00",
                                        "end": "2026-08-05T11:30"})
        check("edit-this: -> 200", code == 200, str(code))
        out, _ = calsvc.list_occurrences("2026-08-03", "2026-08-09")
        by_date = {o["occ_date"]: o for o in out["events"]}
        moved = by_date.get("2026-08-05")
        check("edit-this: overridden occurrence shows the new title/time",
              moved and moved["title"] == "Standup (moved)"
              and moved["start"] == "2026-08-05T11:00:00" and moved["end"] == "2026-08-05T11:30:00",
              str(moved))
        other = by_date.get("2026-08-04")
        check("edit-this: OTHER occurrences unchanged",
              other and other["title"] == "Standup" and other["start"] == "2026-08-04T09:30:00",
              str(other))
        check("edit-this: still 7 occurrences (override does not add/remove)",
              len(out["events"]) == 7, str(len(out["events"])))
    finally:
        _cleanup(d)


def test_edit_all_master():
    d = _fresh_store()
    try:
        res, _ = calsvc.create_event({"title": "Old", "start": "2026-08-10T09:00",
                                      "end": "2026-08-10T10:00"})
        eid = res["id"]
        _r, code = calsvc.update_event({"id": eid, "scope": "all", "title": "New"})
        check("edit-all: -> 200", code == 200, str(code))
        out, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        o = out["events"][0]
        check("edit-all: title changed, start preserved",
              o["title"] == "New" and o["start"] == "2026-08-10T09:00:00", str(o))
        _r, code = calsvc.update_event({"id": "nope", "scope": "all", "title": "x"})
        check("edit-all: unknown id -> 404", code == 404, str(code))
        _r, code = calsvc.update_event({"id": eid, "scope": "sideways"})
        check("edit: bad scope -> 400", code == 400, str(code))
        _r, code = calsvc.update_event({"id": eid, "scope": "this"})   # missing occ_date
        check("edit-this: missing occ_date -> 400", code == 400, str(code))
    finally:
        _cleanup(d)


def test_delete_this_exdate():
    d = _fresh_store()
    try:
        res, _ = calsvc.create_event({"title": "Standup", "start": "2026-08-03T09:30",
                                      "rrule": "FREQ=DAILY"})
        eid = res["id"]
        _r, code = calsvc.delete_event({"id": eid, "scope": "this", "occ_date": "2026-08-05"})
        check("delete-this: -> 200", code == 200, str(code))
        out, _ = calsvc.list_occurrences("2026-08-03", "2026-08-09")
        dates = [o["occ_date"] for o in out["events"]]
        check("delete-this: that occurrence disappears", "2026-08-05" not in dates, str(dates))
        check("delete-this: the rest remain (6 of 7)", len(dates) == 6, str(len(dates)))
        # Idempotent: deleting the same occurrence again does not double the exdate.
        calsvc.delete_event({"id": eid, "scope": "this", "occ_date": "2026-08-05"})
        out2, _ = calsvc.list_occurrences("2026-08-03", "2026-08-09")
        check("delete-this: idempotent", len(out2["events"]) == 6, str(len(out2["events"])))
    finally:
        _cleanup(d)


def test_delete_all_removes_master():
    d = _fresh_store()
    try:
        res, _ = calsvc.create_event({"title": "One-off", "start": "2026-08-10T09:00"})
        eid = res["id"]
        _r, code = calsvc.delete_event({"id": eid, "scope": "all"})
        check("delete-all: -> 200", code == 200, str(code))
        out, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("delete-all: event gone", (out.get("events") or []) == [], str(out))
        _r, code = calsvc.delete_event({"id": eid, "scope": "all"})
        check("delete-all: unknown id -> 404", code == 404, str(code))
    finally:
        _cleanup(d)


def test_window_cap_and_bounds():
    d = _fresh_store()
    try:
        _r, code = calsvc.list_occurrences("2020-01-01", "2026-01-01")   # ~2192 days
        check("window: > 400 days -> 400", code == 400, str(code))
        _r, code = calsvc.list_occurrences("2026-08-10", "2026-08-01")   # to before from
        check("window: to before from -> 400", code == 400, str(code))
        _r, code = calsvc.list_occurrences("", "2026-08-31")             # missing from
        check("window: missing from -> 400", code == 400, str(code))
        _r, code = calsvc.list_occurrences("2026-08-01T00:00", "2026-08-31")  # datetime, not date
        check("window: datetime bound rejected -> 400", code == 400, str(code))
        # Exactly at the cap is allowed.
        _r, code = calsvc.list_occurrences("2026-01-01", "2027-02-05")   # 400 days
        check("window: exactly 400 days allowed -> 200", code == 200, str(code))
    finally:
        _cleanup(d)


def test_allday_event():
    d = _fresh_store()
    try:
        res, code = calsvc.create_event({"title": "Holiday", "start": "2026-08-15", "allday": True})
        check("allday: -> 200", code == 200, str(code))
        out, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        o = out["events"][0]
        check("allday: start stored/served as a bare date",
              o["allday"] is True and o["start"] == "2026-08-15" and o["end"] is None, str(o))
    finally:
        _cleanup(d)


def test_bad_store_file_does_not_crash():
    d = _fresh_store()
    try:
        calsvc.STORE_PATH.write_text("{ this is not valid json", encoding="utf-8")
        out, code = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("badfile: list degrades to empty, no crash",
              code == 200 and (out.get("events") or []) == [], str(out))
        check("badfile: the corrupt file is NOT overwritten by a read",
              calsvc.STORE_PATH.read_text(encoding="utf-8").startswith("{ this is not"), "")
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  HTTP surface — route wiring, defensive body parse, mutation gate presence.
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


def _post(port, path, obj):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post_raw(port, path, raw):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=raw, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_create_list_roundtrip():
    d = _fresh_store()
    httpd, port = _start()
    try:
        code, body = _post(port, "/api/calendar/create",
                           {"title": "Call", "start": "2026-08-12T14:00", "end": "2026-08-12T14:30"})
        out = json.loads(body.decode() or "{}")
        check("http: POST create -> 200 with id", code == 200 and bool(out.get("id")), str(out))
        code, body = _get(port, "/api/calendar/events?from=2026-08-01&to=2026-08-31")
        out = json.loads(body.decode() or "{}")
        check("http: GET events -> 200", code == 200, str(code))
        check("http: created event visible", len(out.get("events") or []) == 1
              and out["events"][0]["title"] == "Call", str(out))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


def test_http_malformed_body_400_nothing_persisted():
    d = _fresh_store()
    httpd, port = _start()
    try:
        code, _b = _post_raw(port, "/api/calendar/create", b"this is not json")
        check("http: malformed JSON -> 400", code == 400, str(code))
        code, _b = _post_raw(port, "/api/calendar/create", b"[1,2,3]")   # valid JSON, not object
        check("http: non-object body -> 400", code == 400, str(code))
        code, _b = _post(port, "/api/calendar/create", {"start": "2026-08-10"})  # no title
        check("http: missing title -> 400", code == 400, str(code))
        code, body = _get(port, "/api/calendar/events?from=2026-08-01&to=2026-08-31")
        out = json.loads(body.decode() or "{}")
        check("http: nothing persisted after the bad bodies",
              (out.get("events") or []) == [], str(out))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


def test_http_window_cap_route():
    d = _fresh_store()
    httpd, port = _start()
    try:
        code, _b = _get(port, "/api/calendar/events?from=2020-01-01&to=2026-01-01")
        check("http: window cap enforced by the route -> 400", code == 400, str(code))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


def test_calendar_mutations_are_in_the_gate():
    # The three writes MUST sit in the loopback + same-origin mutation gate; a LAN
    # host may read the calendar but never drive a write. Assert on the source.
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    for p in ("/api/calendar/create", "/api/calendar/update", "/api/calendar/delete",
              "/api/calendar/gemini"):
        check(f"gate: {p} in _MUT", f'"{p}"' in src.split("_MUT = (", 1)[1].split(")", 1)[0], "")
    getsrc = inspect.getsource(server.Handler.do_GET)
    check("gate: events read is a plain GET (not in the mutation gate)",
          '"/api/calendar/events"' in getsrc and '"/api/calendar/events"' not in src, "")


# --------------------------------------------------------------------------- #
#  F7 — source/ref (calsug's schema addition): default, validated, persisted,
#  and carried onto occurrences the same way remind_min is.
# --------------------------------------------------------------------------- #
def test_source_ref_default_and_roundtrip():
    d = _fresh_store()
    try:
        res, code = calsvc.create_event({"title": "Plain", "start": "2026-08-10T09:00"})
        check("source: create with no source -> 200", code == 200, str(code))
        check("source: defaults to manual, ref None",
              res["event"]["source"] == "manual" and res["event"]["ref"] is None, str(res))
        out, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("source: manual default carried onto the occurrence",
              out["events"][0]["source"] == "manual" and out["events"][0]["ref"] is None,
              str(out["events"][0]))

        res2, code2 = calsvc.create_event({"title": "Rok", "start": "2026-08-12",
                                           "allday": True, "source": "ticket",
                                           "ref": "INV#94313"})
        check("source: explicit source/ref -> 200", code2 == 200, str(code2))
        check("source: persisted on the master",
              res2["event"]["source"] == "ticket" and res2["event"]["ref"] == "INV#94313",
              str(res2))
        out2, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        by_title = {o["title"]: o for o in out2["events"]}
        check("source: carried onto the occurrence",
              by_title["Rok"]["source"] == "ticket" and by_title["Rok"]["ref"] == "INV#94313",
              str(by_title["Rok"]))
    finally:
        _cleanup(d)


def test_source_ref_validation():
    d = _fresh_store()
    try:
        _r, code = calsvc.create_event({"title": "x", "start": "2026-08-10",
                                        "source": "not-a-real-source"})
        check("source: unknown source -> 400", code == 400, str(code))
        _r, code = calsvc.create_event({"title": "x", "start": "2026-08-10", "source": 5})
        check("source: non-string source -> 400", code == 400, str(code))
        _r, code = calsvc.create_event({"title": "x", "start": "2026-08-10", "ref": 5})
        check("source: non-string ref -> 400", code == 400, str(code))
        long_ref = "x" * 200
        res, code = calsvc.create_event({"title": "x", "start": "2026-08-10", "ref": long_ref})
        check("source: overlong ref -> 200 (capped, not rejected)", code == 200, str(code))
        check("source: ref capped at 120 chars",
              len(res["event"]["ref"]) == 120, str(len(res["event"]["ref"])))
        out, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("source: nothing persisted from the two rejected creates",
              len(out.get("events") or []) == 1, str(out))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Phase 2 — reminders (remind_min round-trips create/update and reaches occ).
# --------------------------------------------------------------------------- #
def test_remind_min_roundtrips():
    d = _fresh_store()
    try:
        res, code = calsvc.create_event({"title": "Pill", "start": "2026-08-10T09:00",
                                         "remind_min": 30})
        check("remind: create -> 200", code == 200, str(code))
        eid = res["id"]
        check("remind: stored on the master event", res["event"]["remind_min"] == 30, str(res))
        out, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("remind: appears in the occurrence", out["events"][0].get("remind_min") == 30,
              str(out["events"][0]))
        # Update (scope 'all') changes it; omitting other fields preserves them.
        _r, code = calsvc.update_event({"id": eid, "scope": "all", "remind_min": 15})
        check("remind: update -> 200", code == 200, str(code))
        out, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("remind: updated value in the occurrence",
              out["events"][0].get("remind_min") == 15
              and out["events"][0].get("title") == "Pill", str(out["events"][0]))
        # Absent on create -> 0 (the migration default), and odd values collapse to 0.
        r2, _ = calsvc.create_event({"title": "NoRemind", "start": "2026-08-11T09:00"})
        check("remind: absent -> 0", r2["event"]["remind_min"] == 0, str(r2))
        r3, _ = calsvc.create_event({"title": "Bad", "start": "2026-08-12T09:00",
                                     "remind_min": "soon"})
        check("remind: non-numeric -> 0 (lenient, no 400)", r3["event"]["remind_min"] == 0, str(r3))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Phase 3 — Gemini NL command. _gemini() is swapped for a stub returning canned
#  JSON, so these test calsvc's OWN apply/parse logic, never a real Gemini call.
# --------------------------------------------------------------------------- #
class _FakeGemini:
    """Stands in for gemini_client: .call returns a canned reply, or RAISES the
    canned exception (mirroring gemini_client raising GeminiError on a non-JSON
    reply)."""
    def __init__(self, reply):
        self._reply = reply

    def call(self, prompt, json_out=True, **kw):
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def test_gemini_route_applies_create_update_delete():
    d = _fresh_store()
    httpd, port = _start()
    orig = calsvc._gemini
    try:
        # Seed one event to UPDATE and one to DELETE; the canned reply references
        # their real ids, exactly as Gemini would after seeing the context list.
        a, _ = calsvc.create_event({"title": "Zubar", "start": "2026-08-20T10:00"})
        b, _ = calsvc.create_event({"title": "Sastanak", "start": "2026-08-21T12:00"})
        reply = {"reply": "Gotovo.",
                 "ops": [
                     {"action": "create", "title": "Novi", "start": "2026-08-22T09:00"},
                     {"action": "update", "id": a["id"], "scope": "all",
                      "title": "Zubar (pomeren)", "start": "2026-08-20T14:00"},
                     {"action": "delete", "id": b["id"], "scope": "all"},
                 ]}
        calsvc._gemini = lambda: _FakeGemini(reply)
        code, body = _post(port, "/api/calendar/gemini", {"text": "pomeri zubara i obrisi sastanak"})
        out = json.loads(body.decode() or "{}")
        check("gemini: route -> 200", code == 200, str(code))
        check("gemini: ok + events_changed true",
              out.get("ok") is True and out.get("events_changed") is True, str(out))
        applied = out.get("applied") or []
        check("gemini: all three ops applied ok",
              len(applied) == 3 and all(x.get("ok") for x in applied), str(applied))
        # Verify the effect through the normal read path.
        lst, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        titles = sorted(o["title"] for o in lst["events"])
        check("gemini: create added, update applied, delete removed",
              titles == ["Novi", "Zubar (pomeren)"], str(titles))
    finally:
        calsvc._gemini = orig
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


def test_gemini_route_malformed_reply_no_500_nothing_applied():
    d = _fresh_store()
    httpd, port = _start()
    orig = calsvc._gemini
    try:
        calsvc.create_event({"title": "Keep", "start": "2026-08-20T10:00"})
        # gemini_client raises GeminiError on a non-JSON reply; simulate that here.
        calsvc._gemini = lambda: _FakeGemini(RuntimeError("model did not return JSON"))
        code, body = _post(port, "/api/calendar/gemini", {"text": "napravi nesto"})
        out = json.loads(body.decode() or "{}")
        check("gemini: malformed reply is NOT a 500", code != 500, str(code))
        check("gemini: clean ok:false result",
              out.get("ok") is False and isinstance(out.get("reply"), str) and out["reply"]
              and (out.get("applied") or []) == [] and out.get("events_changed") is False,
              str(out))
        # A non-object JSON reply (a list) is likewise a clean ok:false, not a crash.
        calsvc._gemini = lambda: _FakeGemini([1, 2, 3])
        code, body = _post(port, "/api/calendar/gemini", {"text": "x"})
        out = json.loads(body.decode() or "{}")
        check("gemini: non-object reply -> ok:false, not 500",
              code != 500 and out.get("ok") is False, str((code, out)))
        # Nothing was half-applied by either bad reply.
        lst, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("gemini: nothing half-applied (only the seeded event remains)",
              [o["title"] for o in lst["events"]] == ["Keep"], str(lst))
    finally:
        calsvc._gemini = orig
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


def test_gemini_op_cap():
    d = _fresh_store()
    orig = calsvc._gemini
    try:
        ops = [{"action": "create", "title": f"E{i}", "start": "2026-08-15T09:00"}
               for i in range(calsvc.GEMINI_MAX_OPS + 5)]
        calsvc._gemini = lambda: _FakeGemini({"reply": "ok", "ops": ops})
        res, code = calsvc.gemini_command("napravi mnogo dogadjaja")
        check("gemini-cap: -> ok", code == 200 and res.get("ok") is True, str((code, res.get("ok"))))
        check("gemini-cap: at most GEMINI_MAX_OPS ops applied",
              len(res.get("applied") or []) == calsvc.GEMINI_MAX_OPS,
              str(len(res.get("applied") or [])))
        lst, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("gemini-cap: only GEMINI_MAX_OPS events persisted",
              len(lst["events"]) == calsvc.GEMINI_MAX_OPS, str(len(lst["events"])))
    finally:
        calsvc._gemini = orig
        _cleanup(d)


def test_gemini_bad_op_skipped_and_reported():
    d = _fresh_store()
    orig = calsvc._gemini
    try:
        reply = {"reply": "ok", "ops": [
            {"action": "create", "title": "Good", "start": "2026-08-16T09:00"},
            {"action": "create", "start": "2026-08-16T09:00"},   # no title -> invalid
            {"action": "frobnicate"},                            # unknown action
            "not-an-object",                                     # not even an object
        ]}
        calsvc._gemini = lambda: _FakeGemini(reply)
        res, code = calsvc.gemini_command("...")
        applied = res.get("applied") or []
        oks = [x.get("ok") for x in applied]
        check("gemini-skip: all four ops reported", len(applied) == 4, str(applied))
        check("gemini-skip: only the valid create succeeded",
              oks == [True, False, False, False], str(oks))
        check("gemini-skip: events_changed reflects the one success",
              res.get("events_changed") is True, str(res))
        lst, _ = calsvc.list_occurrences("2026-08-01", "2026-08-31")
        check("gemini-skip: only the good event persisted",
              [o["title"] for o in lst["events"]] == ["Good"], str(lst))
    finally:
        calsvc._gemini = orig
        _cleanup(d)


def main():
    tests = (
        test_create_then_list, test_create_validation, test_recurring_expands_in_range,
        test_edit_this_override, test_edit_all_master, test_delete_this_exdate,
        test_delete_all_removes_master, test_window_cap_and_bounds, test_allday_event,
        test_bad_store_file_does_not_crash, test_http_create_list_roundtrip,
        test_http_malformed_body_400_nothing_persisted, test_http_window_cap_route,
        test_calendar_mutations_are_in_the_gate,
        test_source_ref_default_and_roundtrip, test_source_ref_validation,
        test_remind_min_roundtrips,
        test_gemini_route_applies_create_update_delete,
        test_gemini_route_malformed_reply_no_500_nothing_applied,
        test_gemini_op_cap, test_gemini_bad_op_skipped_and_reported,
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
