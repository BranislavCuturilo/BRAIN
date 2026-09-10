#!/usr/bin/env python3
"""Offline tests for the Focus timer + wellness reminders (focus.py + the
/api/focus routes in server.py).

Two layers:
  * PURE logic over focus.py with a frozen clock (focus._now) and a temp state
    file (focus.STATE_PATH) -- schedule/pause/ack/skip/config, due-ness, the score
    decay, coffee cooldown, the day streak, and graceful degradation on a bad file.
  * HTTP smoke over a loopback server -- each new route returns valid JSON, a
    malformed body is a 400, and the writes are loopback-gated while the read is open.

No network, no credentials. Output is ASCII (the Windows cp1252 console cannot
encode a non-ASCII char and would kill the run mid-way). Run:  python test_focus.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import focus       # noqa: E402
import server      # noqa: E402   (imports the SAME focus module object)

_results = []
_ORIG_NOW = focus._now
_ORIG_STATE_PATH = focus.STATE_PATH

MIN = 60


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


class Clock:
    """A frozen, advanceable clock installed as focus._now for the pure tests."""
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def _ts(y, mo, d, h=12):
    """A stable local-time midday epoch for day-boundary (streak) tests."""
    return datetime(y, mo, d, h, 0, 0).timestamp()


def _new_env(now_ts=None, freeze=True):
    """A fresh temp state file, and (by default) a frozen clock at now_ts."""
    d = tempfile.mkdtemp(prefix="focus_test_")
    focus.STATE_PATH = Path(d) / "focus_state.json"
    if freeze:
        clk = Clock(now_ts if now_ts is not None else time.time())
        focus._now = clk
        return d, clk
    focus._now = _ORIG_NOW
    return d, None


def _cleanup(d):
    focus.STATE_PATH = _ORIG_STATE_PATH
    focus._now = _ORIG_NOW
    shutil.rmtree(d, ignore_errors=True)


def _approx(a, b, tol=1.0):
    return abs(float(a) - float(b)) <= tol


# --------------------------------------------------------------------------- #
#  Pure logic
# --------------------------------------------------------------------------- #
def test_default_state_shape():
    d, clk = _new_env(1_700_000_000.0)
    try:
        v = focus.get_view()
        check("default: enabled fresh state", v["config"]["enabled"] is True)
        check("default: each interval reminder has a future next_at",
              all(v["reminders"][t]["next_at"] > clk() for t in ("water", "stretch", "exercise")))
        check("default: nothing is due at the start",
              not any(v["reminders"][t]["due"] for t in ("water", "stretch", "exercise", "coffee")))
        check("default: score starts at 100", v["focus"]["score"] == 100, str(v["focus"]))
        check("default: metrics zeroed", v["metrics"]["water"]["count"] == 0
              and v["metrics"]["streak_days"] == 0)
    finally:
        _cleanup(d)


def test_score_falls_with_time_since_break():
    # Pure over compute_score; break_after_min default 90.
    t0 = 1_700_000_000.0
    st = focus.default_state(now=t0)
    s0 = focus.compute_score(st, now=t0)
    s45 = focus.compute_score(st, now=t0 + 45 * MIN)
    s90 = focus.compute_score(st, now=t0 + 90 * MIN)
    s135 = focus.compute_score(st, now=t0 + 135 * MIN)
    check("score: 100 right after a break", s0 == 100, str(s0))
    check("score: ~50 at half of break_after_min", s45 == 50, str(s45))
    check("score: 0 at break_after_min", s90 == 0, str(s90))
    check("score: clamped at 0 past break_after_min", s135 == 0, str(s135))
    check("score: strictly falls with time since break", s0 > s45 > s90 >= s135)


def test_activity_bonus_is_a_light_neutral_nudge():
    t0 = 1_700_000_000.0
    st = focus.default_state(now=t0)
    at = t0 + 60 * MIN
    base = focus.compute_score(st, now=at, activity=None)
    fresh = focus.compute_score(st, now=at, activity={"last_event_age_sec": 5})
    stale = focus.compute_score(st, now=at, activity={"last_event_age_sec": 99999})
    check("activity: fresh event adds the small bonus", fresh == base + focus.ACTIVITY_BONUS,
          f"{fresh} vs {base}")
    check("activity: stale event is neutral", stale == base, f"{stale} vs {base}")
    check("activity: no signal is neutral", base == focus.compute_score(st, now=at))


def test_pause_freezes_and_excludes_elapsed():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()                       # init at t0 -> water next_at = t0+2700
        base_next = t0 + focus.DEFAULT_INTERVALS_MIN["water"] * MIN
        clk.advance(10 * MIN)                   # t0+600, 10 min into the session
        focus.set_pause(True)
        clk.advance(40 * MIN)                   # paused for 40 min (t0+3000)
        v, code = focus.set_pause(False)
        check("pause: resume -> 200", code == 200, str(code))
        check("pause: paused span accumulated (~2400s)",
              _approx(v["session"]["paused_total_sec"], 40 * MIN), str(v["session"]["paused_total_sec"]))
        check("pause: water next_at shifted forward by the paused span",
              _approx(v["reminders"]["water"]["next_at"], base_next + 40 * MIN),
              str(v["reminders"]["water"]["next_at"]))
        # Without the pause, t0+3000 (> base_next=2700) would already be due; the
        # shift excludes the paused span, so it is NOT due at resume.
        check("pause: not due at resume (no catch-up burst)",
              v["reminders"]["water"]["due"] is False)
        clk.t = v["reminders"]["water"]["next_at"]   # reach the shifted time
        v2 = focus.get_view()
        check("pause: due again once the shifted next_at is reached",
              v2["reminders"]["water"]["due"] is True)
    finally:
        _cleanup(d)


def test_pause_counts_as_break_and_restores_score():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(80 * MIN)                   # deep into the session, score low
        before = focus.get_view()
        check("break: score low before the break", before["focus"]["score"] < 40,
              str(before["focus"]["score"]))
        check("break: coffee due before the break", before["reminders"]["coffee"]["due"] is True)
        v, _ = focus.set_pause(True)
        check("break: pause restores score to 100 (frozen at the break)",
              v["focus"]["score"] == 100, str(v["focus"]["score"]))
        check("break: coffee not due while paused", v["reminders"]["coffee"]["due"] is False)
        clk.advance(15 * MIN)                    # time passes while paused
        v2 = focus.get_view()
        check("break: score stays 100 while paused (clocks frozen)",
              v2["focus"]["score"] == 100, str(v2["focus"]["score"]))
    finally:
        _cleanup(d)


def test_ack_logs_metric_and_reschedules():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(50 * MIN)                    # past the 45-min water timer
        before = focus.get_view()
        check("ack: water due before ack", before["reminders"]["water"]["due"] is True)
        v, code = focus.ack("water")
        check("ack: water -> 200", code == 200, str(code))
        check("ack: water count/today incremented",
              v["metrics"]["water"]["count"] == 1 and v["metrics"]["water"]["today"] == 1,
              str(v["metrics"]["water"]))
        check("ack: water rescheduled from now",
              _approx(v["reminders"]["water"]["next_at"], clk() + 45 * MIN),
              str(v["reminders"]["water"]["next_at"]))
        check("ack: water no longer due", v["reminders"]["water"]["due"] is False)
        check("ack: water last_at stamped", _approx(v["reminders"]["water"]["last_at"], clk()))
    finally:
        _cleanup(d)


def test_ack_exercise_uses_value_or_default():
    # With no pick present, the default reps fall back to the pool's first exercise
    # (the seeded set's first entry). A typed value overrides.
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        default_reps = focus.DEFAULT_EXERCISES[0]["reps"]
        v, _ = focus.ack("exercise")            # no value -> pool default
        check("exercise: default reps from the pool",
              v["metrics"]["exercise"]["reps_total"] == default_reps,
              str(v["metrics"]["exercise"]))
        v, _ = focus.ack("exercise", value=12)  # typed count
        check("exercise: typed value added",
              v["metrics"]["exercise"]["reps_total"] == default_reps + 12,
              str(v["metrics"]["exercise"]))
        check("exercise: count is number of sets, not reps",
              v["metrics"]["exercise"]["count"] == 2, str(v["metrics"]["exercise"]))
        check("exercise: string number is accepted",
              focus.ack("exercise", value="3")[0]["metrics"]["exercise"]["reps_total"]
              == default_reps + 12 + 3)
    finally:
        _cleanup(d)


def test_ack_rejects_bad_input():
    t0 = 1_700_000_000.0
    d, _ = _new_env(t0)
    try:
        check("bad: unknown type -> 400", focus.ack("sleep")[1] == 400)
        check("bad: exercise negative -> 400", focus.ack("exercise", value=-3)[1] == 400)
        check("bad: exercise zero -> 400", focus.ack("exercise", value=0)[1] == 400)
        check("bad: exercise non-numeric -> 400", focus.ack("exercise", value="abc")[1] == 400)
    finally:
        _cleanup(d)


def test_skip_reschedules_without_logging():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(50 * MIN)
        v, code = focus.skip("water")
        check("skip: -> 200", code == 200, str(code))
        check("skip: metric NOT logged", v["metrics"]["water"]["count"] == 0,
              str(v["metrics"]["water"]))
        check("skip: rescheduled from now",
              _approx(v["reminders"]["water"]["next_at"], clk() + 45 * MIN))
        check("skip: no longer due", v["reminders"]["water"]["due"] is False)
        check("skip: unknown type -> 400", focus.skip("nope")[1] == 400)
    finally:
        _cleanup(d)


def test_coffee_due_then_cooldown():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(80 * MIN)                    # score below threshold
        check("coffee: due when score is low", focus.get_view()["reminders"]["coffee"]["due"] is True)
        v, _ = focus.ack("coffee")
        check("coffee: not due right after firing (cooldown)",
              v["reminders"]["coffee"]["due"] is False)
        check("coffee: last_at stamped, no count column",
              _approx(v["reminders"]["coffee"]["last_at"], clk()))
        clk.advance(10 * MIN)                    # still within the 30-min cooldown
        check("coffee: still quiet inside the cooldown",
              focus.get_view()["reminders"]["coffee"]["due"] is False)
        clk.advance(25 * MIN)                    # cooldown elapsed, score still low
        check("coffee: due again after the cooldown",
              focus.get_view()["reminders"]["coffee"]["due"] is True)
    finally:
        _cleanup(d)


def test_due_respects_enabled_and_pause():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(100 * MIN)                   # every interval timer has elapsed
        v = focus.get_view()
        check("gate: all interval reminders due when enabled and running",
              all(v["reminders"][t]["due"] for t in ("water", "stretch", "exercise")))
        v, _ = focus.update_config({"enabled": False})
        check("gate: disabled -> nothing is due",
              not any(v["reminders"][t]["due"] for t in ("water", "stretch", "exercise", "coffee")))
        focus.update_config({"enabled": True})
        v, _ = focus.set_pause(True)
        check("gate: paused -> nothing is due",
              not any(v["reminders"][t]["due"] for t in ("water", "stretch", "exercise", "coffee")))
    finally:
        _cleanup(d)


def test_config_merge_reschedule_and_validation():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        v, code = focus.update_config({"intervals_min": {"water": 10}})
        check("config: -> 200", code == 200, str(code))
        check("config: changed interval reschedules next_at from now",
              _approx(v["reminders"]["water"]["next_at"], clk() + 10 * MIN),
              str(v["reminders"]["water"]["next_at"]))
        v, _ = focus.update_config({"exercises": [{"name": "A", "weight": 1, "reps": 8}]})
        check("config: exercises replaced", len(v["config"]["exercises"]) == 1
              and v["config"]["exercises"][0]["reps"] == 8, str(v["config"]["exercises"]))
        v, _ = focus.update_config({"focus": {"break_after_min": 30}})
        check("config: break_after_min updated", v["config"]["focus"]["break_after_min"] == 30)
        check("config: non-bool enabled -> 400", focus.update_config({"enabled": "yes"})[1] == 400)
        check("config: zero interval -> 400",
              focus.update_config({"intervals_min": {"water": 0}})[1] == 400)
        check("config: out-of-range threshold -> 400",
              focus.update_config({"focus": {"low_score_threshold": 150}})[1] == 400)
        check("config: a rejected patch is NOT partially applied",
              len(focus.get_view()["config"]["exercises"]) == 1)
    finally:
        _cleanup(d)


def test_streak_increments_and_breaks():
    d, clk = _new_env(_ts(2026, 3, 1))
    try:
        v, _ = focus.ack("water")
        check("streak: first activity -> streak 1", v["metrics"]["streak_days"] == 1,
              str(v["metrics"]["streak_days"]))
        check("streak: last_active_day recorded", v["metrics"]["last_active_day"] == "2026-03-01")
        clk.t = _ts(2026, 3, 2)
        v, _ = focus.ack("water")
        check("streak: consecutive day -> streak 2", v["metrics"]["streak_days"] == 2,
              str(v["metrics"]["streak_days"]))
        check("streak: today counter reset then incremented on the new day",
              v["metrics"]["water"]["today"] == 1, str(v["metrics"]["water"]))
        v, _ = focus.ack("water")               # same day again
        check("streak: same-day ack does not advance the streak",
              v["metrics"]["streak_days"] == 2, str(v["metrics"]["streak_days"]))
        clk.t = _ts(2026, 3, 4)                  # skipped 2026-03-03 entirely
        v, _ = focus.ack("water")
        check("streak: a missed day breaks the streak back to 1",
              v["metrics"]["streak_days"] == 1, str(v["metrics"]["streak_days"]))
    finally:
        _cleanup(d)


def test_corrupt_state_degrades_disabled_not_crash():
    d, clk = _new_env(1_700_000_000.0)
    try:
        focus.STATE_PATH.write_text("{ this is not valid json", encoding="utf-8")
        v = focus.get_view()                    # must not raise
        check("corrupt: degrades to disabled-but-present", v["config"]["enabled"] is False)
        check("corrupt: nothing is due", not any(v["reminders"][t]["due"]
              for t in ("water", "stretch", "exercise", "coffee")))
        check("corrupt: the unreadable file is NOT overwritten",
              focus.STATE_PATH.read_text(encoding="utf-8").startswith("{ this is not"))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  HTTP surface
# --------------------------------------------------------------------------- #
def _http_env():
    d = tempfile.mkdtemp(prefix="focus_http_")
    focus.STATE_PATH = Path(d) / "focus_state.json"
    focus._now = _ORIG_NOW                       # real clock for the live server
    return d


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


def test_focus_routes_smoke():
    d = _http_env()
    httpd, port = _start()
    try:
        code, body = _get(port, "/api/focus")
        out = json.loads(body.decode() or "{}")
        check("http: GET /api/focus -> 200", code == 200, str(code))
        check("http: reminders each carry a due flag",
              all(isinstance(out["reminders"][t].get("due"), bool)
                  for t in ("water", "stretch", "exercise", "coffee")))
        check("http: score is an int in 0..100",
              isinstance(out["focus"]["score"], int) and 0 <= out["focus"]["score"] <= 100,
              str(out["focus"]))
        code, body = _post(port, "/api/focus/ack", {"type": "water"})
        out = json.loads(body.decode() or "{}")
        check("http: POST ack water -> 200", code == 200, str(code))
        check("http: ack incremented the counter", out["metrics"]["water"]["count"] == 1,
              str(out.get("metrics")))
        code, _b = _post(port, "/api/focus/pause", {})            # missing paused
        check("http: pause without paused -> 400", code == 400, str(code))
        code, _b = _post(port, "/api/focus/ack", {"type": "nope"})
        check("http: ack unknown type -> 400", code == 400, str(code))
        code, _b = _post_raw(port, "/api/focus/config", b"[1,2,3]")
        check("http: non-object body -> 400", code == 400, str(code))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


def test_focus_wiring_and_gating():
    import inspect
    get_src = inspect.getsource(server.Handler.do_GET)
    post_src = inspect.getsource(server.Handler.do_POST)
    check("wiring: /api/focus routed in do_GET", '"/api/focus"' in get_src)
    for r in ('"/api/focus/pause"', '"/api/focus/ack"',
              '"/api/focus/skip"', '"/api/focus/config"', '"/api/focus/swap"'):
        check(f"wiring: {r} handled in do_POST", r in post_src)
    orig = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    d = _http_env()
    httpd, port = _start()
    try:
        code, _b = _post(port, "/api/focus/ack", {"type": "water"})
        check("gate: focus write -> 403 for a non-local peer", code == 403, str(code))
        code, _b = _get(port, "/api/focus")
        check("gate: focus read stays open (200) on the LAN", code == 200, str(code))
    finally:
        server.Handler._client_is_local = orig
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Exercise set: weighted pick, no immediate repeat, config validation, migration
# --------------------------------------------------------------------------- #
def test_exercise_weighting_holds():
    """Over many independent picks the frequencies track the weights. Seeded so it
    is deterministic; the tolerance is wide enough it can never flake."""
    import random
    random.seed(20260810)
    st = focus.default_state(now=1_700_000_000.0)
    st["config"]["exercises"] = [
        {"id": "a", "name": "A", "emoji": "", "weight": 3.0, "reps": 5},
        {"id": "b", "name": "B", "emoji": "", "weight": 1.0, "reps": 5},
    ]
    st["session"]["last_exercise"] = None            # each call samples the full set
    n, counts = 6000, {"a": 0, "b": 0}
    for _ in range(n):
        counts[focus._pick_exercise(st)["id"]] += 1
    frac_a = counts["a"] / n
    check("weighting: the 3:1 weight shows as ~0.75 share", 0.70 <= frac_a <= 0.80, str(counts))
    check("weighting: both exercises are reachable", counts["a"] > 0 and counts["b"] > 0, str(counts))


def test_exercise_no_immediate_repeat():
    """A pick never equals session.last_exercise while more than one exercise
    exists; with a single exercise it must still return it."""
    import random
    random.seed(99)
    st = focus.default_state(now=1_700_000_000.0)     # 4 seeded exercises
    prev, ok = None, True
    for _ in range(200):
        pick = focus._pick_exercise(st)
        if prev is not None and pick["id"] == prev:
            ok = False
            break
        st["session"]["last_exercise"] = pick["id"]   # simulate ack/skip stamping last
        prev = pick["id"]
    check("no-repeat: never equals the immediately previous pick (>1 exercise)", ok, str(prev))
    st["config"]["exercises"] = [{"id": "solo", "name": "S", "emoji": "", "weight": 1, "reps": 3}]
    st["session"]["last_exercise"] = "solo"
    check("no-repeat: a single-exercise set still returns that exercise",
          focus._pick_exercise(st)["id"] == "solo")


def test_exercise_no_repeat_through_view_flow():
    """The real path: the reminder goes due, get_view picks + persists (no re-roll),
    ack/skip clears it, and across a dozen cycles no exercise repeats back-to-back."""
    import random
    random.seed(7)
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()                              # init
        seq = []
        for i in range(12):
            clk.advance(91 * MIN)                     # push past the 90-min exercise timer
            v = focus.get_view()                      # transitions to due -> picks + persists
            pick = v["reminders"]["exercise"]["exercise"]
            check("flow: a pick is present once due",
                  isinstance(pick, dict) and bool(pick.get("id")), str(pick))
            # the pick is stable across a re-poll (not re-rolled every 15s)
            again = focus.get_view()["reminders"]["exercise"]["exercise"]
            check("flow: the pick is stable across polls", again and again["id"] == pick["id"],
                  str(again))
            seq.append(pick["id"])
            (focus.ack if i % 2 == 0 else focus.skip)("exercise")   # both stamp last + clear pick
        repeats = any(seq[i] == seq[i - 1] for i in range(1, len(seq)))
        check("flow: no exercise repeats immediately across ack/skip", not repeats, str(seq))
        check("flow: more than one distinct exercise appeared", len(set(seq)) > 1, str(seq))
    finally:
        _cleanup(d)


def test_exercises_config_accept_and_reject():
    t0 = 1_700_000_000.0
    d, _ = _new_env(t0)
    try:
        v, code = focus.update_config({"exercises": [
            {"name": "Klekovi", "weight": 2, "reps": 10},
            {"name": "B", "weight": 0, "reps": 1, "emoji": "x", "id": "bee"},
        ]})
        check("exercises: valid set -> 200", code == 200, str(code))
        exs = v["config"]["exercises"]
        check("exercises: stored count", len(exs) == 2, str(exs))
        check("exercises: id slugged from name when absent", exs[0]["id"] == "klekovi", str(exs[0]))
        check("exercises: explicit id kept", exs[1]["id"] == "bee", str(exs[1]))
        check("exercises: weight 0 allowed", exs[1]["weight"] == 0, str(exs[1]))
        check("exercises: non-list -> 400", focus.update_config({"exercises": {}})[1] == 400)
        check("exercises: empty list -> 400", focus.update_config({"exercises": []})[1] == 400)
        check("exercises: blank name -> 400",
              focus.update_config({"exercises": [{"name": "  ", "weight": 1, "reps": 5}]})[1] == 400)
        check("exercises: negative weight -> 400",
              focus.update_config({"exercises": [{"name": "A", "weight": -1, "reps": 5}]})[1] == 400)
        check("exercises: reps 0 -> 400",
              focus.update_config({"exercises": [{"name": "A", "weight": 1, "reps": 0}]})[1] == 400)
        check("exercises: reps over max -> 400",
              focus.update_config({"exercises": [{"name": "A", "weight": 1, "reps": 1001}]})[1] == 400)
        check("exercises: non-int reps -> 400",
              focus.update_config({"exercises": [{"name": "A", "weight": 1, "reps": 5.5}]})[1] == 400)
        # atomic: a valid sibling field next to a bad exercise must persist NOTHING
        focus.update_config({"enabled": True})
        code = focus.update_config({"enabled": False,
                                    "exercises": [{"name": "A", "weight": 1, "reps": 0}]})[1]
        check("exercises: mixed patch with a bad entry -> 400", code == 400, str(code))
        check("exercises: the valid sibling field did NOT persist",
              focus.get_view()["config"]["enabled"] is True)
        check("exercises: the earlier good set survives the rejected patch",
              len(focus.get_view()["config"]["exercises"]) == 2)
    finally:
        _cleanup(d)


def test_ack_logs_against_picked_exercise():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(91 * MIN)                          # exercise timer elapsed
        v = focus.get_view()                           # picks + persists
        picked = v["reminders"]["exercise"]["exercise"]
        check("picked: present once due", isinstance(picked, dict) and bool(picked.get("id")), str(picked))
        pid = picked["id"]
        v, code = focus.ack("exercise", value=7)
        check("picked: ack -> 200", code == 200, str(code))
        ex = v["metrics"]["exercise"]
        check("picked: aggregate reps incremented by the typed value", ex["reps_total"] == 7, str(ex))
        check("picked: per-exercise by_id updated",
              ex["by_id"].get(pid, {}).get("reps_total") == 7, str(ex.get("by_id")))
        check("picked: last_exercise stamped", v["session"]["last_exercise"] == pid, str(v["session"]))
        check("picked: pick cleared after ack", v["reminders"]["exercise"]["exercise"] is None)
        check("picked: exercise rescheduled from now",
              _approx(v["reminders"]["exercise"]["next_at"], clk() + 90 * MIN),
              str(v["reminders"]["exercise"]["next_at"]))
        clk.advance(91 * MIN)                           # next due -> a fresh pick
        picked2 = focus.get_view()["reminders"]["exercise"]["exercise"]
        v, _ = focus.ack("exercise")                    # no value -> that pick's reps
        check("picked: default reps come from the picked exercise",
              v["metrics"]["exercise"]["reps_total"] == 7 + picked2["reps"],
              str(v["metrics"]["exercise"]))
    finally:
        _cleanup(d)


def test_legacy_pushups_state_migrates():
    """An OLD state file (single hardcoded pushups) loads without crashing and is
    upgraded to the exercises shape, preserving the user's counts."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        legacy = {
            "config": {"enabled": True,
                       "intervals_min": {"water": 45, "stretch": 60, "pushups": 90},
                       "pushups_reps": 13,
                       "focus": {"break_after_min": 90, "low_score_threshold": 40}},
            "session": {"started_at": t0, "paused": False, "paused_at": None,
                        "paused_total_sec": 0.0, "last_break_at": t0},
            "reminders": {"water": {"last_at": None, "next_at": t0 + 2700},
                          "stretch": {"last_at": None, "next_at": t0 + 3600},
                          "pushups": {"last_at": t0, "next_at": t0 + 5400},
                          "coffee": {"last_at": None}},
            "metrics": {"water": {"count": 2, "today": 2},
                        "stretch": {"count": 1, "today": 1},
                        "pushups": {"count": 4, "reps_total": 40, "today_reps": 40},
                        "streak_days": 3, "last_active_day": "2023-11-14"},
            "focus": {"score": 100, "computed_at": t0},
        }
        focus.STATE_PATH.write_text(json.dumps(legacy), encoding="utf-8")
        v = focus.get_view()                            # must migrate, not crash
        cfg = v["config"]
        check("migrate: exercises created",
              isinstance(cfg.get("exercises"), list) and bool(cfg["exercises"]), str(cfg.get("exercises")))
        check("migrate: old pushups_reps folded into the first exercise",
              cfg["exercises"][0]["reps"] == 13, str(cfg["exercises"][0]))
        check("migrate: pushups_reps key dropped", "pushups_reps" not in cfg)
        check("migrate: interval key renamed to exercise",
              cfg["intervals_min"].get("exercise") == 90 and "pushups" not in cfg["intervals_min"],
              str(cfg["intervals_min"]))
        check("migrate: reminder slot renamed, next_at preserved",
              "pushups" not in v["reminders"] and _approx(v["reminders"]["exercise"]["next_at"], t0 + 5400),
              str(v["reminders"].get("exercise")))
        check("migrate: metrics bucket renamed, reps preserved",
              "pushups" not in v["metrics"] and v["metrics"]["exercise"]["reps_total"] == 40,
              str(v["metrics"].get("exercise")))
        check("migrate: new subfields backfilled",
              v["reminders"]["exercise"].get("exercise", "MISSING") is None
              and isinstance(v["metrics"]["exercise"].get("by_id"), dict)
              and "last_exercise" in v["session"])
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Work-day model: downtime exclusion, snooze, work-blocks, end/new day, custom
#  reminders, coffee config, and profile aggregation.
# --------------------------------------------------------------------------- #
def test_downtime_excluded_from_elapsed():
    """A restart continues the work-day clock where it stopped: the gap while the
    server was DOWN (heartbeat stale) is added to paused_total_sec, so elapsed hours
    are continuous across the outage."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()                         # init: heartbeat = t0
        for _ in range(40):                      # 20 min uptime, heartbeat kept fresh (the 30s daemon)
            clk.advance(30)
            focus.heartbeat()
        before = focus.get_view()["day"]["hours_worked"]
        check("downtime: ~20 min worked before the outage",
              _approx(before * 3600, 20 * MIN, tol=2.0), str(before))
        clk.advance(60 * MIN)                     # server DOWN 60 min: NO heartbeat stamped
        focus.check_downtime()                    # restart absorbs the gap
        v = focus.get_view()
        check("downtime: gap added to paused_total_sec (~3600s)",
              _approx(v["session"]["paused_total_sec"], 60 * MIN, tol=2.0),
              str(v["session"]["paused_total_sec"]))
        after = v["day"]["hours_worked"]
        check("downtime: elapsed continuous across the restart (downtime excluded)",
              _approx(after, before, tol=0.001) and _approx(after * 3600, 20 * MIN, tol=2.0),
              f"{after} vs {before}")
        clk.advance(60)                           # a normal poll does not touch heartbeat/downtime
        again = focus.get_view()
        check("downtime: not re-absorbed on the next poll",
              _approx(again["session"]["paused_total_sec"], 60 * MIN, tol=2.0),
              str(again["session"]["paused_total_sec"]))
    finally:
        _cleanup(d)


def test_snooze_reschedules_and_counts():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(50 * MIN)                     # water due (45-min timer)
        check("snooze: water due before snooze", focus.get_view()["reminders"]["water"]["due"] is True)
        v, code = focus.snooze("water")
        check("snooze: -> 200", code == 200, str(code))
        check("snooze: water no longer due", v["reminders"]["water"]["due"] is False)
        check("snooze: water pushed out by snooze_min",
              _approx(v["reminders"]["water"]["next_at"], clk() + focus.DEFAULT_SNOOZE_MIN * MIN),
              str(v["reminders"]["water"]["next_at"]))
        check("snooze: count incremented", v["metrics"]["snoozes"]["count"] == 1,
              str(v["metrics"]["snoozes"]))
        clk.advance(80 * MIN)                     # score below threshold -> coffee due
        check("snooze: coffee due when score low", focus.get_view()["reminders"]["coffee"]["due"] is True)
        v, _ = focus.snooze("coffee")
        check("snooze: coffee suppressed after snooze", v["reminders"]["coffee"]["due"] is False)
        check("snooze: count incremented again", v["metrics"]["snoozes"]["count"] == 2)
        clk.advance(focus.DEFAULT_SNOOZE_MIN * MIN + 60)   # snooze window elapsed, score still low
        check("snooze: coffee due again after the snooze window",
              focus.get_view()["reminders"]["coffee"]["due"] is True)
        check("snooze: unknown type -> 400", focus.snooze("sleep")[1] == 400)
    finally:
        _cleanup(d)


def test_work_blocks_increment_on_resume():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        v = focus.get_view()
        check("blocks: one block at day start", v["day"]["work_blocks"] == 1, str(v["day"]))
        focus.set_pause(True)
        clk.advance(5 * MIN)
        v, _ = focus.set_pause(False)
        check("blocks: a resume opens a second block", v["day"]["work_blocks"] == 2, str(v["day"]))
        check("blocks: the pause was counted", v["day"]["pauses"] == 1, str(v["day"]))
        focus.set_pause(True)
        clk.advance(3 * MIN)
        v, _ = focus.set_pause(False)
        check("blocks: a second resume opens a third block", v["day"]["work_blocks"] == 3, str(v["day"]))
        check("blocks: two pauses counted", v["day"]["pauses"] == 2, str(v["day"]))
    finally:
        _cleanup(d)


def test_end_day_and_new_day():
    """end_day captures the day into history and blocks reminders; new_day resets the
    live counters to zero while history and the calendar streak survive."""
    t0 = _ts(2026, 4, 6, h=9)                     # a Monday
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(30 * MIN); focus.ack("water")
        clk.advance(5 * MIN);  focus.ack("water")
        clk.advance(91 * MIN); focus.get_view()   # exercise due -> pick
        focus.ack("exercise", value=10)
        focus.set_pause(True); clk.advance(4 * MIN); focus.set_pause(False)   # a pause + resume
        pre = focus.get_view()["day"]
        check("day: live water counter is the day total", pre["water"] == 2, str(pre))
        check("day: work_blocks 2 after a resume", pre["work_blocks"] == 2, str(pre))

        report, code = focus.end_day(claude_sessions=3, commits=7)
        check("end-day: -> 200", code == 200, str(code))
        check("end-day: date is the work-day", report["date"] == "2026-04-06", str(report["date"]))
        check("end-day: water captured", report["water"] == 2, str(report))
        check("end-day: exercise_total captured", report["exercise_total"] == 10, str(report))
        check("end-day: exercise_by_id captured", report["exercise_by_id"].get("pushups", 0) == 10
              or sum(report["exercise_by_id"].values()) == 10, str(report["exercise_by_id"]))
        check("end-day: pauses captured", report["pauses"] == 1, str(report))
        check("end-day: work_blocks captured", report["work_blocks"] == 2, str(report))
        check("end-day: external claude_sessions recorded", report["claude_sessions"] == 3, str(report))
        check("end-day: external commits recorded", report["commits"] == 7, str(report))
        check("end-day: hours_worked > 0", report["hours_worked"] > 0, str(report["hours_worked"]))

        after = focus.get_view()
        check("end-day: day_ended flagged", after["session"]["day_ended"] is True)
        check("end-day: nothing is due while ended",
              not any(after["reminders"][t]["due"] for t in ("water", "stretch", "exercise", "coffee")))

        report2, _ = focus.end_day(claude_sessions=99, commits=99)
        prof, _ = focus.profile()
        check("end-day: exactly one history record (idempotent)", prof["days_recorded"] == 1,
              str(prof["days_recorded"]))
        check("end-day: a repeat returns the existing record, no double count",
              report2["claude_sessions"] == 3, str(report2))

        clk.advance(60 * MIN)
        v, code = focus.new_day()
        check("new-day: -> 200", code == 200, str(code))
        check("new-day: day_ended cleared", v["session"]["day_ended"] is False)
        check("new-day: water reset to 0", v["day"]["water"] == 0, str(v["day"]))
        check("new-day: lifetime metrics.water.count reset too", v["metrics"]["water"]["count"] == 0,
              str(v["metrics"]["water"]))
        check("new-day: exercise reset to 0", v["day"]["exercise_total"] == 0, str(v["day"]))
        check("new-day: work_blocks reset to 1", v["day"]["work_blocks"] == 1, str(v["day"]))
        check("new-day: pauses reset to 0", v["day"]["pauses"] == 0, str(v["day"]))
        check("new-day: hours_worked reset to ~0", v["day"]["hours_worked"] < 0.01,
              str(v["day"]["hours_worked"]))
        check("new-day: work_day is today", v["session"]["work_day"] == "2026-04-06",
              str(v["session"]["work_day"]))
        check("new-day: reminders live again (water has a future timer)",
              v["reminders"]["water"]["due"] is False and v["reminders"]["water"]["next_at"] > clk())
        prof2, _ = focus.profile()
        check("new-day: prior day's history preserved", prof2["days_recorded"] == 1,
              str(prof2["days_recorded"]))
    finally:
        _cleanup(d)


def test_custom_reminders_schedule_ack_snooze_skip():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        v, code = focus.update_config({"custom_reminders": [
            {"name": "Proveri mail", "interval_min": 30},
            {"name": "Pozovi tim", "interval_min": 120, "id": "tim"},
        ]})
        check("custom: config -> 200", code == 200, str(code))
        cr = v["config"]["custom_reminders"]
        check("custom: two stored", len(cr) == 2, str(cr))
        check("custom: id slugged from name", cr[0]["id"] == "proveri-mail", str(cr[0]))
        check("custom: explicit id kept", cr[1]["id"] == "tim", str(cr[1]))
        check("custom: slot scheduled at now + interval",
              _approx(v["reminders"]["custom"]["proveri-mail"]["next_at"], clk() + 30 * MIN),
              str(v["reminders"]["custom"].get("proveri-mail")))
        check("custom: not due yet", v["reminders"]["custom"]["proveri-mail"]["due"] is False)
        clk.advance(31 * MIN)
        check("custom: due after its interval",
              focus.get_view()["reminders"]["custom"]["proveri-mail"]["due"] is True)
        v, code = focus.ack("custom", cid="proveri-mail")
        check("custom: ack -> 200", code == 200, str(code))
        check("custom: ack counted", v["metrics"]["custom"]["proveri-mail"]["count"] == 1,
              str(v["metrics"].get("custom")))
        check("custom: ack rescheduled on its interval",
              _approx(v["reminders"]["custom"]["proveri-mail"]["next_at"], clk() + 30 * MIN))
        check("custom: ack did NOT touch the wellness streak", v["metrics"]["streak_days"] == 0,
              str(v["metrics"]["streak_days"]))
        check("custom: ack is not a water/stretch/exercise signal",
              v["metrics"]["water"]["count"] == 0 and v["metrics"]["exercise"]["reps_total"] == 0)
        clk.advance(31 * MIN)
        check("custom: due again", focus.get_view()["reminders"]["custom"]["proveri-mail"]["due"] is True)
        v, _ = focus.snooze("custom", cid="proveri-mail")
        check("custom: snooze suppresses due", v["reminders"]["custom"]["proveri-mail"]["due"] is False)
        check("custom: snooze counted", v["metrics"]["snoozes"]["count"] == 1)
        clk.advance(focus.DEFAULT_SNOOZE_MIN * MIN + 60)
        v, _ = focus.skip("custom", cid="proveri-mail")
        check("custom: skip reschedules on its interval",
              _approx(v["reminders"]["custom"]["proveri-mail"]["next_at"], clk() + 30 * MIN))
        check("custom: unknown id ack -> 400", focus.ack("custom", cid="nope")[1] == 400)
        check("custom: unknown id snooze -> 400", focus.snooze("custom", cid="nope")[1] == 400)
        v, _ = focus.update_config({"custom_reminders": [
            {"name": "Pozovi tim", "interval_min": 120, "id": "tim"}]})
        check("custom: removed id dropped from slots", "proveri-mail" not in v["reminders"]["custom"],
              str(v["reminders"]["custom"]))
        check("custom: kept id retains its schedule", "tim" in v["reminders"]["custom"])
    finally:
        _cleanup(d)


def test_config_snooze_custom_coffee_validation():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        v, code = focus.update_config({"snooze_min": 15})
        check("cfg: snooze_min accepted", code == 200 and v["config"]["snooze_min"] == 15,
              str(v["config"].get("snooze_min")))
        check("cfg: snooze_min 0 -> 400", focus.update_config({"snooze_min": 0})[1] == 400)
        check("cfg: snooze_min non-number -> 400", focus.update_config({"snooze_min": "x"})[1] == 400)

        v, code = focus.update_config({"coffee": {"enabled": False, "low_score_threshold": 25,
                                                  "cooldown_min": 45}})
        cc = v["config"]["coffee"]
        check("cfg: coffee fields stored",
              code == 200 and cc["enabled"] is False and cc["low_score_threshold"] == 25
              and cc["cooldown_min"] == 45, str(cc))
        check("cfg: coffee.enabled non-bool -> 400", focus.update_config({"coffee": {"enabled": "yes"}})[1] == 400)
        check("cfg: coffee threshold out of range -> 400",
              focus.update_config({"coffee": {"low_score_threshold": 200}})[1] == 400)
        check("cfg: coffee cooldown 0 -> 400", focus.update_config({"coffee": {"cooldown_min": 0}})[1] == 400)
        clk.advance(200 * MIN)                    # score 0
        check("cfg: a disabled coffee never fires even at score 0",
              focus.get_view()["reminders"]["coffee"]["due"] is False)
        focus.update_config({"coffee": {"enabled": True}})
        check("cfg: re-enabled coffee fires at a low score",
              focus.get_view()["reminders"]["coffee"]["due"] is True)

        check("cfg: custom non-list -> 400", focus.update_config({"custom_reminders": {}})[1] == 400)
        check("cfg: custom blank name -> 400",
              focus.update_config({"custom_reminders": [{"name": " ", "interval_min": 10}]})[1] == 400)
        check("cfg: custom interval 0 -> 400",
              focus.update_config({"custom_reminders": [{"name": "A", "interval_min": 0}]})[1] == 400)
        check("cfg: custom non-int interval -> 400",
              focus.update_config({"custom_reminders": [{"name": "A", "interval_min": 5.5}]})[1] == 400)
        focus.update_config({"snooze_min": 12})
        code = focus.update_config({"snooze_min": 20,
                                    "custom_reminders": [{"name": "A", "interval_min": 0}]})[1]
        check("cfg: mixed patch with a bad custom -> 400", code == 400, str(code))
        check("cfg: the valid sibling field did NOT persist",
              focus.get_view()["config"]["snooze_min"] == 12)
    finally:
        _cleanup(d)


def test_workday_migration_of_old_file():
    """A file from BEFORE the work-day model loads without crashing: work_day is derived
    from started_at, config.coffee is seeded from the old focus.low_score_threshold, and
    the new day/metric fields are backfilled."""
    t0 = 1_700_000_000.0                          # 2023-11-14
    d, _ = _new_env(t0)
    try:
        legacy = {
            "config": {"enabled": True,
                       "intervals_min": {"water": 45, "stretch": 60, "exercise": 90},
                       "exercises": [{"id": "pushups", "name": "Sklekovi", "emoji": "",
                                      "weight": 1, "reps": 5}],
                       "focus": {"break_after_min": 90, "low_score_threshold": 35}},
            "session": {"started_at": t0, "paused": False, "paused_at": None,
                        "paused_total_sec": 0.0, "last_break_at": t0, "last_exercise": None},
            "reminders": {"water": {"last_at": None, "next_at": t0 + 2700},
                          "stretch": {"last_at": None, "next_at": t0 + 3600},
                          "exercise": {"last_at": None, "next_at": t0 + 5400, "exercise": None},
                          "coffee": {"last_at": None}},
            "metrics": {"water": {"count": 5, "today": 5}, "stretch": {"count": 1, "today": 1},
                        "exercise": {"count": 0, "reps_total": 0, "today_reps": 0, "by_id": {}},
                        "streak_days": 2, "last_active_day": "2023-11-14"},
            "focus": {"score": 100, "computed_at": t0},
        }
        focus.STATE_PATH.write_text(json.dumps(legacy), encoding="utf-8")
        v = focus.get_view()                      # must migrate, not crash
        sess = v["session"]
        check("wd-migrate: work_day derived from started_at", sess["work_day"] == "2023-11-14",
              str(sess.get("work_day")))
        check("wd-migrate: day_ended defaulted false", sess["day_ended"] is False)
        check("wd-migrate: work_blocks defaulted 1", sess["work_blocks"] == 1)
        check("wd-migrate: heartbeat present", isinstance(sess.get("heartbeat"), (int, float)))
        cfg = v["config"]
        check("wd-migrate: coffee config seeded from the old threshold",
              cfg["coffee"]["low_score_threshold"] == 35, str(cfg.get("coffee")))
        check("wd-migrate: threshold moved out of focus", "low_score_threshold" not in cfg["focus"])
        check("wd-migrate: snooze_min defaulted", cfg["snooze_min"] == focus.DEFAULT_SNOOZE_MIN)
        check("wd-migrate: custom_reminders defaulted empty", cfg["custom_reminders"] == [])
        check("wd-migrate: metrics.snoozes/pauses backfilled",
              v["metrics"]["snoozes"]["count"] == 0 and v["metrics"]["pauses"]["count"] == 0)
        check("wd-migrate: history present (empty) via profile", focus.profile()[0]["days_recorded"] == 0)
    finally:
        _cleanup(d)


def _prof_report(dstr, **kw):
    r = {"date": dstr, "hours_worked": 0.0, "water": 0, "stretch": 0, "exercise_total": 0,
         "pauses": 0, "snoozes": 0, "claude_sessions": 0, "work_blocks": 1, "commits": 0}
    r.update(kw)
    return r


def test_profile_radna_vs_cela_nedelja_math():
    """The two week flavors, on a seeded history with a gap and a weekend day:
      present days Mon,Tue,Thu,Fri (water 2,4,8,10) + Sun (water 4); Wed and Sat missing.
      total_all=28 over 5 present days; weekday total=24 over 4 weekday days; span=7.
        avg_all           = 28/5 = 5.6   (present days)
        avg_radna_nedelja = 24/4 = 6.0   (Mon-Fri present days)
        avg_cela_nedelja  = 28/7 = 4.0   (all 7 calendar days, missing = 0)."""
    def _d(s):
        return datetime.strptime(s, "%Y-%m-%d").date()
    days = [
        (_d("2026-03-02"), _prof_report("2026-03-02", water=2)),   # Mon
        (_d("2026-03-03"), _prof_report("2026-03-03", water=4)),   # Tue
        (_d("2026-03-05"), _prof_report("2026-03-05", water=8)),   # Thu (Wed missing)
        (_d("2026-03-06"), _prof_report("2026-03-06", water=10)),  # Fri
        (_d("2026-03-08"), _prof_report("2026-03-08", water=4)),   # Sun (Sat missing)
    ]
    now = datetime(2026, 3, 8, 12, 0, 0).timestamp()               # today = Sun 2026-03-08
    prof = focus._build_profile(days, now)
    w = prof["metrics"]["water"]
    check("profile: total_all", w["total_all"] == 28, str(w))
    check("profile: avg_all is over present days (28/5=5.6)", _approx(w["avg_all"], 5.6, 0.001), str(w))
    check("profile: avg_radna_nedelja is Mon-Fri only (24/4=6.0)",
          _approx(w["avg_radna_nedelja"], 6.0, 0.001), str(w))
    check("profile: avg_cela_nedelja spans all 7 days, missing=0 (28/7=4.0)",
          _approx(w["avg_cela_nedelja"], 4.0, 0.001), str(w))
    check("profile: this-week total picks up the seeded days", w["total_week"] == 28, str(w))
    check("profile: days_recorded", prof["days_recorded"] == 5, str(prof["days_recorded"]))
    check("profile: recent history echoed back", len(prof["history"]) == 5, str(len(prof["history"])))


def test_profile_streaks():
    def _d(s):
        return datetime.strptime(s, "%Y-%m-%d").date()
    days = [
        (_d("2026-03-04"), _prof_report("2026-03-04", water=1)),
        (_d("2026-03-05"), _prof_report("2026-03-05", water=0)),    # breaks the run
        (_d("2026-03-06"), _prof_report("2026-03-06", water=2, exercise_total=0)),
        (_d("2026-03-07"), _prof_report("2026-03-07", water=3)),
        (_d("2026-03-08"), _prof_report("2026-03-08", water=4)),
    ]
    now = datetime(2026, 3, 8, 12, 0, 0).timestamp()
    prof = focus._build_profile(days, now)
    check("streaks: water counts back from latest while > 0 (03-06..03-08 = 3)",
          prof["streaks"]["water"] == 3, str(prof["streaks"]))
    check("streaks: exercise is 0 when the latest day has none",
          prof["streaks"]["exercise"] == 0, str(prof["streaks"]))


def test_profile_route_smoke():
    d = _http_env()
    httpd, port = _start()
    try:
        code, body = _get(port, "/api/focus/profile")
        out = json.loads(body.decode() or "{}")
        check("http: GET /api/focus/profile -> 200", code == 200, str(code))
        check("http: profile carries metrics + streaks + history",
              isinstance(out.get("metrics"), dict) and isinstance(out.get("streaks"), dict)
              and isinstance(out.get("history"), list), str(list(out.keys())))
        for p, obj in (("/api/focus/snooze", {"type": "water"}),
                       ("/api/focus/end-day", {}), ("/api/focus/new-day", {})):
            code, _b = _post(port, p, obj)
            check(f"http: POST {p} -> 200", code == 200, str(code))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


def test_new_focus_routes_gated():
    orig = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    d = _http_env()
    httpd, port = _start()
    try:
        for p in ("/api/focus/snooze", "/api/focus/end-day", "/api/focus/new-day"):
            code, _b = _post(port, p, {"type": "water"})
            check(f"gate: {p} -> 403 for a non-local peer", code == 403, str(code))
        code, _b = _get(port, "/api/focus/profile")
        check("gate: profile read stays open (200) on the LAN", code == 200, str(code))
    finally:
        server.Handler._client_is_local = orig
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  NEW: multi-session/day, Fokus activity pool, exercise progression, swap,
#  muscle/stat mapping.
# --------------------------------------------------------------------------- #
def test_two_sessions_one_day_collapse_in_profile():
    """The bug: ending a day and starting a NEW day on the SAME calendar date used to
    write TWO history records for one date, double-counting the profile. Now each entry
    is a SESSION and the profile GROUPS by work_day: the date counts ONCE, its metrics
    are the SUM of both sessions, and averages/streaks use distinct calendar dates."""
    t0 = _ts(2026, 7, 6, h=8)                     # a Monday
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(30 * MIN); focus.ack("water")                 # session 1: 1 water
        r1, _ = focus.end_day(claude_sessions=2, commits=3)
        check("collapse: session carries work_day", r1["work_day"] == "2026-07-06", str(r1))
        check("collapse: session carries started_at/ended_at",
              isinstance(r1.get("started_at"), (int, float)) and isinstance(r1.get("ended_at"), (int, float)),
              str(r1))
        clk.advance(60 * MIN)
        focus.new_day()                            # SAME calendar date, a second session
        clk.advance(30 * MIN); focus.ack("water"); focus.ack("water")   # session 2: 2 water
        focus.end_day(claude_sessions=5, commits=4)
        check("collapse: two raw history entries on the same date",
              len(focus.load_state()["history"]) == 2, str(len(focus.load_state()["history"])))
        prof, _ = focus.profile()
        check("collapse: days_recorded counts the date ONCE", prof["days_recorded"] == 1,
              str(prof["days_recorded"]))
        w = prof["metrics"]["water"]
        check("collapse: water SUMMED across sessions (1+2=3)", w["total_all"] == 3, str(w))
        check("collapse: avg is over ONE present day (=3, not 1.5)", _approx(w["avg_all"], 3.0, 0.001), str(w))
        check("collapse: claude_sessions summed (2+5=7)",
              prof["metrics"]["claude_sessions"]["total_all"] == 7, str(prof["metrics"]["claude_sessions"]))
        check("collapse: commits summed (3+4=7)",
              prof["metrics"]["commits"]["total_all"] == 7, str(prof["metrics"]["commits"]))
        check("collapse: streak counts the day once (not twice)",
              prof["streaks"]["water"] == 1, str(prof["streaks"]))
        check("collapse: history echo has ONE per-day row", len(prof["history"]) == 1, str(prof["history"]))
        check("collapse: per-day row reports 2 sessions", prof["history"][0].get("sessions") == 2,
              str(prof["history"][0]))
    finally:
        _cleanup(d)


def test_exercise_target_progression_math():
    """The confirmed once-per-day step (pure over _progress_target): diminishing growth
    when met, ease-down toward actual when missed, never below 1."""
    check("prog: 5 met -> 7 (+2)", focus._progress_target(5, 5) == 7, str(focus._progress_target(5, 5)))
    check("prog: 7 met -> 9 (+2)", focus._progress_target(7, 9) == 9, str(focus._progress_target(7, 9)))
    check("prog: 9 met -> 10 (+1)", focus._progress_target(9, 9) == 10, str(focus._progress_target(9, 9)))
    check("prog: 12 met -> 13 (+1, diminishing)", focus._progress_target(12, 20) == 13,
          str(focus._progress_target(12, 20)))
    check("prog: 20 met -> 21 (+1, diminishing)", focus._progress_target(20, 25) == 21,
          str(focus._progress_target(20, 25)))
    check("prog: 10 target, did 5 -> 7 (missed)", focus._progress_target(10, 5) == 7,
          str(focus._progress_target(10, 5)))
    check("prog: never below 1", focus._progress_target(1, 0) >= 1, str(focus._progress_target(1, 0)))
    t, traj = 5, [5]
    for _ in range(3):
        t = focus._progress_target(t, t); traj.append(t)
    check("prog: always-met trajectory 5,7,9,10", traj == [5, 7, 9, 10], str(traj))


def test_exercise_progression_once_per_day():
    """Through the real ack flow with a single-exercise pool: the target adjusts on the
    FIRST completion of the day and NOT on a second set the same day; a missed next day
    eases it down; recent_actuals records every set."""
    d, clk = _new_env(_ts(2026, 5, 4, h=9))       # a Monday
    try:
        focus.get_view()
        focus.update_config({"exercises": [{"id": "solo", "name": "Sklekovi", "weight": 1, "reps": 10}]})

        def ce():
            return focus.load_state()["config"]["exercises"][0]

        check("prog-flow: target seeded to reps", ce()["target"] == 10, str(ce()))
        clk.advance(91 * MIN); focus.get_view()    # solo becomes due -> picked
        focus.ack("exercise", value=12)            # met (12>=10) -> 10 + 1 = 11
        check("prog-flow: met bumps target once", ce()["target"] == 11, str(ce()))
        check("prog-flow: last_done + days_since_done reset",
              ce()["last_done"] == "2026-05-04" and ce()["days_since_done"] == 0, str(ce()))
        clk.advance(91 * MIN); focus.get_view()    # same exercise due again, SAME day
        focus.ack("exercise", value=20)            # a second set the same day
        check("prog-flow: a second set the same day does NOT re-adjust", ce()["target"] == 11, str(ce()))
        check("prog-flow: recent_actuals records both sets", ce()["recent_actuals"][-2:] == [12, 20],
              str(ce()["recent_actuals"]))
        clk.t = _ts(2026, 5, 5, h=9)               # next calendar day
        focus.get_view()
        check("prog-flow: days_since_done advanced on the new day", ce()["days_since_done"] == 1, str(ce()))
        clk.advance(91 * MIN); focus.get_view()
        focus.ack("exercise", value=5)             # missed: 11 - (11-5)*0.6 = 7.4 -> 7
        check("prog-flow: missed eases the target down", ce()["target"] == 7, str(ce()))
    finally:
        _cleanup(d)


def test_progression_preserved_across_config_edit():
    """A config edit that changes weight/name but does not send `target` must KEEP the
    trained target (progression is carried across the edit by stable id)."""
    d, clk = _new_env(_ts(2026, 6, 1, h=9))
    try:
        focus.get_view()
        focus.update_config({"exercises": [{"id": "solo", "name": "Sklekovi", "weight": 1, "reps": 10}]})
        clk.advance(91 * MIN); focus.get_view()
        focus.ack("exercise", value=15)            # met -> target 10 -> 11
        st = focus.load_state()
        check("edit: target trained to 11", st["config"]["exercises"][0]["target"] == 11,
              str(st["config"]["exercises"][0]))
        v, _ = focus.update_config({"exercises": [{"id": "solo", "name": "Sklekovi", "weight": 3, "reps": 10}]})
        e0 = v["config"]["exercises"][0]
        check("edit: weight updated", e0["weight"] == 3.0, str(e0))
        check("edit: trained target preserved across the edit", e0["target"] == 11, str(e0))
    finally:
        _cleanup(d)


def test_focus_activity_pool_fires_and_counts():
    """A low focus score fires ONE weighted Fokus activity, persisted on the coffee slot
    and stable across polls; ack counts it in metrics.coffee and clears the pick."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(80 * MIN)                      # score below threshold -> coffee due
        v = focus.get_view()
        check("fa-fire: coffee due at a low score", v["reminders"]["coffee"]["due"] is True)
        act = v["reminders"]["coffee"]["activity"]
        check("fa-fire: an activity is picked and persisted",
              isinstance(act, dict) and bool(act.get("id")) and bool(act.get("name")), str(act))
        again = focus.get_view()["reminders"]["coffee"]["activity"]
        check("fa-fire: pick is stable across polls (not re-rolled)", again["id"] == act["id"], str(again))
        v, code = focus.ack("coffee")
        check("fa-fire: ack -> 200", code == 200, str(code))
        check("fa-fire: ack counted in metrics.coffee", v["metrics"]["coffee"]["count"] == 1,
              str(v["metrics"].get("coffee")))
        check("fa-fire: per-activity ack recorded",
              v["metrics"]["coffee"]["by_id"].get(act["id"]) == 1, str(v["metrics"]["coffee"]))
        check("fa-fire: last_activity stamped", v["session"]["last_activity"] == act["id"], str(v["session"]))
        check("fa-fire: pick cleared after ack", v["reminders"]["coffee"]["activity"] is None)
    finally:
        _cleanup(d)


