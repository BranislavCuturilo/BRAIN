#!/usr/bin/env python3
"""Focus timer + wellness reminders — the service layer behind the HUD's Focus
tab. Pure stdlib, no HTTP: server.py's /api/focus routes stay thin (parse body,
call one function here, _send the result), exactly like mail_list_page /
patch_triage sit behind their routes.

WHAT IT DOES
    A background focus session that nudges the user to drink water, stretch and
    do one exercise (picked weighted-random from a configurable set) on independent
    interval timers, plus a Fokus activity nudge that fires when a computed focus
    score drops. The user takes real breaks by pressing Pauza (lunch / WC / shop)
    and Nastavi on return.

EXERCISE SET (replaces the old single hardcoded "pushups")
    config.exercises is a list of {id, name, emoji, weight, reps, target, muscles,
    stat, days_since_done, recent_actuals}. When the exercise reminder transitions to
    due we pick ONE by weight (excluding session.last_exercise so it never repeats
    back-to-back) and PERSIST that pick under reminders.exercise.exercise, so a 15s
    poll does not re-roll it. ack/skip clear the pick and record last_exercise, so the
    next due re-rolls. An old state file that still carries a single `pushups_reps` is
    migrated to this shape on load.

    PROGRESSION: `target` (not `reps`) is the live suggestion the card shows. `reps`
    is the immutable seed/base. On a logged completion the user submits `actual` reps;
    ONCE per calendar day (the first completion of that exercise) the target adjusts —
    up by a diminishing step when met, down toward `actual` when missed. A whole day
    skipped leaves target unchanged and grows `days_since_done`.

    MUSCLES: `muscles` is a list of {muscle, pct} (auto-suggested from the name,
    user-overridable, soft-normalized to sum 100). The RPG derivation SPLITS the
    exercise's reps across those muscles by pct; the single `stat` receives the full
    reps. A legacy single-`muscle` file migrates to [{muscle, pct:100}] on load.

FOKUS ACTIVITY POOL (two classes: a mandatory quota + a random draw)
    config.focus_activities is a list of {id, name, kind, focus_boost, ...} split by
    `kind`. A MANDATORY activity (Kafa) carries a daily_limit and MUST be fulfilled: it
    is proposed at N evenly-spaced points of worked time (MANDATORY_WORKDAY_MIN / N) or
    on a focus drop, until done_today reaches the limit; under quota at day end is
    recorded as missed. A RANDOM activity carries a weight and is drawn (no immediate
    repeat via session.last_activity) on a focus drop when no mandatory is pending. The
    chosen one persists under reminders.coffee.activity = {id, name, kind, focus_boost}.
    ack applies the focus_boost as a decaying score bump and advances a mandatory's
    done_today; swap re-rolls the RANDOM pool only; infocus DEFERS the pick.

SWAP
    swap(type) re-rolls the current exercise pick / activity pick to a DIFFERENT
    weighted-random one (excluding the current), unless the pool has <=2 items.

THE ONE WRITE PATH
    save_state() is the only writer of focus_state.json (atomic tmp + os.replace,
    the same discipline as scripts/tickets/store.atomic_write_json). We deliberately
    do NOT reuse that module's cross-process file lock: the tickets file is written
    by TWO processes (the view and the sync), whereas focus_state.json has exactly
    one writer — this single server process — so a plain threading.Lock around each
    read-modify-write is the correct, lighter tool. All IO is guarded; a missing
    file initialises a fresh enabled state, and a corrupt/unreadable one degrades to
    a disabled-but-present state so the routes still answer valid JSON and the server
    never crashes.

TIME
    Every stored timestamp is a wall-clock time.time() epoch (monotonic clocks
    cannot survive a restart or be persisted). "monotonic-safe" here means the math
    never trusts the clock to move forward: every elapsed span is max(0, ...)-clamped,
    so an NTP step backwards can neither make a reminder fire in a burst nor drive a
    negative score.

PAUSE (freezes all clocks, no catch-up burst)
    On pause we stamp paused_at and set last_break_at (a pause IS a break). While
    paused, no reminder is ever `due` and the score is frozen at its break value
    (the score reads paused_at, not the advancing wall clock). On resume we add the
    paused span to paused_total_sec, shift every interval reminder's next_at forward
    by exactly that span (so the paused time is excluded from "elapsed since next_at"
    and nothing floods due at once), and reset last_break_at to now (the break ends
    when work resumes, so time-since-break restarts from zero — this is why a pause
    can never leave you instantly overdue).

FOCUS SCORE (0-100)
    PRIMARY signal — time since the last break, decaying linearly over the EFFECTIVE
    break window (break_after_min * focus_decay_scale):
        base = 100 * (1 - min(minutes_since_break / effective_break, 1))
    100 right after a break, 0 once the window has passed with no break. With the
    defaults (break_after_min=90, decay_scale=1.0, low_score_threshold=40) the coffee
    nudge becomes due at ~54 minutes without a break. focus_decay_scale is auto-calibrated
    by the "U fokusu sam" feedback (see CALIBRATION) and bounded to [0.7, 2.0].

    BOOST signal — acking a Fokus activity adds its focus_boost, decaying to 0 over the
    same effective window (option 1: an instant, decaying bump). Capped with the rest at
    100; cleared by a real break (pause) and by new_day.

    CALIBRATION — POST /api/focus/infocus ("still focused, don't need it") nudges
    focus_decay_scale UP (slower decay: the low score was too pessimistic); acking a coffee
    while the score is genuinely low nudges it back toward 1.0 (the pessimism was warranted).

    SECONDARY signal — a LIGHT activity nudge folded in only when the server hands
    one over. server.py derives it cheaply from the in-memory Hub (the age of the
    most recent session/SSE event across live sessions — no new tracking, no IO) and
    passes {"last_event_age_sec": float}. Fresh activity (an event within
    ACTIVITY_FRESH_SEC) adds a small ACTIVITY_BONUS: you are in flow, so hold the
    interruption a touch longer. When no signal is supplied (no live session) activity
    is NEUTRAL and the score is the primary term alone. The parameter is the documented
    hook for a heavier signal later; v1 intentionally builds no tracking of its own.
"""
from __future__ import annotations

import copy
import json
import math
import os
import random
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: Runtime state, gitignored like the mail cache / ticket store. A module global
#: (not a constant baked into the functions) so a test can point it at a temp file.
STATE_PATH = HERE / "focus_state.json"

# -- defaults (a fresh session, and the fallback for a missing field) ---------
DEFAULT_INTERVALS_MIN = {"water": 45, "stretch": 60, "exercise": 90}
DEFAULT_EXERCISE_REPS = 5                # fallback reps when no pick is available
DEFAULT_BREAK_AFTER_MIN = 90
DEFAULT_LOW_SCORE_THRESHOLD = 40
DEFAULT_SNOOZE_MIN = 10                   # POST /snooze pushes a reminder this far out

#: The seeded exercise set — NEVER empty, so a pick is always possible. Weights are
#: relative (pushups dominates); reps is per-set. Each exercise carries `muscles`, a
#: list of {muscle, pct} that the RPG derivation splits its reps across (pct/100 each);
#: `stat` is the single attribute it feeds. Emoji + Serbian names are real unicode
#: (this source is UTF-8): the state file is written ensure_ascii=False and the API +
#: page are UTF-8. This is the ONE place the default set is defined; default_state()
#: and the migration both deep-copy from it.
DEFAULT_EXERCISES = [
    {"id": "pushups", "name": "Sklekovi", "emoji": "\U0001F4AA", "weight": 0.5,
     "reps": 5, "target": 5,
     "muscles": [{"muscle": "grudi", "pct": 70}, {"muscle": "podlaktice", "pct": 30}],
     "stat": "snaga", "days_since_done": 0, "recent_actuals": [], "last_done": None},
    {"id": "squats", "name": "Čučnjevi", "emoji": "\U0001F9B5", "weight": 0.2,
     "reps": 15, "target": 15, "muscles": [{"muscle": "kvadriceps", "pct": 100}],
     "stat": "snaga", "days_since_done": 0, "recent_actuals": [], "last_done": None},
    {"id": "jumping_jacks", "name": "Poskoci", "emoji": "\U0001F938", "weight": 0.15,
     "reps": 20, "target": 20, "muscles": [{"muscle": "listovi", "pct": 100}],
     "stat": "snaga", "days_since_done": 0, "recent_actuals": [], "last_done": None},
    {"id": "lunges", "name": "Iskoraci", "emoji": "\U0001F3C3", "weight": 0.15,
     "reps": 10, "target": 10, "muscles": [{"muscle": "kvadriceps", "pct": 100}],
     "stat": "snaga", "days_since_done": 0, "recent_actuals": [], "last_done": None},
]

#: The Fokus activity pool, split into TWO classes by `kind`:
#:   * mandatory — a daily QUOTA that must be fulfilled, spread across the work day; N
#:     evenly-spaced proposals over MANDATORY_WORKDAY_MIN (or on a focus drop) until
#:     done_today reaches daily_limit. Under quota at day end is recorded as missed.
#:   * random — the weighted-random draw pool (no immediate repeat), fired on a focus
#:     drop when no mandatory is pending.
#: Every activity carries `focus_boost` (0..100): acking it bumps the live score by that
#: much, decaying back over the effective break window. This is the ONE place the default
#: set is defined; default_state() and the migration both deep-copy from it.
DEFAULT_FOCUS_ACTIVITIES = [
    {"id": "kafa", "name": "Kafa", "kind": "mandatory", "focus_boost": 50, "daily_limit": 3},
    {"id": "sah", "name": "Šah 1min", "kind": "random", "focus_boost": 15, "weight": 0.4},
    {"id": "setnja", "name": "Šetnja", "kind": "random", "focus_boost": 20, "weight": 0.3},
    {"id": "disanje", "name": "Duboko disanje", "kind": "random", "focus_boost": 10, "weight": 0.3},
]
ACTIVITY_KINDS = ("mandatory", "random")
DEFAULT_FOCUS_BOOST = 15                  # boost for an activity that omits one
DEFAULT_DAILY_LIMIT = 3                   # mandatory quota when a config omits it
DEFAULT_ACTIVITY_WEIGHT = 0.3            # random draw weight when a config omits it
MAX_FOCUS_BOOST = 100
MAX_DAILY_LIMIT = 50
#: Mandatory quota is spread over this nominal work-day length: for limit N the k-th
#: proposal is due after k * (MANDATORY_WORKDAY_MIN / N) minutes of ACTUAL work (or
#: earlier if the focus score drops). A generous nominal day; real work rarely exceeds it.
MANDATORY_WORKDAY_MIN = 8 * 60

#: RPG metadata each exercise carries. `muscles` is a list of {muscle, pct}; each muscle
#: is one of this fixed set. `stat` is the single attribute it feeds (default snaga). The
#: RPG derivation splits an exercise's reps across its muscles by pct; the stat gets the
#: full reps.
MUSCLES = ("grudi", "ramena", "biceps", "podlaktice", "trbusnjaci", "kvadriceps", "listovi")
DEFAULT_STAT = "snaga"
MUSCLE_PCT_SUM_TOL = 1.0                  # a muscles list off from 100 by more than this is normalized
#: name-substring -> muscle split [(muscle, pct), ...], matched accent-folded (č/ć→c,
#: š→s, ž→z). First hit wins; default [grudi 100] when nothing matches. A compound lift
#: (sklek) splits across the muscles it actually trains (triceps folds into podlaktice,
#: the nearest group in MUSCLES).
_MUSCLE_SPLIT_HINTS = (
    ("sklek", (("grudi", 70), ("podlaktice", 30))),
    ("cucanj", (("kvadriceps", 100),)),
    ("cucnj", (("kvadriceps", 100),)),
    ("trbusn", (("trbusnjaci", 100),)),
    ("listov", (("listovi", 100),)),
    ("biceps", (("biceps", 100),)),
    ("poskoc", (("listovi", 100),)),
    ("iskorac", (("kvadriceps", 100),)),
    ("ramen", (("ramena", 100),)),
    ("podlakt", (("podlaktice", 100),)),
)

# A coffee nudge, once acknowledged or dismissed, stays quiet for this long before
# it can re-fire — otherwise a low score would mark it due on every 15s poll.
COFFEE_COOLDOWN_MIN = 30
# The secondary activity nudge: an event newer than this counts as "in flow".
ACTIVITY_FRESH_SEC = 120.0
ACTIVITY_BONUS = 5

# "U fokusu sam": an infocus report DEFERS the current pick this long (re-arm window),
# reusing the coffee snooze_until gate. Not counted done, no boost, no last_activity.
INFOCUS_DEFER_MIN = 15

# Focus-decay calibration. focus_decay_scale multiplies break_after_min in the score
# formula: a higher scale means the score decays SLOWER. An "in focus" report nudges it
# UP (the low score was too pessimistic); a coffee-ack-while-low nudges it back toward
# baseline (the pessimism was warranted). Bounded, so a run of one signal cannot run away.
FOCUS_DECAY_SCALE_BASE = 1.0
FOCUS_DECAY_SCALE_MIN = 0.7
FOCUS_DECAY_SCALE_MAX = 2.0
FOCUS_DECAY_SCALE_STEP = 0.1

INTERVAL_TYPES = ("water", "stretch", "exercise")   # the timer-driven reminders
REMINDER_TYPES = ("water", "stretch", "exercise", "coffee")
#: Per-reminder-type alert mute flags. Pure presentation — NOTHING in the backend
#: branches on these; the frontend reads them to decide whether to play the sound. Each
#: custom reminder carries its own `sound` flag too. Default true (audible).
DEFAULT_SOUND_FLAGS = {t: True for t in REMINDER_TYPES}

#: The four toggleable reminder CATEGORIES (config.reminders_enabled). water/stretch/
#: exercise map to their interval reminders; "fokus" gates the coffee/focus nudge. Unlike
#: `sound`, the backend DOES branch on these: a disabled category never goes `due`, and its
#: RPG dimension rests at a neutral base (see rpg()). Default true; absent in an old file →
#: treated as all-true (migration is _deep_fill + the lenient _category_enabled read).
REMINDER_CATEGORIES = ("water", "stretch", "exercise", "fokus")
DEFAULT_REMINDERS_ENABLED = {c: True for c in REMINDER_CATEGORIES}

# Config guard rails (reject an out-of-range config patch rather than store it).
MAX_INTERVAL_MIN = 24 * 60
MAX_EXERCISE_REPS = 1000
MAX_RECENT_ACTUALS = 10          # per-exercise rolling window of logged `actual` reps
# The confirmed once-per-day target step numerator: 3 * 8. A met set grows target by
# max(1, round(EXERCISE_STEP_NUM / (8 + target))) — diminishing at high reps.
EXERCISE_STEP_NUM = 24.0

# Downtime exclusion: the server stamps session.heartbeat every HEARTBEAT_INTERVAL_SEC
# while alive; on the first op after a restart, a heartbeat older than
# DOWNTIME_THRESHOLD_SEC means the server was DOWN for that span, which is excluded from
# elapsed (added to paused_total_sec, every timer shifted forward) so the work-day clock
# continues where it stopped. The threshold sits well above the heartbeat interval so a
# single slow tick never reads as downtime.
HEARTBEAT_INTERVAL_SEC = 30.0
DOWNTIME_THRESHOLD_SEC = 90.0

# One writer in this process → one plain lock around every read-modify-write. See
# the module docstring for why this is not the tickets store's cross-process lock.
_lock = threading.Lock()

# The heartbeat daemon is started once per process (start_heartbeat); this guards it.
_hb_lock = threading.Lock()
_hb_started = False


def _now() -> float:
    """Wall-clock epoch. A single seam so tests can freeze time (focus._now = ...)."""
    return time.time()


