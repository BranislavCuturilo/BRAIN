#!/usr/bin/env python3
"""Offline tests for the AUTO work log in the server: launch -> heartbeat ->
Stop summary -> SessionEnd, the manual end route, and what the ticket rows carry.

Nothing here spawns a process, opens the real tickets store or touches the
running HUD: `subprocess.Popen` and `shutil.which` are faked, `server.load_config`
is stubbed to a fresh temp root (the same pattern as
test_server_routes.test_ticket_rows_carry_sent_counts_keyed_by_file_stem), and the
one HTTP test binds 127.0.0.1:0 — an ephemeral port, never 7666.
Run: python test_worklog_server.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts" / "tickets"))

import server      # noqa: E402  (also puts scripts/tickets on sys.path)
import ai_log      # noqa: E402
import worklog     # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  A temp store + the fakes. Every test gets its own root.
# --------------------------------------------------------------------------- #
def _store(tmp, ids=("94313", "94320")):
    root = Path(tmp)
    (root / "INV.json").write_text(json.dumps({
        "project": {"name": "INV", "helpdesk_module": "INV", "repo": ""},
        "tickets": {tid: {"title": f"t{tid}", "status": "active",
                          "helpdesk": {"is_closed": False}} for tid in ids},
        "rev": 1}), encoding="utf-8")
    return root


class _Root:
    """server.load_config stubbed to a temp tickets_root, restored on exit."""
    def __init__(self, root):
        self.root = str(root)

    def __enter__(self):
        self._orig = server.load_config
        server.load_config = lambda: {"tickets_root": self.root}
        server._tix_cache["data"] = None
        server.WORK_TRANSCRIPT_ROOTS.append(Path(self.root))   # the jail admits this temp dir
        with server._work_lock:
            server._work_touched.clear()
            server._work_transcripts.clear()
            server._work_logged.clear()
        return self

    def __exit__(self, *exc):
        server.load_config = self._orig
        server._tix_cache["data"] = None
        try:
            server.WORK_TRANSCRIPT_ROOTS.remove(Path(self.root))
        except ValueError:
            pass
        return False


class _FakePopen:
    """Records the spawn instead of making one. `boom` raises like a missing CLI."""
    calls = []
    boom = None

    def __init__(self, argv, **kw):
        if _FakePopen.boom:
            raise _FakePopen.boom
        _FakePopen.calls.append({"argv": argv, "cwd": kw.get("cwd"), "env": kw.get("env")})


class _NoSpawn:
    """Popen + which faked for the duration of a block: nothing is executed."""
    def __enter__(self):
        _FakePopen.calls = []
        _FakePopen.boom = None
        self._popen, self._which = subprocess.Popen, shutil.which
        subprocess.Popen = _FakePopen
        shutil.which = lambda name: "/fake/" + name
        return _FakePopen

    def __exit__(self, *exc):
        subprocess.Popen, shutil.which = self._popen, self._which
        _FakePopen.boom = None
        return False


TRANSCRIPT = [
    {"type": "assistant", "timestamp": "2026-08-19T10:00:00.000Z",
     "message": {"role": "assistant", "content": [
         {"type": "tool_use", "id": "u1", "name": "Edit",
          "input": {"file_path": "e:/repo/popis/views.py"}}]}},
    {"type": "user", "timestamp": "2026-08-19T10:00:01.000Z",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "u1", "content": "ok"}]}},
    {"type": "assistant", "timestamp": "2026-08-19T10:01:00.000Z",
     "message": {"role": "assistant", "content": [
         {"type": "tool_use", "id": "u2", "name": "Bash",
          "input": {"command": 'git commit -m "popis: fix #94313"'}}]}},
    {"type": "user", "timestamp": "2026-08-19T10:01:02.000Z",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "u2",
          "content": "[main abc1234] popis: fix #94313"}]}},
    {"type": "assistant", "timestamp": "2026-08-19T10:02:00.000Z",
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "Popravljeno."}]}},
]


def _transcript(root):
    fp = Path(root) / "transcript.jsonl"
    fp.write_text("\n".join(json.dumps(e) for e in TRANSCRIPT) + "\n", encoding="utf-8")
    return str(fp)


def _event(work_id, phase="tool", transcript=None):
    ev = {"session_id": "s1", "cwd": "e:/repo/popis", "agent": "main",
          "phase": phase, "tool": "Edit", "work_id": work_id}
    if transcript:
        ev["transcript_path"] = transcript
    return ev


def _touches(root, work_id):
    return [e for e in worklog.read_events(root)
            if e.get("ev") == "touch" and e.get("work_id") == work_id]


# --------------------------------------------------------------------------- #
def test_launch_with_tickets_opens_intervals_and_hands_the_id_to_the_child():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root), _NoSpawn() as popen:
            res, code = server.launch_claude(
                "uradi ovo", tickets=[{"module": "INV", "ticket": "94313"},
                                      {"module": "INV", "ticket": "94320"}])
        check("launch: 200 with a work_id", code == 200 and bool(res.get("work_id")), str(res))
        wid = res.get("work_id")
        ivs = worklog.open_intervals(str(root))
        check("launch: one open interval per ticket",
              sorted(iv["ticket"] for iv in ivs) == ["94313", "94320"], str(ivs))
        check("launch: they carry the work id and kind=claude",
              all(iv["work_id"] == wid and iv["kind"] == "claude" for iv in ivs), str(ivs))
        check("launch: a merged launch splits the session 1/N",
              all(abs(iv["share"] - 0.5) < 1e-9 for iv in ivs), str(ivs))
        env = popen.calls[0]["env"] if popen.calls else {}
        check("launch: the child is told its work id", env.get("BRAIN_WORK_ID") == wid, str(wid))
        check("launch: and which tickets it covers",
              env.get("BRAIN_TICKETS") == "INV:94313,INV:94320", str(env.get("BRAIN_TICKETS")))
        check("launch: the rest of the environment is inherited, not replaced",
              len(env) > 2 and "PATH" in {k.upper() for k in env}, str(len(env)))
        check("launch: the prompt is never put in the environment",
              not any("uradi ovo" in str(v) for v in env.values()))


def test_a_launch_without_tickets_measures_nothing_and_is_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root), _NoSpawn() as popen:
            res, code = server.launch_claude("bez tiketa")
        check("unmeasured: still a plain ok", (res, code) == ({"ok": True}, 200), str(res))
        check("unmeasured: no work_id in the reply", "work_id" not in res, str(res))
        check("unmeasured: nothing written to the work log",
              worklog.read_events(str(root)) == [])
        check("unmeasured: env=None, so the child inherits ours",
              popen.calls and popen.calls[0]["env"] is None, str(popen.calls))


def test_unknown_module_or_ticket_is_dropped_never_measured():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root):
            picked = server._launch_tickets([
                {"module": "INV", "ticket": "94313"},     # real
                {"module": "INV", "ticket": "999999"},    # unknown ticket
                {"module": "NEMA", "ticket": "94313"},      # unknown module
                {"module": "../etc", "ticket": "94313"},    # outside the jail
                {"module": "ai_log", "ticket": "94313"},    # a catalog, not a module
                "nonsense", {"module": "INV"}, {"ticket": "94313"},
                {"module": "INV", "ticket": "94313"},     # duplicate
            ])
        check("validate: only the pair that exists survives",
              picked == [{"module": "INV", "ticket": "94313"}], str(picked))
        with _Root(root):
            check("validate: a non-list is empty, not an error",
                  server._launch_tickets("INV:1") == [] and server._launch_tickets(None) == [])
        # What the ROUTE does: validate, then launch. An unknown pair must never
        # cost the operator the launch - it just goes unmeasured.
        with _Root(root), _NoSpawn():
            res, code = server.launch_claude(
                "x", tickets=server._launch_tickets([{"module": "NEMA", "ticket": "1"}]))
        check("validate: an unknown pair still launches, just unmeasured",
              code == 200 and "work_id" not in res, str(res))
        check("validate: and opens no interval", worklog.read_events(str(root)) == [])
        # The door itself validates SHAPE only (worklog.start drops junk) - the
        # store lookup lives in the route, which is where the store is known.
        with _Root(root), _NoSpawn():
            res, code = server.launch_claude("x", tickets=["nonsense", {"module": "INV"},
                                                           {"ticket": "94313"}, 42])
        check("validate: shapeless tickets are dropped at the door too",
              code == 200 and "work_id" not in res and worklog.read_events(str(root)) == [],
              str(res))


def test_a_failed_spawn_closes_the_interval_it_opened():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root), _NoSpawn():
            _FakePopen.boom = OSError("no console")
            res, code = server.launch_claude(
                "x", tickets=[{"module": "INV", "ticket": "94313"}])
        check("spawn-fail: a clean 502", code == 502 and "error" in res, str(res))
        check("spawn-fail: nothing is left open", worklog.open_intervals(str(root)) == [])
        ivs = worklog.intervals(str(root))
        check("spawn-fail: the interval is closed and marked auto",
              len(ivs) == 1 and ivs[0]["auto_closed"] is True, str(ivs))
        check("spawn-fail: a launch that never ran is not billable work",
              worklog.hours_by_ticket(str(root)) == {})


def test_an_event_with_a_work_id_is_a_heartbeat_throttled_to_one_a_minute():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root), _NoSpawn():
            res, _ = server.launch_claude(
                "x", tickets=[{"module": "INV", "ticket": "94313"}])
            wid = res["work_id"]
            server.HUB.ingest(_event(wid))
            server.HUB.ingest(_event(wid, phase="tool"))
            server.HUB.ingest(_event(wid, phase="ask"))
            check("heartbeat: three events in a row write ONE touch",
                  len(_touches(str(root), wid)) == 1, str(_touches(str(root), wid)))
            with server._work_lock:                     # pretend a minute passed
                server._work_touched[wid] = time.monotonic() - server.WORK_TOUCH_S - 1
            server.HUB.ingest(_event(wid))
            check("heartbeat: after the interval, the next event writes another",
                  len(_touches(str(root), wid)) == 2, str(_touches(str(root), wid)))
            check("heartbeat: it keeps the interval out of the reaper's reach",
                  worklog.reap(str(root)) == [])
            server.HUB.ingest({"session_id": "s2", "phase": "tool", "agent": "main"})
            check("heartbeat: an event with no work_id writes nothing",
                  len(_touches(str(root), wid)) == 2)


def test_sessionend_ends_the_interval_writes_runs_and_logs_once():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root), _NoSpawn():
            res, _ = server.launch_claude(
                "x", tickets=[{"module": "INV", "ticket": "94313"},
                              {"module": "INV", "ticket": "94320"}])
            wid = res["work_id"]
            server.HUB.ingest(_event(wid, transcript=_transcript(root)))
            server.HUB.ingest(_event(wid, phase="sessionend"))
            # Wait for what is ASSERTED, not for the first thing that happens.
            # This loop used to stop as soon as the intervals closed -- but the
            # off-thread finisher closes intervals and THEN writes the runs into
            # the ticket file, so the wait was satisfied before the thing being
            # checked existed. It failed 2 runs in 5 on an idle machine and
            # nobody could see it, because until 2026-09-10 nothing ran the
            # tests under agent_view/ at all.
            def _settled() -> bool:
                if worklog.open_intervals(str(root)):
                    return False
                try:
                    d = json.loads((root / "INV.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return False          # mid-write; not settled
                return all((d["tickets"].get(t) or {}).get("runs")
                           for t in ("94313", "94320"))

            for _ in range(200):                        # up to 10s, off-thread
                if _settled():
                    break
                time.sleep(0.05)
            check("sessionend: every interval of the run is closed",
                  worklog.open_intervals(str(root)) == [], str(worklog.open_intervals(str(root))))
            check("sessionend: closed by the session, NOT auto-closed",
                  all(iv["auto_closed"] is False for iv in worklog.intervals(str(root))))
            d = json.loads((root / "INV.json").read_text(encoding="utf-8"))
            runs = {tid: t.get("runs") or [] for tid, t in d["tickets"].items()}
            check("sessionend: both tickets carry the run",
                  len(runs["94313"]) == 1 and len(runs["94320"]) == 1, str(runs))
            r = runs["94313"][0]
            check("sessionend: the run names itself and is ended",
                  r["work_id"] == wid and bool(r["ended"]) and bool(r["started"]), str(r))
            check("sessionend: it carries the files the run edited",
                  r["files"] == ["e:/repo/popis/views.py"], str(r["files"]))
            check("sessionend: and the commit it made",
                  [c["hash"] for c in r["commits"]] == ["abc1234"], str(r["commits"]))
            check("sessionend: and the last thing the agent said",
                  r["summary"] == "Popravljeno.", str(r["summary"]))
            check("sessionend: the file rev was bumped", d["rev"] == 2, str(d["rev"]))
            entries = [e for e in ai_log.read(str(root)) if e.get("action") == "run"]
            check("sessionend: exactly ONE ai_log run entry", len(entries) == 1, str(entries))
            e = entries[0] if entries else {}
            check("ai_log: it names both tickets and the module",
                  sorted(e.get("ticket_ids") or []) == ["94313", "94320"]
                  and e.get("modules") == ["INV"], str(e))
            check("ai_log: the summary is the one-liner",
                  "1 fajlova" in (e.get("summary") or "")
                  and "commit abc1234" in (e.get("summary") or ""), str(e.get("summary")))
            check("ai_log: extra carries the work id and hours",
                  (e.get("extra") or {}).get("work_id") == wid
                  and isinstance((e.get("extra") or {}).get("hours"), float), str(e.get("extra")))
            # A second finish (the reaper, a double click) must not log twice.
            server._work_finish(wid)
            again = [x for x in ai_log.read(str(root)) if x.get("action") == "run"]
            check("sessionend: finishing twice still logs once", len(again) == 1, str(again))


def test_the_reaper_closes_an_abandoned_run_and_still_records_it():
    # The Stop hook never fired (Claude was killed, the machine slept). The
    # interval must close AT THE LAST HEARTBEAT and still leave a record - an
    # abandoned run that vanishes without a trace is the failure mode here.
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        r = str(root)
        # Dated in the PAST relative to any clock this can run on, so "is it idle
        # yet" never depends on what time of day the suite is started.
        worklog.append_event(r, {"ev": "start", "work_id": "w9", "module": "INV",
                                 "ticket": "94313", "at": "2026-08-01T09:00:00",
                                 "share": 1.0, "kind": "claude", "device": "a"})
        worklog.append_event(r, {"ev": "touch", "work_id": "w9",
                                 "at": "2026-08-01T09:05:00", "device": "a"})
        with _Root(root):
            with server._work_lock:
                server._work_transcripts["w9"] = _transcript(root)
            ended = server._work_reap()
            check("reaper: the abandoned interval is closed", len(ended) == 1, str(ended))
            iv = worklog.intervals(r)[0]
            check("reaper: at the last heartbeat, marked auto_closed",
                  iv["end"] == "2026-08-01T09:05:00" and iv["auto_closed"] is True, str(iv))
            for _ in range(60):                        # the finaliser runs off-thread
                d = json.loads((root / "INV.json").read_text(encoding="utf-8"))
                if d["tickets"]["94313"].get("runs"):
                    break
                time.sleep(0.05)
            runs = d["tickets"]["94313"].get("runs") or []
            check("reaper: the run is still written onto the ticket",
                  len(runs) == 1 and runs[0]["work_id"] == "w9", str(runs))
            check("reaper: and it is stamped ended, not left running",
                  runs[0].get("ended") == "2026-08-01T09:05:00", str(runs[0].get("ended")))
            check("reaper: exactly one ai_log entry for it",
                  len([e for e in ai_log.read(r) if e.get("action") == "run"]) == 1)
            check("reaper: an auto-closed run is not calibration data",
                  worklog.hours_by_ticket(r) == {})


def test_a_late_stop_summary_does_not_un_end_a_finished_run():
    # The 5 s debounce timer can fire just AFTER the finaliser. It must update
    # the same row, never clear its `ended` - a run that reads as "still going"
    # forever is exactly the lie this feature exists to remove.
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        r = str(root)
        for ev, at in (("start", "2026-08-19T09:00:00"), ("end", "2026-08-19T09:30:00")):
            worklog.append_event(r, {"ev": ev, "work_id": "w7", "module": "INV",
                                     "ticket": "94313", "at": at, "share": 1.0,
                                     "kind": "claude", "device": "a"})
        with _Root(root):
            server._work_write_runs("w7", {"files": ["a.py"], "commits": [], "turns": 1,
                                           "tests": {"ran": 0, "passed": None, "last": ""},
                                           "summary": "prvi", "last_at": "2026-08-19T09:30:00"},
                                    ended="2026-08-19T09:30:00")
            server._work_write_runs("w7", {"files": ["a.py", "b.py"], "commits": [], "turns": 2,
                                           "tests": {"ran": 0, "passed": None, "last": ""},
                                           "summary": "kasni sazetak",
                                           "last_at": "2026-08-19T09:31:00"})
        d = json.loads((root / "INV.json").read_text(encoding="utf-8"))
        runs = d["tickets"]["94313"]["runs"]
        check("late-summary: still ONE row for the run", len(runs) == 1, str(runs))
        check("late-summary: the newer content won",
              runs[0]["summary"] == "kasni sazetak" and len(runs[0]["files"]) == 2, str(runs[0]))
        check("late-summary: `ended` survived it",
              runs[0]["ended"] == "2026-08-19T09:30:00", str(runs[0].get("ended")))


def test_the_manual_end_route_finishes_a_run():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root), _NoSpawn():
            res, _ = server.launch_claude(
                "x", tickets=[{"module": "INV", "ticket": "94313"}])
            wid = res["work_id"]
            server.HUB.ingest(_event(wid, transcript=_transcript(root)))
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            port = httpd.server_address[1]
            try:
                code, out = _post(port, "/api/tickets/work/end", {"work_id": wid})
                check("end-route: 200 with what it closed",
                      code == 200 and out.get("ok") is True
                      and [e["ticket"] for e in out.get("ended") or []] == ["94313"], str(out))
                check("end-route: the interval really is closed",
                      worklog.open_intervals(str(root)) == [])
                check("end-route: it is idempotent",
                      _post(port, "/api/tickets/work/end", {"work_id": wid})[1].get("ended") == [])
                code2, out2 = _post(port, "/api/tickets/work/end", {"work_id": "nema-takvog"})
                check("end-route: an unknown work_id is a quiet 200, not a 500",
                      code2 == 200 and out2.get("ended") == [], str(out2))
                check("end-route: and it never logs an entry for one",
                      not [e for e in ai_log.read(str(root))
                           if (e.get("extra") or {}).get("work_id") == "nema-takvog"])
                code3, _ = _post(port, "/api/tickets/work/end", {}, origin="http://evil.example:1")
                check("end-route: cross-origin POST -> 403 (CSRF gate)", code3 == 403, str(code3))
                code4, out4 = _post(port, "/api/tickets/work/end", {})
                check("end-route: a body with no work_id -> 400", code4 == 400, str(out4))
            finally:
                httpd.shutdown()
                httpd.server_close()


def test_ticket_rows_carry_work_and_runs():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        r = str(root)
        # One COMPLETE hour on 94313 (w1) and a second run still open (w2), plus
        # an auto-closed one that must not be priced.
        for ev, at in (("start", "2026-08-19T09:00:00"), ("end", "2026-08-19T10:00:00")):
            worklog.append_event(r, {"ev": ev, "work_id": "w1", "module": "INV",
                                     "ticket": "94313", "at": at, "share": 1.0,
                                     "kind": "claude", "device": "a"})
        worklog.append_event(r, {"ev": "start", "work_id": "w2", "module": "INV",
                                 "ticket": "94313", "at": "2026-08-19T11:00:00",
                                 "share": 1.0, "kind": "claude", "device": "a"})
        for ev, at, auto in (("start", "2026-08-19T07:00:00", False),
                             ("end", "2026-08-19T08:00:00", True)):
            worklog.append_event(r, {"ev": ev, "work_id": "w0", "module": "INV",
                                     "ticket": "94320", "at": at, "share": 1.0,
                                     "kind": "claude", "device": "a", "auto_closed": auto})
        with _Root(root):
            with server._work_lock:
                server._work_transcripts["w1"] = _transcript(root)
            server._work_write_runs("w1", None, ended="2026-08-19T10:00:00")
            server._work_write_runs("w1", {"files": ["a.py", "b.py"], "commits":
                                           [{"hash": "abc1234", "subject": "s"}],
                                           "tests": {"ran": 1, "passed": True, "last": "passed: 3/3"},
                                           "turns": 4, "summary": "gotovo",
                                           "last_at": "2026-08-19T10:00:00"},
                                    ended="2026-08-19T10:00:00")
            data = server._build_tickets_data()
        rows = {t["id"]: t for p in data.get("projects", []) for t in p.get("tickets", [])}
        w = rows["94313"]["work"]
        check("rows: sessions counts complete AND open intervals", w["sessions"] == 2, str(w))
        check("rows: hours count only the complete, non-auto interval", w["hours"] == 1.0, str(w))
        check("rows: the open run is flagged with its id",
              w["open"] is True and w["open_work_id"] == "w2", str(w))
        last = w["last"] or {}
        check("rows: last summarises the newest run",
              last.get("files") == 2 and last.get("commit") == "abc1234"
              and last.get("summary") == "gotovo"
              and last.get("tests", {}).get("passed") is True, str(last))
        check("rows: one run entry, updated in place - not two",
              len(rows["94313"]["runs"]) == 1, str(rows["94313"]["runs"]))
        w2 = rows["94320"]["work"]
        check("rows: an auto-closed interval counts as a session but no hours",
              w2["sessions"] == 1 and w2["hours"] == 0.0 and w2["open"] is False, str(w2))
        check("rows: a ticket nobody ran has the full zero shape, not a missing key",
              w2["last"] is None and set(w2) == set(server._work_zero()), str(w2))


def test_the_end_route_is_in_the_mutation_gate():
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    check("gate: /api/tickets/work/end is in the mutation tuple _MUT",
          '"/api/tickets/work/end"' in src)


# --------------------------------------------------------------------------- #
def test_the_launch_door_canonicalises_the_module_to_the_file_stem():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root):
            import store as _st
            if _st.resolve(str(root), "inv") is None:
                check("stem: (case-sensitive FS, skipped)", True)
                return
            pairs = server._launch_tickets([{"module": "inv", "ticket": "94313"}])
            check("stem: a lower-case module is stored as the file stem",
                  pairs == [{"module": "INV", "ticket": "94313"}], str(pairs))


def test_a_reaped_run_that_reports_again_is_reopened_and_logged_twice():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root), _NoSpawn():
            res, _ = server.launch_claude(
                "x", tickets=[{"module": "INV", "ticket": "94313"}])
            wid = res["work_id"]
            tp = _transcript(root)
            server.HUB.ingest(_event(wid, phase="turn", transcript=tp))
            # the reaper closes it as idle (simulate: end at start, auto_closed)
            worklog.end(str(root), wid, auto_closed=True)
            server._work_finish(wid)
            first = [e for e in _ailog(root) if e.get("action") == "run"]
            check("resume: the idle-close was logged once", len(first) == 1, str(len(first)))
            # activity arrives again -> the run re-opens
            with server._work_lock:
                server._work_touched.pop(wid, None)
            server.HUB.ingest(_event(wid, phase="tool", transcript=tp))
            open_now = [iv for iv in worklog.open_intervals(str(root)) if iv["work_id"] == wid]
            check("resume: an interval is open again under the same work_id",
                  len(open_now) == 1, str(open_now))
            # a proper end now logs a SECOND line (the resumed part), not silence
            server._work_finish(wid)
            second = [e for e in _ailog(root) if e.get("action") == "run"]
            check("resume: the resumed close is logged as its own entry",
                  len(second) == 2, str(len(second)))
            # and calling finish again with nothing new logs nothing more
            server._work_finish(wid)
            check("resume: no third entry without new activity",
                  len([e for e in _ailog(root) if e.get("action") == "run"]) == 2)


def test_a_transcript_path_outside_the_jail_is_ignored():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
        root = _store(tmp)
        with _Root(root), _NoSpawn():
            res, _ = server.launch_claude(
                "x", tickets=[{"module": "INV", "ticket": "94313"}])
            wid = res["work_id"]
            fp = Path(other) / "secret.jsonl"
            fp.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant",
                          "content": [{"type": "text", "text": "TOP SECRET"}]}}) + "\n",
                          encoding="utf-8")
            server.HUB.ingest(_event(wid, phase="turn", transcript=str(fp)))
            with server._work_lock:
                kept = server._work_transcripts.get(wid)
            check("jail: a path outside WORK_TRANSCRIPT_ROOTS is not recorded", kept is None, str(kept))
            check("jail: a non-.jsonl inside the root is refused",
                  server._work_transcript_ok(str(Path(root) / "x.txt")) == "")
            check("jail: a .jsonl inside an allowed root is accepted",
                  server._work_transcript_ok(_transcript(root)) != "")


def test_lan_readers_get_counts_but_no_run_detail():
    data = {"projects": [{"dir": "INV", "tickets": [
        {"id": "1", "runs": [{"files": ["e:/x.py"], "summary": "secret"}],
         "work": {"sessions": 1, "hours": 0.5, "open": False, "open_work_id": None,
                  "last": {"at": "t", "files": 1, "tests": {"ran": 0, "passed": None},
                           "commit": "abc1234", "summary": "secret"}}}]}]}
    out = server._tickets_without_run_detail(data)
    t = out["projects"][0]["tickets"][0]
    check("lan: runs emptied", t["runs"] == [])
    check("lan: work.last keeps counts, drops commit + summary",
          t["work"]["last"]["files"] == 1 and t["work"]["last"]["commit"] is None
          and t["work"]["last"]["summary"] == "")
    check("lan: the shared payload is untouched",
          data["projects"][0]["tickets"][0]["runs"] and
          data["projects"][0]["tickets"][0]["work"]["last"]["summary"] == "secret")


def _ailog(root):
    import ai_log
    return ai_log.read(str(root), limit=50)



def test_a_prompt_naming_a_ticket_measures_a_session_not_launched_from_the_hud():
    with tempfile.TemporaryDirectory() as tmp:
        root = _store(tmp)
        with _Root(root):
            server._tix_cache["data"] = None
            with server._work_lock:
                server._session_work.clear()
            # 1. the pasted generated prompt ("Tiket #94313 ...") starts a measurement
            server.HUB.ingest({"session_id": "sP1", "phase": "prompt", "agent": "main",
                               "label": "Tiket #94313 - INV -- Kontekst ...", "cwd": "e:/x"})
            ivs = [iv for iv in worklog.open_intervals(str(root)) if iv.get("session") == "sP1"]
            check("prompt: an open interval for #94313 under this session", len(ivs) == 1
                  and ivs[0]["ticket"] == "94313", str(ivs))
            wid = ivs[0]["work_id"]
            # 2. later events of the same session (no BRAIN_WORK_ID) are heartbeats for it
            before = len(_touches(str(root), wid))
            with server._work_lock:
                server._work_touched.pop(wid, None)
            server.HUB.ingest({"session_id": "sP1", "phase": "tool", "agent": "main", "tool": "Edit"})
            check("prompt: a plain tool event of that session touches the interval",
                  len(_touches(str(root), wid)) == before + 1, str(_touches(str(root), wid)))
            # 3. a hand-typed prompt for ANOTHER ticket switches: old ended, new opened
            server.HUB.ingest({"session_id": "sP1", "phase": "prompt", "agent": "main",
                               "label": "resavam tiket 94320, prvo pogledaj model"})
            ivs2 = [iv for iv in worklog.intervals(str(root)) if iv.get("session") == "sP1"]
            old = [iv for iv in ivs2 if iv["ticket"] == "94313"]
            new_ = [iv for iv in ivs2 if iv["ticket"] == "94320"]
            check("prompt: switching tickets ends the old interval", old and old[0]["end"], str(old))
            check("prompt: and opens one for the new ticket", new_ and not new_[0]["end"], str(new_))
            # 4. the same ticket again: no duplicate interval
            server.HUB.ingest({"session_id": "sP1", "phase": "prompt", "agent": "main",
                               "label": "tiket #94320 nastavak"})
            n_open = len([iv for iv in worklog.open_intervals(str(root)) if iv.get("session") == "sP1"])
            check("prompt: re-naming the same ticket keeps ONE open interval", n_open == 1, str(n_open))
            # 5. an id mentioned deep in prose (not in the head) is NOT a signal
            server.HUB.ingest({"session_id": "sP2", "phase": "prompt", "agent": "main",
                               "label": ("x" * 600) + " kao u tiketu #94313"})
            check("prompt: an id past the head of the prompt does not start work",
                  not [iv for iv in worklog.open_intervals(str(root)) if iv.get("session") == "sP2"])
            # 6. an unknown ticket id is ignored
            server.HUB.ingest({"session_id": "sP3", "phase": "prompt", "agent": "main", "label": "Tiket #11111"})
            check("prompt: an id the store does not know starts nothing",
                  not [iv for iv in worklog.open_intervals(str(root)) if iv.get("session") == "sP3"])
            # 7. after a 'restart' (map cleared) the session is found again from the open interval
            with server._work_lock:
                server._session_work.clear()
            check("prompt: the session->work map is rebuilt from the open interval's session",
                  server._work_for_session("sP1") == new_[0]["work_id"])

def _post(port, path, body, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def main():
    for fn in (test_launch_with_tickets_opens_intervals_and_hands_the_id_to_the_child,
               test_a_launch_without_tickets_measures_nothing_and_is_unchanged,
               test_unknown_module_or_ticket_is_dropped_never_measured,
               test_a_failed_spawn_closes_the_interval_it_opened,
               test_an_event_with_a_work_id_is_a_heartbeat_throttled_to_one_a_minute,
               test_sessionend_ends_the_interval_writes_runs_and_logs_once,
               test_the_reaper_closes_an_abandoned_run_and_still_records_it,
               test_a_late_stop_summary_does_not_un_end_a_finished_run,
               test_the_manual_end_route_finishes_a_run,
               test_ticket_rows_carry_work_and_runs,
               test_the_end_route_is_in_the_mutation_gate,
               test_the_launch_door_canonicalises_the_module_to_the_file_stem,
               test_a_reaped_run_that_reports_again_is_reopened_and_logged_twice,
               test_a_transcript_path_outside_the_jail_is_ignored,
               test_lan_readers_get_counts_but_no_run_detail,
               test_a_prompt_naming_a_ticket_measures_a_session_not_launched_from_the_hud):
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