def test_focus_activity_no_immediate_repeat():
    """A pick never equals session.last_activity while more than one activity exists."""
    import random
    random.seed(31)
    st = focus.default_state(now=1_700_000_000.0)
    st["config"]["focus_activities"] = [
        {"id": "a", "name": "A", "weight": 1.0},
        {"id": "b", "name": "B", "weight": 1.0},
    ]
    prev, ok = None, True
    for _ in range(200):
        pick = focus._pick_activity(st)
        if prev is not None and pick["id"] == prev:
            ok = False
            break
        st["session"]["last_activity"] = pick["id"]
        prev = pick["id"]
    check("fa-repeat: never equals the immediately previous activity", ok, str(prev))
    st["config"]["focus_activities"] = [{"id": "solo", "name": "S", "weight": 1}]
    st["session"]["last_activity"] = "solo"
    check("fa-repeat: a single-activity pool still returns it",
          focus._pick_activity(st)["id"] == "solo")


def test_focus_activities_config_accept_and_reject():
    t0 = 1_700_000_000.0
    d, _ = _new_env(t0)
    try:
        v, code = focus.update_config({"focus_activities": [
            {"name": "Voda", "weight": 1},
            {"name": "Sklek pauza", "weight": 0.5, "id": "sk"},
        ]})
        check("fa-cfg: valid -> 200", code == 200, str(code))
        fa = v["config"]["focus_activities"]
        check("fa-cfg: stored count", len(fa) == 2, str(fa))
        check("fa-cfg: id slugged from name", fa[0]["id"] == "voda", str(fa[0]))
        check("fa-cfg: explicit id kept", fa[1]["id"] == "sk", str(fa[1]))
        check("fa-cfg: non-list -> 400", focus.update_config({"focus_activities": {}})[1] == 400)
        check("fa-cfg: empty list -> 400", focus.update_config({"focus_activities": []})[1] == 400)
        check("fa-cfg: blank name -> 400",
              focus.update_config({"focus_activities": [{"name": " ", "weight": 1}]})[1] == 400)
        check("fa-cfg: negative weight -> 400",
              focus.update_config({"focus_activities": [{"name": "A", "weight": -1}]})[1] == 400)
        focus.update_config({"enabled": True})
        code = focus.update_config({"enabled": False,
                                    "focus_activities": [{"name": "A", "weight": -1}]})[1]
        check("fa-cfg: mixed patch with a bad entry -> 400", code == 400, str(code))
        check("fa-cfg: the valid sibling field did NOT persist",
              focus.get_view()["config"]["enabled"] is True)
        check("fa-cfg: the earlier good set survives the rejected patch",
              len(focus.get_view()["config"]["focus_activities"]) == 2)
    finally:
        _cleanup(d)