# --------------------------------------------------------------------------- #
#  Small typed readers — tolerant of a hand-edited / older state file, strict on
#  an incoming config patch.
# --------------------------------------------------------------------------- #
def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _num(v, default):
    """Lenient numeric read for a value already IN the state (tolerates a
    stringified number from a hand-edited file). bool is rejected — it is an int
    subclass and `True` as a duration is never intended."""
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default):
    if isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _strict_num(v):
    """A real JSON number (int/float, never bool, never a string) or None — used
    to validate an incoming config patch, where a non-number is a 400."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _day_str(now) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def _parse_day(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  State shape + persistence
# --------------------------------------------------------------------------- #
def default_state(now=None) -> dict:
    """A fresh, ENABLED session starting now — the first-run state."""
    now = _now() if now is None else now
    return {
        "config": {
            "enabled": True,
            # Per-CATEGORY on/off (water/stretch/exercise + the coffee/focus nudge, keyed
            # "fokus"). Default true; a disabled category never goes due AND its RPG
            # dimension sits at a neutral base. Absent in an old file → all-true.
            "reminders_enabled": dict(DEFAULT_REMINDERS_ENABLED),
            "intervals_min": dict(DEFAULT_INTERVALS_MIN),
            "exercises": copy.deepcopy(DEFAULT_EXERCISES),
            # focus holds the score-decay window AND the auto-calibrated decay_scale
            # (a multiplier on break_after_min); the coffee TRIGGER lives in `coffee`.
            "focus": {"break_after_min": DEFAULT_BREAK_AFTER_MIN,
                      "decay_scale": FOCUS_DECAY_SCALE_BASE},
            # Coffee is a FIRST-CLASS, productivity-driven reminder: it fires when the
            # focus score drops below low_score_threshold (never on an interval), and can
            # be individually enabled/tuned separately from the wellness timers.
            "coffee": {"enabled": True,
                       "low_score_threshold": DEFAULT_LOW_SCORE_THRESHOLD,
                       "cooldown_min": COFFEE_COOLDOWN_MIN},
            # The Fokus activity pool (mandatory quota + random draw) the coffee slot draws from.
            "focus_activities": copy.deepcopy(DEFAULT_FOCUS_ACTIVITIES),
            # Per-reminder-type alert mute flags (presentation only; see DEFAULT_SOUND_FLAGS).
            "sound": dict(DEFAULT_SOUND_FLAGS),
            "snooze_min": DEFAULT_SNOOZE_MIN,
            # user-defined PLAIN reminders: [{id, name, interval_min}] — each gets a slot
            # in reminders.custom that becomes due on its interval. Count-only; they never
            # touch the focus score or coffee.
            "custom_reminders": [],
        },
        "session": {
            "started_at": now,              # the work-day START (until an explicit end-day)
            "work_day": _day_str(now),      # its LOCAL calendar date (YYYY-MM-DD)
            "day_ended": False,             # set by end_day(); while true nothing is due
            "work_blocks": 1,               # work segments: 1 at day start, +1 per resume
            "heartbeat": now,               # server-alive stamp (downtime exclusion)
            "paused": False,
            "paused_at": None,
            "paused_total_sec": 0.0,
            "last_break_at": now,
            "last_exercise": None,          # id of the last acked/skipped pick → no repeat
            "last_activity": None,          # id of the last acked/skipped Fokus activity → no repeat
            # A decaying focus-activity bump (option 1): acking an activity sets these; the
            # score adds boost_amount decaying to 0 over the effective break window.
            "boost_amount": 0.0,
            "boost_at": None,
        },
        "reminders": {
            "water": {"last_at": None, "next_at": now + DEFAULT_INTERVALS_MIN["water"] * 60},
            "stretch": {"last_at": None, "next_at": now + DEFAULT_INTERVALS_MIN["stretch"] * 60},
            # `exercise` carries the PERSISTED pick chosen when it became due (None = not yet picked)
            "exercise": {"last_at": None, "next_at": now + DEFAULT_INTERVALS_MIN["exercise"] * 60,
                         "exercise": None},
            # `coffee` carries the PERSISTED Fokus activity picked when it went due (None = not yet)
            "coffee": {"last_at": None, "activity": None},
            # id -> {last_at, next_at} for each config.custom_reminders entry
            "custom": {},
        },
        # The count/reps/by_id/pauses/snoozes fields are the CURRENT WORK-DAY's counters:
        # captured into a history record at end_day, then reset to 0 at new_day, so a fresh
        # day starts clean and the profile/history keeps the running totals. `today` is the
        # separate legacy midnight counter that drives the calendar-day streak below.
        "metrics": {
            "water": {"count": 0, "today": 0},
            "stretch": {"count": 0, "today": 0},
            # aggregate reps across all exercises + a cheap per-exercise breakdown in by_id
            "exercise": {"count": 0, "reps_total": 0, "today_reps": 0, "by_id": {}},
            # Fokus activity acks this work-day (count + a per-activity breakdown, which
            # doubles as each mandatory activity's done_today). in_focus_reports counts the
            # "U fokusu sam" deferrals; both reset with the work-day.
            "coffee": {"count": 0, "by_id": {}, "in_focus_reports": 0, "in_focus_at": None},
            "pauses": {"count": 0},         # pauses this work-day
            "snoozes": {"count": 0},        # snoozes this work-day
            "custom": {},                   # id -> {count} acks of a custom reminder (count-only)
            "streak_days": 0,
            "last_active_day": None,
        },
        "focus": {"score": 100, "computed_at": now},
        # one appended day-report per ended work-day; the profile aggregates over it.
        "history": [],
    }


def _reset_day_metrics(state: dict) -> None:
    """Zero the work-day counters (water/stretch/exercise/pauses/snoozes/custom) at the
    start of a new day. The day's totals have already been captured into a history record
    by end_day; the LIVE counters restart from 0. streak_days / last_active_day are the
    calendar-day streak and are deliberately NOT reset here."""
    m = state["metrics"]
    for key in ("water", "stretch"):
        d = m.get(key)
        if isinstance(d, dict):
            d["count"] = 0
            d["today"] = 0
    ex = m.get("exercise")
    if isinstance(ex, dict):
        ex["count"] = 0
        ex["reps_total"] = 0
        ex["today_reps"] = 0
        ex["by_id"] = {}
    m["coffee"] = {"count": 0, "by_id": {}, "in_focus_reports": 0, "in_focus_at": None}
    m["pauses"] = {"count": 0}
    m["snoozes"] = {"count": 0}
    m["custom"] = {}


def _fallback_state() -> dict:
    """Disabled-but-present: the shape is whole so routes answer valid JSON, but
    the feature is off so nothing fires. Returned only when a state file exists yet
    cannot be read/parsed — we do NOT overwrite it (it may be a transient lock or a
    file the user can still recover); the next successful config write replaces it."""
    st = default_state()
    st["config"]["enabled"] = False
    return st


def _deep_fill(dst: dict, template: dict) -> dict:
    """Fill any key missing from `dst` with the template's default, recursing into
    nested dicts. Lets an older/partial state file gain new fields without losing
    the user's existing values."""
    for k, v in template.items():
        if k not in dst:
            dst[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_fill(dst[k], v)
    return dst


def load_state() -> dict:
    """Read state from disk, initialising on first run. NEVER raises — a bad file
    degrades to _fallback_state() rather than taking the server down. Callers hold
    _lock around load_state()+save_state() for a consistent read-modify-write."""
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        st = default_state()
        save_state(st)                 # best-effort initialise; a failure is swallowed
        return st
    except Exception:
        return _fallback_state()
    try:
        data = json.loads(raw)
    except Exception:
        return _fallback_state()
    if not isinstance(data, dict):
        return _fallback_state()
    _migrate_legacy(data)
    _migrate_workday(data)
    _migrate_exercise_fields(data)
    _migrate_focus_activities(data)
    _migrate_sound_flags(data)
    _migrate_history_sessions(data)
    return _deep_fill(data, default_state())


def _migrate_legacy(data: dict) -> dict:
    """In-place upgrade of a PRE-exercises state file (the single hardcoded
    "pushups") to the exercises shape, so an old focus_state.json loads cleanly
    instead of carrying dead `pushups` slots. Idempotent — a file already on the new
    shape is left untouched. Called before _deep_fill, which then adds any brand-new
    keys (session.last_exercise, reminders.exercise.exercise, metrics.exercise.by_id)."""
    if not isinstance(data, dict):
        return data
    cfg = data.get("config")
    if isinstance(cfg, dict):
        if "exercises" not in cfg:              # fold the old single reps into pushups
            exs = copy.deepcopy(DEFAULT_EXERCISES)
            old = _int(cfg.get("pushups_reps"), None)
            if old is not None and 1 <= old <= MAX_EXERCISE_REPS:
                exs[0]["reps"] = old
                exs[0]["target"] = old          # no prior progression → seed target = reps
            cfg["exercises"] = exs
        cfg.pop("pushups_reps", None)
        iv = cfg.get("intervals_min")
        if isinstance(iv, dict) and "pushups" in iv and "exercise" not in iv:
            iv["exercise"] = iv.pop("pushups")  # rename the interval key
    rem = data.get("reminders")                 # rename the reminder slot
    if isinstance(rem, dict) and "pushups" in rem and "exercise" not in rem:
        rem["exercise"] = rem.pop("pushups")
    m = data.get("metrics")                     # rename the metrics bucket (keeps counts)
    if isinstance(m, dict) and "pushups" in m and "exercise" not in m:
        m["exercise"] = m.pop("pushups")
    return data


def _migrate_workday(data: dict) -> dict:
    """Upgrade a PRE-work-day state file. Runs before _deep_fill (which supplies every
    other new key at its default). Two fixups that must NOT take a template default:
    work_day is derived from the existing started_at (not 'today'), and config.coffee is
    seeded from the old config.focus.low_score_threshold (which then moves out of focus,
    its new home being config.coffee). Idempotent."""
    if not isinstance(data, dict):
        return data
    sess = data.get("session")
    if isinstance(sess, dict) and "work_day" not in sess:
        started = _num(sess.get("started_at"), None)
        sess["work_day"] = _day_str(started) if started is not None else _day_str(_now())
    cfg = data.get("config")
    if isinstance(cfg, dict) and "coffee" not in cfg:
        focus_cfg = cfg.get("focus") if isinstance(cfg.get("focus"), dict) else {}
        thr = _num(focus_cfg.get("low_score_threshold"), DEFAULT_LOW_SCORE_THRESHOLD)
        cfg["coffee"] = {"enabled": True, "low_score_threshold": thr,
                         "cooldown_min": COFFEE_COOLDOWN_MIN}
        if isinstance(focus_cfg, dict):
            focus_cfg.pop("low_score_threshold", None)   # one home for the threshold: coffee
    return data


def _migrate_exercise_fields(data: dict) -> dict:
    """Backfill the progression + RPG fields on each stored exercise so an older file
    surfaces the whole shape without a config write. The single-`muscle` field is migrated
    to the `muscles` list: a legacy muscle string becomes [{muscle, pct:100}] and the old
    key is dropped (one source of truth); a missing one is auto-suggested from the name.
    Runs before _deep_fill, which only fills TOP-LEVEL keys, never inside list entries.
    Idempotent — a present field is left as the user set it."""
    if not isinstance(data, dict):
        return data
    cfg = data.get("config")
    exs = cfg.get("exercises") if isinstance(cfg, dict) else None
    if not isinstance(exs, list):
        return data
    for e in exs:
        if not isinstance(e, dict):
            continue
        reps = _int(e.get("reps"), DEFAULT_EXERCISE_REPS)
        if _int(e.get("target"), None) is None:
            e["target"] = reps
        if not _valid_muscles_list(e.get("muscles")):
            legacy = e.get("muscle")
            if isinstance(legacy, str) and legacy in MUSCLES:
                e["muscles"] = [{"muscle": legacy, "pct": 100}]
            else:
                e["muscles"] = _suggest_muscles(e.get("name") or "")
        e.pop("muscle", None)                       # drop the legacy single-muscle key
        if not (isinstance(e.get("stat"), str) and e["stat"].strip()):
            e["stat"] = DEFAULT_STAT
        if _int(e.get("days_since_done"), None) is None:
            e["days_since_done"] = 0
        if not isinstance(e.get("recent_actuals"), list):
            e["recent_actuals"] = []
        if "last_done" not in e:
            e["last_done"] = None
    return data


def _migrate_focus_activities(data: dict) -> dict:
    """Upgrade an old FLAT focus_activities file ([{id, name, weight}]) to the two-class
    shape ({kind, focus_boost, daily_limit|weight}). A known seed id (kafa/sah/setnja/
    disanje) takes the seed's kind + boost (+ daily_limit for the mandatory Kafa); an
    unknown id becomes a random activity at the default boost, keeping any weight. Runs
    before _deep_fill. Idempotent — an entry already carrying a valid kind is left alone."""
    if not isinstance(data, dict):
        return data
    cfg = data.get("config")
    fas = cfg.get("focus_activities") if isinstance(cfg, dict) else None
    if not isinstance(fas, list):
        return data
    seed_by_id = {a["id"]: a for a in DEFAULT_FOCUS_ACTIVITIES}
    for a in fas:
        if not isinstance(a, dict) or a.get("kind") in ACTIVITY_KINDS:
            continue                                # not a dict, or already upgraded
        seed = seed_by_id.get(a.get("id"))
        if seed:
            a["kind"] = seed["kind"]
            a["focus_boost"] = _num(a.get("focus_boost"), seed["focus_boost"])
            if seed["kind"] == "mandatory":
                a["daily_limit"] = seed.get("daily_limit", DEFAULT_DAILY_LIMIT)
                a.pop("weight", None)
            else:
                a["weight"] = _num(a.get("weight"), seed.get("weight", DEFAULT_ACTIVITY_WEIGHT))
        else:
            a["kind"] = "random"
            a["focus_boost"] = _num(a.get("focus_boost"), DEFAULT_FOCUS_BOOST)
            a["weight"] = _num(a.get("weight"), DEFAULT_ACTIVITY_WEIGHT)
    return data


def _migrate_sound_flags(data: dict) -> dict:
    """Backfill the per-reminder alert mute flags. config.sound is supplied by _deep_fill;
    here we only give each pre-existing custom_reminders entry a default `sound:true` (the
    flags are new, and absent means audible). Idempotent."""
    if not isinstance(data, dict):
        return data
    cfg = data.get("config")
    if not isinstance(cfg, dict):
        return data
    for c in cfg.get("custom_reminders") or []:
        if isinstance(c, dict) and not isinstance(c.get("sound"), bool):
            c["sound"] = True
    return data


def _migrate_history_sessions(data: dict) -> dict:
    """Upgrade a PRE-session history: each old entry was one record per calendar date,
    so treat it as a one-session day by copying its `date` into `work_day` (the profile
    groups on work_day). Idempotent; leaves started_at/ended_at absent on legacy rows."""
    if not isinstance(data, dict):
        return data
    hist = data.get("history")
    if not isinstance(hist, list):
        return data
    for r in hist:
        if isinstance(r, dict) and "work_day" not in r and isinstance(r.get("date"), str):
            r["work_day"] = r["date"]
    return data


def _write(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)        # atomic on Windows and POSIX


def save_state(state: dict) -> bool:
    """The ONE write path. Returns False on a failed persist instead of raising, so
    a wellness feature can never crash the server over a disk hiccup."""
    try:
        _write(state)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Downtime exclusion — the work-day clock must continue where it stopped, never
#  counting time the server was DOWN. The server keeps session.heartbeat fresh
#  (start_heartbeat); a stale heartbeat on the first op after a restart is downtime.
# --------------------------------------------------------------------------- #
def _apply_downtime(state: dict, now: float) -> bool:
    """If session.heartbeat is older than DOWNTIME_THRESHOLD_SEC the server was down;
    treat the gap exactly like a pause — add it to paused_total_sec, shift every timer
    (interval + custom) forward by it so nothing floods due, and push last_break_at
    forward (clamped to now) so downtime does not tank the score. Always restamps
    heartbeat to now. Returns True iff a gap was absorbed (the caller then persists)."""
    sess = state.get("session")
    if not isinstance(sess, dict):
        return False
    hb = _num(sess.get("heartbeat"), None)
    absorbed = False
    if hb is not None:
        gap = now - hb
        if gap > DOWNTIME_THRESHOLD_SEC:
            sess["paused_total_sec"] = _num(sess.get("paused_total_sec"), 0.0) + gap
            rem = state.get("reminders", {}) or {}
            for rtype in INTERVAL_TYPES:
                slot = rem.get(rtype)
                if isinstance(slot, dict) and slot.get("next_at") is not None:
                    slot["next_at"] = _num(slot.get("next_at"), now) + gap
            for slot in (rem.get("custom") or {}).values():
                if isinstance(slot, dict) and slot.get("next_at") is not None:
                    slot["next_at"] = _num(slot.get("next_at"), now) + gap
            lb = _num(sess.get("last_break_at"), None)
            if lb is not None:
                sess["last_break_at"] = min(now, lb + gap)
            absorbed = True
    sess["heartbeat"] = now
    return absorbed


def check_downtime() -> bool:
    """Run ONCE at server startup (before serving): absorb any downtime since the last
    persisted heartbeat, then stamp a fresh one. Safe on a fresh/absent/corrupt file."""
    now = _now()
    with _lock:
        state = load_state()
        _apply_downtime(state, now)
        save_state(state)
        return True


def heartbeat() -> bool:
    """Stamp session.heartbeat = now. Called by the daemon every HEARTBEAT_INTERVAL_SEC
    so a later restart can measure how long the server was down."""
    now = _now()
    with _lock:
        state = load_state()
        _apply_downtime(state, now)
        save_state(state)
        return True


def start_heartbeat(interval: float = HEARTBEAT_INTERVAL_SEC) -> None:
    """Absorb startup downtime, then keep the heartbeat fresh on a daemon thread. This
    daemon is OWNED by focus.py (server.py calls this once at boot); it is the only
    writer of session.heartbeat besides the ops themselves. Idempotent — one per process."""
    global _hb_started
    with _hb_lock:
        if _hb_started:
            return
        _hb_started = True
    check_downtime()

    def _loop():
        while True:
            time.sleep(interval)
            try:
                heartbeat()
            except Exception:
                pass                        # a heartbeat that dies must not sink the server

    threading.Thread(target=_loop, daemon=True).start()


# --------------------------------------------------------------------------- #
#  Score + due-ness (pure over a state dict)
# --------------------------------------------------------------------------- #
def _decay_scale(state: dict) -> float:
    """The auto-calibrated focus-decay multiplier (config.focus.decay_scale), clamped to
    [FOCUS_DECAY_SCALE_MIN, FOCUS_DECAY_SCALE_MAX]. The ONE reader — compute_score, the
    boost window and the view all go through it, so the bound is enforced in one place."""
    fc = (state.get("config", {}) or {}).get("focus", {}) or {}
    return _clamp(_num(fc.get("decay_scale"), FOCUS_DECAY_SCALE_BASE),
                  FOCUS_DECAY_SCALE_MIN, FOCUS_DECAY_SCALE_MAX)


def _effective_break(state: dict) -> float:
    """break_after_min stretched by the calibrated decay_scale — the window BOTH the
    primary score decay and the focus-activity boost decay over. Never <= 0."""
    fc = (state.get("config", {}) or {}).get("focus", {}) or {}
    bam = _num(fc.get("break_after_min"), DEFAULT_BREAK_AFTER_MIN)
    if bam <= 0:
        bam = DEFAULT_BREAK_AFTER_MIN
    return bam * _decay_scale(state)


def _current_boost(state: dict, now: float) -> float:
    """The decaying focus-activity bump remaining now: boost_amount linearly decaying to
    0 over the effective break window since boost_at. Frozen while paused (reads paused_at)
    and max(0,...)-clamped like every span here. 0 when no boost is active."""
    sess = state.get("session", {}) or {}
    amount = _num(sess.get("boost_amount"), 0.0)
    at = sess.get("boost_at")
    if amount <= 0 or at is None:
        return 0.0
    paused = bool(sess.get("paused"))
    paused_at = sess.get("paused_at")
    effective_now = paused_at if (paused and paused_at is not None) else now
    window = _effective_break(state)
    mins = max(0.0, (effective_now - _num(at, effective_now)) / 60.0)
    frac = _clamp(mins / window, 0.0, 1.0)
    return amount * (1.0 - frac)


def _nudge_toward_baseline(scale: float) -> float:
    """Move `scale` one step toward FOCUS_DECAY_SCALE_BASE (a coffee-ack-while-low says the
    pessimism was warranted). Never overshoots the baseline."""
    if scale > FOCUS_DECAY_SCALE_BASE:
        return max(FOCUS_DECAY_SCALE_BASE, scale - FOCUS_DECAY_SCALE_STEP)
    if scale < FOCUS_DECAY_SCALE_BASE:
        return min(FOCUS_DECAY_SCALE_BASE, scale + FOCUS_DECAY_SCALE_STEP)
    return scale


def compute_score(state: dict, now=None, activity=None) -> int:
    """The 0-100 focus score. See the module docstring for the formula. Frozen at
    its break value while paused (reads paused_at, not the live clock)."""
    now = _now() if now is None else now
    sess = state.get("session", {}) or {}
    effective_break_min = _effective_break(state)

    paused = bool(sess.get("paused"))
    paused_at = sess.get("paused_at")
    effective_now = paused_at if (paused and paused_at is not None) else now
    last_break = sess.get("last_break_at")
    if last_break is None:
        last_break = sess.get("started_at", now)

    minutes_since_break = max(0.0, (effective_now - last_break) / 60.0)
    frac = _clamp(minutes_since_break / effective_break_min, 0.0, 1.0)
    score = 100.0 * (1.0 - frac)

    # A decaying bump from a recently-acked focus activity (option 1: instant + decay).
    score += _current_boost(state, now)

    # SECONDARY: a light "in flow" nudge, neutral when no signal is supplied.
    if activity and activity.get("last_event_age_sec") is not None:
        age = _num(activity.get("last_event_age_sec"), None)
        if age is not None and 0 <= age < ACTIVITY_FRESH_SEC:
            score += ACTIVITY_BONUS

    return int(_clamp(round(score), 0, 100))


def _reminders_off(state: dict) -> bool:
    """Nothing is ever due when the feature is disabled, the session is paused, or the
    work-day has been explicitly ended. The single gate every due-check shares."""
    cfg = state.get("config", {}) or {}
    if not cfg.get("enabled"):
        return True
    sess = state.get("session", {}) or {}
    return bool(sess.get("paused")) or bool(sess.get("day_ended"))


def _category_enabled(state: dict, category: str) -> bool:
    """Whether a reminder CATEGORY (water/stretch/exercise/fokus) is turned on. Absent →
    enabled: an old file predates config.reminders_enabled, and absent means all-true. Only
    an explicit False disables, so a hand-edited / partial map never silently mutes one."""
    re_cfg = (state.get("config", {}) or {}).get("reminders_enabled", {}) or {}
    return re_cfg.get(category) is not False


def _interval_due(state: dict, rtype: str, now: float) -> bool:
    if _reminders_off(state):
        return False
    if not _category_enabled(state, rtype):     # water/stretch/exercise turned off
        return False
    na = ((state.get("reminders", {}) or {}).get(rtype, {}) or {}).get("next_at")
    if na is None:
        return False
    try:
        return now >= float(na)
    except (TypeError, ValueError):
        return False


def _custom_due(state: dict, cid: str, now: float) -> bool:
    if _reminders_off(state):
        return False
    slot = ((state.get("reminders", {}) or {}).get("custom", {}) or {}).get(cid)
    if not isinstance(slot, dict) or slot.get("next_at") is None:
        return False
    try:
        return now >= float(slot.get("next_at"))
    except (TypeError, ValueError):
        return False


def _coffee_due(state: dict, now: float, score: int) -> bool:
    """The Fokus/coffee slot is due when — past its cooldown and not snoozed — EITHER a
    mandatory activity still owes today's quota (its evenly-spaced checkpoint has passed
    or the score is low) OR the score has simply dropped below the threshold (the random
    draw). The pick that fires is chosen by _ensure_activity_pick: mandatory first."""
    if _reminders_off(state):
        return False
    if not _category_enabled(state, "fokus"):   # the whole coffee/focus family turned off
        return False
    coffee_cfg = (state.get("config", {}) or {}).get("coffee", {}) or {}
    if not coffee_cfg.get("enabled", True):     # coffee can be individually disabled
        return False
    cooldown = _num(coffee_cfg.get("cooldown_min"), COFFEE_COOLDOWN_MIN)
    coffee = (state.get("reminders", {}) or {}).get("coffee", {}) or {}
    su = coffee.get("snooze_until")             # an explicit snooze / infocus deferral suppresses it
    if su is not None:
        try:
            if now < float(su):
                return False
        except (TypeError, ValueError):
            pass
    last = coffee.get("last_at")
    if last is not None:
        try:
            if (now - float(last)) < cooldown * 60:
                return False        # still cooling down since the last coffee nudge
        except (TypeError, ValueError):
            pass
    if _mandatory_pending(state, now, score) is not None:
        return True
    threshold = _num(coffee_cfg.get("low_score_threshold"), DEFAULT_LOW_SCORE_THRESHOLD)
    return score < threshold                    # PRODUCTIVITY-driven: the random draw on a low score


def _activity_done_today(state: dict, aid: str) -> int:
    """How many times activity `aid` was acked this work-day — read straight from
    metrics.coffee.by_id (reset at new_day), which doubles as the mandatory done_today."""
    by = ((state.get("metrics", {}) or {}).get("coffee", {}) or {}).get("by_id")
    return _int(by.get(aid), 0) if isinstance(by, dict) else 0


def _mandatory_pending(state: dict, now: float, score) -> dict | None:
    """The first mandatory activity (config order) that still owes quota AND should fire
    now — either enough WORKED time has elapsed to cross its next evenly-spaced checkpoint
    (k * MANDATORY_WORKDAY_MIN / limit) or the score has dropped below the threshold.
    None when every mandatory quota is either met or not yet due."""
    coffee_cfg = (state.get("config", {}) or {}).get("coffee", {}) or {}
    threshold = _num(coffee_cfg.get("low_score_threshold"), DEFAULT_LOW_SCORE_THRESHOLD)
    elapsed_min = _elapsed_worked_sec(state, now) / 60.0
    for a in _mandatory_activities(state):
        limit = a["daily_limit"]
        if limit <= 0:
            continue
        done = _activity_done_today(state, a["id"])
        if done >= limit:
            continue                            # quota already met today
        spacing = MANDATORY_WORKDAY_MIN / limit
        should_have = int(elapsed_min // spacing) if spacing > 0 else limit
        if done < should_have or score < threshold:
            return a
    return None


def _elapsed_worked_sec(state: dict, now: float) -> float:
    """Seconds actually worked since the work-day started: wall span minus pauses
    minus downtime (both live in paused_total_sec) minus any currently-open pause.
    max(0,...)-clamped like every other span in this module."""
    sess = state.get("session", {}) or {}
    started = _num(sess.get("started_at"), now)
    paused_total = _num(sess.get("paused_total_sec"), 0.0)
    open_pause = 0.0
    if sess.get("paused") and sess.get("paused_at") is not None:
        open_pause = max(0.0, now - _num(sess.get("paused_at"), now))
    return max(0.0, (now - started) - paused_total - open_pause)


def _day_metrics_core(state: dict, now: float) -> dict:
    """The current work-day counters, read straight from the live metrics (which are
    reset to 0 at new_day). The ONE place these are shaped — the live `day` block in the
    view and the persisted end-day report both call it, so the preview can never disagree
    with the record. claude_sessions/commits are added by the report only (external)."""
    sess = state.get("session", {}) or {}
    m = state.get("metrics", {}) or {}
    ex = m.get("exercise") if isinstance(m.get("exercise"), dict) else {}
    by = ex.get("by_id") if isinstance(ex.get("by_id"), dict) else {}
    by_id = {k: _int(v.get("reps_total"), 0) for k, v in by.items()
             if isinstance(v, dict) and _int(v.get("reps_total"), 0) > 0}
    return {
        "hours_worked": round(_elapsed_worked_sec(state, now) / 3600.0, 4),
        "water": _int((m.get("water") or {}).get("count"), 0),
        "stretch": _int((m.get("stretch") or {}).get("count"), 0),
        "exercise_total": _int(ex.get("reps_total"), 0),
        "exercise_sets": _int(ex.get("count"), 0),      # number of logged sets (movement cadence)
        "exercise_by_id": by_id,
        "pauses": _int((m.get("pauses") or {}).get("count"), 0),
        "snoozes": _int((m.get("snoozes") or {}).get("count"), 0),
        "work_blocks": _int(sess.get("work_blocks"), 1),
    }


def _day_summary(state: dict, now: float) -> dict:
    """The live `day` block on the view — the work-day metrics so far, plus the day's
    identity. claude_sessions/commits are NOT here (they are external, gathered only on
    the explicit end-day action; a poll must not sweep git)."""
    sess = state.get("session", {}) or {}
    started = _num(sess.get("started_at"), now)
    core = _day_metrics_core(state, now)
    core.update({
        "work_day": sess.get("work_day") or _day_str(started),
        "day_ended": bool(sess.get("day_ended")),
        "started_at": started,
    })
    return core


def _view(state: dict, now: float, activity=None) -> dict:
    """The response body: the state with a freshly computed score, a `due` flag on
    every reminder (interval, coffee and each custom one), and a live `day` summary.
    The score is written back into the state dict so a caller that persists (a
    mutation, or a day-roll) stores the current snapshot. `history` is stripped from
    the poll response — it grows one row per day and is served by /api/focus/profile."""
    score = compute_score(state, now, activity)
    state["focus"] = {"score": score, "computed_at": now}
    resp = copy.deepcopy(state)
    resp["focus"]["decay_scale"] = _decay_scale(state)   # surface the calibrated scale
    for rtype in INTERVAL_TYPES:
        resp["reminders"][rtype]["due"] = _interval_due(state, rtype, now)
    resp["reminders"]["coffee"]["due"] = _coffee_due(state, now, score)
    custom = resp["reminders"].get("custom")
    if isinstance(custom, dict):
        for cid, slot in custom.items():
            if isinstance(slot, dict):
                slot["due"] = _custom_due(state, cid, now)
    resp["day"] = _day_summary(state, now)
    resp["mandatory"] = _mandatory_state(state, now)     # per-mandatory quota (done/remaining)
    resp.pop("history", None)
    return resp


# --------------------------------------------------------------------------- #
#  Day rollover — today's counters reset at midnight; the streak breaks on a
#  fully missed day. Advancing the streak is an ACK's job (_touch_day), so a mere
#  day change never inflates it.
# --------------------------------------------------------------------------- #
def _roll(state: dict, now: float) -> bool:
    """Fold the calendar into the metrics. Returns True if anything changed (so a
    read path only persists when there is a real change, not on every 15s poll)."""
    m = state.get("metrics", {}) or {}
    today = _day_str(now)
    if m.get("last_active_day") == today:
        return False
    changed = False
    for key, field in (("water", "today"), ("stretch", "today"), ("exercise", "today_reps")):
        d = m.get(key)
        if isinstance(d, dict) and d.get(field):
            d[field] = 0
            changed = True
    ld = _parse_day(m.get("last_active_day"))
    td = _parse_day(today)
    if ld is not None and td is not None and (td - ld).days >= 2:
        if _int(m.get("streak_days"), 0) != 0:
            m["streak_days"] = 0        # a full day missed → streak broken
            changed = True
    if _refresh_exercise_days(state, now):   # per-exercise days-since-done tracking
        changed = True
    return changed


def _refresh_exercise_days(state: dict, now: float) -> bool:
    """Recompute `days_since_done` = calendar days since each exercise's last completion.
    Idempotent (a pure function of last_done and today), so it is safe to run on any op
    that rolls the day. An exercise never done keeps days_since_done at 0. Returns True
    iff a value changed (the caller then persists)."""
    exs = (state.get("config", {}) or {}).get("exercises")
    if not isinstance(exs, list):
        return False
    today = _parse_day(_day_str(now))
    if today is None:
        return False
    changed = False
    for ce in exs:
        if not isinstance(ce, dict):
            continue
        ld = _parse_day(ce.get("last_done"))
        if ld is None:
            continue
        new = max(0, (today - ld).days)
        if _int(ce.get("days_since_done"), 0) != new:
            ce["days_since_done"] = new
            changed = True
    return changed


def _touch_day(state: dict, now: float) -> None:
    """Record activity for today and advance the day streak. Called by ack only."""
    m = state["metrics"]
    today = _day_str(now)
    last = m.get("last_active_day")
    if last == today:
        return                          # already counted an activity today
    ld = _parse_day(last)
    td = _parse_day(today)
    if ld is None or td is None:
        m["streak_days"] = 1
    elif (td - ld).days == 1:
        m["streak_days"] = _int(m.get("streak_days"), 0) + 1   # consecutive day
    else:
        m["streak_days"] = 1            # gap (or clock moved back) → fresh streak
    m["last_active_day"] = today


def _interval_min(state: dict, rtype: str) -> float:
    return _num((state["config"].get("intervals_min", {}) or {}).get(rtype),
                DEFAULT_INTERVALS_MIN[rtype])


def _snooze_min(state: dict) -> float:
    sm = _num((state.get("config", {}) or {}).get("snooze_min"), DEFAULT_SNOOZE_MIN)
    return sm if sm > 0 else DEFAULT_SNOOZE_MIN


def _custom_interval(state: dict, cid: str) -> float:
    """The interval (minutes) for a custom reminder, read from config (its source of
    truth) — falls back to snooze_min if the config row vanished under us."""
    for c in (state.get("config", {}) or {}).get("custom_reminders") or []:
        if isinstance(c, dict) and c.get("id") == cid:
            iv = _num(c.get("interval_min"), None)
            if iv is not None and iv >= 1:
                return iv
    return _snooze_min(state)


def _sync_custom_reminders(state: dict, now: float, force: bool = False) -> None:
    """Reconcile reminders.custom against config.custom_reminders: a NEW id gets a fresh
    next_at (now + its interval), an existing id keeps its schedule, a removed id is
    dropped. `force` reschedules every slot from now (used on new-day). Config is the
    source of truth for the interval; the slot only carries last_at/next_at."""
    customs = (state.get("config", {}) or {}).get("custom_reminders") or []
    rem = state["reminders"]
    old = rem.get("custom") if isinstance(rem.get("custom"), dict) else {}
    new = {}
    for c in customs:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or not cid:
            continue
        interval = _num(c.get("interval_min"), 1) or 1
        prev = old.get(cid)
        if not force and isinstance(prev, dict) and prev.get("next_at") is not None:
            new[cid] = {"last_at": prev.get("last_at"), "next_at": prev.get("next_at")}
        else:
            new[cid] = {"last_at": (prev.get("last_at") if isinstance(prev, dict) else None),
                        "next_at": now + interval * 60}
    rem["custom"] = new


# --------------------------------------------------------------------------- #
#  Exercise selection — a configurable set, picked weighted-random with no
#  immediate repeat. The pick is PERSISTED when the reminder goes due so the poll
#  does not re-roll it; ack/skip clear it and stamp session.last_exercise.
# --------------------------------------------------------------------------- #
def _slug(name: str) -> str:
    """A stable id from a name (lower, non-alnum → single dashes). Used only when an
    exercise arrives without an explicit id."""
    s = "".join(c if c.isalnum() else "-" for c in str(name).strip().lower())
    return "-".join(p for p in s.split("-") if p)


def _normalize_exercise(e, idx=0):
    """Tolerant read of ONE stored exercise into {id,name,emoji,weight,reps,target}, or
    None if unusable (missing name / bad reps). `target` is the live suggestion (falls
    back to reps when absent/invalid). Lenient like the other state readers — strict
    validation lives on the config WRITE path (update_config)."""
    if not isinstance(e, dict):
        return None
    name = e.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    reps = _int(e.get("reps"), None)
    if reps is None or reps < 1 or reps > MAX_EXERCISE_REPS:
        return None
    target = _int(e.get("target"), None)
    if target is None or target < 1 or target > MAX_EXERCISE_REPS:
        target = reps
    weight = _num(e.get("weight"), None)
    if weight is None or weight < 0:
        weight = 0.0
    eid = e.get("id")
    if not isinstance(eid, str) or not eid.strip():
        eid = _slug(name) or ("ex%d" % (idx + 1))
    emoji = e.get("emoji")
    emoji = emoji if isinstance(emoji, str) else ""
    return {"id": eid.strip(), "name": name.strip(), "emoji": emoji,
            "weight": float(weight), "reps": reps, "target": target}


def _exercise_pool(state: dict):
    """The usable exercises for a pick: config.exercises filtered to valid rows,
    falling back to the seeded default set so a pick is ALWAYS possible."""
    raw = (state.get("config", {}) or {}).get("exercises")
    out = []
    if isinstance(raw, list):
        for i, e in enumerate(raw):
            ne = _normalize_exercise(e, i)
            if ne is not None:
                out.append(ne)
    if not out:
        out = [_normalize_exercise(e, i) for i, e in enumerate(DEFAULT_EXERCISES)]
    return out


def _weighted_choice(pool, last_id=None, exclude_last: bool = True):
    """Weighted-random pick over `pool` (dicts each carrying 'id' and 'weight'). When
    exclude_last and the pool has more than one item, `last_id` is dropped first so a
    pick never repeats back-to-back (unless that would empty the pool). All-zero weights
    fall back to uniform. Returns a REFERENCE into the pool, or None on an empty pool.
    The ONE weighted-pick primitive — exercises and Fokus activities both use it."""
    if not pool:
        return None
    candidates = pool
    if exclude_last and last_id is not None and len(pool) > 1:
        trimmed = [e for e in pool if e.get("id") != last_id]
        if trimmed:
            candidates = trimmed
    total = sum(max(0.0, _num(e.get("weight"), 0.0)) for e in candidates)
    if total <= 0:
        return random.choice(candidates)             # all-zero weights → uniform
    r = random.random() * total
    acc = 0.0
    for e in candidates:
        acc += max(0.0, _num(e.get("weight"), 0.0))
        if r < acc:
            return e
    return candidates[-1]


def _exercise_pick_dict(choice) -> dict:
    """The compact pick the exercise card carries. `reps` is the LIVE suggestion (the
    exercise's progression target), duplicated as `target` for the frontend/Backend-B."""
    target = _int(choice.get("target"), None)
    if target is None or target < 1:
        target = _int(choice.get("reps"), DEFAULT_EXERCISE_REPS)
    return {"id": choice["id"], "name": choice["name"],
            "emoji": choice.get("emoji", ""), "reps": target, "target": target}


def _pick_exercise(state: dict, exclude_last: bool = True) -> dict:
    """Weighted-random pick over the exercise pool, excluding session.last_exercise so
    it never repeats back-to-back. Returns the compact {id,name,emoji,reps,target} the
    card shows; `reps` == `target` (the live progression suggestion)."""
    pool = _exercise_pool(state)
    if not pool:
        return {"id": "", "name": "", "emoji": "",
                "reps": DEFAULT_EXERCISE_REPS, "target": DEFAULT_EXERCISE_REPS}
    last = (state.get("session", {}) or {}).get("last_exercise")
    choice = _weighted_choice(pool, last, exclude_last)
    return _exercise_pick_dict(choice)


# --------------------------------------------------------------------------- #
#  Fokus activity pool — the score-driven nudge that generalizes coffee. Same
#  weighted-pick / no-immediate-repeat discipline as exercises, persisted on the
#  coffee slot as reminders.coffee.activity.
# --------------------------------------------------------------------------- #
def _normalize_activity(a, idx=0):
    """Tolerant read of ONE stored Fokus activity into {id,name,kind,focus_boost,weight,
    daily_limit}, or None if unusable (missing name). Lenient like the other state readers:
    an absent/invalid kind falls back to 'random', boost/weight/limit to their defaults."""
    if not isinstance(a, dict):
        return None
    name = a.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    aid = a.get("id")
    if not isinstance(aid, str) or not aid.strip():
        aid = _slug(name) or ("act%d" % (idx + 1))
    kind = a.get("kind") if a.get("kind") in ACTIVITY_KINDS else "random"
    boost = _num(a.get("focus_boost"), None)
    boost = DEFAULT_FOCUS_BOOST if boost is None else boost
    boost = _clamp(boost, 0.0, float(MAX_FOCUS_BOOST))
    weight = _num(a.get("weight"), None)
    if weight is None or weight < 0:
        weight = 0.0
    limit = _int(a.get("daily_limit"), None)
    if limit is None or limit < 1:
        limit = DEFAULT_DAILY_LIMIT
    return {"id": aid.strip(), "name": name.strip(), "kind": kind,
            "focus_boost": float(boost), "weight": float(weight), "daily_limit": limit}


def _all_activities(state: dict):
    """Every usable Fokus activity: config.focus_activities filtered to valid rows, falling
    back to the seeded defaults so a pick is ALWAYS possible."""
    raw = (state.get("config", {}) or {}).get("focus_activities")
    out = []
    if isinstance(raw, list):
        for i, a in enumerate(raw):
            na = _normalize_activity(a, i)
            if na is not None:
                out.append(na)
    if not out:
        out = [_normalize_activity(a, i) for i, a in enumerate(DEFAULT_FOCUS_ACTIVITIES)]
    return out


def _random_activity_pool(state: dict):
    """The weighted-draw pool: only kind=='random' activities (mandatory ones are proposed
    by quota, never drawn). Falls back to the seeded random defaults if none remain."""
    pool = [a for a in _all_activities(state) if a["kind"] == "random"]
    if not pool:
        pool = [a for a in (_normalize_activity(x, i)
                            for i, x in enumerate(DEFAULT_FOCUS_ACTIVITIES)) if a["kind"] == "random"]
    return pool


def _mandatory_activities(state: dict):
    """The quota activities (kind=='mandatory'), in config order. May be empty (a user is
    free to keep only random nudges)."""
    return [a for a in _all_activities(state) if a["kind"] == "mandatory"]


def _pick_activity(state: dict, exclude_last: bool = True):
    """Weighted-random RANDOM Fokus activity, excluding session.last_activity (no immediate
    repeat). Returns the {id,name,kind,focus_boost} the coffee slot carries, or None if the
    random pool is empty."""
    pool = _random_activity_pool(state)
    if not pool:
        return None
    last = (state.get("session", {}) or {}).get("last_activity")
    choice = _weighted_choice(pool, last, exclude_last)
    if not choice:
        return None
    return {"id": choice["id"], "name": choice["name"], "kind": "random",
            "focus_boost": choice.get("focus_boost", DEFAULT_FOCUS_BOOST)}


def _ensure_activity_pick(state: dict, now: float, activity=None) -> bool:
    """When the coffee/Fokus nudge is due and no activity is stored, pick one and persist
    it under reminders.coffee.activity: a mandatory with remaining quota is preferred, else
    a weighted random draw. Returns True if it changed state. The _reminders_off gate (via
    _coffee_due) means a disabled/paused/ended day never picks."""
    score = compute_score(state, now, activity)
    if not _coffee_due(state, now, score):
        return False
    coffee = (state.get("reminders", {}) or {}).get("coffee")
    if not isinstance(coffee, dict) or coffee.get("activity"):
        return False                                 # no slot, or already picked
    mand = _mandatory_pending(state, now, score)
    if mand is not None:
        coffee["activity"] = {"id": mand["id"], "name": mand["name"],
                              "kind": "mandatory", "focus_boost": mand["focus_boost"]}
    else:
        coffee["activity"] = _pick_activity(state)   # random draw
    return coffee.get("activity") is not None


def _mandatory_state(state: dict, now: float):
    """The live quota state per mandatory activity for the view + the day report: how many
    of today's quota are done and how many remain."""
    out = []
    for a in _mandatory_activities(state):
        done = _activity_done_today(state, a["id"])
        limit = a["daily_limit"]
        out.append({"id": a["id"], "name": a["name"], "focus_boost": a["focus_boost"],
                    "daily_limit": limit, "done_today": done,
                    "remaining": max(0, limit - done)})
    return out


# --------------------------------------------------------------------------- #
#  Exercise → muscle/element auto-suggest + the once-per-day target progression.
# --------------------------------------------------------------------------- #
def _fold(s: str) -> str:
    """Accent-fold a Serbian name for substring matching (č/ć→c, š→s, ž→z, đ→dj)."""
    s = str(s).strip().lower()
    for a, b in (("č", "c"), ("ć", "c"), ("š", "s"), ("ž", "z"), ("đ", "dj")):
        s = s.replace(a, b)
    return s


def _suggest_muscles(name: str) -> list:
    """Guess the muscle SPLIT from an exercise name via _MUSCLE_SPLIT_HINTS; default
    [grudi 100] when nothing matches. Returns a fresh [{muscle, pct}] list."""
    folded = _fold(name)
    for key, split in _MUSCLE_SPLIT_HINTS:
        if key in folded:
            return [{"muscle": m, "pct": p} for m, p in split]
    return [{"muscle": "grudi", "pct": 100}]


def _valid_muscles_list(x) -> bool:
    """True iff `x` is a non-empty list whose every entry is {muscle in MUSCLES, numeric
    pct}. Used to decide whether a stored exercise already carries the new muscles shape."""
    if not isinstance(x, list) or not x:
        return False
    for m in x:
        if not (isinstance(m, dict) and m.get("muscle") in MUSCLES
                and _num(m.get("pct"), None) is not None):
            return False
    return True


def _exercise_muscles(e: dict):
    """One exercise's muscle split as [(muscle, pct), ...]. Reads the new `muscles` list;
    falls back to a legacy single `muscle` string as 100%. Drops zero/negative pcts and
    unknown muscles. The ONE reader the RPG derivation uses, so legacy and new files agree."""
    out = []
    raw = e.get("muscles")
    if isinstance(raw, list):
        for m in raw:
            if isinstance(m, dict) and m.get("muscle") in MUSCLES:
                pct = _num(m.get("pct"), 0.0)
                if pct > 0:
                    out.append((m["muscle"], pct))
    if not out:
        legacy = e.get("muscle")
        if isinstance(legacy, str) and legacy in MUSCLES:
            out = [(legacy, 100.0)]
    return out


def _normalize_muscle_pcts(parsed):
    """Soft-normalize a validated [{muscle, pct}] list: drop non-positive pcts, and if the
    total departs from 100 by more than MUSCLE_PCT_SUM_TOL, rescale to sum 100 (relative
    proportions kept). Never hard-rejects; returns [] when nothing usable so the caller
    falls back to auto-suggest. This is the 'warn/normalize, don't reject' soft check."""
    entries = [{"muscle": m["muscle"], "pct": float(m["pct"])} for m in parsed if m["pct"] > 0]
    if not entries:
        return []
    total = sum(e["pct"] for e in entries)
    if total > 0 and abs(total - 100.0) > MUSCLE_PCT_SUM_TOL:
        for e in entries:
            e["pct"] = round(e["pct"] * 100.0 / total, 2)
    return entries


def _renormalize_weights(entries) -> None:
    """Rescale each entry's `weight` so the set sums to 1.0, preserving relative
    proportions. Applied when a config edit DELETES an exercise / random activity, so the
    surviving pool is never left degenerate (e.g. a lone item at weight 0.1, or all-zero).
    All-zero → equal weights. Mutates in place; a no-op on an empty set."""
    n = len(entries)
    if n == 0:
        return
    total = sum(max(0.0, _num(e.get("weight"), 0.0)) for e in entries)
    if total <= 0:
        for e in entries:
            e["weight"] = round(1.0 / n, 6)
    else:
        for e in entries:
            e["weight"] = round(max(0.0, _num(e.get("weight"), 0.0)) / total, 6)


def _progress_target(target, actual) -> int:
    """The confirmed once-per-day target adjustment. actual >= target → grow by a
    diminishing step max(1, round(24 / (8 + target))); actual < target → ease down
    toward actual by 0.6 of the shortfall. Never below 1."""
    target = max(1, _int(target, 1))
    actual = _int(actual, 0)
    if actual >= target:
        return target + max(1, round(EXERCISE_STEP_NUM / (8 + target)))
    return max(1, round(target - (target - actual) * 0.6))


def _find_exercise_config(state: dict, eid: str):
    """The real (mutable, persisted) config.exercises entry for `eid`, or None."""
    for ce in (state.get("config", {}) or {}).get("exercises") or []:
        if isinstance(ce, dict) and ce.get("id") == eid:
            return ce
    return None


def _apply_exercise_progression(state: dict, eid: str, actual, now: float) -> None:
    """Record `actual` into the exercise's rolling window and — ONCE per calendar day
    (the first completion of this exercise) — adjust its `target`. A second set the same
    day is recorded but does not re-adjust the target."""
    ce = _find_exercise_config(state, eid)
    if ce is None:
        return
    ra = ce.get("recent_actuals")
    if not isinstance(ra, list):
        ra = ce["recent_actuals"] = []
    ra.append(_int(actual, 0))
    if len(ra) > MAX_RECENT_ACTUALS:
        del ra[:len(ra) - MAX_RECENT_ACTUALS]
    today = _day_str(now)
    if ce.get("last_done") == today:
        return                                       # already adjusted the target today
    base = _int(ce.get("target"), None)
    if base is None or base < 1:
        base = _int(ce.get("reps"), DEFAULT_EXERCISE_REPS)
    ce["target"] = _progress_target(base, actual)
    ce["last_done"] = today
    ce["days_since_done"] = 0


def _ensure_exercise_pick(state: dict, now: float) -> bool:
    """When the exercise reminder is due and no pick is stored, choose one and
    persist it. Returns True if it changed state (so a read path knows to save).
    Not-due, disabled or paused → no pick (that's what _interval_due encodes)."""
    if not _interval_due(state, "exercise", now):
        return False
    rem = (state.get("reminders", {}) or {}).get("exercise")
    if not isinstance(rem, dict) or rem.get("exercise"):
        return False                                 # already picked; keep it stable
    rem["exercise"] = _pick_exercise(state)
    return True


def _resolve_exercise_reps(value, picked, state):
    """Reps to log for an exercise ack: the user's typed value when valid, else the
    picked exercise's reps (else the pool's first default). None → 400 for an
    out-of-range typed value."""
    if value is None or value == "":
        if picked:
            r = _int(picked.get("reps"), None)
            if r is not None and 1 <= r <= MAX_EXERCISE_REPS:
                return r
        pool = _exercise_pool(state)
        return pool[0]["reps"] if pool else DEFAULT_EXERCISE_REPS
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v <= 0 or v > MAX_EXERCISE_REPS:
        return None
    return v


# --------------------------------------------------------------------------- #
#  Public operations — each returns (view_or_error, http_code), the same shape
#  patch_triage / launch_claude use, so the routes stay one line.
# --------------------------------------------------------------------------- #
def get_view(activity=None) -> dict:
    """GET /api/focus — the full state with `due` per reminder and a fresh score.
    Persists only when the day actually rolled (the score is recomputed each call,
    so it need not be written on every poll)."""
    now = _now()
    with _lock:
        state = load_state()
        changed = _roll(state, now)
        if _ensure_exercise_pick(state, now):   # first poll that finds it due picks + persists
            changed = True
        if _ensure_activity_pick(state, now, activity):   # coffee/Fokus activity pick
            changed = True
        resp = _view(state, now, activity)
        if changed:
            save_state(state)
        return resp


def set_pause(paused, activity=None):
    """POST /api/focus/pause {paused: bool}. Idempotent: pausing while paused (or
    resuming while running) is a no-op. On pause, freeze and count a break; on
    resume, exclude the paused span from every timer so nothing floods due."""
    if not isinstance(paused, bool):
        return {"error": "paused must be a boolean"}, 400
    now = _now()
    with _lock:
        state = load_state()
        sess = state["session"]
        if paused and not sess.get("paused"):
            sess["paused"] = True
            sess["paused_at"] = now
            sess["last_break_at"] = now             # a pause counts as a break
            sess["boost_amount"] = 0.0              # a real break supersedes the coffee bump
            sess["boost_at"] = None
            pm = state["metrics"].setdefault("pauses", {"count": 0})
            pm["count"] = _int(pm.get("count"), 0) + 1
        elif not paused and sess.get("paused"):
            pa = sess.get("paused_at")
            delta = max(0.0, now - pa) if pa is not None else 0.0
            sess["paused_total_sec"] = _num(sess.get("paused_total_sec"), 0.0) + delta
            for rtype in INTERVAL_TYPES:
                na = state["reminders"][rtype].get("next_at")
                if na is not None:
                    # shift the timer by exactly the paused span → the pause is
                    # excluded from "elapsed since next_at", no catch-up burst.
                    state["reminders"][rtype]["next_at"] = float(na) + delta
            for slot in (state["reminders"].get("custom") or {}).values():
                if isinstance(slot, dict) and slot.get("next_at") is not None:
                    slot["next_at"] = _num(slot.get("next_at"), now) + delta
            sess["paused"] = False
            sess["paused_at"] = None
            sess["last_break_at"] = now             # break ends here → time-since-break restarts
            sess["work_blocks"] = _int(sess.get("work_blocks"), 1) + 1   # a resume opens a new block
        _roll(state, now)
        _ensure_exercise_pick(state, now)           # a resume may leave the exercise due
        _ensure_activity_pick(state, now, activity)
        save_state(state)
        return _view(state, now, activity), 200


def ack(rtype, value=None, activity=None, cid=None):
    """POST /api/focus/ack {type, value?, id?}. Log the reminder's metric and reschedule
    its next_at (coffee has no timer — it just records that it fired). A custom reminder
    (type='custom', id=<id>) counts an ack in metrics.custom and reschedules on its own
    interval. Advances the day streak."""
    is_custom = rtype == "custom"
    if not is_custom and rtype not in REMINDER_TYPES:
        return {"error": "unknown reminder type"}, 400
    now = _now()
    with _lock:
        state = load_state()
        _roll(state, now)
        m = state["metrics"]
        rem = state["reminders"]
        if is_custom:
            slot = (rem.get("custom") or {}).get(cid) if cid else None
            if not isinstance(slot, dict):
                return {"error": "unknown custom reminder"}, 400
            slot["last_at"] = now
            slot["next_at"] = now + _custom_interval(state, cid) * 60
            cm = m.get("custom")
            if not isinstance(cm, dict):
                cm = m["custom"] = {}
            entry = cm.get(cid)
            if not isinstance(entry, dict):
                entry = cm[cid] = {"count": 0}
            entry["count"] = _int(entry.get("count"), 0) + 1
        elif rtype == "coffee":
            coffee = rem["coffee"]
            coffee_cfg = (state.get("config", {}) or {}).get("coffee", {}) or {}
            threshold = _num(coffee_cfg.get("low_score_threshold"), DEFAULT_LOW_SCORE_THRESHOLD)
            score_before = compute_score(state, now, activity)   # pre-boost, for calibration
            coffee["last_at"] = now                  # fired; no timer to reschedule
            coffee.pop("snooze_until", None)
            picked_act = coffee.get("activity") if isinstance(coffee.get("activity"), dict) else None
            cm = m.get("coffee")
            if not isinstance(cm, dict):
                cm = m["coffee"] = {"count": 0, "by_id": {}}
            cm["count"] = _int(cm.get("count"), 0) + 1
            if picked_act and picked_act.get("id"):
                by = cm.get("by_id")
                if not isinstance(by, dict):
                    by = cm["by_id"] = {}
                # by_id doubles as each mandatory activity's done_today → this advances the quota
                by[picked_act["id"]] = _int(by.get(picked_act["id"]), 0) + 1
                state["session"]["last_activity"] = picked_act["id"]   # no immediate repeat
            # apply the decaying focus boost (option 1); never LOWER a still-live bump
            boost = _clamp(_num(picked_act.get("focus_boost"), 0.0) if picked_act else 0.0,
                           0.0, float(MAX_FOCUS_BOOST))
            new_amount = max(boost, _current_boost(state, now))
            if new_amount > 0:
                state["session"]["boost_amount"] = new_amount
                state["session"]["boost_at"] = now
            # calibrate: acking WHILE the score was genuinely low validates the pessimism
            if score_before < threshold:
                fc = state["config"].setdefault("focus", {})
                fc["decay_scale"] = round(_nudge_toward_baseline(_decay_scale(state)), 4)
            coffee["activity"] = None                # clear the pick → next due re-rolls
        elif rtype == "exercise":
            ex_rem = rem["exercise"] if isinstance(rem.get("exercise"), dict) else {}
            picked = ex_rem.get("exercise") if isinstance(ex_rem.get("exercise"), dict) else None
            reps = _resolve_exercise_reps(value, picked, state)   # the ACTUAL reps to log
            if reps is None:
                return {"error": "invalid exercise value"}, 400   # reject BEFORE any metric write
            ex = m["exercise"]
            ex["count"] = _int(ex.get("count"), 0) + 1
            ex["reps_total"] = _int(ex.get("reps_total"), 0) + reps
            ex["today_reps"] = _int(ex.get("today_reps"), 0) + reps
            if picked and picked.get("id"):
                by = ex.get("by_id")
                if not isinstance(by, dict):
                    by = ex["by_id"] = {}
                entry = by.get(picked["id"])
                if not isinstance(entry, dict):
                    entry = by[picked["id"]] = {"count": 0, "reps_total": 0}
                entry["count"] = _int(entry.get("count"), 0) + 1
                entry["reps_total"] = _int(entry.get("reps_total"), 0) + reps
                state["session"]["last_exercise"] = picked["id"]   # no immediate repeat next roll
                _apply_exercise_progression(state, picked["id"], reps, now)   # once-per-day target
            rem["exercise"]["exercise"] = None       # clear the pick → next due re-rolls
            rem["exercise"]["last_at"] = now
            rem["exercise"]["next_at"] = now + _interval_min(state, "exercise") * 60
        else:                                        # water | stretch
            d = m[rtype]
            d["count"] = _int(d.get("count"), 0) + 1
            d["today"] = _int(d.get("today"), 0) + 1
            rem[rtype]["last_at"] = now
            rem[rtype]["next_at"] = now + _interval_min(state, rtype) * 60
        if not is_custom:                        # custom acks are count-only — never a
            _touch_day(state, now)               # wellness/productivity signal (streak untouched)
        save_state(state)
        return _view(state, now, activity), 200


def skip(rtype, activity=None, cid=None):
    """POST /api/focus/skip {type, id?}. Reschedule the timer WITHOUT logging a metric
    (coffee has no timer — dismissing it starts its cooldown so it stops being due). A
    custom reminder (type='custom', id=<id>) reschedules on its own interval."""
    is_custom = rtype == "custom"
    if not is_custom and rtype not in REMINDER_TYPES:
        return {"error": "unknown reminder type"}, 400
    now = _now()
    with _lock:
        state = load_state()
        _roll(state, now)
        rem = state["reminders"]
        if is_custom:
            slot = (rem.get("custom") or {}).get(cid) if cid else None
            if not isinstance(slot, dict):
                return {"error": "unknown custom reminder"}, 400
            slot["next_at"] = now + _custom_interval(state, cid) * 60
        elif rtype == "coffee":
            coffee = rem["coffee"]
            coffee["last_at"] = now                  # snooze via the cooldown
            coffee.pop("snooze_until", None)
            picked_act = coffee.get("activity") if isinstance(coffee.get("activity"), dict) else None
            if picked_act and picked_act.get("id"):
                state["session"]["last_activity"] = picked_act["id"]   # no immediate repeat
            coffee["activity"] = None                # clear the pick → next due re-rolls
        elif rtype == "exercise":
            picked = rem["exercise"].get("exercise") if isinstance(rem.get("exercise"), dict) else None
            if picked and picked.get("id"):
                state["session"]["last_exercise"] = picked["id"]   # no immediate repeat next roll
            rem["exercise"]["exercise"] = None       # clear the pick → next due re-rolls
            rem["exercise"]["next_at"] = now + _interval_min(state, "exercise") * 60
        else:
            rem[rtype]["next_at"] = now + _interval_min(state, rtype) * 60
        save_state(state)
        return _view(state, now, activity), 200


def snooze(rtype, activity=None, cid=None):
    """POST /api/focus/snooze {type, id?}. Push a reminder out by config.snooze_min and
    count it in metrics.snoozes. water/stretch/exercise/custom move their next_at; coffee
    (which has no timer) is suppressed until now+snooze_min via snooze_until."""
    is_custom = rtype == "custom"
    if not is_custom and rtype not in REMINDER_TYPES:
        return {"error": "unknown reminder type"}, 400
    now = _now()
    with _lock:
        state = load_state()
        _roll(state, now)
        rem = state["reminders"]
        span = _snooze_min(state) * 60
        if is_custom:
            slot = (rem.get("custom") or {}).get(cid) if cid else None
            if not isinstance(slot, dict):
                return {"error": "unknown custom reminder"}, 400
            slot["next_at"] = now + span
        elif rtype == "coffee":
            rem["coffee"]["snooze_until"] = now + span
        else:                                        # water | stretch | exercise
            rem[rtype]["next_at"] = now + span
        sm = state["metrics"].setdefault("snoozes", {"count": 0})
        sm["count"] = _int(sm.get("count"), 0) + 1
        save_state(state)
        return _view(state, now, activity), 200


def swap(rtype, activity=None):
    """POST /api/focus/swap {type}. Re-roll the CURRENT pick to a DIFFERENT
    weighted-random one (excluding the current) — unless the pool has <=2 items, when
    the previous is allowed. type='exercise' rotates the exercise pick; type='coffee'
    rotates the Fokus activity, drawing from the RANDOM pool ONLY (a mandatory quota is
    never re-rolled away). Never stamps last_* (that is ack/skip's job); a missing current
    pick is fine — it just rolls a fresh one."""
    if rtype not in ("exercise", "coffee"):
        return {"error": "swap type must be 'exercise' or 'coffee'"}, 400
    now = _now()
    with _lock:
        state = load_state()
        _roll(state, now)
        rem = state["reminders"]
        if rtype == "exercise":
            slot = rem.get("exercise") if isinstance(rem.get("exercise"), dict) else None
            if slot is None:
                return {"error": "no exercise reminder"}, 400
            cur = slot.get("exercise") if isinstance(slot.get("exercise"), dict) else None
            pool = _exercise_pool(state)
            # exclude the current only when >2 items exist (else the previous is allowed)
            choice = _weighted_choice(pool, cur.get("id") if cur else None,
                                      exclude_last=len(pool) > 2)
            slot["exercise"] = _exercise_pick_dict(choice) if choice else None
        else:                                        # coffee → a RANDOM Fokus activity
            slot = rem.get("coffee") if isinstance(rem.get("coffee"), dict) else None
            if slot is None:
                return {"error": "no coffee reminder"}, 400
            cur = slot.get("activity") if isinstance(slot.get("activity"), dict) else None
            pool = _random_activity_pool(state)
            choice = _weighted_choice(pool, cur.get("id") if cur else None,
                                      exclude_last=len(pool) > 2)
            slot["activity"] = ({"id": choice["id"], "name": choice["name"], "kind": "random",
                                 "focus_boost": choice.get("focus_boost", DEFAULT_FOCUS_BOOST)}
                                if choice else None)
        save_state(state)
        return _view(state, now, activity), 200


def infocus(rtype, activity=None):
    """POST /api/focus/infocus {type:"coffee"}. The user asserts they are still focused and
    do NOT need the suggested activity. DEFER the current pick: clear it and re-arm after
    INFOCUS_DEFER_MIN (reusing the coffee snooze_until gate). It does NOT count toward a
    mandatory quota, applies NO boost, and does NOT stamp last_activity (a deferral is not a
    use). Record the feedback (metrics.coffee.in_focus_reports + timestamp) and CALIBRATE the
    decay: nudge focus_decay_scale UP (the low score was too pessimistic)."""
    if rtype != "coffee":
        return {"error": "infocus type must be 'coffee'"}, 400
    now = _now()
    with _lock:
        state = load_state()
        _roll(state, now)
        rem = state["reminders"]
        coffee = rem.get("coffee") if isinstance(rem.get("coffee"), dict) else None
        if coffee is None:
            return {"error": "no coffee reminder"}, 400
        coffee["activity"] = None                    # defer the pick (not a use → no last_activity)
        coffee["snooze_until"] = now + INFOCUS_DEFER_MIN * 60
        cm = state["metrics"].get("coffee")
        if not isinstance(cm, dict):
            cm = state["metrics"]["coffee"] = {"count": 0, "by_id": {}}
        cm["in_focus_reports"] = _int(cm.get("in_focus_reports"), 0) + 1
        cm["in_focus_at"] = now
        fc = state["config"].setdefault("focus", {})
        fc["decay_scale"] = round(min(FOCUS_DECAY_SCALE_MAX,
                                      _decay_scale(state) + FOCUS_DECAY_SCALE_STEP), 4)
        save_state(state)
        return _view(state, now, activity), 200


def end_day(claude_sessions=0, commits=0, session_hours=0.0, max_concurrency=0,
            avg_concurrency=0.0, output_tokens=0, input_tokens=0, cache_read=0,
            cache_write=0, activity=None):
    """POST /api/focus/end-day. Build the work-SESSION report, APPEND it to history[],
    mark day_ended (so nothing is due), and return the report. Each history entry is ONE
    session (started_at/ended_at/work_day + its metrics); the profile GROUPS them by
    work_day so two sessions on the same calendar date count as ONE day. Idempotent —
    calling it again while already ended returns the last report without appending.

    server.py supplies the EXTERNAL fields (focus.py stays HUB/gitviz/usage-free):
    claude_sessions (Hub), commits (gitviz), and — the productivity base —
    session_hours/max_concurrency/avg_concurrency + the token totals for the work
    window (usage transcripts). WALL-CLOCK stays `hours_worked` (never inflated);
    `parallelism` = session_hours / wall-clock is derived here and is the SEPARATE
    concurrency indicator. focus_score/exercise_sets are derived internally."""
    now = _now()
    with _lock:
        state = load_state()
        _roll(state, now)
        sess = state["session"]
        started = _num(sess.get("started_at"), now)
        work_day = sess.get("work_day") or _day_str(started)
        core = _day_metrics_core(state, now)
        focus_score = compute_score(state, now, activity)
        wall = core["hours_worked"]
        sh = round(_num(session_hours, 0.0), 4)
        mand_state = _mandatory_state(state, now)                       # quota done/remaining
        mandatory_missed = sum(m["remaining"] for m in mand_state)      # under-quota at day end
        report = {
            "work_day": work_day,           # the GROUPING key (calendar date of this session)
            "date": work_day,               # back-compat alias
            "started_at": started,
            "ended_at": now,
            "hours_worked": wall,           # WALL-CLOCK worked time — never inflated
            "mandatory_missed": mandatory_missed,
            "mandatory": [{"id": m["id"], "daily_limit": m["daily_limit"],
                           "done": m["done_today"], "missed": m["remaining"]} for m in mand_state],
            "water": core["water"],
            "stretch": core["stretch"],
            "exercise_total": core["exercise_total"],
            "exercise_sets": core["exercise_sets"],
            "exercise_by_id": core["exercise_by_id"],
            "pauses": core["pauses"],
            "snoozes": core["snoozes"],
            "claude_sessions": _int(claude_sessions, 0),
            "work_blocks": core["work_blocks"],
            "commits": _int(commits, 0),
            "focus_score": focus_score,     # the day-end focus snapshot (deep-work base)
            # --- external productivity base (server.py gathers from usage transcripts) ---
            "session_hours": sh,            # SUM of Claude active-durations in the window
            "max_concurrency": _int(max_concurrency, 0),
            "avg_concurrency": round(_num(avg_concurrency, 0.0), 4),
            "parallelism": round(sh / max(wall, EPS), 4),   # session_hours / wall-clock
            "output_tokens": _int(output_tokens, 0),
            "input_tokens": _int(input_tokens, 0),
            "cache_read": _int(cache_read, 0),
            "cache_write": _int(cache_write, 0),
        }
        if sess.get("day_ended"):
            hist = state.get("history")
            last = hist[-1] if isinstance(hist, list) and hist else report
            return last, 200                         # already ended: do not double-append
        hist = state.get("history")
        if not isinstance(hist, list):
            hist = state["history"] = []
        hist.append(report)
        sess["day_ended"] = True
        save_state(state)
        return report, 200


def new_day(activity=None):
    """POST /api/focus/new-day. Start a fresh work-day: zero the day's live wellness
    counters (their totals are already in the history record end_day appended), restart
    the timers/reminders, and clear day_ended. The calendar-day streak and history are
    preserved."""
    now = _now()
    with _lock:
        state = load_state()
        _roll(state, now)
        _reset_day_metrics(state)
        sess = state["session"]
        sess["started_at"] = now
        sess["work_day"] = _day_str(now)
        sess["day_ended"] = False
        sess["work_blocks"] = 1
        sess["heartbeat"] = now
        sess["paused"] = False
        sess["paused_at"] = None
        sess["paused_total_sec"] = 0.0
        sess["last_break_at"] = now
        sess["last_exercise"] = None
        sess["last_activity"] = None
        sess["boost_amount"] = 0.0
        sess["boost_at"] = None
        rem = state["reminders"]
        rem["water"] = {"last_at": None, "next_at": now + _interval_min(state, "water") * 60}
        rem["stretch"] = {"last_at": None, "next_at": now + _interval_min(state, "stretch") * 60}
        rem["exercise"] = {"last_at": None, "next_at": now + _interval_min(state, "exercise") * 60,
                           "exercise": None}
        rem["coffee"] = {"last_at": None, "activity": None}
        _sync_custom_reminders(state, now, force=True)   # every custom timer restarts
        save_state(state)
        return _view(state, now, activity), 200


def update_config(patch, activity=None):
    """POST /api/focus/config (partial config). Merge-validate enabled / exercises /
    focus_activities / intervals_min / focus.{break_after_min,low_score_threshold,decay_scale}
    / coffee / snooze_min / custom_reminders / sound. A bad field is a 400 and NOTHING is
    persisted (we save only after every field validates). A changed interval reschedules that
    reminder's next_at from now. `exercises` fully REPLACES the set: each entry needs a
    non-empty name, weight >= 0 and reps 1..1000; optional target (int 1..1000), muscles
    ([{muscle in MUSCLES, pct 0..100}] — soft-normalized to sum 100) and stat (non-empty
    string) — these and the trained progression state are carried across the edit by stable
    id. `focus_activities` fully replaces the Fokus pool: each entry needs a non-empty name,
    a kind ('mandatory' or 'random'), focus_boost 0..100, plus daily_limit (mandatory) or
    weight (random). Deleting an exercise / random activity renormalizes the surviving pool's
    weights. `sound` is per-reminder-type mute flags (booleans; presentation only)."""
    if not isinstance(patch, dict):
        return {"error": "config must be an object"}, 400
    now = _now()
    with _lock:
        state = load_state()
        cfg = state["config"]

        if "enabled" in patch:
            if not isinstance(patch["enabled"], bool):
                return {"error": "enabled must be a boolean"}, 400
            cfg["enabled"] = patch["enabled"]

        if "exercises" in patch:
            ex_list = patch["exercises"]
            if not isinstance(ex_list, list) or not ex_list:
                return {"error": "exercises must be a non-empty list"}, 400
            # Carry per-exercise progression state (target/days_since_done/recent_actuals/
            # last_done) across a config edit, keyed by stable id, so editing name/weight
            # never resets an exercise's trained target.
            prev_by_id = {}
            for pe in cfg.get("exercises") or []:
                if isinstance(pe, dict) and isinstance(pe.get("id"), str):
                    prev_by_id[pe["id"]] = pe
            normalized, seen = [], set()
            for i, e in enumerate(ex_list):
                if not isinstance(e, dict):
                    return {"error": "each exercise must be an object"}, 400
                name = e.get("name")
                if not isinstance(name, str) or not name.strip():
                    return {"error": "each exercise needs a non-empty name"}, 400
                w = _strict_num(e.get("weight"))
                if w is None or w < 0:
                    return {"error": "exercise weight must be a number >= 0"}, 400
                reps = e.get("reps")
                if not isinstance(reps, int) or isinstance(reps, bool) or reps < 1 or reps > MAX_EXERCISE_REPS:
                    return {"error": "exercise reps must be an integer 1..%d" % MAX_EXERCISE_REPS}, 400
                eid = e.get("id")
                if not isinstance(eid, str) or not eid.strip():
                    eid = _slug(name) or ("ex%d" % (i + 1))
                eid, base, n = eid.strip(), eid.strip(), 2
                while eid in seen:                    # keep ids unique within the set
                    eid = "%s-%d" % (base, n)
                    n += 1
                seen.add(eid)
                emoji = e.get("emoji")
                emoji = emoji.strip() if isinstance(emoji, str) else ""
                prev = prev_by_id.get(eid, {})
                # target: explicit override wins; else keep the trained target; else seed = reps
                if "target" in e and e.get("target") is not None:
                    tv = e.get("target")
                    if not isinstance(tv, int) or isinstance(tv, bool) or tv < 1 or tv > MAX_EXERCISE_REPS:
                        return {"error": "exercise target must be an integer 1..%d" % MAX_EXERCISE_REPS}, 400
                    target = tv
                else:
                    target = _int(prev.get("target"), None)
                    if target is None or target < 1:
                        target = reps
                # muscles: explicit list (each muscle in MUSCLES, pct 0..100; a soft sum
                # check normalizes to 100, never rejects); else keep a prior split; else
                # auto-suggest from the name.
                if "muscles" in e and e.get("muscles") is not None:
                    mv = e.get("muscles")
                    if not isinstance(mv, list):
                        return {"error": "exercise muscles must be a list"}, 400
                    parsed = []
                    for mentry in mv:
                        if not isinstance(mentry, dict):
                            return {"error": "each muscle entry must be an object"}, 400
                        mm = mentry.get("muscle")
                        if not isinstance(mm, str) or mm.strip().lower() not in MUSCLES:
                            return {"error": "muscle must be one of: %s" % ", ".join(MUSCLES)}, 400
                        pv = _strict_num(mentry.get("pct"))
                        if pv is None or pv < 0 or pv > 100:
                            return {"error": "muscle pct must be a number 0..100"}, 400
                        parsed.append({"muscle": mm.strip().lower(), "pct": pv})
                    muscles = _normalize_muscle_pcts(parsed) or _suggest_muscles(name)
                elif _valid_muscles_list(prev.get("muscles")):
                    muscles = copy.deepcopy(prev["muscles"])
                elif isinstance(prev.get("muscle"), str) and prev.get("muscle") in MUSCLES:
                    muscles = [{"muscle": prev["muscle"], "pct": 100}]   # legacy prior
                else:
                    muscles = _suggest_muscles(name)
                # stat: explicit override; else keep a prior override; else default snaga
                if "stat" in e and e.get("stat") is not None:
                    sv = e.get("stat")
                    if not isinstance(sv, str) or not sv.strip():
                        return {"error": "exercise stat must be a non-empty string"}, 400
                    stat = sv.strip()
                elif isinstance(prev.get("stat"), str) and prev.get("stat").strip():
                    stat = prev["stat"].strip()
                else:
                    stat = DEFAULT_STAT
                dsd = _int(prev.get("days_since_done"), 0)
                ra = prev.get("recent_actuals")
                ra = [x for x in ra if isinstance(x, int) and not isinstance(x, bool)] \
                    if isinstance(ra, list) else []
                last_done = prev.get("last_done") if isinstance(prev.get("last_done"), str) else None
                normalized.append({"id": eid, "name": name.strip(), "emoji": emoji,
                                   "weight": float(w), "reps": reps, "target": target,
                                   "muscles": muscles, "stat": stat,
                                   "days_since_done": dsd, "recent_actuals": ra,
                                   "last_done": last_done})
            # renormalize surviving weights when the edit DELETED an exercise (a prior id
            # is gone), so the pool is never left degenerate. A pure edit keeps weights.
            if set(prev_by_id) - seen:
                _renormalize_weights(normalized)
            cfg["exercises"] = normalized
            picked = ((state.get("reminders", {}) or {}).get("exercise", {}) or {}).get("exercise")
            if picked and picked.get("id") not in seen:
                state["reminders"]["exercise"]["exercise"] = None   # dropped exercise → re-roll

        if "focus_activities" in patch:
            fa_list = patch["focus_activities"]
            if not isinstance(fa_list, list) or not fa_list:
                return {"error": "focus_activities must be a non-empty list"}, 400
            prev_random_ids = {a["id"] for a in _all_activities(state) if a["kind"] == "random"}
            normalized, seen = [], set()
            for i, a in enumerate(fa_list):
                if not isinstance(a, dict):
                    return {"error": "each focus activity must be an object"}, 400
                name = a.get("name")
                if not isinstance(name, str) or not name.strip():
                    return {"error": "each focus activity needs a non-empty name"}, 400
                if "kind" in a:
                    kind = a.get("kind")
                    if kind not in ACTIVITY_KINDS:
                        return {"error": "focus activity kind must be 'mandatory' or 'random'"}, 400
                else:
                    kind = "random"
                if "focus_boost" in a and a.get("focus_boost") is not None:
                    bv = _strict_num(a.get("focus_boost"))
                    if bv is None or bv < 0 or bv > MAX_FOCUS_BOOST:
                        return {"error": "focus_boost must be a number 0..%d" % MAX_FOCUS_BOOST}, 400
                    boost = float(bv)
                else:
                    boost = float(DEFAULT_FOCUS_BOOST)
                aid = a.get("id")
                if not isinstance(aid, str) or not aid.strip():
                    aid = _slug(name) or ("act%d" % (i + 1))
                aid, base, n = aid.strip(), aid.strip(), 2
                while aid in seen:                    # keep ids unique within the set
                    aid = "%s-%d" % (base, n)
                    n += 1
                seen.add(aid)
                entry = {"id": aid, "name": name.strip(), "kind": kind, "focus_boost": boost}
                if kind == "mandatory":
                    if "daily_limit" in a and a.get("daily_limit") is not None:
                        dl = a.get("daily_limit")
                        if not isinstance(dl, int) or isinstance(dl, bool) or dl < 1 or dl > MAX_DAILY_LIMIT:
                            return {"error": "daily_limit must be an integer 1..%d" % MAX_DAILY_LIMIT}, 400
                        entry["daily_limit"] = dl
                    else:
                        entry["daily_limit"] = DEFAULT_DAILY_LIMIT
                else:                                 # random → a draw weight
                    if "weight" in a and a.get("weight") is not None:
                        wv = _strict_num(a.get("weight"))
                        if wv is None or wv < 0:
                            return {"error": "focus activity weight must be a number >= 0"}, 400
                        entry["weight"] = float(wv)
                    else:
                        entry["weight"] = float(DEFAULT_ACTIVITY_WEIGHT)
                normalized.append(entry)
            # renormalize surviving random weights when the edit DELETED a random activity.
            new_random = [e for e in normalized if e["kind"] == "random"]
            if prev_random_ids - {e["id"] for e in new_random}:
                _renormalize_weights(new_random)
            cfg["focus_activities"] = normalized
            act = ((state.get("reminders", {}) or {}).get("coffee", {}) or {}).get("activity")
            if isinstance(act, dict) and act.get("id") not in seen:
                state["reminders"]["coffee"]["activity"] = None   # dropped activity → re-roll

        if "intervals_min" in patch:
            iv = patch["intervals_min"]
            if not isinstance(iv, dict):
                return {"error": "intervals_min must be an object"}, 400
            for rtype, raw in iv.items():
                if rtype not in INTERVAL_TYPES:
                    continue                        # ignore unknown interval keys
                mins = _strict_num(raw)
                if mins is None or mins <= 0 or mins > MAX_INTERVAL_MIN:
                    return {"error": "interval for %s must be 1..%d minutes"
                            % (rtype, MAX_INTERVAL_MIN)}, 400
                old = _num(cfg["intervals_min"].get(rtype), None)
                cfg["intervals_min"][rtype] = mins
                if old != mins:                     # reschedule the upcoming nudge
                    state["reminders"][rtype]["next_at"] = now + mins * 60

        if "focus" in patch:
            fc = patch["focus"]
            if not isinstance(fc, dict):
                return {"error": "focus must be an object"}, 400
            if "break_after_min" in fc:
                bm = _strict_num(fc["break_after_min"])
                if bm is None or bm <= 0 or bm > MAX_INTERVAL_MIN:
                    return {"error": "break_after_min must be 1..%d minutes" % MAX_INTERVAL_MIN}, 400
                cfg.setdefault("focus", {})["break_after_min"] = bm
            if "decay_scale" in fc:                         # user reset of the auto-calibration
                ds = _strict_num(fc["decay_scale"])
                if ds is None or ds < FOCUS_DECAY_SCALE_MIN or ds > FOCUS_DECAY_SCALE_MAX:
                    return {"error": "decay_scale must be %.1f..%.1f"
                            % (FOCUS_DECAY_SCALE_MIN, FOCUS_DECAY_SCALE_MAX)}, 400
                cfg.setdefault("focus", {})["decay_scale"] = ds
            if "low_score_threshold" in fc:                 # legacy alias → its new home is coffee
                th = _strict_num(fc["low_score_threshold"])
                if th is None or th < 0 or th > 100:
                    return {"error": "low_score_threshold must be 0..100"}, 400
                cfg.setdefault("coffee", {})["low_score_threshold"] = th

        if "coffee" in patch:
            cc = patch["coffee"]
            if not isinstance(cc, dict):
                return {"error": "coffee must be an object"}, 400
            coffee_cfg = cfg.setdefault("coffee", {})
            if "enabled" in cc:
                if not isinstance(cc["enabled"], bool):
                    return {"error": "coffee.enabled must be a boolean"}, 400
                coffee_cfg["enabled"] = cc["enabled"]
            if "low_score_threshold" in cc:
                th = _strict_num(cc["low_score_threshold"])
                if th is None or th < 0 or th > 100:
                    return {"error": "coffee.low_score_threshold must be 0..100"}, 400
                coffee_cfg["low_score_threshold"] = th
            if "cooldown_min" in cc:
                cd = _strict_num(cc["cooldown_min"])
                if cd is None or cd < 1 or cd > MAX_INTERVAL_MIN:
                    return {"error": "coffee.cooldown_min must be 1..%d minutes" % MAX_INTERVAL_MIN}, 400
                coffee_cfg["cooldown_min"] = cd

        if "sound" in patch:                    # per-reminder mute flags (presentation only)
            sc = patch["sound"]
            if not isinstance(sc, dict):
                return {"error": "sound must be an object"}, 400
            sound_cfg = cfg.setdefault("sound", dict(DEFAULT_SOUND_FLAGS))
            for rtype, val in sc.items():
                if rtype not in REMINDER_TYPES:
                    continue                    # ignore unknown keys, like intervals_min
                if not isinstance(val, bool):
                    return {"error": "sound.%s must be a boolean" % rtype}, 400
                sound_cfg[rtype] = val

        if "reminders_enabled" in patch:        # per-CATEGORY on/off (backend DOES branch)
            re_patch = patch["reminders_enabled"]
            if not isinstance(re_patch, dict):
                return {"error": "reminders_enabled must be an object"}, 400
            re_cfg = cfg.setdefault("reminders_enabled", dict(DEFAULT_REMINDERS_ENABLED))
            for category, val in re_patch.items():
                if category not in REMINDER_CATEGORIES:
                    continue                    # ignore unknown keys, like sound/intervals_min
                if not isinstance(val, bool):
                    return {"error": "reminders_enabled.%s must be a boolean" % category}, 400
                re_cfg[category] = val

        if "snooze_min" in patch:
            sm = _strict_num(patch["snooze_min"])
            if sm is None or sm < 1 or sm > MAX_INTERVAL_MIN:
                return {"error": "snooze_min must be 1..%d minutes" % MAX_INTERVAL_MIN}, 400
            cfg["snooze_min"] = sm

        custom_changed = False
        if "custom_reminders" in patch:
            cr = patch["custom_reminders"]
            if not isinstance(cr, list):
                return {"error": "custom_reminders must be a list"}, 400
            normalized, seen = [], set()
            for i, c in enumerate(cr):
                if not isinstance(c, dict):
                    return {"error": "each custom reminder must be an object"}, 400
                name = c.get("name")
                if not isinstance(name, str) or not name.strip():
                    return {"error": "each custom reminder needs a non-empty name"}, 400
                iv = c.get("interval_min")
                if not isinstance(iv, int) or isinstance(iv, bool) or iv < 1 or iv > MAX_INTERVAL_MIN:
                    return {"error": "custom reminder interval_min must be an integer 1..%d"
                            % MAX_INTERVAL_MIN}, 400
                cid = c.get("id")
                if not isinstance(cid, str) or not cid.strip():
                    cid = _slug(name) or ("custom%d" % (i + 1))
                cid, base_id, n = cid.strip(), cid.strip(), 2
                while cid in seen:                    # keep ids unique within the set
                    cid = "%s-%d" % (base_id, n)
                    n += 1
                seen.add(cid)
                snd = c.get("sound")
                if snd is not None and not isinstance(snd, bool):
                    return {"error": "custom reminder sound must be a boolean"}, 400
                normalized.append({"id": cid, "name": name.strip(), "interval_min": iv,
                                   "sound": snd if isinstance(snd, bool) else True})
            cfg["custom_reminders"] = normalized
            custom_changed = True

        _roll(state, now)
        if custom_changed:                  # add new slots / drop removed ones, keep the rest
            _sync_custom_reminders(state, now)
        _ensure_exercise_pick(state, now)   # re-enabled / dropped pick may need a fresh roll
        _ensure_activity_pick(state, now, activity)
        save_state(state)
        return _view(state, now, activity), 200


# --------------------------------------------------------------------------- #
#  Profile aggregation — totals + averages over history[], plus current streaks.
#  Pure over the parsed (date, report) list so the window math is testable offline.
# --------------------------------------------------------------------------- #
#: the metrics the profile rolls up (every numeric field of a day report). Kept as
#: the /profile contract — a subset of SUM_METRICS below, all sum-type — so /profile
#: is unchanged by the productivity layer.
PROFILE_METRICS = ("hours_worked", "water", "stretch", "exercise_total",
                   "pauses", "snoozes", "claude_sessions", "work_blocks", "commits")
PROFILE_RECENT_DAYS = 30            # raw recent history returned for charts

#: A session report carries more numbers than /profile rolls up. Collapsing the
#: SESSIONS of one calendar date aggregates each by its NATURE: most SUM, concurrency
#: peaks MAX, rates MEAN. (Added with the productivity + RPG layer, 2026-08-11.)
SUM_METRICS = ("hours_worked", "water", "stretch", "exercise_total", "exercise_sets",
               "pauses", "snoozes", "claude_sessions", "work_blocks", "commits",
               "session_hours", "output_tokens", "input_tokens", "cache_read",
               "cache_write", "mandatory_missed")
MAX_METRICS = ("max_concurrency",)
MEAN_METRICS = ("avg_concurrency", "focus_score")
#: which report keys read as a real (float) number rather than an int count.
_FLOAT_METRICS = frozenset({"hours_worked", "session_hours", "avg_concurrency",
                            "parallelism", "focus_score"})
EPS = 1e-9                          # guards a divide by a near-zero wall-clock


def _report_val(report: dict, key: str) -> float:
    v = report.get(key, 0)
    return _num(v, 0.0) if key in _FLOAT_METRICS else _int(v, 0)


def _round(x) -> float:
    return round(float(x), 4)


def _profile_streaks(days) -> dict:
    """Current consecutive-day streaks (water/stretch/exercise), counting back from the
    LATEST history day while that metric is > 0 and the days stay calendar-consecutive.
    `days` is [(date, report), ...] sorted ascending."""
    res = {"water": 0, "stretch": 0, "exercise": 0}
    if not days:
        return res
    by_date = {d: r for d, r in days}
    latest = days[-1][0]
    for key, metric in (("water", "water"), ("stretch", "stretch"),
                        ("exercise", "exercise_total")):
        streak, cur = 0, latest
        while cur in by_date and _int(by_date[cur].get(metric), 0) > 0:
            streak += 1
            cur = cur - timedelta(days=1)
        res[key] = streak
    return res


def _build_profile(days, now: float) -> dict:
    """Pure aggregation over `days` = [(date, report), ...] ascending. For each metric:
    totals + present-day averages over this week / this month / all-time, PLUS two
    week-flavored all-time averages —
      * avg_radna_nedelja: sum over Mon-Fri days / count of those present days
      * avg_cela_nedelja : all-time total / calendar-day span (missing days count as 0)."""
    today = datetime.fromtimestamp(now).date()
    monday = today - timedelta(days=today.weekday())    # Monday of the current week
    month_first = today.replace(day=1)

    week_days = [(d, r) for d, r in days if monday <= d <= today]
    month_days = [(d, r) for d, r in days if month_first <= d <= today]
    weekday_days = [(d, r) for d, r in days if d.weekday() < 5]      # Mon-Fri (all-time)
    span_days = ((days[-1][0] - days[0][0]).days + 1) if days else 0  # calendar span, all-time

    def _agg(subset, key):
        return sum(_report_val(r, key) for _d, r in subset)

    metrics = {}
    for key in PROFILE_METRICS:
        total_all = _agg(days, key)
        total_week = _agg(week_days, key)
        total_month = _agg(month_days, key)
        radna_total = _agg(weekday_days, key)
        n_all, n_week, n_month, n_radna = (len(days), len(week_days),
                                           len(month_days), len(weekday_days))
        metrics[key] = {
            "total_week": _round(total_week),
            "avg_week": _round(total_week / n_week) if n_week else 0.0,
            "total_month": _round(total_month),
            "avg_month": _round(total_month / n_month) if n_month else 0.0,
            "total_all": _round(total_all),
            "avg_all": _round(total_all / n_all) if n_all else 0.0,
            "avg_radna_nedelja": _round(radna_total / n_radna) if n_radna else 0.0,
            "avg_cela_nedelja": _round(total_all / span_days) if span_days else 0.0,
        }
    return {
        "generated_at": now,
        "days_recorded": len(days),
        "week_start": monday.strftime("%Y-%m-%d"),
        "month_start": month_first.strftime("%Y-%m-%d"),
        "metrics": metrics,
        "streaks": _profile_streaks(days),
        "history": [r for _d, r in days[-PROFILE_RECENT_DAYS:]],
    }


def _group_sessions_by_day(raw):
    """Collapse the work-SESSION history into one aggregated report per CALENDAR day.
    Each metric folds by its NATURE — SUM_METRICS add, MAX_METRICS peak, MEAN_METRICS
    average over the day's sessions — keyed on `work_day` (falling back to the legacy
    `date`); exercise_by_id is merged and a `sessions` count carried. This is the fix
    for two sessions on one date double-counting: each calendar day is seen EXACTLY
    once. Returns [(date, aggregated_report), ...] ascending."""
    agg = {}
    for r in raw:
        if not isinstance(r, dict):
            continue
        day = _parse_day(r.get("work_day") or r.get("date"))
        if day is None:
            continue
        acc = agg.get(day)
        if acc is None:
            acc = agg[day] = {}
            for k in SUM_METRICS:
                acc[k] = 0.0 if k in _FLOAT_METRICS else 0
            for k in MAX_METRICS:
                acc[k] = 0
            for k in MEAN_METRICS:
                acc["_mean_" + k] = 0.0         # running sum; divided out below
            acc["exercise_by_id"] = {}
            acc["sessions"] = 0
            acc["date"] = day.strftime("%Y-%m-%d")
            acc["work_day"] = acc["date"]
        acc["sessions"] += 1
        for k in SUM_METRICS:
            acc[k] = acc[k] + _report_val(r, k)
        for k in MAX_METRICS:
            acc[k] = max(_int(acc[k], 0), _int(_report_val(r, k), 0))
        for k in MEAN_METRICS:
            acc["_mean_" + k] += _report_val(r, k)
        for eid, v in (r.get("exercise_by_id") or {}).items():
            acc["exercise_by_id"][eid] = _int(acc["exercise_by_id"].get(eid), 0) + _int(v, 0)
    out = []
    for day in sorted(agg):
        acc = agg[day]
        n = acc["sessions"] or 1
        for k in MEAN_METRICS:
            acc[k] = round(acc.pop("_mean_" + k) / n, 4)
        for k in SUM_METRICS:                   # tidy the summed floats for the payload
            if k in _FLOAT_METRICS:
                acc[k] = round(acc[k], 4)
        out.append((day, acc))
    return out


def profile():
    """GET /api/focus/profile — group history[] work-SESSIONS by calendar day, then
    aggregate into totals, averages (incl. the two week flavors) and current streaks,
    plus the last ~30 per-day reports for charts. Two sessions on one date count as ONE
    day everywhere. A read; needs no external data (claude_sessions/commits are baked in)."""
    now = _now()
    with _lock:
        state = load_state()
        raw = state.get("history") if isinstance(state.get("history"), list) else []
    days = _group_sessions_by_day(raw)
    return _build_profile(days, now), 200


def session_window() -> dict:
    """The current work-session's [started_at, work_day] — server.py reads this to scope
    the transcript scan for session-hours/concurrency to the ACTUAL work window before it
    calls end_day. A read; holds the lock for a consistent snapshot. focus.py deliberately
    does NOT do the scan (it stays HUB/gitviz/usage-free); server.py owns the gathering."""
    now = _now()
    with _lock:
        state = load_state()
        sess = state.get("session", {}) or {}
        started = _num(sess.get("started_at"), now)
        return {"started_at": started, "work_day": sess.get("work_day") or _day_str(started)}


# --------------------------------------------------------------------------- #
#  Productivity analytics — per-day rows (history grouped by calendar date), the
#  aggregates over them, and a best-day-per-metric highlight. Every number is
#  DERIVED read-only from the day reports; nothing new is written. The wall-clock
#  headline stays `hours_worked`; the derived rates never inflate it.
# --------------------------------------------------------------------------- #
#: derived per-day metrics for which a HIGHER value is the "best day" (raw sums that
#: are unambiguously good + the derived rates). Snoozes/pauses are excluded — more of
#: them is not obviously better, so ranking them would mislead.
PRODUCTIVITY_HIGHLIGHT_METRICS = (
    "hours_worked", "commits", "water", "stretch", "exercise_total", "exercise_sets",
    "session_hours", "claude_sessions", "max_concurrency", "output_tokens",
    "commits_per_hour", "parallelism", "deep_work_ratio", "avg_session_length_h",
    "break_adherence", "water_adherence", "stretch_adherence", "movement_adherence",
    "cache_hit_ratio", "output_per_hour")


def _adherence(count, worked_min, interval_min):
    """Fraction of a cadence target met over a day: count / (worked_minutes / interval),
    clamped to [0,1]. None when there is nothing to measure against (no worked time or no
    configured interval), so the metric is SKIPPED rather than shown as a misleading 0."""
    if worked_min <= 0 or interval_min is None or interval_min <= 0:
        return None
    expected = worked_min / interval_min
    if expected <= 0:
        return None
    return round(min(1.0, count / expected), 4)


def _day_productivity(acc, cfg) -> dict:
    """The derived rates for ONE aggregated calendar day. Each is computed only when its
    inputs exist, else None (cleanly skipped). Adherence uses the CURRENT config cadence
    (historical config is not stored) — an approximation, documented as such."""
    hours = _num(acc.get("hours_worked"), 0.0)
    worked_min = hours * 60.0
    out = {}
    commits = _int(acc.get("commits"), 0)
    out["commits_per_hour"] = round(commits / hours, 4) if hours > 0 else None
    sh = _num(acc.get("session_hours"), 0.0)
    out["parallelism"] = round(sh / max(hours, EPS), 4) if (hours > 0 and sh > 0) else None
    fs = _num(acc.get("focus_score"), 0.0)          # focus-weighted hours / wall-clock
    out["deep_work_ratio"] = round(fs / 100.0, 4) if fs > 0 else None
    wb = _int(acc.get("work_blocks"), 0)
    out["avg_session_length_h"] = round(hours / wb, 4) if wb > 0 else None
    focus_cfg = cfg.get("focus") if isinstance(cfg.get("focus"), dict) else {}
    bam = _num(focus_cfg.get("break_after_min"), DEFAULT_BREAK_AFTER_MIN)
    out["break_adherence"] = _adherence(_int(acc.get("pauses"), 0), worked_min, bam)
    iv = cfg.get("intervals_min") if isinstance(cfg.get("intervals_min"), dict) else {}
    out["water_adherence"] = _adherence(
        _int(acc.get("water"), 0), worked_min, _num(iv.get("water"), DEFAULT_INTERVALS_MIN["water"]))
    out["stretch_adherence"] = _adherence(
        _int(acc.get("stretch"), 0), worked_min, _num(iv.get("stretch"), DEFAULT_INTERVALS_MIN["stretch"]))
    out["movement_adherence"] = _adherence(
        _int(acc.get("exercise_sets"), 0), worked_min, _num(iv.get("exercise"), DEFAULT_INTERVALS_MIN["exercise"]))
    out_tok = _int(acc.get("output_tokens"), 0)     # tokens/cache only when the scan caught them
    cr = _int(acc.get("cache_read"), 0)
    seen = cr + _int(acc.get("input_tokens"), 0) + _int(acc.get("cache_write"), 0)
    out["cache_hit_ratio"] = round(cr / seen, 4) if seen > 0 else None
    out["output_per_hour"] = round(out_tok / hours, 4) if (hours > 0 and out_tok > 0) else None
    return out


def _productivity_row(acc, cfg) -> dict:
    """One aggregated day report + its derived `productivity` block, for the page."""
    row = copy.deepcopy(acc)
    row["productivity"] = _day_productivity(acc, cfg)
    return row


def _row_metric(row, key):
    """Read a highlight/aggregate metric from a row — the derived block first, then the
    raw aggregated field — so both name spaces are addressable by one key."""
    prod = row.get("productivity") or {}
    if key in prod:
        return prod.get(key)
    return row.get(key)


def _productivity_highlights(rows) -> dict:
    """For EACH highlightable metric, the row (date) with the best (max) value + that
    value. Days where the metric is None are skipped; the first max wins a tie."""
    out = {}
    for key in PRODUCTIVITY_HIGHLIGHT_METRICS:
        best_date, best_val = None, None
        for row in rows:
            fv = _num(_row_metric(row, key), None)
            if fv is None:
                continue
            if best_val is None or fv > best_val:
                best_val, best_date = fv, row.get("date")
        if best_date is not None:
            out[key] = {"date": best_date, "value": best_val}
    return out


def _productivity_aggregates(rows) -> dict:
    """Totals of the summed raw fields + the mean of every highlight metric across the
    rows (only over days where the metric is present)."""
    totals = {k: round(sum(_num(r.get(k), 0.0) for r in rows), 4) for k in SUM_METRICS}
    averages = {}
    for key in PRODUCTIVITY_HIGHLIGHT_METRICS:
        vals = [v for v in (_num(_row_metric(r, key), None) for r in rows) if v is not None]
        if vals:
            averages[key] = round(sum(vals) / len(vals), 4)
    return {"days": len(rows), "totals": totals, "averages": averages}


def productivity(date=None, dfrom=None, dto=None):
    """GET /api/focus/productivity. Group history work-SESSIONS by calendar date (the
    same grouping /profile uses) and derive per-day productivity.

      ?date=YYYY-MM-DD  -> that one day's full report: the aggregated day row + EVERY raw
                           session on that date (the click-a-day drill-down).
      ?from=&to=        -> restrict the per-day rows to a date range (search).
      (neither)         -> all recorded days.

    Returns per-day rows, aggregates over them, and category highlights (best date per
    metric). A read; needs no external data (session_hours/tokens are baked into history)."""
    now = _now()
    with _lock:
        state = load_state()
        raw = state.get("history") if isinstance(state.get("history"), list) else []
        cfg = copy.deepcopy(state.get("config", {}) or {})
    days = _group_sessions_by_day(raw)

    if date:
        want = _parse_day(date)
        day_row = None
        if want is not None:
            for d, acc in days:
                if d == want:
                    day_row = _productivity_row(acc, cfg)
                    break
        sessions = [r for r in raw if isinstance(r, dict)
                    and _parse_day(r.get("work_day") or r.get("date")) == want] if want else []
        return {"generated_at": now, "date": date, "day": day_row, "sessions": sessions}, 200

    df = _parse_day(dfrom) if dfrom else None
    dt = _parse_day(dto) if dto else None
    sel = [(d, acc) for d, acc in days
           if (df is None or d >= df) and (dt is None or d <= dt)]
    rows = [_productivity_row(acc, cfg) for _d, acc in sel]
    return {
        "generated_at": now,
        "days_recorded": len(rows),
        "range": {"from": dfrom or "", "to": dto or ""},
        "days": rows,
        "aggregates": _productivity_aggregates(rows),
        "highlights": _productivity_highlights(rows),
    }, 200


# --------------------------------------------------------------------------- #
#  RPG character — stats/vitals/muscles/level derived read-only from cumulative
#  activity, with LONG-TERM decay (whole days, never intra-day): a driver idle past
#  a grace loses development gradually, so an untrained muscle or a hydration/focus
#  lapse softens the related value. Nothing is written; save_state stays the one writer.
# --------------------------------------------------------------------------- #
RPG_STATS = ("snaga", "spretnost", "izdrzljivost", "intelekt", "fokus", "volja")
RPG_STAT_MAX = 20
#: Disabling a reminder = choosing a character class. A DISABLED wellness dimension does
#: not develop OR decay — its stat rests at NEUTRAL_BASE (0..20 midpoint), its muscles at
#: MUSCLE_NEUTRAL_BASE (0..1), and a vital with no enabled inputs at VITAL_NEUTRAL. The four
#: toggles map to dims below; intelekt+volja come from "rad" (commits/consistency) and are
#: ALWAYS derived. Level/xp are UN-decayed cumulative activity and are never affected.
NEUTRAL_BASE = 8.0
MUSCLE_NEUTRAL_BASE = 0.4
VITAL_NEUTRAL = 0.5
WELLNESS_STAT_BY_TOGGLE = {"water": "izdrzljivost", "stretch": "spretnost",
                           "exercise": "snaga", "fokus": "fokus"}
#: Long-term decay. A driver idle up to the grace holds full; past it a linear daily
#: loss. Whole calendar days only (days_since_done / days-idle), never within a day.
DECAY_GRACE_DAYS = 3
DECAY_PER_DAY = 0.08
DECAY_FLOOR = 0.0
#: Saturation scales — the cumulative driver amount reaching ~63% (1-1/e) of full
#: development. Tuning constants; picked so a few weeks of steady use approaches full.
MUSCLE_REP_SCALE = 400.0
STAT_REP_SCALE = 800.0
STAT_STRETCH_SCALE = 120.0
STAT_WATER_SCALE = 300.0
STAT_COMMIT_SCALE = 300.0
STAT_OUTPUT_SCALE = 4_000_000.0
STAT_STREAK_SCALE = 20.0
STAT_DAYS_SCALE = 60.0
STAT_HOURS_SCALE = 400.0
#: Level curve: reaching level L costs LEVEL_STEP*L activity points (bands widen each
#: level). Points are UN-decayed cumulative activity, so a level is never lost.
LEVEL_STEP = 120.0
VITAL_DAYS = 3                       # recent window the 0..1 vitals average over


def _decay_factor(days_idle) -> float:
    """1.0 while a driver is fresher than the grace; a linear daily loss past it; never
    below the floor. None (never done) fully decays to 0 — no phantom development."""
    if days_idle is None:
        return 0.0
    if days_idle < DECAY_GRACE_DAYS:
        return 1.0
    return max(DECAY_FLOOR, 1.0 - DECAY_PER_DAY * (days_idle - DECAY_GRACE_DAYS))


def _saturate(x, scale) -> float:
    """A 0..1 development curve 1-exp(-x/scale): fast early gains, diminishing later."""
    if x <= 0 or scale <= 0:
        return 0.0
    return 1.0 - math.exp(-x / scale)


def _blend(pairs) -> float:
    """Weighted average of (value, weight) pairs — keeps a 0..1 result from 0..1 parts."""
    tw = sum(w for _v, w in pairs)
    if tw <= 0:
        return 0.0
    return sum(v * w for v, w in pairs) / tw


def _mean_notnone(vals):
    vs = [v for v in vals if v is not None]
    return sum(vs) / len(vs) if vs else None


def _live_today_bundle(state, now):
    """Today's UNCAPTURED activity from the live day counters (reset at new_day), so the
    in-progress day folds into the character and gains_today. None once the day is ended
    (its numbers are in history — folding the live counters too would double-count).
    commits/output are 0 here: they are only gathered at end-day, never live."""
    sess = state.get("session", {}) or {}
    if sess.get("day_ended"):
        return None
    m = state.get("metrics", {}) or {}
    ex = m.get("exercise") if isinstance(m.get("exercise"), dict) else {}
    by = ex.get("by_id") if isinstance(ex.get("by_id"), dict) else {}
    reps_by_ex = {k: _int(v.get("reps_total"), 0) for k, v in by.items() if isinstance(v, dict)}
    return {
        "date": _day_str(now),
        "reps_by_ex": reps_by_ex,
        "water": _int((m.get("water") or {}).get("count"), 0),
        "stretch": _int((m.get("stretch") or {}).get("count"), 0),
        "exercise_total": _int(ex.get("reps_total"), 0),
        "exercise_sets": _int(ex.get("count"), 0),
        "pauses": _int((m.get("pauses") or {}).get("count"), 0),
        "work_blocks": _int(sess.get("work_blocks"), 1),
        "commits": 0,
        "output_tokens": 0,
        "hours_worked": _elapsed_worked_sec(state, now) / 3600.0,
        "focus_score": compute_score(state, now),
    }


def _days_idle(days, today_bundle, key, today_date):
    """Calendar days since the most recent day whose `key` value was > 0 (today's live
    bundle counts as today). None when it has never happened — which fully decays."""
    if today_bundle and _int(today_bundle.get(key), 0) > 0:
        return 0
    last = None
    for d, acc in days:
        if _report_val(acc, key) > 0 and (last is None or d > last):
            last = d
    if last is None:
        return None
    return max(0, (today_date - last).days)


def _effective_exercise(cfg, cum_reps_by_ex):
    """(eff_by_muscle, eff_by_stat): cumulative reps per exercise, each DECAYED by that
    exercise's own days_since_done past the grace. The reps are SPLIT across the exercise's
    muscles by pct (reps * pct/100 to each); the stat receives the FULL reps. An idle
    exercise shrinks its muscles/stat even though the raw reps are unchanged."""
    eff_m = {m: 0.0 for m in MUSCLES}
    eff_s = {}
    for e in (cfg.get("exercises") or []):
        if not isinstance(e, dict):
            continue
        reps = _int(cum_reps_by_ex.get(e.get("id")), 0)
        if reps <= 0:
            continue
        factor = _decay_factor(_int(e.get("days_since_done"), 0))
        stat = e.get("stat") if isinstance(e.get("stat"), str) and e.get("stat").strip() else DEFAULT_STAT
        eff_s[stat] = eff_s.get(stat, 0.0) + reps * factor          # stat: the full reps
        muscles = _exercise_muscles(e) or [("grudi", 100.0)]
        for muscle, pct in muscles:                                 # muscles: split by pct
            eff_m[muscle] = eff_m.get(muscle, 0.0) + reps * (pct / 100.0) * factor
    return eff_m, eff_s


def _bundle(days, today_bundle, cfg, streak, now):
    """Assemble the RPG inputs from history (+ today's live bundle when present): the
    UN-decayed cumulative raw totals (drive level/xp — never lost) and the DECAYED
    effective drivers (drive stats/muscles/vitals). Pure over the grouped days."""
    today_date = _parse_day(_day_str(now))
    raw = {k: 0.0 for k in ("hours", "water", "stretch", "exercise_total", "commits",
                            "output", "session_hours", "sessions")}
    focus_vals = []
    cum_reps = {}
    for _d, acc in days:
        raw["hours"] += _num(acc.get("hours_worked"), 0.0)
        raw["water"] += _int(acc.get("water"), 0)
        raw["stretch"] += _int(acc.get("stretch"), 0)
        raw["exercise_total"] += _int(acc.get("exercise_total"), 0)
        raw["commits"] += _int(acc.get("commits"), 0)
        raw["output"] += _int(acc.get("output_tokens"), 0)
        raw["session_hours"] += _num(acc.get("session_hours"), 0.0)
        raw["sessions"] += _int(acc.get("claude_sessions"), 0)
        fs = _num(acc.get("focus_score"), 0.0)
        if fs > 0:
            focus_vals.append(fs)
        for eid, v in (acc.get("exercise_by_id") or {}).items():
            cum_reps[eid] = _int(cum_reps.get(eid), 0) + _int(v, 0)
    if today_bundle:
        raw["hours"] += _num(today_bundle.get("hours_worked"), 0.0)
        raw["water"] += _int(today_bundle.get("water"), 0)
        raw["stretch"] += _int(today_bundle.get("stretch"), 0)
        raw["exercise_total"] += _int(today_bundle.get("exercise_total"), 0)
        raw["commits"] += _int(today_bundle.get("commits"), 0)
        raw["output"] += _int(today_bundle.get("output_tokens"), 0)
        fs = _num(today_bundle.get("focus_score"), 0.0)
        if fs > 0:
            focus_vals.append(fs)
        for eid, v in (today_bundle.get("reps_by_ex") or {}).items():
            cum_reps[eid] = _int(cum_reps.get(eid), 0) + _int(v, 0)
    raw["days_recorded"] = len(days) + (1 if today_bundle else 0)
    raw["streak"] = _int(streak, 0)
    focus_mean = sum(focus_vals) / len(focus_vals) if focus_vals else 0.0

    eff_m, eff_s = _effective_exercise(cfg, cum_reps)
    di_water = _days_idle(days, today_bundle, "water", today_date)
    di_stretch = _days_idle(days, today_bundle, "stretch", today_date)
    di_commit = _days_idle(days, today_bundle, "commits", today_date)
    di_work = _days_idle(days, today_bundle, "hours_worked", today_date)
    eff = {
        "eff_by_stat": eff_s,
        "eff_by_muscle": eff_m,
        "water": raw["water"] * _decay_factor(di_water),
        "stretch": raw["stretch"] * _decay_factor(di_stretch),
        "commits": raw["commits"] * _decay_factor(di_commit),
        "output": raw["output"] * _decay_factor(di_commit),     # same driver (dev output)
        "hours": raw["hours"] * _decay_factor(di_work),
        "streak": raw["streak"],
        "days_recorded": raw["days_recorded"],
        "focus_mean": focus_mean * _decay_factor(di_work),
        "deep_work_mean": (focus_mean / 100.0) * _decay_factor(di_work),
    }
    return raw, eff, di_work


def _derive_stats(eff) -> dict:
    """The six 0..20 stats from the decayed drivers. Each exercise's `stat` feeds a small
    reps component into its named stat (so a user remapping a lift to izdrzljivost lands
    there); the described drivers carry the rest."""
    rs = eff["eff_by_stat"]

    def reps_c(stat):
        return _saturate(rs.get(stat, 0.0), STAT_REP_SCALE)

    stats01 = {
        "snaga": reps_c("snaga"),
        "spretnost": _blend([(reps_c("spretnost"), 0.3),
                             (_saturate(eff["stretch"], STAT_STRETCH_SCALE), 0.7)]),
        "izdrzljivost": _blend([(reps_c("izdrzljivost"), 0.2),
                                (_saturate(eff["water"], STAT_WATER_SCALE), 0.5),
                                (_saturate(eff["streak"], STAT_STREAK_SCALE), 0.3)]),
        "intelekt": _blend([(reps_c("intelekt"), 0.1),
                            (_saturate(eff["commits"], STAT_COMMIT_SCALE), 0.5),
                            (_saturate(eff["output"], STAT_OUTPUT_SCALE), 0.4)]),
        "fokus": _blend([(reps_c("fokus"), 0.1),
                        (_clamp(eff["focus_mean"] / 100.0, 0.0, 1.0), 0.55),
                        (_clamp(eff["deep_work_mean"], 0.0, 1.0), 0.35)]),
        "volja": _blend([(reps_c("volja"), 0.1),
                        (_saturate(eff["streak"], STAT_STREAK_SCALE), 0.5),
                        (_saturate(eff["days_recorded"], STAT_DAYS_SCALE), 0.2),
                        (_saturate(eff["hours"], STAT_HOURS_SCALE), 0.2)]),
    }
    return {s: round(RPG_STAT_MAX * _clamp(stats01[s], 0.0, 1.0), 2) for s in RPG_STATS}


def _activity_points(raw) -> float:
    """Total accumulated activity → level/xp. UN-decayed cumulative totals, so leveling
    only ever moves forward. Weights make an hour of focused work the dominant driver."""
    return (raw["hours"] * 10.0 + raw["water"] + raw["stretch"]
            + raw["exercise_total"] * 0.2 + raw["commits"] * 3.0
            + raw["output"] / 100000.0 + raw["session_hours"] * 5.0
            + raw["sessions"] * 2.0)


def _level_xp(points):
    """(level, xp-fraction 0..1) from activity points on a widening-band triangular curve."""
    lvl, need, rem = 1, float(LEVEL_STEP), max(0.0, points)
    while rem >= need:
        rem -= need
        lvl += 1
        need = LEVEL_STEP * lvl
    return lvl, round(_clamp(rem / need if need > 0 else 0.0, 0.0, 1.0), 4)


def _character(raw, eff, enabled=None) -> dict:
    """Assemble stats, muscles and level/xp from one input set. Pure, so gains_today can
    diff a WITH-today against a WITHOUT-today assembly. A DISABLED wellness dim is PINNED to
    its neutral base instead of derived — stat=NEUTRAL_BASE, and (exercise off) every muscle
    =MUSCLE_NEUTRAL_BASE — so it neither develops nor decays. Applied to BOTH the with- and
    without-today assemblies, so a disabled dim shows no gains_today. Level/xp are untouched
    (UN-decayed cumulative activity, never lost). enabled absent → all-on (old behaviour)."""
    enabled = enabled or {}
    muscles = {m: round(_saturate(eff["eff_by_muscle"].get(m, 0.0), MUSCLE_REP_SCALE), 4)
               for m in MUSCLES}
    if not enabled.get("exercise", True):
        muscles = {m: MUSCLE_NEUTRAL_BASE for m in MUSCLES}   # baseline, not 0; no decay
    stats = _derive_stats(eff)
    for toggle, stat in WELLNESS_STAT_BY_TOGGLE.items():
        if not enabled.get(toggle, True):
            stats[stat] = NEUTRAL_BASE                        # pinned; excluded from decay/streak
    points = _activity_points(raw)
    level, xp = _level_xp(points)
    return {"stats": stats, "muscles": muscles,
            "level": level, "xp": xp, "points": round(points, 2)}


def _today_acc(tb) -> dict:
    """A live today bundle shaped like an aggregated day row, so _day_productivity can
    derive today's adherence/deep-work for the recent-window vitals."""
    return {
        "hours_worked": _num(tb.get("hours_worked"), 0.0),
        "water": _int(tb.get("water"), 0), "stretch": _int(tb.get("stretch"), 0),
        "exercise_total": _int(tb.get("exercise_total"), 0),
        "exercise_sets": _int(tb.get("exercise_sets"), 0),
        "pauses": _int(tb.get("pauses"), 0), "work_blocks": _int(tb.get("work_blocks"), 1),
        "commits": _int(tb.get("commits"), 0), "focus_score": _num(tb.get("focus_score"), 0.0),
        "session_hours": 0.0, "output_tokens": _int(tb.get("output_tokens"), 0),
        "input_tokens": 0, "cache_read": 0, "cache_write": 0, "date": tb.get("date"),
    }


def _vitals(days, today_bundle, cfg, di_work, enabled=None) -> dict:
    """hp/stamina/mana in 0..1 from the RECENT window (last VITAL_DAYS days incl. today):
    hp = wellness adherence, stamina = break adherence, mana = deep-work — each softened by
    the same lapse decay when work has gone idle. Each vital averages over its ENABLED inputs
    ONLY (hp: water/stretch/exercise; mana: fokus), so a disabled dim never DRAGS a vital
    down; a vital with no enabled inputs rests at VITAL_NEUTRAL (0.5). stamina (breaks) is
    not tied to a wellness toggle. enabled absent → all-on (old behaviour)."""
    enabled = enabled or {}
    rows = [_day_productivity(acc, cfg) for _d, acc in days[-VITAL_DAYS:]]
    if today_bundle:
        rows.append(_day_productivity(_today_acc(today_bundle), cfg))
    rows = rows[-VITAL_DAYS:]
    f = _decay_factor(di_work)

    def mean(getter):
        if not rows:
            return 0.0
        return _mean_notnone([getter(r) for r in rows]) or 0.0

    hp_getters = []
    if enabled.get("water", True):
        hp_getters.append(lambda r: r.get("water_adherence"))
    if enabled.get("stretch", True):
        hp_getters.append(lambda r: r.get("stretch_adherence"))
    if enabled.get("exercise", True):
        hp_getters.append(lambda r: r.get("movement_adherence"))
    if hp_getters:
        hp = _clamp(mean(lambda r: _mean_notnone([g(r) for g in hp_getters])) * f, 0.0, 1.0)
    else:
        hp = VITAL_NEUTRAL                       # no enabled wellness input → neutral, no decay
    stamina = _clamp(mean(lambda r: r.get("break_adherence")) * f, 0.0, 1.0)
    if enabled.get("fokus", True):
        mana = _clamp(mean(lambda r: r.get("deep_work_ratio")) * f, 0.0, 1.0)
    else:
        mana = VITAL_NEUTRAL
    return {"hp": round(hp, 4), "stamina": round(stamina, 4), "mana": round(mana, 4)}


def _gains(before, after) -> dict:
    """The buffs from today's activity: the positive stat/muscle deltas between a
    WITHOUT-today and a WITH-today character, plus the point gain and any level-up."""
    stats = {s: round(after["stats"][s] - before["stats"][s], 2)
             for s in RPG_STATS if after["stats"][s] - before["stats"][s] > 0}
    muscles = {m: round(after["muscles"][m] - before["muscles"][m], 4)
               for m in MUSCLES if after["muscles"][m] - before["muscles"][m] > 0}
    return {"stats": stats, "muscles": muscles,
            "points": max(0.0, round(after["points"] - before["points"], 2)),
            "leveled_up": after["level"] > before["level"]}


def _enabled_map(cfg) -> dict:
    """The four category toggles as a {water,stretch,exercise,fokus}->bool map. Absent →
    True (all-on), so an old file (or a partial map) reads as fully enabled."""
    re_cfg = cfg.get("reminders_enabled") if isinstance(cfg.get("reminders_enabled"), dict) else {}
    return {c: re_cfg.get(c) is not False for c in REMINDER_CATEGORIES}


def _character_class(enabled) -> dict:
    """The FORM + CLASS the enabled combo picks — computed AUTHORITATIVELY here (the two web
    frontends render this string, never re-derive it). v=exercise, w=water, s=stretch,
    f=fokus. With a body (exercise on) you are human; without it you take a bodiless form."""
    v = bool(enabled.get("exercise", True))
    w = bool(enabled.get("water", True))
    s = bool(enabled.get("stretch", True))
    f = bool(enabled.get("fokus", True))
    if v:
        form = "human"
        if w and s and f:
            char_class, sub = "Kodni Vitez", "balansiran build"
        elif not w:
            char_class, sub = "Golem", "snaga bez vode"
        elif not f:
            char_class, sub = "Atleta", "telo, miran um"
        elif not s:
            char_class, sub = "Tenk", "snaga bez gipkosti"
        else:
            char_class, sub = "Ratnik", "telo vodi"
    elif w and f:
        form, char_class, sub = "meduza", "Meduza", "voda + um, bez tela"
    elif w:
        form, char_class, sub = "slime", "Sluz", "samo hidracija"
    elif f:
        form, char_class, sub = "wisp", "Duh", "čist um"          # "čist um"
    elif s:
        form, char_class, sub = "skelet", "Gipki Kostur", "pokretljiv, bez mišića"  # mišića
    else:
        form, char_class, sub = "iskra", "Iskra", "samo rad"
    return {"form": form, "char_class": char_class, "class_sub": sub}


def rpg():
    """GET /api/focus/rpg — the derived character: stats (0..20), vitals (0..1),
    muscles (0..1), level + xp, and gains_today. All derived read-only from cumulative
    history + config + the live current day; long-term decay softens an idle driver
    (past DECAY_GRACE_DAYS). save_state stays the one writer — nothing here persists.

    Disabling a reminder = choosing a character class: a disabled wellness dim is pinned to
    a neutral base (stat NEUTRAL_BASE, muscles MUSCLE_NEUTRAL_BASE, vital VITAL_NEUTRAL) and
    excluded from decay/streak, and the enabled combo picks the FORM + CLASS (authoritative
    here). `enabled`, `stats_base`, `form`, `char_class`, `class_sub` are exposed alongside."""
    now = _now()
    with _lock:
        state = load_state()
        raw_hist = state.get("history") if isinstance(state.get("history"), list) else []
        cfg = copy.deepcopy(state.get("config", {}) or {})
        streak = _int((state.get("metrics", {}) or {}).get("streak_days"), 0)
        today_bundle = _live_today_bundle(state, now)
    days = _group_sessions_by_day(raw_hist)     # history-only (today lives in the bundle)
    enabled = _enabled_map(cfg)

    raw_w, eff_w, di_work = _bundle(days, today_bundle, cfg, streak, now)
    char_w = _character(raw_w, eff_w, enabled)
    raw_o, eff_o, _diw = _bundle(days, None, cfg, streak, now)
    char_o = _character(raw_o, eff_o, enabled)
    klass = _character_class(enabled)
    stats_base = [WELLNESS_STAT_BY_TOGGLE[c] for c in REMINDER_CATEGORIES
                  if not enabled[c]]
    return {
        "generated_at": now,
        "enabled": dict(enabled),
        "level": char_w["level"], "xp": char_w["xp"], "points": char_w["points"],
        "stats": char_w["stats"], "stats_base": stats_base, "muscles": char_w["muscles"],
        "vitals": _vitals(days, today_bundle, cfg, di_work, enabled),
        "gains_today": _gains(char_o, char_w),
        "form": klass["form"], "char_class": klass["char_class"], "class_sub": klass["class_sub"],
        "decay": {"grace_days": DECAY_GRACE_DAYS, "per_day": DECAY_PER_DAY},
    }, 200


# --------------------------------------------------------------------------- #
#  Thin READ-ONLY accessors for the idle-game overlay (idlegame.py). These add NO
#  behaviour to focus: they only expose numbers focus already derives, so the game
#  reads ONE Level identity (the RPG character) and the day's worked hours without
#  importing anything focus-internal or writing state.
# --------------------------------------------------------------------------- #
def character_level() -> dict:
    """The RPG character's Level, xp-fraction and activity points — the SAME numbers
    rpg() returns (level/xp/points depend only on the UN-decayed cumulative `raw` bundle
    via _activity_points/_level_xp, never on the enabled toggles or the decayed drivers).
    Read-only: reuses the rpg() pipeline (group history -> bundle -> points -> level) and
    NEVER writes. idlegame stores no level field and reads it through here, so the game's
    Level can neither diverge from the character nor be gamed."""
    now = _now()
    with _lock:
        state = load_state()
        raw_hist = state.get("history") if isinstance(state.get("history"), list) else []
        cfg = copy.deepcopy(state.get("config", {}) or {})
        streak = _int((state.get("metrics", {}) or {}).get("streak_days"), 0)
        today_bundle = _live_today_bundle(state, now)
    days = _group_sessions_by_day(raw_hist)
    raw, _eff, _di = _bundle(days, today_bundle, cfg, streak, now)
    points = _activity_points(raw)
    level, xp = _level_xp(points)
    return {"level": level, "xp": xp, "points": round(points, 2)}


def hours_today() -> float:
    """Worked hours in the CURRENT work-day so far — the same wall-clock measure focus
    surfaces as day.hours_worked (elapsed since started_at minus pauses and downtime,
    max(0,...)-clamped). Read-only, one consistent snapshot under the lock; idlegame's
    daily Collect multiplier reads it. No behaviour change to focus."""
    now = _now()
    with _lock:
        state = load_state()
        return round(_elapsed_worked_sec(state, now) / 3600.0, 4)
