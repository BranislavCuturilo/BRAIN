#!/usr/bin/env python3
"""Offline tests for the idle-game economy (idlegame.py + the /api/game routes in
server.py), and the two read-only focus accessors the game reads (focus.character_level
/ focus.hours_today).

Two layers, mirroring test_focus.py:
  * PURE logic over idlegame.py with a frozen clock (idlegame._now) and a temp state file
    (idlegame.STATE_PATH) -- timestamp accrual + the commit/checkpoint discipline, the tool
    window, the shop registry + price growth, daily Collect, the toggle, presence-driven
    defense, the Level read, and graceful degradation on a corrupt file.
  * HTTP smoke over a loopback server -- the GET returns the frozen contract shape, the three
    writes are loopback + same-origin gated (a forged Origin is 403), and a malformed body 400s.

No network, no credentials. Output is ASCII (the Windows cp1252 console cannot encode a
non-ASCII char and would kill the run mid-way). Run:  python test_idlegame.py
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

import idlegame    # noqa: E402
import focus       # noqa: E402
import quiz        # noqa: E402   (the pure F2 scheduler idlegame composes)
import quizbank    # noqa: E402   (server threads the bank into idlegame)
import server      # noqa: E402   (imports the SAME idlegame/focus module objects)

_results = []
_IG_ORIG_NOW = idlegame._now
_IG_ORIG_PATH = idlegame.STATE_PATH
_FOCUS_ORIG_NOW = focus._now
_FOCUS_ORIG_PATH = focus.STATE_PATH
_BANK_ORIG_PATH = quizbank.BANK_PATH


def _served_correct_label(question, seen=0):
    """The SERVED label (A-D) that maps to the stored correct option for a serving of
    `question` at `seen` — how a test 'answers correctly' without the answer ever being
    in a payload (grading is server-side)."""
    correct_stored = next(o["key"] for o in question["options"] if o.get("correct"))
    _served, key_map = quiz.shuffle_options(question, quiz.option_seed(question["id"], seen))
    return next(lbl for lbl, stored in key_map.items() if stored == correct_stored)


def _served_wrong_label(question, seen=0):
    correct_stored = next(o["key"] for o in question["options"] if o.get("correct"))
    _served, key_map = quiz.shuffle_options(question, quiz.option_seed(question["id"], seen))
    return next(lbl for lbl, stored in key_map.items() if stored != correct_stored)


def _bank_q(qid, tier=1, tags=None, correct_key="A"):
    opts = []
    for k in ("A", "B", "C", "D"):
        if k == correct_key:
            opts.append({"key": k, "text": f"{qid}-{k}", "correct": True})
        else:
            opts.append({"key": k, "text": f"{qid}-{k}", "misconception": f"m-{k}",
                         "why_wrong": f"why-{k}"})
    return {"id": qid, "tags": tags or ["t"], "tier": tier, "stem": f"stem {qid}?",
            "options": opts, "explanation": f"expl {qid}", "learn_more": None,
            "stem_norm": qid, "retired": False}


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


class Clock:
    """A frozen, advanceable clock installed as idlegame._now for the pure tests."""
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def _ts(y, mo, d, h=12):
    return datetime(y, mo, d, h, 0, 0).timestamp()


def _new_env(now_ts=None, freeze=True):
    """A fresh temp game-state file, and (by default) a frozen clock at now_ts."""
    d = tempfile.mkdtemp(prefix="game_test_")
    idlegame.STATE_PATH = Path(d) / "game_state.json"
    if freeze:
        clk = Clock(now_ts if now_ts is not None else time.time())
        idlegame._now = clk
        return d, clk
    idlegame._now = _IG_ORIG_NOW
    return d, None


def _cleanup(d):
    idlegame.STATE_PATH = _IG_ORIG_PATH
    idlegame._now = _IG_ORIG_NOW
    shutil.rmtree(d, ignore_errors=True)


def _sig(live=0, tool_ts=None, session_ids=None, level=None, hours_today=0.0):
    return {"live_sessions": live, "tool_ts": tool_ts or [],
            "session_ids": session_ids or [],
            "level": level or {"level": 0, "xp": 0.0, "points": 0.0},
            "hours_today": hours_today}


def _approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# --------------------------------------------------------------------------- #
#  State shape + persistence
# --------------------------------------------------------------------------- #
def test_default_state_shape():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        v = idlegame.get_view(_sig())
        check("default: enabled fresh state", v["config"]["enabled"] is True)
        check("default: tokens start at 0", v["economy"]["tokens"] == 0, str(v["economy"]))
        check("default: shield 0, hp full", v["defense"]["shield"] == 0
              and v["defense"]["hp"] == v["defense"]["hp_max"], str(v["defense"]))
        check("default: caps at base", v["defense"]["shield_max"] == idlegame.SHIELD_MAX_BASE
              and v["defense"]["hp_max"] == idlegame.HP_MAX_BASE, str(v["defense"]))
        check("default: shop lists every catalog id",
              [s["id"] for s in v["shop"]] == [i["id"] for i in idlegame.SHOP_CATALOG],
              str([s["id"] for s in v["shop"]]))
        check("default: collect available on a fresh day", v["collect"]["available"] is True)
    finally:
        _cleanup(d)


def test_corrupt_state_degrades_disabled_not_crash():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        idlegame.STATE_PATH.write_text("{ this is not valid json", encoding="utf-8")
        v = idlegame.get_view(_sig(live=3))          # must not raise
        check("corrupt: degrades to disabled-but-present", v["config"]["enabled"] is False)
        check("corrupt: nothing accrues while disabled", v["economy"]["tokens"] == 0, str(v["economy"]))
        check("corrupt: the unreadable file is NOT overwritten by a read",
              idlegame.STATE_PATH.read_text(encoding="utf-8").startswith("{ this is not"))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Accrual — timestamp based, commit-on-mutation (the crux)
# --------------------------------------------------------------------------- #
def test_accrual_advances_only_on_commit():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        idlegame.get_view(_sig(live=2))              # init -> anchor persisted at t0
        clk.advance(10)                              # < CHECKPOINT_SECS (30): no checkpoint
        v = idlegame.get_view(_sig(live=2))
        check("accrual: pending shown in the display (not zero)", v["economy"]["tokens"] >= 1,
              str(v["economy"]))
        persisted = idlegame.load_state()["economy"]["last_accrual_at"]
        check("accrual: a non-checkpoint poll does NOT advance the persisted anchor",
              _approx(persisted, t0), str(persisted))
        idlegame.toggle(True, _sig(live=2))          # a mutation commits pending
        after = idlegame.load_state()
        check("accrual: the mutation advanced the persisted anchor to now",
              _approx(after["economy"]["last_accrual_at"], t0 + 10),
              str(after["economy"]["last_accrual_at"]))
        expect = idlegame.PASSIVE_RATE * min(2, idlegame.SESSION_CAP) * 10
        check("accrual: committed tokens match PASSIVE_RATE * live * span",
              _approx(after["economy"]["tokens"], expect, tol=1e-3),
              f'{after["economy"]["tokens"]} vs {expect}')
    finally:
        _cleanup(d)


def test_one_hour_gap_credits_at_most_max_span():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        idlegame.get_view(_sig(live=1))              # anchor at t0
        clk.advance(3600)                            # a slept laptop: 1 hour with the game open
        v, code = idlegame.toggle(True, _sig(live=1))    # commit
        cap = idlegame.PASSIVE_RATE * 1 * idlegame.MAX_ACCRUAL_SPAN
        check("span: commit -> 200", code == 200, str(code))
        check("span: a 1-hour gap credits at most MAX_ACCRUAL_SPAN worth (no phantom hours)",
              0 < v["economy"]["tokens"] <= cap + 1e-6, f'{v["economy"]["tokens"]} vs cap {cap}')
    finally:
        _cleanup(d)


def test_tool_bump_counts_only_in_window_events():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        idlegame.get_view(_sig())                    # anchor at t0
        clk.advance(10)                              # now = t0 + 10
        # live=0 isolates the tool bump from passive; boundaries: t0 (==anchor, excluded),
        # t0+10 (==now, included), t0+20 (future, excluded), t0-5 (before anchor, excluded).
        tool_ts = [t0 - 5, t0, t0 + 3, t0 + 7, t0 + 10, t0 + 20]
        v, _ = idlegame.toggle(True, _sig(live=0, tool_ts=tool_ts))
        expect = idlegame.TOOL_BONUS * 3
        # the view floors tokens to an int for display; assert on the persisted float
        check("toolbump: only the 3 in-window events credit",
              _approx(idlegame.load_state()["economy"]["tokens"], expect, tol=1e-6),
              f'{idlegame.load_state()["economy"]["tokens"]} vs {expect}')
        check("toolbump: stats.tools_credited counts exactly the in-window events",
              v["stats"]["tools_credited"] == 3, str(v["stats"]))
    finally:
        _cleanup(d)


def test_disabled_game_does_not_accrue():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        idlegame.toggle(False, _sig())               # disable
        clk.advance(20)
        v = idlegame.get_view(_sig(live=5, tool_ts=[t0 + 5, t0 + 10]))
        check("disabled: no passive/tool income while off", v["economy"]["tokens"] == 0,
              str(v["economy"]))
        check("disabled: rate_per_min reads 0 while off", v["economy"]["rate_per_min"] == 0.0,
              str(v["economy"]))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Shop — registry key, real spend, price growth, derived effects
# --------------------------------------------------------------------------- #
def _seed_tokens(now, amount):
    st = idlegame.default_state(now=now)
    st["economy"]["tokens"] = float(amount)
    idlegame.save_state(st)


def test_buy_unknown_id_is_400():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        _seed_tokens(t0, 10_000)
        res, code = idlegame.buy("does_not_exist", _sig())
        check("buy: unknown id -> 400 (registry key, never dynamic resolution)", code == 400,
              str(code))
        check("buy: unknown id returns an error, not a view", res.get("error") == "unknown item",
              str(res))
        res, code = idlegame.buy(None, _sig())
        check("buy: non-string id -> 400", code == 400, str(code))
    finally:
        _cleanup(d)


def test_buy_spends_tokens_and_price_grows():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        _seed_tokens(t0, 1000)
        item = idlegame._CATALOG_BY_ID["tool_grep"]
        p0 = idlegame._price(item, 0)
        v, code = idlegame.buy("tool_grep", _sig())
        check("buy: -> 200", code == 200, str(code))
        check("buy: real tokens spent", v["economy"]["tokens"] == 1000 - p0, str(v["economy"]))
        entry = next(s for s in v["shop"] if s["id"] == "tool_grep")
        check("buy: owned incremented", entry["owned"] == 1, str(entry))
        p1 = idlegame._price(item, 1)
        check("buy: the next copy costs more (price_growth)", entry["price"] == p1 and p1 > p0,
              f"{entry['price']} / {p1} vs {p0}")
        v, _ = idlegame.buy("tool_grep", _sig())
        check("buy: second buy spends the grown price",
              v["economy"]["tokens"] == 1000 - p0 - p1, str(v["economy"]))
    finally:
        _cleanup(d)


def test_buy_insufficient_tokens_is_400():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        _seed_tokens(t0, 5)                          # far below any base_price
        res, code = idlegame.buy("subagent_worker", _sig())
        check("buy: insufficient tokens -> 400", code == 400, str(code))
        check("buy: insufficient returns the documented error",
              res.get("error") == "insufficient tokens", str(res))
        check("buy: nothing spent on a refused buy",
              idlegame.load_state()["economy"]["tokens"] == 5, str(idlegame.load_state()["economy"]))
    finally:
        _cleanup(d)


def test_buy_shield_upgrade_raises_cap_and_subagent_raises_rate():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        _seed_tokens(t0, 1000)
        base_rate = idlegame.get_view(_sig(live=1))["economy"]["rate_per_min"]
        v, _ = idlegame.buy("skill_shield", _sig())
        amt = idlegame._CATALOG_BY_ID["skill_shield"]["effect"]["amount"]
        check("effect: shield_max upgrade raises the cap",
              v["defense"]["shield_max"] == idlegame.SHIELD_MAX_BASE + amt, str(v["defense"]))
        v, _ = idlegame.buy("subagent_scout", _sig(live=1))
        after_rate = idlegame.get_view(_sig(live=1))["economy"]["rate_per_min"]
        check("effect: a subagent upgrade raises the passive rate (grows-faster)",
              after_rate > base_rate, f"{after_rate} vs {base_rate}")
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Daily Collect — once per day, capped multiplier, missing a day forfeits nothing
# --------------------------------------------------------------------------- #
def test_collect_once_per_day_then_refused():
    d, clk = _new_env(_ts(2026, 5, 1))
    try:
        v, code = idlegame.collect(0.0, _sig(hours_today=0.0))
        check("collect: -> 200", code == 200, str(code))
        check("collect: base reward at 0 hours is DAILY_BASE",
              v["economy"]["tokens"] == int(round(idlegame.DAILY_BASE)), str(v["economy"]))
        check("collect: no longer available after collecting", v["collect"]["available"] is False)
        res, code = idlegame.collect(8.0, _sig(hours_today=8.0))
        check("collect: a second collect the same day -> 409", code == 409, str(code))
        check("collect: 409 carries the documented error",
              res.get("error") == "already collected today", str(res))
    finally:
        _cleanup(d)


def test_collect_thresholds_pay_the_capped_multiplier():
    for hours, mult in ((0.0, idlegame.COLLECT_BASE_MULT), (4.0, 1.5), (8.0, 2.0),
                        (12.0, 2.5), (100.0, 2.5)):     # 100h is capped at the 12h tier
        d, _clk = _new_env(_ts(2026, 5, 2))
        try:
            v, _ = idlegame.collect(hours, _sig(hours_today=hours))
            expect = int(round(idlegame.DAILY_BASE * mult))
            check(f"collect: {hours}h pays DAILY_BASE x {mult}",
                  v["economy"]["tokens"] == expect, f'{v["economy"]["tokens"]} vs {expect}')
        finally:
            _cleanup(d)


def test_missing_a_day_forfeits_nothing():
    d, clk = _new_env(_ts(2026, 5, 3))
    try:
        v, _ = idlegame.collect(0.0, _sig())
        day1 = v["economy"]["tokens"]
        clk.t = _ts(2026, 5, 6)                       # skipped 5-04 and 5-05 entirely
        check("forfeit: a new day makes Collect available again",
              idlegame.get_view(_sig())["collect"]["available"] is True)
        v, code = idlegame.collect(0.0, _sig())
        check("forfeit: collecting after a gap succeeds (nothing lost)", code == 200, str(code))
        check("forfeit: the earlier balance is intact + the new reward added",
              v["economy"]["tokens"] == day1 + int(round(idlegame.DAILY_BASE)),
              str(v["economy"]))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Toggle
# --------------------------------------------------------------------------- #
def test_toggle_validates_bool():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        check("toggle: non-bool string -> 400", idlegame.toggle("yes", _sig())[1] == 400)
        check("toggle: int 1 (not a bool) -> 400", idlegame.toggle(1, _sig())[1] == 400)
        check("toggle: None -> 400", idlegame.toggle(None, _sig())[1] == 400)
        v, code = idlegame.toggle(False, _sig())
        check("toggle: bool False -> 200 and disables", code == 200
              and v["config"]["enabled"] is False, str(v["config"]))
        v, _ = idlegame.toggle(True, _sig())
        check("toggle: re-enable", v["config"]["enabled"] is True)
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Defense — presence-driven, non-punitive (pure math + a commit path)
# --------------------------------------------------------------------------- #
def test_defense_step_math():
    smax, hmax = 100, 130
    # presence (live + a recent tool) regenerates the shield toward shield_max
    s, h = idlegame._defense_step(0.0, 100.0, smax, hmax, 10.0, 1, True, True)
    check("defense: presence regenerates the shield",
          _approx(s, idlegame.SHIELD_REGEN_RATE * 10), str(s))
    check("defense: hp regenerates toward hp_max (no damage in F1)",
          _approx(h, min(hmax, 100.0 + idlegame.HP_REGEN_RATE * 10)), str(h))
    # live but idle (no recent tool) drains the shield gently, clamped at 0
    s, _ = idlegame._defense_step(50.0, 100.0, smax, hmax, 10.0, 1, False, True)
    check("defense: active-session idleness drains the shield gently",
          _approx(s, 50.0 - idlegame.SHIELD_DRAIN_RATE * 10), str(s))
    # nothing live -> the shield HOLDS (days off cost nothing)
    s, _ = idlegame._defense_step(50.0, 100.0, smax, hmax, 10.0, 0, False, True)
    check("defense: nothing live -> shield holds (never drains on a day off)",
          _approx(s, 50.0), str(s))
    # disabled -> no change
    s, h = idlegame._defense_step(50.0, 100.0, smax, hmax, 10.0, 1, True, False)
    check("defense: disabled -> bars hold", _approx(s, 50.0) and _approx(h, 100.0), f"{s},{h}")


def test_defense_regen_through_commit():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        idlegame.get_view(_sig(live=1))              # anchor defense at t0
        clk.advance(20)
        v, _ = idlegame.toggle(True, _sig(live=1, tool_ts=[t0 + 18]))  # presence -> commit regens
        check("defense: shield rose from presence over the committed span",
              v["defense"]["shield"] > 0, str(v["defense"]))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Level — one identity, read from the RPG character
# --------------------------------------------------------------------------- #
def test_level_read_matches_focus_character_level():
    t0 = _ts(2026, 6, 1)
    d, clk = _new_env(t0)
    fd = tempfile.mkdtemp(prefix="game_focus_")
    focus.STATE_PATH = Path(fd) / "focus_state.json"
    focus._now = clk
    try:
        st = focus.default_state(now=t0)
        st["session"]["day_ended"] = True            # today's live bundle excluded -> deterministic
        st["history"] = [{"work_day": "2026-01-01", "hours_worked": 10.0, "commits": 20}]
        focus.save_state(st)

        lvl = focus.character_level()
        rpg, _c = focus.rpg()
        check("level: character_level matches rpg's level/xp/points (one identity)",
              lvl["level"] == rpg["level"] and _approx(lvl["xp"], rpg["xp"], tol=1e-4)
              and _approx(lvl["points"], rpg["points"], tol=1e-2),
              f"{lvl} vs {(rpg['level'], rpg['xp'], rpg['points'])}")
        check("level: seeded history reaches level > 1", lvl["level"] > 1, str(lvl))

        view = idlegame.get_view(_sig(level=lvl))
        check("level: the game surfaces the threaded Level verbatim",
              view["level"]["level"] == lvl["level"]
              and _approx(view["level"]["xp"], lvl["xp"], tol=1e-4)
              and _approx(view["level"]["points"], lvl["points"], tol=1e-2),
              f'{view["level"]} vs {lvl}')
    finally:
        focus.STATE_PATH = _FOCUS_ORIG_PATH
        focus._now = _FOCUS_ORIG_NOW
        shutil.rmtree(fd, ignore_errors=True)
        _cleanup(d)


def test_hours_today_reads_worked_time():
    t0 = _ts(2026, 6, 2, h=9)
    fd = tempfile.mkdtemp(prefix="game_focus_")
    focus.STATE_PATH = Path(fd) / "focus_state.json"
    clk = Clock(t0)
    focus._now = clk
    try:
        focus.get_view()                             # init the work-day at t0
        clk.advance(2 * 3600)                        # two hours worked, no pause
        h = focus.hours_today()
        check("hours_today: reflects ~2 worked hours", _approx(h, 2.0, tol=0.01), str(h))
    finally:
        focus.STATE_PATH = _FOCUS_ORIG_PATH
        focus._now = _FOCUS_ORIG_NOW
        shutil.rmtree(fd, ignore_errors=True)


def test_session_start_cost_charged_once_per_sid():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        _seed_tokens(t0, 1000)
        sig = _sig(session_ids=["s1", "s2"], level={"level": 1, "xp": 0.0, "points": 0.0})
        v, _ = idlegame.toggle(True, sig)            # commit sees two new sids
        cost = idlegame.SESSION_START_COST_BASE + idlegame.SESSION_START_COST_PER_LEVEL * 1
        # assert on the persisted FLOAT balance (the view floors tokens to an int for display)
        check("session-cost: two new sids charged once each",
              _approx(idlegame.load_state()["economy"]["tokens"], 1000 - 2 * cost, tol=1e-6),
              str(idlegame.load_state()["economy"]))
        check("session-cost: stats.sessions_charged counts them", v["stats"]["sessions_charged"] == 2,
              str(v["stats"]))
        idlegame.toggle(True, sig)                   # same sids again -> not re-charged
        check("session-cost: an already-seen sid is never charged twice",
              _approx(idlegame.load_state()["economy"]["tokens"], 1000 - 2 * cost, tol=1e-6),
              str(idlegame.load_state()["economy"]))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  F2 — schema growth, honesty coupling, knowledge, boss, prestige (over state)
# --------------------------------------------------------------------------- #
def test_v1_state_upgrades_in_place_additively():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        # a genuine F1-shaped file: version 1, NO quiz/prestige/knowledge keys.
        f1 = {"version": 1, "config": {"enabled": True},
              "economy": {"tokens": 123.0, "lifetime_tokens": 456.0, "last_accrual_at": t0},
              "defense": {"shield": 10.0, "shield_max": 100, "hp": 100.0, "hp_max": 100,
                          "last_defense_at": t0},
              "upgrades": {"tool_grep": 3}, "collect": {"last_collect_day": None},
              "sessions_seen": ["s1"], "stats": {"collects": 2}}
        idlegame.STATE_PATH.write_text(json.dumps(f1), encoding="utf-8")
        st = idlegame.load_state()
        check("migrate: version bumped to v2", st["version"] == idlegame.STATE_VERSION, str(st["version"]))
        check("migrate: F1 economy values are UNTOUCHED",
              st["economy"]["tokens"] == 123.0 and st["economy"]["lifetime_tokens"] == 456.0, str(st["economy"]))
        check("migrate: F1 upgrade count is kept", st["upgrades"]["tool_grep"] == 3, str(st["upgrades"]))
        check("migrate: new F2 keys backfilled with defaults",
              st["economy"]["knowledge"] == 0.0 and st["prestige"] == {"rank": 0, "mult": 1.0}
              and st["quiz"]["tier_active"] == 1, str((st.get("prestige"), st.get("quiz", {}).get("tier_active"))))
        check("migrate: quiz sub-structure present (progress/boss/gen)",
              all(k in st["quiz"] for k in ("tags", "recent", "progress", "boss", "gen")), str(list(st["quiz"])))
    finally:
        _cleanup(d)


def test_wrong_answer_withholds_shield_correct_refills():
    """THE honesty pin (§1.5 / locked decision 1): a wrong answer must leave shield
    NUMERICALLY IDENTICAL (withhold, never confiscate); a correct answer adds a refill."""
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        st = idlegame.default_state(now=t0)
        st["defense"]["shield"] = 50.0
        st["defense"]["last_defense_at"] = t0        # frozen clock -> _commit's defense span is 0
        st["economy"]["last_accrual_at"] = t0
        idlegame.save_state(st)
        q = _bank_q("q_a", correct_key="C")

        res, code = idlegame.quiz_answer("q_a", _served_wrong_label(q, seen=0), q, _sig())
        check("withhold: a wrong answer -> 200, correct false", code == 200 and res["correct"] is False, str(res))
        check("withhold: WRONG leaves shield numerically UNCHANGED (no subtraction anywhere)",
              _approx(idlegame.load_state()["defense"]["shield"], 50.0),
              str(idlegame.load_state()["defense"]["shield"]))
        check("withhold: the pre-authored explanation still teaches", res["explanation"] == "expl q_a", str(res))

        # the miss reset the box to 1 and bumped seen to 1 -> the next serving reshuffles at seen=1
        res2, _c = idlegame.quiz_answer("q_a", _served_correct_label(q, seen=1), q, _sig())
        check("refill: a CORRECT answer ADDS QUIZ_SHIELD_REFILL",
              _approx(idlegame.load_state()["defense"]["shield"], 50.0 + idlegame.QUIZ_SHIELD_REFILL),
              str(idlegame.load_state()["defense"]["shield"]))
        check("refill: response echoes shield + knowledge", "shield" in res2 and "knowledge" in res2, str(res2))
    finally:
        _cleanup(d)


def test_box5_first_maturation_grants_knowledge_once_ever():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        st = idlegame.default_state(now=t0)
        st["quiz"]["progress"] = {"q_a": {"box": 4, "due": t0, "seen": 3, "correct": 3,
                                          "wrong": 0, "last_result": "correct", "matured": False}}
        idlegame.save_state(st)
        q = _bank_q("q_a")
        idlegame.quiz_answer("q_a", _served_correct_label(q, seen=3), q, _sig())
        ls = idlegame.load_state()
        check("box5: first 4->5 maturation grants KNOW_PER_BOX5",
              _approx(ls["economy"]["knowledge"], idlegame.KNOW_PER_BOX5), str(ls["economy"]))
        check("box5: the question is flagged matured (box 5)",
              ls["quiz"]["progress"]["q_a"]["matured"] is True and ls["quiz"]["progress"]["q_a"]["box"] == 5,
              str(ls["quiz"]["progress"]["q_a"]))

        # force a RE-CLIMB to box 5 with matured already True -> NO second award (once ever)
        ls["quiz"]["progress"]["q_a"]["box"] = 4
        idlegame.save_state(ls)
        idlegame.quiz_answer("q_a", _served_correct_label(q, seen=4), q, _sig())
        check("box5: a second maturation of the same question grants NOTHING (unfarmable)",
              _approx(idlegame.load_state()["economy"]["knowledge"], idlegame.KNOW_PER_BOX5),
              str(idlegame.load_state()["economy"]))
    finally:
        _cleanup(d)


def test_knowledge_never_comes_from_work_or_collect():
    t0 = _ts(2026, 5, 1)
    d, _clk = _new_env(t0)
    try:
        idlegame.get_view(_sig(live=3, tool_ts=[t0 + 1, t0 + 2]))
        idlegame.toggle(True, _sig(live=3, tool_ts=[t0 + 1, t0 + 2]))     # accrue work income
        idlegame.collect(12.0, _sig(hours_today=12.0))                    # a fat Collect
        ls = idlegame.load_state()
        check("knowledge: work + Collect earn tokens but ZERO knowledge",
              ls["economy"]["tokens"] > 0 and _approx(ls["economy"]["knowledge"], 0.0),
              str(ls["economy"]))
    finally:
        _cleanup(d)


def _play_boss(bank, answer_label):
    """Drive a boss to completion, choosing each answer via answer_label(question, seen).
    Returns the final boss_answer response."""
    res = None
    for _ in range(len(bank) + 2):
        st = idlegame.load_state()
        sess = (st["quiz"]["boss"] or {}).get("session")
        if not sess:
            break
        qid = sess["qids"][sess["idx"]]
        q = next(x for x in bank if x["id"] == qid)
        seen = idlegame._int((st["quiz"]["progress"].get(qid) or {}).get("seen"), 0)
        res, _c = idlegame.boss_answer(bank, answer_label(q, seen), _sig())
    return res


def test_boss_pass_grants_shield_and_knowledge():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        st = idlegame.default_state(now=t0)
        st["quiz"]["boss"]["active_secs"] = idlegame.BOSS_INTERVAL_SECS + 1
        st["defense"]["shield"] = 0.0
        st["defense"]["last_defense_at"] = t0
        idlegame.save_state(st)
        bank = [_bank_q("qb1"), _bank_q("qb2"), _bank_q("qb3")]
        res, code = idlegame.boss_start(bank, _sig())
        check("boss: start when cadence is met -> 200 with a first question",
              code == 200 and res["total"] == 3 and "first" in res, str(res))
        final = _play_boss(bank, _served_correct_label)
        check("boss: an all-correct run passes (>70%)",
              final["done"] is True and final["passed"] is True and final["score"] == 1.0, str(final))
        ls = idlegame.load_state()
        check("boss: a pass grants KNOW_PER_BOSS", _approx(ls["economy"]["knowledge"], idlegame.KNOW_PER_BOSS),
              str(ls["economy"]))
        check("boss: a pass grants BOSS_SHIELD_REFILL", _approx(ls["defense"]["shield"], idlegame.BOSS_SHIELD_REFILL),
              str(ls["defense"]))
        check("boss: rewards echoed in the final response",
              final["rewards"]["knowledge"] == idlegame.KNOW_PER_BOSS, str(final["rewards"]))
        check("boss: the session is cleared + cadence reset",
              ls["quiz"]["boss"]["session"] is None
              and _approx(ls["quiz"]["boss"]["last_boss_at"], ls["quiz"]["boss"]["active_secs"]),
              str(ls["quiz"]["boss"]))
    finally:
        _cleanup(d)


def test_boss_fail_never_zeroes_progress():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        st = idlegame.default_state(now=t0)
        st["quiz"]["boss"]["active_secs"] = idlegame.BOSS_INTERVAL_SECS + 1
        # a pre-existing MATURE question is among the boss set: a fail must not wipe it
        st["quiz"]["progress"] = {"qb1": {"box": 5, "due": t0 + 30 * 86400, "seen": 6,
                                          "correct": 6, "wrong": 0, "matured": True}}
        idlegame.save_state(st)
        bank = [_bank_q("qb1"), _bank_q("qb2"), _bank_q("qb3")]
        idlegame.boss_start(bank, _sig())
        final = _play_boss(bank, _served_wrong_label)
        check("boss: an all-wrong run fails", final["done"] is True and final["passed"] is False, str(final))
        ls = idlegame.load_state()
        prog = ls["quiz"]["progress"]
        check("boss-fail: progress is NOT wiped (every answered question still tracked)",
              all(q in prog for q in ("qb1", "qb2", "qb3")), str(list(prog)))
        check("boss-fail: misses are rescheduled to box 1", all(prog[q]["box"] == 1 for q in prog), str(prog))
        check("boss-fail: the once-ever matured flag is preserved (not reset)",
              prog["qb1"]["matured"] is True, str(prog["qb1"]))
        check("boss-fail: no knowledge + no shield refill on a fail",
              _approx(ls["economy"]["knowledge"], 0.0) and _approx(ls["defense"]["shield"], 0.0),
              str((ls["economy"]["knowledge"], ls["defense"]["shield"])))
    finally:
        _cleanup(d)


def test_boss_cancel_clears_session_grants_nothing_reopens():
    """Abandoning a boss (client closed the modal) must clear the session so it stops
    blocking availability, grant NOTHING, and NOT reset the cadence — else boss_start
    409s forever (the soft-lock the reviewer found)."""
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        st = idlegame.default_state(now=t0)
        st["quiz"]["boss"]["active_secs"] = idlegame.BOSS_INTERVAL_SECS + 1
        st["defense"]["last_defense_at"] = t0
        idlegame.save_state(st)
        bank = [_bank_q("qb1"), _bank_q("qb2"), _bank_q("qb3")]
        idlegame.boss_start(bank, _sig())
        # answer ONE question wrong, then abandon mid-run
        sess = idlegame.load_state()["quiz"]["boss"]["session"]
        first_qid = sess["qids"][0]
        q0 = next(x for x in bank if x["id"] == first_qid)
        idlegame.boss_answer(bank, _served_wrong_label(q0, seen=0), _sig())

        res, code = idlegame.boss_cancel()
        check("boss-cancel: -> 200 {ok:true}", code == 200 and res == {"ok": True}, str((code, res)))
        ls = idlegame.load_state()
        check("boss-cancel: the session is cleared", ls["quiz"]["boss"]["session"] is None, str(ls["quiz"]["boss"]))
        check("boss-cancel: grants NOTHING (knowledge + shield untouched)",
              _approx(ls["economy"]["knowledge"], 0.0) and _approx(ls["defense"]["shield"], 0.0),
              str((ls["economy"]["knowledge"], ls["defense"]["shield"])))
        check("boss-cancel: an answered miss stays rescheduled to box 1 (not undone)",
              ls["quiz"]["progress"][first_qid]["box"] == 1, str(ls["quiz"]["progress"].get(first_qid)))
        check("boss-cancel: cadence NOT reset -> boss available again once the interval is met",
              idlegame.get_view(_sig())["quiz"]["boss"]["available"] is True)
        r2, c2 = idlegame.boss_cancel()
        check("boss-cancel: idempotent -> no active session still returns {ok:true}",
              c2 == 200 and r2 == {"ok": True} and idlegame.load_state()["quiz"]["boss"]["session"] is None,
              str((c2, r2)))
    finally:
        _cleanup(d)


def test_prestige_resets_base_keeps_lifetime_level_quizprogress():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        st = idlegame.default_state(now=t0)
        cost0 = idlegame.prestige_cost(0)
        st["economy"]["knowledge"] = cost0 + 2.0
        st["economy"]["lifetime_knowledge"] = cost0 + 2.0
        st["economy"]["lifetime_tokens"] = 500.0
        st["economy"]["tokens"] = 300.0
        st["economy"]["last_accrual_at"] = t0
        st["upgrades"]["tool_grep"] = 2
        st["quiz"]["tier_active"] = 3
        st["quiz"]["progress"] = {"q_a": {"box": 3, "due": t0 + 100, "seen": 5, "matured": False}}
        idlegame.save_state(st)
        lvl = {"level": 7, "xp": 1.0, "points": 2.0}

        view, code = idlegame.prestige(_sig(level=lvl))
        ls = idlegame.load_state()
        check("prestige: -> 200", code == 200, str(code))
        check("prestige: rank +1", ls["prestige"]["rank"] == 1, str(ls["prestige"]))
        check("prestige: tokens reset to 0", _approx(ls["economy"]["tokens"], 0.0), str(ls["economy"]))
        check("prestige: ALL upgrades reset to 0", all(v == 0 for v in ls["upgrades"].values()), str(ls["upgrades"]))
        check("prestige: knowledge spent exactly the cost",
              _approx(ls["economy"]["knowledge"], 2.0), str(ls["economy"]["knowledge"]))
        check("prestige: lifetime_tokens KEPT", _approx(ls["economy"]["lifetime_tokens"], 500.0),
              str(ls["economy"]["lifetime_tokens"]))
        check("prestige: sub-linear mult gained (k*sqrt(lifetime))",
              _approx(ls["prestige"]["mult"], 1.0 + idlegame.prestige_gain(500.0)), str(ls["prestige"]["mult"]))
        check("prestige: ALL quiz progress KEPT", ls["quiz"]["progress"]["q_a"]["box"] == 3, str(ls["quiz"]["progress"]))
        check("prestige: tier_active KEPT", ls["quiz"]["tier_active"] == 3, str(ls["quiz"]["tier_active"]))
        check("prestige: RPG Level surfaced from the signal (never stored/reset)",
              view["level"]["level"] == 7, str(view["level"]))
        check("prestige: rank_name is career-framed", view["prestige"]["rank_name"] == "Medior",
              str(view["prestige"]))
    finally:
        _cleanup(d)


def test_prestige_insufficient_knowledge_is_400():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        st = idlegame.default_state(now=t0)
        st["economy"]["knowledge"] = idlegame.prestige_cost(0) - 1.0
        st["economy"]["tokens"] = 999.0
        idlegame.save_state(st)
        res, code = idlegame.prestige(_sig())
        check("prestige: insufficient knowledge -> 400", code == 400 and res["error"] == "insufficient knowledge",
              str((code, res)))
        ls = idlegame.load_state()
        check("prestige: a refused prestige changes nothing (rank/tokens intact)",
              ls["prestige"]["rank"] == 0 and _approx(ls["economy"]["tokens"], 999.0), str(ls))
    finally:
        _cleanup(d)


def test_prestige_mult_multiplies_income_only():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        st = idlegame.default_state(now=t0)
        st["prestige"]["mult"] = 2.0
        st["economy"]["last_accrual_at"] = t0
        idlegame.save_state(st)
        rate2 = idlegame.get_view(_sig(live=1))["economy"]["rate_per_min"]
        st = idlegame.load_state()
        st["prestige"]["mult"] = 1.0
        idlegame.save_state(st)
        rate1 = idlegame.get_view(_sig(live=1))["economy"]["rate_per_min"]
        check("mult: prestige mult scales the passive rate (x2 -> double)",
              rate1 > 0 and _approx(rate2, 2 * rate1, tol=1e-3), f"{rate2} vs 2*{rate1}")
    finally:
        _cleanup(d)


def test_boss_cadence_accrues_active_secs_and_gates_availability():
    t0 = 1_700_000_000.0
    d, clk = _new_env(t0)
    try:
        idlegame.get_view(_sig(live=1))                  # anchor at t0
        clk.advance(20)
        idlegame.toggle(True, _sig(live=1))              # a commit with a live session accrues active_secs
        check("cadence: active_secs accrues the clamped span while a session is live",
              _approx(idlegame.load_state()["quiz"]["boss"]["active_secs"], 20.0, tol=0.5),
              str(idlegame.load_state()["quiz"]["boss"]))
        st = idlegame.load_state()
        st["quiz"]["boss"]["active_secs"] = idlegame.BOSS_INTERVAL_SECS
        st["quiz"]["boss"]["last_boss_at"] = 0.0
        idlegame.save_state(st)
        check("cadence: available once BOSS_INTERVAL_SECS of active time has passed",
              idlegame.get_view(_sig())["quiz"]["boss"]["available"] is True)
        st = idlegame.load_state()
        st["quiz"]["boss"]["active_secs"] = idlegame.BOSS_INTERVAL_SECS - 100
        idlegame.save_state(st)
        check("cadence: not yet available below the interval",
              idlegame.get_view(_sig())["quiz"]["boss"]["available"] is False)
    finally:
        _cleanup(d)


def test_set_tags_validates_and_reports_change():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        res, code = idlegame.set_tags(["net", "db"], _sig())
        check("tags: a valid list -> 200, cleaned + changed", code == 200 and res["tags"] == ["net", "db"]
              and res["changed"] is True, str(res))
        res2, _c = idlegame.set_tags(["net", "db"], _sig())
        check("tags: an unchanged selection reports changed False (no needless regenerate)",
              res2["changed"] is False, str(res2))
        check("tags: non-list -> 400", idlegame.set_tags("net", _sig())[1] == 400)
        check("tags: a non-string entry -> 400", idlegame.set_tags([123], _sig())[1] == 400)
        check("tags: an over-long tag -> 400", idlegame.set_tags(["x" * (idlegame.TAG_MAXLEN + 1)], _sig())[1] == 400)
        check("tags: too many tags -> 400",
              idlegame.set_tags([f"t{i}" for i in range(idlegame.TAGS_MAX + 1)], _sig())[1] == 400)
    finally:
        _cleanup(d)


def test_quiz_answer_untrusted_qid_and_choice():
    t0 = 1_700_000_000.0
    d, _clk = _new_env(t0)
    try:
        q = _bank_q("q_a")
        check("untrusted: an unknown/absent question (server passes None) -> 400",
              idlegame.quiz_answer("q_a", "A", None, _sig())[1] == 400)
        check("untrusted: a retired question -> 400",
              idlegame.quiz_answer("q_a", "A", dict(q, retired=True), _sig())[1] == 400)
        check("untrusted: a choice this serving never offered -> 400",
              idlegame.quiz_answer("q_a", "Z", q, _sig())[1] == 400)
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  HTTP surface — the frozen GET contract + write gating
# --------------------------------------------------------------------------- #
def _http_env():
    d = tempfile.mkdtemp(prefix="game_http_")
    idlegame.STATE_PATH = Path(d) / "game_state.json"
    idlegame._now = _IG_ORIG_NOW                      # real clock for the live server
    focus.STATE_PATH = Path(d) / "focus_state.json"   # _game_signals reads focus read-only
    focus._now = _FOCUS_ORIG_NOW
    quizbank.BANK_PATH = Path(d) / "quiz_bank" / "bank.json"   # empty bank, deterministic
    return d


def _http_cleanup(d):
    idlegame.STATE_PATH = _IG_ORIG_PATH
    idlegame._now = _IG_ORIG_NOW
    focus.STATE_PATH = _FOCUS_ORIG_PATH
    focus._now = _FOCUS_ORIG_NOW
    quizbank.BANK_PATH = _BANK_ORIG_PATH
    shutil.rmtree(d, ignore_errors=True)


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


def _post(port, path, obj, headers=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(obj).encode(), method="POST", headers=h)
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


def test_game_state_route_shape():
    d = _http_env()
    httpd, port = _start()
    try:
        code, body = _get(port, "/api/game/state")
        out = json.loads(body.decode() or "{}")
        check("http: GET /api/game/state -> 200", code == 200, str(code))
        check("http: config.enabled is a bool", isinstance(out["config"]["enabled"], bool))
        check("http: economy carries tokens/lifetime/rate_per_min",
              all(k in out["economy"] for k in ("tokens", "lifetime", "rate_per_min")),
              str(out["economy"]))
        check("http: defense carries shield/shield_max/hp/hp_max",
              all(k in out["defense"] for k in ("shield", "shield_max", "hp", "hp_max")),
              str(out["defense"]))
        check("http: level carries level/xp/points",
              all(k in out["level"] for k in ("level", "xp", "points")), str(out["level"]))
        check("http: collect carries available/multiplier/preview/hours_today",
              all(k in out["collect"] for k in ("available", "multiplier", "preview", "hours_today")),
              str(out["collect"]))
        check("http: each shop row carries the contract fields",
              all(all(k in row for k in ("id", "name", "category", "price", "owned",
                                         "affordable", "effect_desc")) for row in out["shop"]),
              str(out["shop"][:1]))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _http_cleanup(d)


def test_game_writes_loopback_and_origin_gated():
    d = _http_env()
    httpd, port = _start()
    try:
        code, _b = _post(port, "/api/game/toggle", {"enabled": True})
        check("http: loopback POST toggle -> 200", code == 200, str(code))
        code, _b = _post(port, "/api/game/buy", {"id": "nope"})
        check("http: unknown buy id -> 400 over HTTP", code == 400, str(code))
        code, _b = _post(port, "/api/game/toggle", {"enabled": "yes"})
        check("http: non-bool toggle -> 400", code == 400, str(code))
        code, _b = _post_raw(port, "/api/game/buy", b"[1,2,3]")
        check("http: non-object body -> 400", code == 400, str(code))
        # a forged cross-site Origin is rejected even from loopback (CSRF guard)
        code, _b = _post(port, "/api/game/toggle", {"enabled": True},
                         headers={"Origin": "http://evil.example:1234"})
        check("http: forged Origin -> 403", code == 403, str(code))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _http_cleanup(d)


def test_game_writes_rejected_for_non_local_peer():
    import inspect
    get_src = inspect.getsource(server.Handler.do_GET)
    post_src = inspect.getsource(server.Handler.do_POST)
    check("wiring: /api/game/state routed in do_GET", '"/api/game/state"' in get_src)
    for r in ('"/api/game/collect"', '"/api/game/buy"', '"/api/game/toggle"'):
        check(f"wiring: {r} handled in do_POST", r in post_src)
    orig = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    d = _http_env()
    httpd, port = _start()
    try:
        code, _b = _post(port, "/api/game/toggle", {"enabled": True})
        check("gate: game write -> 403 for a non-local peer", code == 403, str(code))
        code, _b = _get(port, "/api/game/state")
        check("gate: game read stays open (200) on the LAN", code == 200, str(code))
    finally:
        server.Handler._client_is_local = orig
        httpd.shutdown()
        httpd.server_close()
        _http_cleanup(d)


def test_state_route_grows_additively_with_f2():
    d = _http_env()
    httpd, port = _start()
    try:
        code, body = _get(port, "/api/game/state")
        out = json.loads(body.decode() or "{}")
        check("http: state still 200 with the F2 additions", code == 200, str(code))
        check("http: economy grew knowledge/lifetime_knowledge",
              all(k in out["economy"] for k in ("knowledge", "lifetime_knowledge")), str(out["economy"]))
        check("http: prestige block present",
              all(k in out["prestige"] for k in ("rank", "rank_name", "mult", "next_cost", "can_prestige")),
              str(out["prestige"]))
        check("http: quiz block present",
              all(k in out["quiz"] for k in ("tier_active", "accuracy", "due_count", "boss"))
              and all(k in out["quiz"]["boss"] for k in ("available", "active_secs", "interval_secs")),
              str(out["quiz"]))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _http_cleanup(d)


def test_quiz_next_route_empty_bank():
    d = _http_env()
    httpd, port = _start()
    try:
        code, body = _get(port, "/api/game/quiz/next")
        out = json.loads(body.decode() or "{}")
        check("http: GET /api/game/quiz/next -> 200", code == 200, str(code))
        check("http: an empty bank yields {empty:true} (never nothing)", out.get("empty") is True, str(out))
    finally:
        httpd.shutdown()
        httpd.server_close()
        _http_cleanup(d)


def test_new_game_posts_gated_and_shaped():
    # stub the key so a generate/tags POST never spins a REAL Gemini call in a test
    orig_key = server._gemini_key
    server._gemini_key = lambda: ""
    d = _http_env()
    httpd, port = _start()
    try:
        code, _b = _post(port, "/api/game/quiz/generate", {"tags": ["net"]})
        check("http: quiz/generate returns started INSTANTLY", code == 200, str(code))
        check("http: generate body is {ok,status:started}",
              json.loads(_b.decode()) == {"ok": True, "status": "started"}, str(_b))
        code, _b = _post(port, "/api/game/quiz/retire", {"qid": "q_nope"})
        check("http: retire of an unknown qid -> 400", code == 400, str(code))
        code, b = _post(port, "/api/game/quiz/tags", {"tags": ["net", "db"]})
        check("http: quiz/tags valid -> 200", code == 200 and json.loads(b.decode())["tags"] == ["net", "db"], str(b))
        code, _b = _post(port, "/api/game/quiz/answer", {"qid": "q_nope", "choice": "A"})
        check("http: answer for an unknown qid -> 400", code == 400, str(code))
        code, _b = _post(port, "/api/game/boss/start", {})
        check("http: boss/start with no cadence yet -> 409", code == 409, str(code))
        code, b = _post(port, "/api/game/boss/cancel", {})
        check("http: boss/cancel -> 200 {ok:true} (idempotent, no active run)",
              code == 200 and json.loads(b.decode()) == {"ok": True}, str((code, b)))
        code, _b = _post(port, "/api/game/prestige", {})
        check("http: prestige with no knowledge -> 400", code == 400, str(code))
        # every new POST is a mutation: a forged cross-site Origin is rejected (CSRF)
        code, _b = _post(port, "/api/game/prestige", {}, headers={"Origin": "http://evil.example:1234"})
        check("http: a forged Origin on a new game POST -> 403", code == 403, str(code))
    finally:
        server._gemini_key = orig_key
        httpd.shutdown()
        httpd.server_close()
        _http_cleanup(d)


def test_topup_fires_on_answer_post_not_on_next_get():
    """appsec A02: a GET must not trigger a Gemini spend. The low-water top-up lives on
    the gated POST /quiz/answer (which cannot be driven cross-origin), never on the bare
    GET /quiz/next (a cross-origin <img> can hit it)."""
    d = _http_env()
    # seed a one-question bank so the GET serves something and the answer 200s
    q = _bank_q("q_topup")
    quizbank.BANK_PATH.parent.mkdir(parents=True, exist_ok=True)
    quizbank.BANK_PATH.write_text(json.dumps({"version": 1, "questions": [q]}), encoding="utf-8")
    calls = {"n": 0}
    orig = server._maybe_quiz_topup
    server._maybe_quiz_topup = lambda: calls.__setitem__("n", calls["n"] + 1)
    httpd, port = _start()
    try:
        code, body = _get(port, "/api/game/quiz/next")
        served = json.loads(body.decode())
        check("topup: GET /quiz/next serves the question -> 200", code == 200 and served["qid"] == "q_topup",
              str((code, served)))
        check("topup: a GET triggers ZERO top-up (no Gemini spend off a bare GET)", calls["n"] == 0, str(calls))
        code, _b = _post(port, "/api/game/quiz/answer",
                         {"qid": "q_topup", "choice": served["options"][0]["key"]})
        check("topup: the answer POST -> 200", code == 200, str(code))
        check("topup: the gated POST /quiz/answer DOES run the top-up exactly once", calls["n"] == 1, str(calls))
    finally:
        server._maybe_quiz_topup = orig
        httpd.shutdown()
        httpd.server_close()
        _http_cleanup(d)


def _drain_game_broadcasts(q):
    """Every {"type":"game"} message currently queued for one SSE subscriber. The
    initial snapshot and any session deltas are ignored — we only care that a game
    push landed."""
    import queue as _queue
    games = []
    while True:
        try:
            msg = q.get_nowait()
        except _queue.Empty:
            break
        if isinstance(msg, dict) and msg.get("type") == "game":
            games.append(msg)
    return games


def test_game_mutation_broadcasts_state_over_sse():
    """A successful game mutation fans {"type":"game","state":<view>} to every open
    /stream subscriber over the SHARED Hub broadcast — so the HUD updates in real time
    (and cross-tab) without a fast poll. Driven in-process: subscribe to the real Hub,
    POST a mutation over loopback, then drain the subscriber queue. The broadcast is
    synchronous inside the request handler, so by the time the POST returns it is
    already enqueued. Both the reuse path (toggle -> res IS the view) and the
    compute-fresh path (boss/cancel -> {ok:true}, not the view) must push a game msg."""
    d = _http_env()
    httpd, port = _start()
    sub = server.HUB.subscribe()          # a stand-in for one open /stream browser tab
    try:
        _drain_game_broadcasts(sub)       # discard the initial snapshot message

        code, _b = _post(port, "/api/game/toggle", {"enabled": True})
        check("sse: toggle mutation -> 200", code == 200, str(code))
        games = _drain_game_broadcasts(sub)
        check("sse: a successful mutation enqueues exactly one {type:'game'} broadcast",
              len(games) == 1, str(len(games)))
        state = games[0].get("state") if games else {}
        check("sse: the game message carries state:{type:'game', state:<view>}",
              isinstance(state, dict) and "economy" in state and "defense" in state
              and isinstance(state.get("config", {}).get("enabled"), bool),
              str(state)[:200])

        # the compute-fresh branch (200 body is {ok:true}, not the view) also pushes
        code, _b = _post(port, "/api/game/boss/cancel", {})
        check("sse: boss/cancel -> 200", code == 200, str(code))
        games = _drain_game_broadcasts(sub)
        check("sse: the compute-fresh branch also enqueues a game view",
              len(games) == 1 and isinstance(games[0].get("state"), dict)
              and "economy" in games[0]["state"], str(games))

        # a REJECTED mutation must NOT broadcast (unknown buy id -> 400)
        code, _b = _post(port, "/api/game/buy", {"id": "nope"})
        check("sse: rejected buy -> 400", code == 400, str(code))
        check("sse: a 4xx mutation pushes NOTHING (broadcast only on 200)",
              _drain_game_broadcasts(sub) == [], "a failed write broadcast a state")
    finally:
        server.HUB.unsubscribe(sub)
        httpd.shutdown()
        httpd.server_close()
        _http_cleanup(d)


def main():
    for fn in (test_default_state_shape, test_corrupt_state_degrades_disabled_not_crash,
               test_accrual_advances_only_on_commit, test_one_hour_gap_credits_at_most_max_span,
               test_tool_bump_counts_only_in_window_events, test_disabled_game_does_not_accrue,
               test_buy_unknown_id_is_400, test_buy_spends_tokens_and_price_grows,
               test_buy_insufficient_tokens_is_400,
               test_buy_shield_upgrade_raises_cap_and_subagent_raises_rate,
               test_collect_once_per_day_then_refused,
               test_collect_thresholds_pay_the_capped_multiplier,
               test_missing_a_day_forfeits_nothing, test_toggle_validates_bool,
               test_defense_step_math, test_defense_regen_through_commit,
               test_level_read_matches_focus_character_level, test_hours_today_reads_worked_time,
               test_session_start_cost_charged_once_per_sid,
               test_v1_state_upgrades_in_place_additively,
               test_wrong_answer_withholds_shield_correct_refills,
               test_box5_first_maturation_grants_knowledge_once_ever,
               test_knowledge_never_comes_from_work_or_collect,
               test_boss_pass_grants_shield_and_knowledge,
               test_boss_fail_never_zeroes_progress,
               test_boss_cancel_clears_session_grants_nothing_reopens,
               test_prestige_resets_base_keeps_lifetime_level_quizprogress,
               test_prestige_insufficient_knowledge_is_400,
               test_prestige_mult_multiplies_income_only,
               test_boss_cadence_accrues_active_secs_and_gates_availability,
               test_set_tags_validates_and_reports_change,
               test_quiz_answer_untrusted_qid_and_choice,
               test_game_state_route_shape, test_game_writes_loopback_and_origin_gated,
               test_game_writes_rejected_for_non_local_peer,
               test_state_route_grows_additively_with_f2, test_quiz_next_route_empty_bank,
               test_new_game_posts_gated_and_shaped,
               test_topup_fires_on_answer_post_not_on_next_get,
               test_game_mutation_broadcasts_state_over_sse):
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