def test_exercise_muscle_stat_autosuggest_and_validation():
    t0 = 1_700_000_000.0
    d, _ = _new_env(t0)

    def muscles_of(e):
        return {m["muscle"]: m["pct"] for m in e["muscles"]}

    try:
        v, code = focus.update_config({"exercises": [
            {"name": "Sklekovi", "weight": 1, "reps": 5},
            {"name": "Čučnjevi", "weight": 1, "reps": 10},
            {"name": "Trbušnjaci", "weight": 1, "reps": 20},
            {"name": "Nepoznato", "weight": 1, "reps": 8},
        ]})
        check("ms: valid set -> 200", code == 200, str(code))
        by = {e["name"]: e for e in v["config"]["exercises"]}
        check("ms: sklek -> grudi 70 / podlaktice 30 (compound split)",
              muscles_of(by["Sklekovi"]) == {"grudi": 70, "podlaktice": 30}, str(by["Sklekovi"]))
        check("ms: cucanj -> kvadriceps 100", muscles_of(by["Čučnjevi"]) == {"kvadriceps": 100},
              str(by["Čučnjevi"]))
        check("ms: trbusnjaci -> trbusnjaci 100", muscles_of(by["Trbušnjaci"]) == {"trbusnjaci": 100},
              str(by["Trbušnjaci"]))
        check("ms: unknown name -> default grudi 100", muscles_of(by["Nepoznato"]) == {"grudi": 100},
              str(by["Nepoznato"]))
        check("ms: legacy single-muscle key is gone",
              all("muscle" not in e for e in v["config"]["exercises"]), str(v["config"]["exercises"]))
        check("ms: default stat snaga", all(e["stat"] == "snaga" for e in v["config"]["exercises"]),
              str(v["config"]["exercises"]))
        check("ms: target seeded from reps", by["Sklekovi"]["target"] == 5 and by["Čučnjevi"]["target"] == 10)
        v, _ = focus.update_config({"exercises": [
            {"name": "Sklekovi", "weight": 1, "reps": 5,
             "muscles": [{"muscle": "ramena", "pct": 60}, {"muscle": "grudi", "pct": 40}],
             "stat": "izdrzljivost", "target": 12}]})
        e0 = v["config"]["exercises"][0]
        check("ms: explicit muscles override kept (sum 100)",
              muscles_of(e0) == {"ramena": 60, "grudi": 40}, str(e0))
        check("ms: explicit stat override", e0["stat"] == "izdrzljivost", str(e0))
        check("ms: explicit target override", e0["target"] == 12, str(e0))
        # soft check: pcts summing to 50 are NORMALIZED to 100, proportions kept (not rejected)
        v, _ = focus.update_config({"exercises": [
            {"name": "A", "weight": 1, "reps": 5,
             "muscles": [{"muscle": "grudi", "pct": 30}, {"muscle": "podlaktice", "pct": 20}]}]})
        check("ms: soft-normalize 30/20 -> 60/40 (sum 100)",
              muscles_of(v["config"]["exercises"][0]) == {"grudi": 60.0, "podlaktice": 40.0},
              str(v["config"]["exercises"][0]))
        focus.update_config({"enabled": True})
        code = focus.update_config({"enabled": False, "exercises": [
            {"name": "A", "weight": 1, "reps": 5, "muscles": [{"muscle": "levo-uvo", "pct": 100}]}]})[1]
        check("ms: bad muscle -> 400", code == 400, str(code))
        check("ms: the valid sibling field did NOT persist",
              focus.get_view()["config"]["enabled"] is True)
        check("ms: pct over 100 -> 400", focus.update_config({"exercises": [
            {"name": "A", "weight": 1, "reps": 5, "muscles": [{"muscle": "grudi", "pct": 150}]}]})[1] == 400)
        check("ms: muscles not a list -> 400", focus.update_config({"exercises": [
            {"name": "A", "weight": 1, "reps": 5, "muscles": {"muscle": "grudi", "pct": 100}}]})[1] == 400)
        check("ms: bad target -> 400", focus.update_config({"exercises": [
            {"name": "A", "weight": 1, "reps": 5, "target": 0}]})[1] == 400)
        check("ms: bad stat (blank) -> 400", focus.update_config({"exercises": [
            {"name": "A", "weight": 1, "reps": 5, "stat": "  "}]})[1] == 400)
    finally:
        _cleanup(d)


def test_swap_rerolls_pick():
    """swap re-rolls the current pick to a DIFFERENT one when the pool has >2 items
    (exercise + Fokus activity), and rejects an unknown type."""
    import random
    random.seed(5)
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(91 * MIN); v = focus.get_view()          # exercise pick (4 seeded)
        before = v["reminders"]["exercise"]["exercise"]["id"]
        v, code = focus.swap("exercise")
        check("swap: exercise -> 200", code == 200, str(code))
        after = v["reminders"]["exercise"]["exercise"]["id"]
        check("swap: exercise pick changed with >2 items", after != before, f"{before}->{after}")
        clk.advance(80 * MIN); v = focus.get_view()           # coffee due -> activity (4 seeded)
        a_before = v["reminders"]["coffee"]["activity"]["id"]
        v, code = focus.swap("coffee")
        check("swap: coffee -> 200", code == 200, str(code))
        a_after = v["reminders"]["coffee"]["activity"]["id"]
        check("swap: activity pick changed with >2 items", a_after != a_before, f"{a_before}->{a_after}")
        check("swap: unknown type -> 400", focus.swap("water")[1] == 400)
    finally:
        _cleanup(d)


def test_swap_two_items_allows_previous():
    """With <=2 items swap does not force a switch (the previous is allowed); it must
    still return a valid pick and never crash."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        focus.update_config({"exercises": [
            {"id": "a", "name": "A", "weight": 1, "reps": 5},
            {"id": "b", "name": "B", "weight": 1, "reps": 5}]})
        clk.advance(91 * MIN); focus.get_view()
        v, code = focus.swap("exercise")
        after = v["reminders"]["exercise"]["exercise"]["id"]
        check("swap2: -> 200 with a valid pick from the 2-item pool",
              code == 200 and after in ("a", "b"), f"{code}/{after}")
    finally:
        _cleanup(d)


def test_swap_route_smoke_and_gated():
    d = _http_env()
    httpd, port = _start()
    try:
        code, body = _post(port, "/api/focus/swap", {"type": "exercise"})
        out = json.loads(body.decode() or "{}")
        check("http: POST swap exercise -> 200", code == 200, str(code))
        check("http: swap returns the view", isinstance(out.get("reminders"), dict), str(list(out.keys())))
        code, _b = _post(port, "/api/focus/swap", {"type": "bogus"})
        check("http: swap bad type -> 400", code == 400, str(code))
    finally:
        httpd.shutdown(); httpd.server_close(); _cleanup(d)


def test_swap_route_gated():
    orig = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    d = _http_env()
    httpd, port = _start()
    try:
        code, _b = _post(port, "/api/focus/swap", {"type": "exercise"})
        check("gate: swap -> 403 for a non-local peer", code == 403, str(code))
    finally:
        server.Handler._client_is_local = orig
        httpd.shutdown(); httpd.server_close(); _cleanup(d)


# --------------------------------------------------------------------------- #
#  NEW: session-hours + concurrency (pure), productivity analytics, RPG + decay.
# --------------------------------------------------------------------------- #
def _sess_report(date, **kw):
    """A full work-SESSION report for a seeded history (every field end_day writes)."""
    r = {"work_day": date, "date": date, "started_at": 0, "ended_at": 0,
         "hours_worked": 0.0, "water": 0, "stretch": 0, "exercise_total": 0,
         "exercise_sets": 0, "exercise_by_id": {}, "pauses": 0, "snoozes": 0,
         "claude_sessions": 0, "work_blocks": 1, "commits": 0, "focus_score": 0,
         "session_hours": 0.0, "max_concurrency": 0, "avg_concurrency": 0.0,
         "parallelism": 0.0, "output_tokens": 0, "input_tokens": 0,
         "cache_read": 0, "cache_write": 0}
    r.update(kw)
    return r


def _seed(now, history, exercises=None, streak=0, day_ended=True, live=None,
          reminders_enabled=None):
    """Write a state file with a seeded history/config, at a frozen clock. Returns the
    dir to clean up. `live` (dict) seeds the current-day live counters for gains_today.
    `reminders_enabled` (dict) overrides the per-category toggles (default all-true)."""
    st = focus.default_state(now=now)
    st["history"] = history
    if exercises is not None:
        st["config"]["exercises"] = exercises
    if reminders_enabled is not None:
        st["config"]["reminders_enabled"].update(reminders_enabled)
    st["metrics"]["streak_days"] = streak
    st["session"]["day_ended"] = day_ended
    if live:
        st["session"]["day_ended"] = False
        st["session"]["started_at"] = now - live.get("worked_sec", 3600)
        st["metrics"]["water"]["count"] = live.get("water", 0)
        st["metrics"]["stretch"]["count"] = live.get("stretch", 0)
        ex = st["metrics"]["exercise"]
        ex["reps_total"] = live.get("exercise_total", 0)
        ex["count"] = live.get("exercise_sets", 0)
        ex["by_id"] = live.get("exercise_by_id", {})
    focus.save_state(st)


def test_session_concurrency_math_pure():
    """server._active_segments clips active spans to the window; _window_concurrency
    SUMS overlapping spans (session_hours), peaks max, and averages simultaneity."""
    start, end, idle = 1000.0, 2000.0, 300.0
    a = server._active_segments([1000, 1100, 1250], start, end, idle)   # two spans
    b = server._active_segments([1050, 1150], start, end, idle)         # overlaps A
    check("conc: A has two consecutive active spans", a == [(1000.0, 1100), (1100, 1250)], str(a))
    sh, mx, avg = server._window_concurrency(a + b)
    check("conc: session_hours SUMS every span (350s)", _approx(sh * 3600, 350.0, 0.5), str(sh))
    check("conc: max_concurrency counts overlap, not a session's own back-to-back (2)",
          mx == 2, str(mx))
    check("conc: avg_concurrency = summed/union (350/250=1.4)", _approx(avg, 1.4, 0.001), str(avg))
    # a gap longer than idle is NOT active; disjoint sessions -> avg 1.0, max 1
    idle_gap = server._active_segments([1000, 1000 + idle + 10], start, end, idle)
    check("conc: a gap over idle_gap yields no span", idle_gap == [], str(idle_gap))
    s1 = server._active_segments([1000, 1100], start, end, idle)
    s2 = server._active_segments([1300, 1400], start, end, idle)
    sh2, mx2, avg2 = server._window_concurrency(s1 + s2)
    check("conc: disjoint spans -> max 1, avg 1.0", mx2 == 1 and _approx(avg2, 1.0, 0.001),
          f"{mx2}/{avg2}")
    check("conc: empty -> zeros", server._window_concurrency([]) == (0.0, 0, 0.0))
    # clipping: a stamp before the window is clipped to start
    clip = server._active_segments([900, 1000, 1100], 1050, 2000, idle)
    check("conc: spans are clipped to the window", clip == [(1050, 1100)], str(clip))


def test_end_day_stores_parallelism_and_external_fields():
    """end_day records the external productivity base and derives parallelism =
    session_hours / wall-clock without inflating the wall-clock (hours_worked)."""
    t0 = _ts(2026, 6, 1, h=9)
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(120 * MIN)                     # two wall-clock hours worked
        report, code = focus.end_day(claude_sessions=4, commits=5, session_hours=6.0,
                                     max_concurrency=3, avg_concurrency=2.0,
                                     output_tokens=120000, input_tokens=40000, cache_read=90000)
        check("end-day-ext: -> 200", code == 200, str(code))
        check("end-day-ext: wall-clock NOT inflated (~2h)", _approx(report["hours_worked"], 2.0, 0.01),
              str(report["hours_worked"]))
        check("end-day-ext: session_hours stored", report["session_hours"] == 6.0, str(report))
        check("end-day-ext: parallelism = session_hours/wall (~3.0)",
              _approx(report["parallelism"], 3.0, 0.05), str(report["parallelism"]))
        check("end-day-ext: concurrency stored", report["max_concurrency"] == 3
              and report["avg_concurrency"] == 2.0, str(report))
        check("end-day-ext: tokens stored", report["output_tokens"] == 120000
              and report["cache_read"] == 90000, str(report))
        check("end-day-ext: focus_score snapshot present",
              isinstance(report["focus_score"], int), str(report.get("focus_score")))
    finally:
        _cleanup(d)


def test_productivity_grouping_and_derived_math():
    """Two sessions on one date collapse: SUM fields add, concurrency MAXes, rate fields
    MEAN; the derived per-day metrics compute off the collapsed row."""
    now = _ts(2026, 5, 8, h=18)                    # Friday
    d, _ = _new_env(now)
    try:
        hist = [
            _sess_report("2026-05-06", hours_worked=4.0, commits=8, water=3, stretch=2,
                         exercise_total=30, exercise_sets=3, exercise_by_id={"pushups": 30},
                         focus_score=80, session_hours=6.0, max_concurrency=3, avg_concurrency=1.5,
                         output_tokens=200000, input_tokens=50000, cache_read=150000, work_blocks=2),
            _sess_report("2026-05-07", hours_worked=2.0, commits=2, water=2, exercise_total=10,
                         exercise_sets=1, exercise_by_id={"pushups": 10}, focus_score=60,
                         session_hours=2.0, max_concurrency=1, avg_concurrency=1.0, work_blocks=1),
            _sess_report("2026-05-07", hours_worked=3.0, commits=4, water=3, exercise_total=20,
                         exercise_sets=2, exercise_by_id={"squats": 20}, focus_score=90,
                         session_hours=5.0, max_concurrency=2, avg_concurrency=2.0, work_blocks=1),
        ]
        _seed(now, hist)
        prod, code = focus.productivity()
        check("prod: -> 200", code == 200, str(code))
        check("prod: two calendar days recorded", prod["days_recorded"] == 2, str(prod["days_recorded"]))
        day7 = next(r for r in prod["days"] if r["date"] == "2026-05-07")
        check("prod: SUM collapses (hours 5, commits 6, session_hours 7)",
              day7["hours_worked"] == 5.0 and day7["commits"] == 6 and day7["session_hours"] == 7.0,
              str((day7["hours_worked"], day7["commits"], day7["session_hours"])))
        check("prod: concurrency MAXes (2), rate MEANs (avg_conc 1.5, focus 75)",
              day7["max_concurrency"] == 2 and day7["avg_concurrency"] == 1.5
              and day7["focus_score"] == 75, str((day7["max_concurrency"], day7["avg_concurrency"], day7["focus_score"])))
        check("prod: two sessions on the date", day7["sessions"] == 2, str(day7["sessions"]))
        p = day7["productivity"]
        check("prod: commits/hour = 6/5 = 1.2", _approx(p["commits_per_hour"], 1.2, 0.001), str(p))
        check("prod: parallelism = 7/5 = 1.4", _approx(p["parallelism"], 1.4, 0.001), str(p))
        check("prod: deep_work_ratio = mean focus/100 = 0.75", _approx(p["deep_work_ratio"], 0.75, 0.001), str(p))
        check("prod: avg_session_length = 5h / 2 blocks = 2.5", _approx(p["avg_session_length_h"], 2.5, 0.001), str(p))
        check("prod: movement_adherence = 3 sets / (300/90) = 0.9", _approx(p["movement_adherence"], 0.9, 0.001), str(p))
        check("prod: cache_hit unavailable this day -> None (skipped cleanly)", p["cache_hit_ratio"] is None, str(p))
        agg = prod["aggregates"]
        check("prod: aggregate totals sum both days (commits 14, session_hours 13)",
              agg["totals"]["commits"] == 14 and agg["totals"]["session_hours"] == 13.0, str(agg["totals"]))
    finally:
        _cleanup(d)


def test_productivity_date_lookup_and_range_filter():
    now = _ts(2026, 5, 8, h=18)
    d, _ = _new_env(now)
    try:
        hist = [
            _sess_report("2026-05-06", hours_worked=4.0, commits=8),
            _sess_report("2026-05-07", hours_worked=2.0, commits=2),
            _sess_report("2026-05-07", hours_worked=3.0, commits=4),
        ]
        _seed(now, hist)
        one, _ = focus.productivity(date="2026-05-07")
        check("date: returns the day's full report", one["date"] == "2026-05-07" and one["day"] is not None)
        check("date: ALL sessions on that date returned (2)", len(one["sessions"]) == 2, str(len(one["sessions"])))
        check("date: the aggregated day sums the sessions (commits 6)", one["day"]["commits"] == 6, str(one["day"]))
        miss, _ = focus.productivity(date="2026-05-01")
        check("date: an absent date -> day None, no sessions", miss["day"] is None and miss["sessions"] == [], str(miss))
        rng, _ = focus.productivity(dfrom="2026-05-07", dto="2026-05-07")
        check("range: from/to narrows to the one day", rng["days_recorded"] == 1
              and rng["days"][0]["date"] == "2026-05-07", str(rng["days_recorded"]))
        rng2, _ = focus.productivity(dfrom="2026-05-06")
        check("range: open-ended from includes later days", rng2["days_recorded"] == 2, str(rng2["days_recorded"]))
    finally:
        _cleanup(d)


def test_productivity_highlights_pick_the_right_date():
    now = _ts(2026, 5, 8, h=18)
    d, _ = _new_env(now)
    try:
        hist = [
            _sess_report("2026-05-06", hours_worked=4.0, commits=8, water=1),   # commits/hr = 2.0
            _sess_report("2026-05-07", hours_worked=5.0, commits=5, water=9),   # commits/hr = 1.0
        ]
        _seed(now, hist)
        prod, _ = focus.productivity()
        h = prod["highlights"]
        check("highlight: best commits_per_hour is 05-06 (2.0)",
              h["commits_per_hour"]["date"] == "2026-05-06" and _approx(h["commits_per_hour"]["value"], 2.0, 0.001),
              str(h.get("commits_per_hour")))
        check("highlight: most water is 05-07", h["water"]["date"] == "2026-05-07" and h["water"]["value"] == 9,
              str(h.get("water")))
        check("highlight: most hours is 05-07", h["hours_worked"]["date"] == "2026-05-07", str(h.get("hours_worked")))
        check("highlight: most commits is 05-06", h["commits"]["date"] == "2026-05-06", str(h.get("commits")))
    finally:
        _cleanup(d)


def test_rpg_derivation_from_seeded_history():
    """stats/muscles/level derive from cumulative activity; muscles map via each
    exercise's `muscle`; an untrained muscle stays 0."""
    now = _ts(2026, 5, 3, h=18)
    d, _ = _new_env(now)
    try:
        hist = [
            _sess_report("2026-05-01", hours_worked=5, commits=10, water=6, stretch=4,
                         exercise_total=200, exercise_sets=8,
                         exercise_by_id={"pushups": 120, "squats": 80}, focus_score=85, output_tokens=500000),
            _sess_report("2026-05-02", hours_worked=4, commits=6, water=5, stretch=3,
                         exercise_total=150, exercise_sets=6, exercise_by_id={"pushups": 150}, focus_score=70),
        ]
        exs = [
            {"id": "pushups", "name": "Sklekovi", "weight": 1, "reps": 10, "target": 10,
             "muscle": "grudi", "stat": "snaga", "days_since_done": 1, "recent_actuals": [], "last_done": "2026-05-02"},
            {"id": "squats", "name": "Cucnjevi", "weight": 1, "reps": 20, "target": 20,
             "muscle": "kvadriceps", "stat": "snaga", "days_since_done": 1, "recent_actuals": [], "last_done": "2026-05-01"},
        ]
        _seed(now, hist, exercises=exs, streak=5, day_ended=True)
        r, code = focus.rpg()
        check("rpg: -> 200", code == 200, str(code))
        check("rpg: six stats each 0..20",
              all(s in r["stats"] and 0 <= r["stats"][s] <= 20 for s in focus.RPG_STATS), str(r["stats"]))
        check("rpg: snaga is developed (exercise reps drive it)", r["stats"]["snaga"] > 3, str(r["stats"]))
        check("rpg: grudi muscle developed (pushups mapped there)", r["muscles"]["grudi"] > 0, str(r["muscles"]))
        check("rpg: kvadriceps developed (squats mapped there)", r["muscles"]["kvadriceps"] > 0, str(r["muscles"]))
        check("rpg: an untrained muscle stays 0 (no biceps exercise)", r["muscles"]["biceps"] == 0.0, str(r["muscles"]))
        check("rpg: pushups(270) > squats(80) so grudi > kvadriceps",
              r["muscles"]["grudi"] > r["muscles"]["kvadriceps"], str(r["muscles"]))
        check("rpg: all seven muscle groups present",
              all(m in r["muscles"] for m in focus.MUSCLES), str(list(r["muscles"])))
        check("rpg: vitals hp/stamina/mana each 0..1",
              all(0 <= r["vitals"][v] <= 1 for v in ("hp", "stamina", "mana")), str(r["vitals"]))
        check("rpg: level >= 1 and xp in 0..1", r["level"] >= 1 and 0 <= r["xp"] <= 1, str((r["level"], r["xp"])))
        check("rpg: day ended -> no gains_today buffs", r["gains_today"]["stats"] == {}
              and r["gains_today"]["muscles"] == {}, str(r["gains_today"]))
    finally:
        _cleanup(d)


def test_rpg_decay_engages_after_three_idle_days():
    """The long-term decay: full within the grace, a linear loss past it, and end-to-end
    an idle exercise's muscle/stat shrinks below the freshly-trained value."""
    check("decay: fresh (0 idle) holds full", focus._decay_factor(0) == 1.0)
    check("decay: 2 idle days still full (within grace)", focus._decay_factor(2) == 1.0)
    check("decay: exactly at the 3-day grace still full (0 days past)", focus._decay_factor(3) == 1.0)
    check("decay: 4 idle days engages decay", focus._decay_factor(4) < 1.0, str(focus._decay_factor(4)))
    check("decay: 5 idle = 1 - 0.08*2 = 0.84", _approx(focus._decay_factor(5), 0.84, 0.001),
          str(focus._decay_factor(5)))
    check("decay: never below the floor", focus._decay_factor(1000) >= focus.DECAY_FLOOR)

    hist = [_sess_report("2026-05-01", exercise_total=300, exercise_sets=12,
                         exercise_by_id={"pushups": 300}, hours_worked=4, focus_score=80)]

    def grudi_at(dsd, day):
        now = _ts(2026, 5, day, h=18)
        d, _ = _new_env(now)
        try:
            exs = [{"id": "pushups", "name": "Sklekovi", "weight": 1, "reps": 10, "target": 10,
                    "muscle": "grudi", "stat": "snaga", "days_since_done": dsd,
                    "recent_actuals": [], "last_done": "2026-05-01"}]
            _seed(now, hist, exercises=exs, streak=1, day_ended=True)
            r, _ = focus.rpg()
            return r["muscles"]["grudi"], r["stats"]["snaga"]
        finally:
            _cleanup(d)

    fresh_m, fresh_s = grudi_at(1, 3)
    grace_m, _gs = grudi_at(3, 5)
    decayed_m, decayed_s = grudi_at(6, 8)
    check("decay-e2e: within grace unchanged from fresh", _approx(grace_m, fresh_m, 0.0001),
          f"{grace_m} vs {fresh_m}")
    check("decay-e2e: an idle muscle shrinks past the grace", decayed_m < fresh_m,
          f"{decayed_m} vs {fresh_m}")
    check("decay-e2e: the mapped stat softens too", decayed_s < fresh_s, f"{decayed_s} vs {fresh_s}")


def test_rpg_gains_today_from_live_day():
    """A live (un-ended) day folds its counters in and reports them as gains_today."""
    now = _ts(2026, 5, 10, h=15)
    d, _ = _new_env(now)
    try:
        hist = [_sess_report("2026-05-09", hours_worked=2.0, water=2, exercise_total=50,
                             exercise_sets=2, exercise_by_id={"pushups": 50}, focus_score=75, commits=3)]
        exs = [{"id": "pushups", "name": "Sklekovi", "weight": 1, "reps": 10, "target": 10,
                "muscle": "grudi", "stat": "snaga", "days_since_done": 0, "recent_actuals": [],
                "last_done": "2026-05-10"}]
        _seed(now, hist, exercises=exs, streak=2,
              live={"water": 4, "exercise_total": 40, "exercise_sets": 2,
                    "exercise_by_id": {"pushups": {"count": 2, "reps_total": 40}}, "worked_sec": 3600})
        r, _ = focus.rpg()
        g = r["gains_today"]
        check("gains: today's pushups raise the grudi muscle", g["muscles"].get("grudi", 0) > 0, str(g["muscles"]))
        check("gains: today's reps raise snaga", g["stats"].get("snaga", 0) > 0, str(g["stats"]))
        check("gains: today's water raises izdrzljivost", g["stats"].get("izdrzljivost", 0) > 0, str(g["stats"]))
        check("gains: points rose today", g["points"] > 0, str(g["points"]))
    finally:
        _cleanup(d)


def test_productivity_and_rpg_routes():
    """The two GET routes answer 200 with the documented shape, and stay OPEN on the LAN
    (a read, like /api/focus/profile) rather than loopback-gated."""
    d = _http_env()
    httpd, port = _start()
    try:
        code, body = _get(port, "/api/focus/productivity")
        out = json.loads(body.decode() or "{}")
        check("http: GET /api/focus/productivity -> 200", code == 200, str(code))
        check("http: productivity carries days/aggregates/highlights",
              isinstance(out.get("days"), list) and isinstance(out.get("aggregates"), dict)
              and isinstance(out.get("highlights"), dict), str(list(out.keys())))
        code, body = _get(port, "/api/focus/productivity?date=2026-01-01")
        out = json.loads(body.decode() or "{}")
        check("http: productivity?date carries day + sessions",
              "day" in out and isinstance(out.get("sessions"), list), str(list(out.keys())))
        code, body = _get(port, "/api/focus/rpg")
        out = json.loads(body.decode() or "{}")
        check("http: GET /api/focus/rpg -> 200", code == 200, str(code))
        check("http: rpg carries stats/vitals/muscles/level/gains_today",
              all(k in out for k in ("stats", "vitals", "muscles", "level", "xp", "gains_today")),
              str(list(out.keys())))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


def test_productivity_rpg_reads_open_on_lan():
    orig = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    d = _http_env()
    httpd, port = _start()
    try:
        for p in ("/api/focus/productivity", "/api/focus/rpg"):
            code, _b = _get(port, p)
            check(f"gate: {p} read stays open (200) on the LAN", code == 200, str(code))
    finally:
        server.Handler._client_is_local = orig
        httpd.shutdown()
        httpd.server_close()
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  NEW: multi-muscle split, focus boost + decay, mandatory quota, infocus
#  calibration, delete-renormalize, migrations, and the sound flags.
# --------------------------------------------------------------------------- #
def test_rpg_reps_split_across_muscles_by_pct():
    """The core of requirement 1: _effective_exercise distributes an exercise's reps to
    each listed muscle by pct/100, while the single stat receives the FULL reps."""
    cfg = {"exercises": [
        {"id": "x", "name": "X", "stat": "snaga", "days_since_done": 0,
         "muscles": [{"muscle": "grudi", "pct": 70}, {"muscle": "podlaktice", "pct": 30}]},
        {"id": "y", "name": "Y", "stat": "izdrzljivost", "days_since_done": 0,
         "muscles": [{"muscle": "kvadriceps", "pct": 100}]},
    ]}
    eff_m, eff_s = focus._effective_exercise(cfg, {"x": 200, "y": 50})
    check("split: grudi gets 70% of 200 (=140)", _approx(eff_m["grudi"], 140.0, 0.001), str(eff_m))
    check("split: podlaktice gets 30% of 200 (=60)", _approx(eff_m["podlaktice"], 60.0, 0.001), str(eff_m))
    check("split: kvadriceps gets 100% of 50 (=50)", _approx(eff_m["kvadriceps"], 50.0, 0.001), str(eff_m))
    check("split: an unlisted muscle stays 0", eff_m["biceps"] == 0.0, str(eff_m))
    check("split: stat gets the FULL reps (snaga=200)", _approx(eff_s["snaga"], 200.0, 0.001), str(eff_s))
    check("split: second stat gets its full reps (izdrzljivost=50)",
          _approx(eff_s["izdrzljivost"], 50.0, 0.001), str(eff_s))
    # end-to-end through rpg(): a 70/30 split shows grudi > podlaktice > 0
    now = _ts(2026, 5, 3, h=18)
    d, _ = _new_env(now)
    try:
        hist = [_sess_report("2026-05-01", exercise_total=200, exercise_sets=8,
                             exercise_by_id={"x": 200}, hours_worked=4, focus_score=80)]
        exs = [{"id": "x", "name": "X", "weight": 1, "reps": 10, "target": 10, "stat": "snaga",
                "days_since_done": 1, "recent_actuals": [], "last_done": "2026-05-01",
                "muscles": [{"muscle": "grudi", "pct": 70}, {"muscle": "podlaktice", "pct": 30}]}]
        _seed(now, hist, exercises=exs, streak=1, day_ended=True)
        r, _ = focus.rpg()
        check("split-e2e: grudi > podlaktice (70 vs 30)",
              r["muscles"]["grudi"] > r["muscles"]["podlaktice"] > 0, str(r["muscles"]))
    finally:
        _cleanup(d)


def test_focus_boost_bumps_and_decays():
    """Acking a Fokus activity raises the live score by focus_boost, decaying back over the
    effective break window (requirement 2)."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(80 * MIN)                        # score low, coffee due (Kafa, boost 50)
        v = focus.get_view()
        low = v["focus"]["score"]
        act = v["reminders"]["coffee"]["activity"]
        check("boost: pick carries kind + focus_boost",
              act.get("kind") == "mandatory" and act.get("focus_boost") == 50, str(act))
        v, _ = focus.ack("coffee")
        boosted = v["focus"]["score"]
        check("boost: ack raises the live score", boosted > low, f"{boosted} vs {low}")
        check("boost: the bump is ~focus_boost above the primary", _approx(boosted, low + 50, 2.0),
              f"{boosted} vs {low}")
        clk.advance(45 * MIN)                         # half the 90-min window
        mid = focus.get_view()["focus"]["score"]
        clk.advance(50 * MIN)                         # past the window
        gone = focus.get_view()["focus"]["score"]
        check("boost: decays as time passes", boosted > mid > gone, f"{boosted}/{mid}/{gone}")
        check("boost: fully decayed once the window has passed", gone == 0, str(gone))
    finally:
        _cleanup(d)


def test_mandatory_quota_spread_and_random_fallback():
    """Mandatory Kafa is proposed on evenly-spaced WORKED-time checkpoints (not only on a
    low score), is PREFERRED over random while it owes quota, and once its quota is met a
    low score draws from the RANDOM pool instead (requirement 3)."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        # A very long break window so the SCORE stays high — isolating the time-spread trigger.
        focus.update_config({"focus": {"break_after_min": 1440}})
        v = focus.get_view()
        kafa = next(m for m in v["mandatory"] if m["id"] == "kafa")
        check("mand: quota state exposed (limit 3, none done)",
              kafa["daily_limit"] == 3 and kafa["done_today"] == 0 and kafa["remaining"] == 3, str(kafa))
        check("mand: not due at day start (high score, no checkpoint yet)",
              v["reminders"]["coffee"]["due"] is False, str(v["reminders"]["coffee"]))
        clk.advance(160 * MIN)                        # cross checkpoint 1 (480/3=160) with score still high
        v = focus.get_view()
        check("mand: score is still high here", v["focus"]["score"] > 40, str(v["focus"]))
        check("mand: due on the spread checkpoint despite the high score",
              v["reminders"]["coffee"]["due"] is True, str(v["reminders"]["coffee"]))
        act = v["reminders"]["coffee"]["activity"]
        check("mand: the pick is the mandatory Kafa", act["id"] == "kafa" and act["kind"] == "mandatory",
              str(act))
        # fulfill all three (cooldown is 30 min; checkpoints are 160 apart)
        for _ in range(3):
            focus.get_view()
            focus.ack("coffee")
            clk.advance(160 * MIN)
        v = focus.get_view()
        kafa = next(m for m in v["mandatory"] if m["id"] == "kafa")
        check("mand: quota fully met (3/3, remaining 0)",
              kafa["done_today"] == 3 and kafa["remaining"] == 0, str(kafa))
        # now drop the score: no mandatory pending -> a RANDOM draw
        focus.update_config({"focus": {"break_after_min": 90}})
        clk.advance(80 * MIN)
        v = focus.get_view()
        check("mand: a low score with quota met draws RANDOM",
              v["reminders"]["coffee"]["due"] is True
              and v["reminders"]["coffee"]["activity"]["kind"] == "random",
              str(v["reminders"]["coffee"]["activity"]))
    finally:
        _cleanup(d)


def test_mandatory_missed_recorded_at_day_end():
    """A mandatory quota left unfulfilled at end_day is recorded as missed in the report."""
    t0 = _ts(2026, 6, 2, h=9)
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        focus.update_config({"focus": {"break_after_min": 1440}})   # keep score high
        clk.advance(160 * MIN); focus.get_view()      # checkpoint 1 fires
        focus.ack("coffee")                            # fulfill 1 of 3
        clk.advance(400 * MIN)
        report, _ = focus.end_day()
        check("missed: 2 of Kafa's 3 unfulfilled", report["mandatory_missed"] == 2, str(report.get("mandatory_missed")))
        kafa = next(m for m in report["mandatory"] if m["id"] == "kafa")
        check("missed: per-activity breakdown (done 1, missed 2)",
              kafa["done"] == 1 and kafa["missed"] == 2 and kafa["daily_limit"] == 3, str(kafa))
    finally:
        _cleanup(d)


def test_infocus_defers_and_calibrates_decay():
    """POST infocus defers the pick without counting it done or boosting, records feedback,
    and nudges focus_decay_scale UP; a subsequent ack-while-low nudges it back toward
    baseline (requirement 4)."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.get_view()
        clk.advance(80 * MIN)
        v = focus.get_view()
        check("infocus: coffee due before the report", v["reminders"]["coffee"]["due"] is True)
        check("infocus: baseline scale is 1.0", v["focus"]["decay_scale"] == 1.0, str(v["focus"]))
        v, code = focus.infocus("coffee")
        check("infocus: -> 200", code == 200, str(code))
        check("infocus: current pick deferred (cleared)", v["reminders"]["coffee"]["activity"] is None)
        check("infocus: suppressed during the defer window", v["reminders"]["coffee"]["due"] is False)
        check("infocus: feedback recorded", v["metrics"]["coffee"]["in_focus_reports"] == 1,
              str(v["metrics"]["coffee"]))
        check("infocus: decay_scale nudged up", v["focus"]["decay_scale"] > 1.0, str(v["focus"]["decay_scale"]))
        kafa = next(m for m in v["mandatory"] if m["id"] == "kafa")
        check("infocus: NOT counted toward the quota", kafa["done_today"] == 0 and kafa["remaining"] == 3,
              str(kafa))
        check("infocus: bad type -> 400", focus.infocus("water")[1] == 400)
        # re-arms after the defer window
        clk.advance(focus.INFOCUS_DEFER_MIN * MIN + 60)
        after = focus.get_view()
        check("infocus: re-arms after the defer window", after["reminders"]["coffee"]["due"] is True)
        scale_up = after["focus"]["decay_scale"]
        # ack while genuinely low -> nudge back toward baseline
        v, _ = focus.ack("coffee")
        check("infocus: an ack-while-low pulls the scale back toward baseline",
              v["focus"]["decay_scale"] < scale_up, f"{v['focus']['decay_scale']} vs {scale_up}")
    finally:
        _cleanup(d)


def test_delete_renormalizes_weights():
    """Deleting a random activity / an exercise renormalizes the surviving pool's weights to
    sum 1 (proportions kept); a pure edit that deletes nothing leaves weights untouched
    (requirement 5)."""
    t0 = 1_700_000_000.0
    d, _ = _new_env(t0)
    try:
        base = [{"id": "kafa", "name": "Kafa", "kind": "mandatory", "focus_boost": 50, "daily_limit": 3},
                {"id": "a", "name": "A", "kind": "random", "focus_boost": 10, "weight": 2},
                {"id": "b", "name": "B", "kind": "random", "focus_boost": 10, "weight": 1},
                {"id": "c", "name": "C", "kind": "random", "focus_boost": 10, "weight": 1}]
        focus.update_config({"focus_activities": base})
        v, _ = focus.update_config({"focus_activities": [
            {"id": "kafa", "name": "Kafa", "kind": "mandatory", "focus_boost": 50, "daily_limit": 3},
            {"id": "a", "name": "A", "kind": "random", "focus_boost": 10, "weight": 2},
            {"id": "b", "name": "B", "kind": "random", "focus_boost": 10, "weight": 1}]})   # delete c
        fa = {x["id"]: x for x in v["config"]["focus_activities"]}
        check("renorm: surviving random weights sum to 1 after a delete",
              _approx(fa["a"]["weight"] + fa["b"]["weight"], 1.0, 0.001),
              str((fa["a"]["weight"], fa["b"]["weight"])))
        check("renorm: 2:1 proportion preserved (0.667 / 0.333)",
              _approx(fa["a"]["weight"], 0.6667, 0.001) and _approx(fa["b"]["weight"], 0.3333, 0.001),
              str((fa["a"]["weight"], fa["b"]["weight"])))
        v, _ = focus.update_config({"focus_activities": [
            {"id": "kafa", "name": "Kafa", "kind": "mandatory", "focus_boost": 50, "daily_limit": 3},
            {"id": "a", "name": "A", "kind": "random", "focus_boost": 10, "weight": 5},
            {"id": "b", "name": "B", "kind": "random", "focus_boost": 10, "weight": 1}]})   # no delete
        fa = {x["id"]: x for x in v["config"]["focus_activities"]}
        check("renorm: a pure edit (no delete) leaves weights as sent",
              fa["a"]["weight"] == 5.0 and fa["b"]["weight"] == 1.0, str((fa["a"]["weight"], fa["b"]["weight"])))
        # exercises: same rule
        focus.update_config({"exercises": [
            {"id": "e1", "name": "E1", "weight": 2, "reps": 5},
            {"id": "e2", "name": "E2", "weight": 1, "reps": 5},
            {"id": "e3", "name": "E3", "weight": 1, "reps": 5}]})
        v, _ = focus.update_config({"exercises": [
            {"id": "e1", "name": "E1", "weight": 2, "reps": 5},
            {"id": "e2", "name": "E2", "weight": 1, "reps": 5}]})   # delete e3
        ex = {x["id"]: x for x in v["config"]["exercises"]}
        check("renorm: exercise delete renormalizes too (0.667 / 0.333)",
              _approx(ex["e1"]["weight"], 0.6667, 0.001) and _approx(ex["e2"]["weight"], 0.3333, 0.001),
              str((ex["e1"]["weight"], ex["e2"]["weight"])))
    finally:
        _cleanup(d)


def test_migration_muscle_and_flat_activities():
    """An OLD file (single-muscle exercises + flat {id,name,weight} focus_activities) loads,
    migrating muscles -> [{muscle,pct:100}] and activities to the two-class shape."""
    t0 = 1_700_000_000.0
    d, _ = _new_env(t0)
    try:
        legacy = {
            "config": {"enabled": True,
                       "intervals_min": {"water": 45, "stretch": 60, "exercise": 90},
                       "exercises": [{"id": "pushups", "name": "Sklekovi", "emoji": "",
                                      "weight": 1, "reps": 5, "target": 5, "muscle": "grudi",
                                      "stat": "snaga", "days_since_done": 0,
                                      "recent_actuals": [], "last_done": None}],
                       "focus": {"break_after_min": 90},
                       "coffee": {"enabled": True, "low_score_threshold": 40, "cooldown_min": 30},
                       "focus_activities": [{"id": "kafa", "name": "Kafa", "weight": 0.5},
                                            {"id": "sah", "name": "Šah 1min", "weight": 0.2},
                                            {"id": "setnja", "name": "Šetnja", "weight": 0.2},
                                            {"id": "disanje", "name": "Duboko disanje", "weight": 0.1}],
                       "snooze_min": 10,
                       "custom_reminders": [{"id": "mail", "name": "Mail", "interval_min": 30}]},
            "session": {"started_at": t0, "work_day": "2023-11-14", "paused": False,
                        "paused_at": None, "paused_total_sec": 0.0, "last_break_at": t0,
                        "last_exercise": None, "last_activity": None},
            "reminders": {"water": {"last_at": None, "next_at": t0 + 2700},
                          "stretch": {"last_at": None, "next_at": t0 + 3600},
                          "exercise": {"last_at": None, "next_at": t0 + 5400, "exercise": None},
                          "coffee": {"last_at": None, "activity": None}, "custom": {}},
            "metrics": {"water": {"count": 0, "today": 0}, "stretch": {"count": 0, "today": 0},
                        "exercise": {"count": 0, "reps_total": 0, "today_reps": 0, "by_id": {}},
                        "coffee": {"count": 0, "by_id": {}}, "streak_days": 0, "last_active_day": None},
            "focus": {"score": 100, "computed_at": t0}, "history": [],
        }
        focus.STATE_PATH.write_text(json.dumps(legacy), encoding="utf-8")
        v = focus.get_view()                          # must migrate, not crash
        e0 = v["config"]["exercises"][0]
        check("mig: single muscle -> [{muscle,pct:100}]",
              e0["muscles"] == [{"muscle": "grudi", "pct": 100}], str(e0.get("muscles")))
        check("mig: legacy muscle key dropped", "muscle" not in e0, str(e0))
        fa = {a["id"]: a for a in v["config"]["focus_activities"]}
        check("mig: kafa -> mandatory boost 50 limit 3",
              fa["kafa"]["kind"] == "mandatory" and fa["kafa"]["focus_boost"] == 50
              and fa["kafa"]["daily_limit"] == 3, str(fa["kafa"]))
        check("mig: sah -> random boost 15", fa["sah"]["kind"] == "random"
              and fa["sah"]["focus_boost"] == 15, str(fa["sah"]))
        check("mig: setnja -> random boost 20", fa["setnja"]["focus_boost"] == 20, str(fa["setnja"]))
        check("mig: decay_scale backfilled to 1.0", v["focus"]["decay_scale"] == 1.0, str(v["focus"]))
        check("mig: sound flags backfilled (all true)",
              v["config"]["sound"] == {"water": True, "stretch": True, "exercise": True, "coffee": True},
              str(v["config"].get("sound")))
        check("mig: custom reminder gets a default sound flag",
              v["config"]["custom_reminders"][0]["sound"] is True, str(v["config"]["custom_reminders"][0]))
    finally:
        _cleanup(d)


def test_sound_flags_roundtrip():
    """The per-reminder mute flags round-trip through config and reject a non-bool."""
    t0 = 1_700_000_000.0
    d, _ = _new_env(t0)
    try:
        v = focus.get_view()
        check("sound: defaults all true",
              v["config"]["sound"] == {"water": True, "stretch": True, "exercise": True, "coffee": True},
              str(v["config"].get("sound")))
        v, code = focus.update_config({"sound": {"water": False, "coffee": False}})
        s = v["config"]["sound"]
        check("sound: partial update merges", code == 200 and s["water"] is False
              and s["coffee"] is False and s["stretch"] is True, str(s))
        check("sound: non-bool value -> 400", focus.update_config({"sound": {"water": "yes"}})[1] == 400)
        check("sound: non-object -> 400", focus.update_config({"sound": []})[1] == 400)
        v, _ = focus.update_config({"custom_reminders": [
            {"id": "x", "name": "X", "interval_min": 30, "sound": False}]})
        check("sound: custom reminder sound stored", v["config"]["custom_reminders"][0]["sound"] is False,
              str(v["config"]["custom_reminders"][0]))
        v, _ = focus.update_config({"custom_reminders": [{"name": "Y", "interval_min": 30}]})
        check("sound: custom reminder sound defaults true", v["config"]["custom_reminders"][0]["sound"] is True,
              str(v["config"]["custom_reminders"][0]))
        check("sound: custom bad sound -> 400", focus.update_config({"custom_reminders": [
            {"name": "Z", "interval_min": 30, "sound": "x"}]})[1] == 400)
    finally:
        _cleanup(d)


def test_infocus_route_smoke_and_gated():
    d = _http_env()
    httpd, port = _start()
    try:
        code, body = _post(port, "/api/focus/infocus", {"type": "coffee"})
        out = json.loads(body.decode() or "{}")
        check("http: POST infocus coffee -> 200", code == 200, str(code))
        check("http: infocus returns the view with decay_scale",
              isinstance(out.get("reminders"), dict) and "decay_scale" in out.get("focus", {}),
              str(list(out.keys())))
        code, _b = _post(port, "/api/focus/infocus", {"type": "water"})
        check("http: infocus bad type -> 400", code == 400, str(code))
    finally:
        httpd.shutdown(); httpd.server_close(); _cleanup(d)


def test_infocus_route_gated():
    orig = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    d = _http_env()
    httpd, port = _start()
    try:
        code, _b = _post(port, "/api/focus/infocus", {"type": "coffee"})
        check("gate: infocus -> 403 for a non-local peer", code == 403, str(code))
    finally:
        server.Handler._client_is_local = orig
        httpd.shutdown(); httpd.server_close(); _cleanup(d)


# --------------------------------------------------------------------------- #
#  NEW: disabling a reminder = choosing a character class (per-category enable +
#  the RPG form/class it selects).
# --------------------------------------------------------------------------- #
def test_reminders_enabled_gates_due():
    """A disabled category NEVER goes due: water/stretch/exercise gate their interval
    reminder, 'fokus' gates the coffee/focus nudge. A bad boolean is a 400 and persists
    nothing; the default config carries all four toggles true."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        v = focus.get_view()
        check("cat-gate: default reminders_enabled all true",
              v["config"]["reminders_enabled"] == {"water": True, "stretch": True,
                                                    "exercise": True, "fokus": True},
              str(v["config"].get("reminders_enabled")))
        clk.advance(100 * MIN)                      # every interval elapsed + score low
        v = focus.get_view()
        check("cat-gate: all due when every category is enabled",
              all(v["reminders"][t]["due"] for t in ("water", "stretch", "exercise"))
              and v["reminders"]["coffee"]["due"] is True, str(v["reminders"]))
        v, code = focus.update_config({"reminders_enabled": {"water": False, "fokus": False}})
        check("cat-gate: config -> 200", code == 200, str(code))
        check("cat-gate: flags stored (others untouched)",
              v["config"]["reminders_enabled"]["water"] is False
              and v["config"]["reminders_enabled"]["fokus"] is False
              and v["config"]["reminders_enabled"]["stretch"] is True, str(v["config"]["reminders_enabled"]))
        check("cat-gate: disabled water never due", v["reminders"]["water"]["due"] is False)
        check("cat-gate: disabled fokus -> coffee never due", v["reminders"]["coffee"]["due"] is False)
        check("cat-gate: still-enabled stretch/exercise remain due",
              v["reminders"]["stretch"]["due"] is True and v["reminders"]["exercise"]["due"] is True)
        v, _ = focus.update_config({"reminders_enabled": {"water": True, "fokus": True}})
        check("cat-gate: re-enabled water due again", v["reminders"]["water"]["due"] is True)
        check("cat-gate: re-enabled fokus -> coffee due again", v["reminders"]["coffee"]["due"] is True)
        check("cat-gate: bad boolean -> 400",
              focus.update_config({"reminders_enabled": {"water": "yes"}})[1] == 400)
        check("cat-gate: non-object -> 400", focus.update_config({"reminders_enabled": []})[1] == 400)
        check("cat-gate: a rejected patch persists nothing",
              focus.get_view()["config"]["reminders_enabled"]["water"] is True)
        check("cat-gate: unknown category key is ignored (not a 400)",
              focus.update_config({"reminders_enabled": {"bogus": False}})[1] == 200)
    finally:
        _cleanup(d)


def test_snooze_never_gates_reminder_firing():
    """Firing is gated by config.enabled / reminders_enabled / pause / day_ended and the
    timer -- NEVER by the snooze setting. snooze() only PUSHES a due reminder out (a delay),
    so a reminder must fire with snooze at its minimum and identically across every snooze
    value. Pins the inverse of a reported (mis)diagnosis that "reminders only fire when the
    snooze option is on": there is no snooze-enable flag, and there must never be one."""
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        focus.update_config({"intervals_min": {"water": 1}})
        focus.update_config({"snooze_min": 1})           # snooze at the MINIMUM the config allows
        clk.advance(61)
        check("snooze-gate: fires with snooze_min at the minimum (1)",
              focus.get_view()["reminders"]["water"]["due"] is True)
        # Firing is INDEPENDENT of the snooze value (min 1 vs the 1440 cap) ...
        for sm in (1, 10, 1440):
            focus.ack("water")
            focus.update_config({"snooze_min": sm})
            clk.advance(61)
            check("snooze-gate: fires regardless of snooze_min=%d" % sm,
                  focus.get_view()["reminders"]["water"]["due"] is True, str(sm))
        # ... and a hand-edited snooze_min=0 (which _snooze_min treats as unset) never blocks.
        st = focus.load_state(); st["config"]["snooze_min"] = 0; focus.save_state(st)
        focus.ack("water"); clk.advance(61)
        check("snooze-gate: fires with a hand-edited snooze_min=0",
              focus.get_view()["reminders"]["water"]["due"] is True)
        # Presence pair so the assertions cannot pass vacuously: the REAL gate (config.enabled)
        # DOES block and re-enabling restores -- something gates firing, just never snooze.
        focus.update_config({"enabled": False})
        check("snooze-gate: enabled=False blocks the due reminder",
              focus.get_view()["reminders"]["water"]["due"] is False)
        focus.update_config({"enabled": True})
        check("snooze-gate: re-enabling fires it again",
              focus.get_view()["reminders"]["water"]["due"] is True)
        # snooze() is DELAY-only: it suppresses a currently-due reminder, then it re-fires.
        v, _ = focus.snooze("water")
        check("snooze-gate: snooze() suppresses the due reminder (delay, not a prerequisite)",
              v["reminders"]["water"]["due"] is False)
        clk.advance(focus._snooze_min(focus.load_state()) * MIN + 5)
        check("snooze-gate: the reminder re-fires after the snooze window",
              focus.get_view()["reminders"]["water"]["due"] is True)
    finally:
        _cleanup(d)


def _re_hist(now):
    """A two-day history with strong exercise/water/stretch/focus, for the neutral-base
    and vitals tests."""
    return [
        _sess_report("2026-05-01", hours_worked=5, commits=10, water=6, stretch=4,
                     exercise_total=300, exercise_sets=12, exercise_by_id={"pushups": 300},
                     focus_score=85, output_tokens=500000),
        _sess_report("2026-05-02", hours_worked=4, commits=6, water=5, stretch=3,
                     exercise_total=200, exercise_sets=8, exercise_by_id={"pushups": 200},
                     focus_score=70),
    ]


def _re_exs(days_since_done=1):
    return [{"id": "pushups", "name": "Sklekovi", "weight": 1, "reps": 10, "target": 10,
             "muscle": "grudi", "stat": "snaga", "days_since_done": days_since_done,
             "recent_actuals": [], "last_done": "2026-05-02"}]


def test_disabled_wellness_dim_pins_to_neutral_base():
    """A disabled dim's stat == NEUTRAL_BASE, appears in stats_base, and (exercise off)
    every muscle sits at the neutral baseline, not 0. The enabled map is surfaced."""
    now = _ts(2026, 5, 3, h=18)
    d, _ = _new_env(now)
    try:
        _seed(now, _re_hist(now), exercises=_re_exs(), streak=5)     # all enabled
        base, _ = focus.rpg()
        check("neutral: baseline snaga trained above the neutral base",
              base["stats"]["snaga"] > focus.NEUTRAL_BASE, str(base["stats"]["snaga"]))
        check("neutral: stats_base empty when all enabled", base["stats_base"] == [], str(base["stats_base"]))
        check("neutral: baseline muscles developed (> the neutral baseline)",
              base["muscles"]["grudi"] > focus.MUSCLE_NEUTRAL_BASE, str(base["muscles"]["grudi"]))
        _seed(now, _re_hist(now), exercises=_re_exs(), streak=5,
              reminders_enabled={"exercise": False})
        r, _ = focus.rpg()
        check("neutral: disabled exercise pins snaga to NEUTRAL_BASE",
              r["stats"]["snaga"] == focus.NEUTRAL_BASE, str(r["stats"]["snaga"]))
        check("neutral: snaga is listed in stats_base", r["stats_base"] == ["snaga"], str(r["stats_base"]))
        check("neutral: muscles all sit at the neutral baseline (0.4), not 0",
              all(r["muscles"][m] == focus.MUSCLE_NEUTRAL_BASE for m in focus.MUSCLES), str(r["muscles"]))
        check("neutral: enabled map surfaced", r["enabled"]["exercise"] is False, str(r["enabled"]))
        check("neutral: an always-derived dim (intelekt) is NOT pinned",
              r["stats"]["intelekt"] != focus.NEUTRAL_BASE or "intelekt" not in r["stats_base"],
              str(r["stats"]["intelekt"]))
        # water off -> izdrzljivost pinned; two toggles off -> two stats_base
        _seed(now, _re_hist(now), exercises=_re_exs(), streak=5,
              reminders_enabled={"water": False, "stretch": False})
        r2, _ = focus.rpg()
        check("neutral: water off pins izdrzljivost, stretch off pins spretnost",
              r2["stats"]["izdrzljivost"] == focus.NEUTRAL_BASE
              and r2["stats"]["spretnost"] == focus.NEUTRAL_BASE, str(r2["stats"]))
        check("neutral: stats_base lists both (config order)",
              r2["stats_base"] == ["izdrzljivost", "spretnost"], str(r2["stats_base"]))
    finally:
        _cleanup(d)


def test_disabled_dim_excluded_from_decay():
    """No 'use it or lose it' for a dimension you turned off: an exercise idle far past the
    grace would normally decay snaga/grudi, but a disabled exercise holds them at neutral."""
    now = _ts(2026, 5, 20, h=18)
    hist = [_sess_report("2026-05-01", exercise_total=300, exercise_sets=12,
                         exercise_by_id={"pushups": 300}, hours_worked=4, focus_score=80)]
    d, _ = _new_env(now)
    try:
        # enabled + long idle -> snaga/grudi decay below their fresh value
        _seed(now, hist, exercises=_re_exs(days_since_done=15), streak=1)
        on, _ = focus.rpg()
        _seed(now, hist, exercises=_re_exs(days_since_done=15), streak=1,
              reminders_enabled={"exercise": False})
        off, _ = focus.rpg()
        check("decay-off: disabled snaga == NEUTRAL_BASE despite 15 idle days",
              off["stats"]["snaga"] == focus.NEUTRAL_BASE, str(off["stats"]["snaga"]))
        check("decay-off: disabled muscles == the baseline despite idleness",
              off["muscles"]["grudi"] == focus.MUSCLE_NEUTRAL_BASE, str(off["muscles"]["grudi"]))
        check("decay-off: the neutral base is unaffected by idleness (enabled would decay)",
              on["muscles"]["grudi"] != off["muscles"]["grudi"]
              or on["stats"]["snaga"] != off["stats"]["snaga"],
              f"on={on['stats']['snaga']}/{on['muscles']['grudi']}")
    finally:
        _cleanup(d)


def test_vitals_renormalize_over_enabled_inputs():
    """A vital averages over its ENABLED inputs only; with none enabled it rests at the
    neutral 0.5 rather than being dragged down by a disabled input's low value."""
    now = _ts(2026, 5, 3, h=18)
    d, _ = _new_env(now)
    try:
        # A day with zero deep-work (focus 0) but good wellness.
        hist = [_sess_report("2026-05-02", hours_worked=2.0, water=10, stretch=8,
                             exercise_total=40, exercise_sets=8, exercise_by_id={"pushups": 40},
                             focus_score=0, pauses=3)]
        _seed(now, hist)                                        # all enabled
        base, _ = focus.rpg()
        check("vitals: fokus enabled -> mana derived low (focus_score 0)",
              base["vitals"]["mana"] < focus.VITAL_NEUTRAL, str(base["vitals"]))
        _seed(now, hist, reminders_enabled={"fokus": False})   # mana has no enabled input
        r, _ = focus.rpg()
        check("vitals: fokus off -> mana rests at neutral 0.5 (not dragged to 0)",
              r["vitals"]["mana"] == focus.VITAL_NEUTRAL, str(r["vitals"]))
        _seed(now, hist, reminders_enabled={"water": False, "stretch": False, "exercise": False})
        r2, _ = focus.rpg()
        check("vitals: all hp inputs off -> hp rests at neutral 0.5",
              r2["vitals"]["hp"] == focus.VITAL_NEUTRAL, str(r2["vitals"]))
    finally:
        _cleanup(d)


def test_vitals_disabled_input_does_not_drag_down():
    """A disabled input must not DRAG a vital down: with high water/stretch but no exercise,
    disabling exercise drops the 0-movement term and RAISES hp."""
    now = _ts(2026, 5, 3, h=18)
    d, _ = _new_env(now)
    try:
        hist = [_sess_report("2026-05-02", hours_worked=1.0, water=20, stretch=20,
                             exercise_total=0, exercise_sets=0, focus_score=50)]
        _seed(now, hist)                                       # all enabled (movement adh = 0)
        base, _ = focus.rpg()
        _seed(now, hist, reminders_enabled={"exercise": False})
        off, _ = focus.rpg()
        check("vitals-drag: disabling exercise raises hp (0-movement no longer averaged in)",
              off["vitals"]["hp"] > base["vitals"]["hp"],
              f"{off['vitals']['hp']} vs {base['vitals']['hp']}")
    finally:
        _cleanup(d)


def test_character_class_and_form_by_combo():
    """The FORM + CLASS the enabled combo selects, computed authoritatively in the backend."""
    now = _ts(2026, 5, 3, h=18)

    def klass(enabled):
        d, _ = _new_env(now)
        try:
            _seed(now, [], reminders_enabled=enabled)
            r, _ = focus.rpg()
            return r["form"], r["char_class"], r["class_sub"]
        finally:
            _cleanup(d)

    check("class: all on -> human / Kodni Vitez",
          klass({}) == ("human", "Kodni Vitez", "balansiran build"))
    check("class: exercise off + water only -> slime / Sluz",
          klass({"exercise": False, "stretch": False, "fokus": False})
          == ("slime", "Sluz", "samo hidracija"))
    check("class: water + fokus (exercise off) -> meduza / Meduza",
          klass({"exercise": False, "stretch": False})
          == ("meduza", "Meduza", "voda + um, bez tela"))
    check("class: fokus only (exercise off) -> wisp / Duh",
          klass({"exercise": False, "water": False, "stretch": False})
          == ("wisp", "Duh", "čist um"))
    check("class: stretch only (exercise off) -> skelet / Gipki Kostur",
          klass({"exercise": False, "water": False, "fokus": False})
          == ("skelet", "Gipki Kostur", "pokretljiv, bez mišića"))
    check("class: none but rad (all off) -> iskra / Iskra",
          klass({"water": False, "stretch": False, "exercise": False, "fokus": False})
          == ("iskra", "Iskra", "samo rad"))
    # a few of the human sub-classes
    check("class: exercise on, water off -> human / Golem",
          klass({"water": False})[:2] == ("human", "Golem"))
    check("class: exercise on, fokus off -> human / Atleta",
          klass({"fokus": False})[:2] == ("human", "Atleta"))
    check("class: exercise on, stretch off -> human / Tenk",
          klass({"stretch": False})[:2] == ("human", "Tenk"))


def test_migration_reminders_enabled_backfilled():
    """A state file predating config.reminders_enabled loads and backfills the four toggles
    all-true, and rpg() reads a migrated file as fully enabled."""
    t0 = 1_700_000_000.0
    d, _ = _new_env(t0)
    try:
        legacy = {
            "config": {"enabled": True,
                       "intervals_min": {"water": 45, "stretch": 60, "exercise": 90},
                       "exercises": [{"id": "pushups", "name": "Sklekovi", "emoji": "",
                                      "weight": 1, "reps": 5, "target": 5, "muscle": "grudi",
                                      "stat": "snaga"}],
                       "focus": {"break_after_min": 90},
                       "coffee": {"enabled": True, "low_score_threshold": 40, "cooldown_min": 30}},
            "session": {"started_at": t0, "work_day": "2023-11-14", "paused": False,
                        "paused_at": None, "paused_total_sec": 0.0, "last_break_at": t0},
            "reminders": {"water": {"last_at": None, "next_at": t0 + 2700},
                          "stretch": {"last_at": None, "next_at": t0 + 3600},
                          "exercise": {"last_at": None, "next_at": t0 + 5400, "exercise": None},
                          "coffee": {"last_at": None, "activity": None}, "custom": {}},
            "metrics": {"water": {"count": 0, "today": 0}, "stretch": {"count": 0, "today": 0},
                        "exercise": {"count": 0, "reps_total": 0, "today_reps": 0, "by_id": {}},
                        "streak_days": 0, "last_active_day": None},
            "focus": {"score": 100, "computed_at": t0}, "history": [],
        }
        focus.STATE_PATH.write_text(json.dumps(legacy), encoding="utf-8")
        v = focus.get_view()                          # must migrate, not crash
        check("mig-re: reminders_enabled backfilled all true",
              v["config"]["reminders_enabled"] == {"water": True, "stretch": True,
                                                   "exercise": True, "fokus": True},
              str(v["config"].get("reminders_enabled")))
        r, _ = focus.rpg()
        check("mig-re: rpg reads a migrated file as fully enabled",
              r["enabled"] == {"water": True, "stretch": True, "exercise": True, "fokus": True},
              str(r["enabled"]))
        check("mig-re: a migrated file is human / Kodni Vitez",
              r["form"] == "human" and r["char_class"] == "Kodni Vitez", str((r["form"], r["char_class"])))
    finally:
        _cleanup(d)


def main():
    for fn in (test_default_state_shape, test_score_falls_with_time_since_break,
               test_activity_bonus_is_a_light_neutral_nudge,
               test_pause_freezes_and_excludes_elapsed,
               test_pause_counts_as_break_and_restores_score,
               test_ack_logs_metric_and_reschedules,
               test_ack_exercise_uses_value_or_default, test_ack_rejects_bad_input,
               test_skip_reschedules_without_logging, test_coffee_due_then_cooldown,
               test_due_respects_enabled_and_pause,
               test_config_merge_reschedule_and_validation,
               test_streak_increments_and_breaks,
               test_corrupt_state_degrades_disabled_not_crash,
               test_exercise_weighting_holds, test_exercise_no_immediate_repeat,
               test_exercise_no_repeat_through_view_flow,
               test_exercises_config_accept_and_reject,
               test_ack_logs_against_picked_exercise,
               test_legacy_pushups_state_migrates,
               test_focus_routes_smoke, test_focus_wiring_and_gating,
               test_downtime_excluded_from_elapsed, test_snooze_reschedules_and_counts,
               test_work_blocks_increment_on_resume, test_end_day_and_new_day,
               test_custom_reminders_schedule_ack_snooze_skip,
               test_config_snooze_custom_coffee_validation,
               test_workday_migration_of_old_file,
               test_profile_radna_vs_cela_nedelja_math, test_profile_streaks,
               test_profile_route_smoke, test_new_focus_routes_gated,
               test_two_sessions_one_day_collapse_in_profile,
               test_exercise_target_progression_math,
               test_exercise_progression_once_per_day,
               test_progression_preserved_across_config_edit,
               test_focus_activity_pool_fires_and_counts,
               test_focus_activity_no_immediate_repeat,
               test_focus_activities_config_accept_and_reject,
               test_exercise_muscle_stat_autosuggest_and_validation,
               test_swap_rerolls_pick, test_swap_two_items_allows_previous,
               test_swap_route_smoke_and_gated, test_swap_route_gated,
               test_session_concurrency_math_pure,
               test_end_day_stores_parallelism_and_external_fields,
               test_productivity_grouping_and_derived_math,
               test_productivity_date_lookup_and_range_filter,
               test_productivity_highlights_pick_the_right_date,
               test_rpg_derivation_from_seeded_history,
               test_rpg_decay_engages_after_three_idle_days,
               test_rpg_gains_today_from_live_day,
               test_productivity_and_rpg_routes,
               test_productivity_rpg_reads_open_on_lan,
               test_rpg_reps_split_across_muscles_by_pct,
               test_focus_boost_bumps_and_decays,
               test_mandatory_quota_spread_and_random_fallback,
               test_mandatory_missed_recorded_at_day_end,
               test_infocus_defers_and_calibrates_decay,
               test_delete_renormalizes_weights,
               test_migration_muscle_and_flat_activities,
               test_sound_flags_roundtrip,
               test_infocus_route_smoke_and_gated, test_infocus_route_gated,
               test_reminders_enabled_gates_due,
               test_snooze_never_gates_reminder_firing,
               test_disabled_wellness_dim_pins_to_neutral_base,
               test_disabled_dim_excluded_from_decay,
               test_vitals_renormalize_over_enabled_inputs,
               test_vitals_disabled_input_does_not_drag_down,
               test_character_class_and_form_by_combo,
               test_migration_reminders_enabled_backfilled):
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
