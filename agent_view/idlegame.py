#!/usr/bin/env python3
"""Idle-game economy — the service layer behind the HUD's optional game overlay (F1).
Pure stdlib, no HTTP and no Hub/focus imports: server.py's /api/game routes stay thin
(parse body, gather external signals, call one function here, _send the result), exactly
like focus.py sits behind /api/focus. A carbon copy of focus.py's persistence discipline.

WHAT IT DOES (F1 — the honest thin slice; NO quiz, NO prestige/rank, NO HP damage)
    A tokens economy driven by REAL work telemetry the HUD already emits, threaded in by
    server.py: passive income while Claude sessions are live, a small bump per hook tool
    event, a once-per-session start cost, a daily Collect scaled by hours worked, and a
    token-priced shop whose upgrades make the economy grow faster. The character's Level is
    READ from the existing RPG character (focus.character_level), never a second identity.
    Shield/HP are presence-driven, non-punitive bars (see DEFENSE).

ACCRUAL — timestamp-based, commit-on-mutation (the crux; NEVER a tick counter)
    The committed anchor is (economy.tokens, economy.last_accrual_at). Given `now` and the
    server-supplied signals (live session count + the tool-event timestamps):
        span     = min(max(0, now - last_accrual_at), MAX_ACCRUAL_SPAN)   # unobserved excluded
        passive  = PASSIVE_RATE * passive_mult * min(live, SESSION_CAP) * span
        toolbump = TOOL_BONUS * tool_mult * (# tool events with ts in (last_accrual_at, now])
        pending  = passive + toolbump
    get_view() shows display_tokens = tokens + pending but PERSISTS only on a coarse
    checkpoint (first touch ≥ CHECKPOINT_SECS since the anchor) or on a mutation — never
    every poll (mirrors focus "recompute each poll, persist when the day rolled"). A restart
    loses ≤ CHECKPOINT_SECS of uncommitted accrual — acceptable and honest. Mutations COMMIT
    the pending balance FIRST, then act (collect/buy/toggle fold pending before spending).
    MAX_ACCRUAL_SPAN is the honesty guard: a slept laptop credits at most that span, never
    phantom hours (the focus downtime-exclusion analogue). The Hub tool ring caps at 40 per
    session, so >40 tool events between two touches UNDER-count — safe, never over-credit.

SHOP (a CODE CONSTANT, not data)
    SHOP_CATALOG lists {id, name, category, base_price, price_growth, effect}. State holds
    only the owned COUNT per id. buy(id) treats the id as a REGISTRY KEY: an id absent from
    the catalog is a 400, never resolved dynamically (no importlib, no getattr — the
    craft-security registry rule). The next copy costs base_price * price_growth ** owned.
    Effects plug into the REAL F1 mechanics: passive_mult raises the passive rate, tool_mult
    raises the per-tool bump, shield_max / hp_max raise the defense caps. shield_max/hp_max
    are DERIVED from the owned counts on every read (ONE source of truth = upgrades); the
    stored defense.shield_max/hp_max are a mirror refreshed on commit.

DEFENSE — presence-driven, non-punitive (§3 days-reality)
    Shield regenerates toward shield_max from PRESENCE (a live session that fired a tool
    within IDLE_SECS) and drains gently + recoverably ONLY during observed active-session
    idleness (a session live but no tool event for > IDLE_SECS). It NEVER drains when nothing
    is live, so days off cost nothing. HP has NO damage source in F1 (enemies are visual);
    hp regenerates toward hp_max, which grows via upgrades — so an hp_max upgrade visibly
    fills. Defense advances over its own (last_defense_at, now) span, same honesty clamp.

DAILY COLLECT — opportunities, never obligations
    collect() pays DAILY_BASE × multiplier(hours_today) once per LOCAL calendar day, capped
    at the 12h tier (a long day never pays MORE than the cap, and never LESS by "punishing"
    it). Missing a day forfeits nothing already earned — there is nothing to decay.

THE ONE WRITE PATH / TIME / FALLBACK
    save_state() is the only writer of game_state.json (atomic tmp + os.replace, one
    threading.Lock — a single writer in this one server process, like focus). load_state /
    save_state NEVER raise: a missing file initialises fresh, a corrupt one degrades to a
    disabled-but-present state so the routes still answer valid JSON. Every timestamp is a
    wall-clock time.time() epoch; every span is max(0, ...)-clamped. _now() is the test seam.
"""
from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import quiz  # the PURE F2 scheduler (Leitner / ratchet / boss / grade / shuffle); no cycle — quiz imports nothing here

HERE = Path(__file__).resolve().parent
#: Runtime state, gitignored beside focus_state.json. A module global (not baked into the
#: functions) so a test can point it at a temp file.
STATE_PATH = HERE / "game_state.json"

#: v2 adds the F2 layer (quiz progress + knowledge + prestige) ADDITIVELY: _deep_fill
#: backfills every new default, so an F1 file upgrades in place and a v2 file still reads
#: cleanly under F1 (F1 never touches the new keys). See _migrate_v2.
STATE_VERSION = 2

# ============================================================================ #
#  Tunable constants — ONE block (F1 seeds plausible values; F3 tunes on real
#  telemetry). Nothing below this block hardcodes a rate/cost/threshold.
# ============================================================================ #
PASSIVE_RATE = 0.10              # tokens/sec per live session (before upgrade multipliers)
TOOL_BONUS = 0.50               # tokens per in-window tool event (before upgrade multipliers)
SESSION_CAP = 5                 # live sessions counted for passive income (5 live ~= 5x)

DAILY_BASE = 50.0               # base daily Collect reward, before the hours multiplier
COLLECT_BASE_MULT = 1.0         # under 4h worked
#: (hours-threshold, multiplier) DESCENDING — the first tier whose threshold is met wins,
#: so the 12h tier is the CAP: beyond it Collect pays no more (never punishes the long day).
COLLECT_TIERS = ((12.0, 2.5), (8.0, 2.0), (4.0, 1.5))

SESSION_START_COST_BASE = 1.0        # tiny -n once per new session (a nudge, not a gate)...
SESSION_START_COST_PER_LEVEL = 0.25  # ...grows slowly with Level, always << one session's income
SESSIONS_SEEN_MAX = 500              # cap the sessions_seen ledger; sids are unique so old ones roll off harmlessly

MAX_ACCRUAL_SPAN = 45.0         # s — honesty guard: a slept laptop credits at most this span
CHECKPOINT_SECS = 30.0          # s — a get_view persists at most this often (else display-only)
IDLE_SECS = 90.0               # s — a live session with no tool event for this long is "idle" -> shield drains

SHIELD_MAX_BASE = 100           # base shield/hp caps; shield_max/hp_max upgrades raise them
HP_MAX_BASE = 100
SHIELD_REGEN_RATE = 1.5         # shield points/sec regained from presence
SHIELD_DRAIN_RATE = 0.5         # shield points/sec drained during active-session idleness (gentle)
HP_REGEN_RATE = 1.5             # hp points/sec toward hp_max (no damage source in F1)

#: The shop catalog — the ONE place upgrades are defined (like focus.DEFAULT_EXERCISES). State
#: holds only the owned count per id; the effect is applied by DERIVING from that count on read,
#: so there is a single source of truth. `effect_desc` is an ASCII hint (the frontend localises).
SHOP_CATALOG = (
    {"id": "subagent_scout", "name": "Scout subagent", "category": "subagent",
     "base_price": 50, "price_growth": 1.5, "effect": {"type": "passive_mult", "amount": 0.15},
     "effect_desc": "+15% passive income"},
    {"id": "subagent_worker", "name": "Worker subagent", "category": "subagent",
     "base_price": 200, "price_growth": 1.6, "effect": {"type": "passive_mult", "amount": 0.35},
     "effect_desc": "+35% passive income"},
    {"id": "tool_grep", "name": "Grep tool", "category": "tool",
     "base_price": 40, "price_growth": 1.5, "effect": {"type": "tool_mult", "amount": 0.20},
     "effect_desc": "+20% per tool event"},
    {"id": "tool_editor", "name": "Editor tool", "category": "tool",
     "base_price": 150, "price_growth": 1.55, "effect": {"type": "tool_mult", "amount": 0.40},
     "effect_desc": "+40% per tool event"},
    {"id": "skill_shield", "name": "Shield skill", "category": "skill",
     "base_price": 80, "price_growth": 1.7, "effect": {"type": "shield_max", "amount": 25},
     "effect_desc": "+25 shield max"},
    {"id": "skill_armor", "name": "Armor skill", "category": "skill",
     "base_price": 120, "price_growth": 1.7, "effect": {"type": "hp_max", "amount": 30},
     "effect_desc": "+30 HP max"},
    {"id": "subagent_reviewer", "name": "Reviewer subagent", "category": "subagent",
     "base_price": 600, "price_growth": 1.65, "effect": {"type": "passive_mult", "amount": 0.6},
     "effect_desc": "+60% passive income"},
    {"id": "subagent_planner", "name": "Planner subagent", "category": "subagent",
     "base_price": 2000, "price_growth": 1.7, "effect": {"type": "passive_mult", "amount": 1.0},
     "effect_desc": "+100% passive income"},
    {"id": "tool_bash", "name": "Bash tool", "category": "tool",
     "base_price": 500, "price_growth": 1.6, "effect": {"type": "tool_mult", "amount": 0.7},
     "effect_desc": "+70% per tool event"},
    {"id": "tool_task", "name": "Task tool", "category": "tool",
     "base_price": 1500, "price_growth": 1.65, "effect": {"type": "tool_mult", "amount": 1.2},
     "effect_desc": "+120% per tool event"},
    {"id": "skill_bulwark", "name": "Bulwark skill", "category": "skill",
     "base_price": 400, "price_growth": 1.75, "effect": {"type": "shield_max", "amount": 60},
     "effect_desc": "+60 shield max"},
    {"id": "skill_titan", "name": "Titan skill", "category": "skill",
     "base_price": 600, "price_growth": 1.75, "effect": {"type": "hp_max", "amount": 75},
     "effect_desc": "+75 HP max"},
)
_CATALOG_BY_ID = {item["id"]: item for item in SHOP_CATALOG}

# ============================================================================ #
#  F2 tunables — quiz -> defense/knowledge coupling + prestige. Same "one block"
#  discipline; F3 tunes on real telemetry. The honesty invariants (§1) are STRUCTURAL,
#  not values: shield only ever ADDS on success (never subtracts), knowledge comes
#  only from learning (box-5-first + boss pass), and prestige keeps lifetime/Level/quiz.
# ============================================================================ #
QUIZ_SHIELD_REFILL = 20.0       # a CORRECT normal answer adds this much shield (a wrong one adds 0)
BOSS_SHIELD_REFILL = 60.0       # a boss PASS adds this much shield (larger — the big win)

KNOW_PER_BOX5 = 1.0             # knowledge for the FIRST time a question matures to box 5 (once ever)
KNOW_PER_BOSS = 5.0            # knowledge for passing a boss

PRESTIGE_COST_BASE = 10.0       # knowledge cost of the FIRST prestige (rank 0 -> 1); first reset arrives fast
PRESTIGE_COST_GROWTH = 1.6      # each rank costs this much more knowledge
PRESTIGE_MULT_K = 0.02          # permanent income mult gained per prestige = K * sqrt(lifetime_tokens) (sub-linear)

BOSS_INTERVAL_SECS = 3.5 * 3600.0  # a boss becomes available every ~3.5 ACTIVE hours (cumulative session-active secs)

RECENT_CAP = 100               # cap the ratchet's rolling-answers ledger (normal answers only)
QUIZ_MAX_TIER = 5              # the ratchet never raises the active tier past this
QUIZ_LOW_WATER = 10           # background top-up when eligible unseen at the active tier drops below this
TAGS_MAX = 20                 # at most this many tags may be selected
TAG_MAXLEN = 40               # each tag is at most this many chars

#: Career-framed rank names (§3). A code constant beside SHOP_CATALOG — this is a
#: single-user tool, so "no hardcoded choices" (a multi-tenant rule) does not apply.
#: rank_name = RANK_NAMES[min(rank, last)].
RANK_NAMES = ("Junior", "Medior", "Senior", "Staff", "Principal",
              "Distinguished", "Fellow")

# One writer in this process -> one plain lock around every read-modify-write (same reasoning
# as focus.py: not the tickets store's cross-process lock — game_state has a single writer).
_lock = threading.Lock()


def _now() -> float:
    """Wall-clock epoch. A single seam so tests can freeze time (idlegame._now = ...)."""
    return time.time()


# --------------------------------------------------------------------------- #
#  Small tolerant readers (a hand-edited / older state file must never crash a read).
# --------------------------------------------------------------------------- #
def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _num(v, default):
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


def _local_day(now) -> str:
    """The LOCAL calendar date (YYYY-MM-DD) — matches focus._day_str, so 'once per day'
    means the same day boundary the rest of the HUD uses."""
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
#  State shape + persistence (the focus.py discipline, verbatim in spirit).
# --------------------------------------------------------------------------- #
def default_state(now=None) -> dict:
    """A fresh, ENABLED game anchored to now — the first-run state. The accrual/defense
    anchors are `now` (NOT 0), so the first poll credits ~0 rather than a MAX_ACCRUAL_SPAN
    windfall or the whole tool ring."""
    now = _now() if now is None else now
    return {
        "version": STATE_VERSION,
        "config": {"enabled": True},
        "economy": {"tokens": 0.0, "lifetime_tokens": 0.0, "last_accrual_at": now,
                    "knowledge": 0.0, "lifetime_knowledge": 0.0},
        "prestige": {"rank": 0, "mult": 1.0},
        "defense": {"shield": 0.0, "shield_max": SHIELD_MAX_BASE,
                    "hp": float(HP_MAX_BASE), "hp_max": HP_MAX_BASE, "last_defense_at": now},
        "upgrades": {item["id"]: 0 for item in SHOP_CATALOG},
        "collect": {"last_collect_day": None, "last_collect_at": None},
        "sessions_seen": [],
        "stats": {"collects": 0, "tools_credited": 0, "sessions_charged": 0},
        # F2 quiz layer (progress + scheduler cursors + boss cadence + gen guard).
        "quiz": {
            "tags": [], "tier_active": 1,
            "recent": [],            # capped [{tier, ok}] — ratchet input (NORMAL answers only)
            "progress": {},          # qid -> {box, due, seen, correct, wrong, last_result, matured}
            "boss": {"last_boss_at": 0.0, "active_secs": 0.0, "session": None},
            "gen": {"in_flight": False, "last_gen_at": 0.0, "tags_key": ""},
        },
    }


def _fallback_state() -> dict:
    """Disabled-but-present: the shape is whole so routes answer valid JSON, but the feature
    is off so nothing accrues. Returned only when a state file exists yet cannot be read /
    parsed — we do NOT overwrite it (it may be a transient lock or a recoverable file); the
    next successful mutation replaces it."""
    st = default_state()
    st["config"]["enabled"] = False
    return st


def _deep_fill(dst: dict, template: dict) -> dict:
    """Fill any key missing from `dst` with the template's default, recursing into nested
    dicts. Lets an older/partial state file gain new fields (e.g. a newly-added shop id in
    upgrades) without losing the user's existing values."""
    for k, v in template.items():
        if k not in dst:
            dst[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_fill(dst[k], v)
    return dst


def _migrate_v2(data: dict) -> dict:
    """v1 -> v2 (the F2 layer: quiz progress + knowledge + prestige). The growth is
    purely ADDITIVE, so there is no field to rewrite — _deep_fill (run right after)
    backfills every new default from default_state(), and the F1 keys are left
    exactly as they were. The seam exists explicitly so a future NON-additive change
    has one obvious home and the version is stamped in one place. Idempotent."""
    return data


def _migrate_state(data: dict) -> dict:
    """The version-migration seam (idempotent). Applies each step in order, then
    stamps the current version. Runs before _deep_fill, which backfills any new
    default a step relies on."""
    if not isinstance(data, dict):
        return data
    if _int(data.get("version"), 0) < 2:
        _migrate_v2(data)
    if _int(data.get("version"), 0) < STATE_VERSION:
        data["version"] = STATE_VERSION
    return data


def load_state() -> dict:
    """Read state from disk, initialising on first run. NEVER raises — a bad file degrades to
    _fallback_state() rather than taking the server down. Callers hold _lock around
    load_state()+save_state() for a consistent read-modify-write."""
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
    _migrate_state(data)
    return _deep_fill(data, default_state())


def _write(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)        # atomic on Windows and POSIX


def save_state(state: dict) -> bool:
    """The ONE write path. Returns False on a failed persist instead of raising, so an
    optional overlay can never crash the server over a disk hiccup."""
    try:
        _write(state)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Signals + derived upgrade effects (pure over a state dict).
# --------------------------------------------------------------------------- #
def _enabled(state: dict) -> bool:
    return bool((state.get("config", {}) or {}).get("enabled"))


def _owned(state: dict, item_id: str) -> int:
    return _int((state.get("upgrades", {}) or {}).get(item_id), 0)


def _passive_mult(state: dict) -> float:
    """1 + the summed passive_mult bonuses of owned subagent upgrades."""
    m = 1.0
    for item in SHOP_CATALOG:
        if item["effect"]["type"] == "passive_mult":
            m += _owned(state, item["id"]) * item["effect"]["amount"]
    return m


def _tool_mult(state: dict) -> float:
    """1 + the summed tool_mult bonuses of owned tool upgrades."""
    m = 1.0
    for item in SHOP_CATALOG:
        if item["effect"]["type"] == "tool_mult":
            m += _owned(state, item["id"]) * item["effect"]["amount"]
    return m


def _prestige_mult(state: dict) -> float:
    """The permanent prestige income multiplier (>= 1.0). Applied in exactly ONE
    place — _pending_tokens (accrual) + _rate_per_min (the shown rate) — so passive
    and tool income scale with rank while PRICES do not (owned counts reset at
    prestige, so a fixed price with reset counts already means a fresh curve)."""
    return max(1.0, _num((state.get("prestige", {}) or {}).get("mult"), 1.0))


def _derive_maxes(state: dict) -> tuple:
    """(shield_max, hp_max) DERIVED from the owned counts — the ONE source of truth. base +
    the summed shield_max/hp_max upgrade amounts."""
    shield_max = SHIELD_MAX_BASE
    hp_max = HP_MAX_BASE
    for item in SHOP_CATALOG:
        eff = item["effect"]
        if eff["type"] == "shield_max":
            shield_max += _owned(state, item["id"]) * eff["amount"]
        elif eff["type"] == "hp_max":
            hp_max += _owned(state, item["id"]) * eff["amount"]
    return shield_max, hp_max


def _price(item: dict, owned: int) -> int:
    """The cost of the NEXT copy given `owned` already held: base_price * price_growth**owned,
    rounded to a whole token."""
    base = _num(item.get("base_price"), 0.0)
    growth = _num(item.get("price_growth"), 1.0)
    return int(round(base * (growth ** max(0, owned))))


def _span(anchor: float, now: float) -> float:
    """The clamped, observed span since an anchor: max(0, now-anchor) capped at
    MAX_ACCRUAL_SPAN (the honesty guard shared by accrual and defense)."""
    return min(max(0.0, now - _num(anchor, now)), MAX_ACCRUAL_SPAN)


# --------------------------------------------------------------------------- #
#  Prestige (rank) + boss-cadence helpers — pure over the state dict.
# --------------------------------------------------------------------------- #
def prestige_cost(rank: int) -> int:
    """Knowledge cost to buy the NEXT rank from `rank`: base * growth**rank. The
    first reset (rank 0 -> 1) is deliberately cheap so the loop is learned fast."""
    return int(round(PRESTIGE_COST_BASE * (PRESTIGE_COST_GROWTH ** max(0, _int(rank, 0)))))


def prestige_gain(lifetime_tokens) -> float:
    """The permanent income-mult increment a prestige grants: SUB-LINEAR in lifetime
    (~k*sqrt), so each reset helps but with diminishing returns (§3)."""
    return PRESTIGE_MULT_K * math.sqrt(max(0.0, _num(lifetime_tokens, 0.0)))


def rank_name(rank: int) -> str:
    return RANK_NAMES[min(max(0, _int(rank, 0)), len(RANK_NAMES) - 1)]


def _quiz(state: dict) -> dict:
    return state.setdefault("quiz", {})


def _active_secs(state: dict) -> float:
    return _num(((state.get("quiz", {}) or {}).get("boss", {}) or {}).get("active_secs"), 0.0)


def _boss_available(state: dict) -> bool:
    """A boss is available when the game is on, no boss run is in progress, and at
    least BOSS_INTERVAL_SECS of cumulative active time has passed since the last one.
    Whether enough questions EXIST is checked at boss_start (a clean 409), not here."""
    if not _enabled(state):
        return False
    boss = (state.get("quiz", {}) or {}).get("boss", {}) or {}
    if boss.get("session") is not None:
        return False
    return (_active_secs(state) - _num(boss.get("last_boss_at"), 0.0)) >= BOSS_INTERVAL_SECS


def _tools_in_window(tool_ts, lo: float, hi: float) -> int:
    """Count tool-event timestamps in (lo, hi]. Strictly > lo so the same event is never
    credited across two commits; <= hi so a future-stamped event does not leak in."""
    n = 0
    for ts in (tool_ts or []):
        try:
            t = float(ts)
        except (TypeError, ValueError):
            continue
        if lo < t <= hi:
            n += 1
    return n


def _recent_tool(tool_ts, now: float) -> bool:
    """True when any tool fired within the last IDLE_SECS — the presence signal that keeps
    the shield regenerating rather than draining."""
    return _tools_in_window(tool_ts, now - IDLE_SECS, now) > 0


def _pending_tokens(state: dict, now: float, live: int, tool_ts) -> tuple:
    """(pending_tokens, tool_count) accrued but not yet committed since last_accrual_at. Zero
    when the game is disabled. Pure — never mutates state."""
    if not _enabled(state):
        return 0.0, 0
    anchor = _num((state.get("economy", {}) or {}).get("last_accrual_at"), now)
    span = _span(anchor, now)
    pm = _prestige_mult(state)               # the ONE place prestige mult multiplies income
    passive = PASSIVE_RATE * _passive_mult(state) * pm * min(max(0, int(live)), SESSION_CAP) * span
    n_tools = _tools_in_window(tool_ts, anchor, now)
    toolbump = TOOL_BONUS * _tool_mult(state) * pm * n_tools
    return passive + toolbump, n_tools


def _rate_per_min(state: dict, live: int) -> float:
    """The CURRENT passive rate shown in the economy panel (tokens/min), 0 when disabled or
    nothing is live. The tool bump is event-driven, so it is not part of the ambient rate."""
    if not _enabled(state):
        return 0.0
    return round(PASSIVE_RATE * _passive_mult(state) * _prestige_mult(state)
                 * min(max(0, int(live)), SESSION_CAP) * 60.0, 4)


def _defense_step(shield, hp, shield_max, hp_max, span, live, recent_tool, enabled):
    """Advance (shield, hp) over `span` seconds. HP regenerates toward hp_max (no damage in
    F1). Shield: presence (live + a recent tool) regenerates it; observed active-session
    idleness (live, no recent tool) drains it gently; nothing live -> HOLD (days off cost
    nothing). All clamped to their caps. Pure — the caller decides whether to persist."""
    shield = _clamp(_num(shield, 0.0), 0.0, shield_max)
    hp = _clamp(_num(hp, hp_max), 0.0, hp_max)
    if not enabled or span <= 0:
        return shield, hp
    hp = min(float(hp_max), hp + HP_REGEN_RATE * span)
    if live <= 0:
        pass                                        # nothing live: shield holds
    elif recent_tool:
        shield = min(float(shield_max), shield + SHIELD_REGEN_RATE * span)   # presence regens
    else:
        shield = max(0.0, shield - SHIELD_DRAIN_RATE * span)                 # live+idle: gentle drain
    return _clamp(shield, 0.0, shield_max), _clamp(hp, 0.0, hp_max)


# --------------------------------------------------------------------------- #
#  Commit — the single "bring the committed anchor up to now" routine, shared by
#  the get_view checkpoint and EVERY mutation (mutations commit before they act).
# --------------------------------------------------------------------------- #
def _commit(state: dict, now: float, signals: dict) -> None:
    """Fold pending accrual into the true balance, charge any newly-seen session, advance the
    defense bars, refresh the derived caps, and move both anchors to now. Mutates state in
    place; the caller persists."""
    live = _int((signals or {}).get("live_sessions"), 0)
    tool_ts = (signals or {}).get("tool_ts") or []
    econ = state.setdefault("economy", {})

    pending, n_tools = _pending_tokens(state, now, live, tool_ts)
    econ["tokens"] = _num(econ.get("tokens"), 0.0) + pending
    econ["lifetime_tokens"] = _num(econ.get("lifetime_tokens"), 0.0) + pending
    econ["last_accrual_at"] = now
    if n_tools:
        stats = state.setdefault("stats", {})
        stats["tools_credited"] = _int(stats.get("tools_credited"), 0) + n_tools

    _charge_new_sessions(state, signals)

    # Defense advances over its own span; refresh the caps from upgrades first so a just-bought
    # shield_max/hp_max upgrade lifts the ceiling this same commit.
    defense = state.setdefault("defense", {})
    shield_max, hp_max = _derive_maxes(state)
    defense["shield_max"], defense["hp_max"] = shield_max, hp_max
    span = _span(defense.get("last_defense_at"), now)
    shield, hp = _defense_step(defense.get("shield"), defense.get("hp"),
                               shield_max, hp_max, span, live,
                               _recent_tool(tool_ts, now), _enabled(state))
    defense["shield"], defense["hp"] = shield, hp
    defense["last_defense_at"] = now

    # Boss cadence signal (§ locked decision 4): the anti-phone window is CUMULATIVE
    # session-active seconds, not focus worked-time. Accrue the same clamped span
    # whenever a session is live (and the game is on) — a day off adds nothing.
    if _enabled(state) and live > 0:
        boss = state.setdefault("quiz", {}).setdefault("boss", {})
        boss["active_secs"] = _num(boss.get("active_secs"), 0.0) + span


def _charge_new_sessions(state: dict, signals: dict) -> None:
    """Deduct the tiny session-start cost once per never-seen sid (grows with Level, always
    << one session's income). Clamped so tokens never go negative — a nudge, not a gate.
    Skipped when the game is disabled."""
    if not _enabled(state):
        return
    ids = [str(s) for s in ((signals or {}).get("session_ids") or []) if s]
    if not ids:
        return
    seen = state.setdefault("sessions_seen", [])
    seen_set = set(seen)
    level = _int(((signals or {}).get("level") or {}).get("level"), 1)
    cost = SESSION_START_COST_BASE + SESSION_START_COST_PER_LEVEL * max(0, level)
    econ = state.setdefault("economy", {})
    charged = 0
    for sid in ids:
        if sid in seen_set:
            continue
        econ["tokens"] = max(0.0, _num(econ.get("tokens"), 0.0) - cost)
        seen.append(sid)
        seen_set.add(sid)
        charged += 1
    if charged:
        if len(seen) > SESSIONS_SEEN_MAX:
            del seen[:-SESSIONS_SEEN_MAX]       # keep the most recent; unique sids never re-charge
        stats = state.setdefault("stats", {})
        stats["sessions_charged"] = _int(stats.get("sessions_charged"), 0) + charged


# --------------------------------------------------------------------------- #
#  Collect (daily) helpers.
# --------------------------------------------------------------------------- #
def _collect_multiplier(hours) -> float:
    """DAILY_BASE's multiplier for `hours` worked today — the first (descending) tier whose
    threshold is met, capped at the 12h tier; COLLECT_BASE_MULT below the first threshold."""
    h = _num(hours, 0.0)
    for thr, mult in COLLECT_TIERS:
        if h >= thr:
            return mult
    return COLLECT_BASE_MULT


def _collect_available(state: dict, now: float) -> bool:
    """True when today's Collect has not been taken. 'today' is the LOCAL calendar day; a
    missed day forfeits nothing (there is nothing stored to decay)."""
    return (state.get("collect", {}) or {}).get("last_collect_day") != _local_day(now)


def _collect_preview(state: dict, now: float, hours) -> tuple:
    """(available, multiplier, reward-if-collected-now)."""
    available = _collect_available(state, now)
    mult = _collect_multiplier(hours)
    reward = int(round(DAILY_BASE * mult)) if available else 0
    return available, mult, reward


# --------------------------------------------------------------------------- #
#  The view (response body) — pure over state + signals; computes the DISPLAY
#  balances (tokens + pending, shield/hp previewed forward) without persisting.
# --------------------------------------------------------------------------- #
def _build_view(state: dict, now: float, signals: dict) -> dict:
    """The GET/mutation response. display_tokens = committed tokens + pending accrual; the
    shield/hp bars are previewed forward from their last commit; the shop reports per-item
    price/owned/affordability; Level is READ from the threaded signal (never stored here)."""
    signals = signals or {}
    live = _int(signals.get("live_sessions"), 0)
    tool_ts = signals.get("tool_ts") or []

    pending, _n = _pending_tokens(state, now, live, tool_ts)
    econ = state.get("economy", {}) or {}
    display_tokens = _num(econ.get("tokens"), 0.0) + pending
    lifetime = _num(econ.get("lifetime_tokens"), 0.0)

    shield_max, hp_max = _derive_maxes(state)
    defense = state.get("defense", {}) or {}
    span = _span(defense.get("last_defense_at"), now)
    disp_shield, disp_hp = _defense_step(defense.get("shield"), defense.get("hp"),
                                         shield_max, hp_max, span, live,
                                         _recent_tool(tool_ts, now), _enabled(state))

    level = signals.get("level") or {}
    hours = _num(signals.get("hours_today"), 0.0)
    available, mult, reward = _collect_preview(state, now, hours)

    shop = []
    for item in SHOP_CATALOG:
        owned = _owned(state, item["id"])
        price = _price(item, owned)
        shop.append({
            "id": item["id"], "name": item["name"], "category": item["category"],
            "price": price, "owned": owned,
            "affordable": display_tokens >= price,
            "effect_desc": item["effect_desc"],
        })

    stats = state.get("stats", {}) or {}

    # --- F2: prestige + knowledge + quiz (all ADDITIVE; F1 keys above unchanged) ---
    prestige = state.get("prestige", {}) or {}
    rank = _int(prestige.get("rank"), 0)
    knowledge = _num(econ.get("knowledge"), 0.0)
    next_cost = prestige_cost(rank)

    quiz_st = state.get("quiz", {}) or {}
    tier_active = _int(quiz_st.get("tier_active"), 1)
    progress = quiz_st.get("progress", {}) or {}
    due_count = sum(1 for p in progress.values()
                    if isinstance(p, dict) and p.get("due") is not None
                    and _num(p.get("due"), now) <= now)
    acc = quiz.ratchet(quiz_st.get("recent", []), tier_active).get("accuracy")

    return {
        "config": {"enabled": _enabled(state)},
        "economy": {"tokens": int(display_tokens), "lifetime": int(lifetime),
                    "rate_per_min": _rate_per_min(state, live),
                    "knowledge": round(knowledge, 2),
                    "lifetime_knowledge": round(_num(econ.get("lifetime_knowledge"), 0.0), 2)},
        "prestige": {"rank": rank, "rank_name": rank_name(rank),
                     "mult": round(_prestige_mult(state), 4),
                     "next_cost": next_cost,
                     "can_prestige": _enabled(state) and knowledge >= next_cost},
        "defense": {"shield": int(disp_shield), "shield_max": int(shield_max),
                    "hp": int(disp_hp), "hp_max": int(hp_max)},
        "level": {"level": _int(level.get("level"), 0),
                  "xp": round(_num(level.get("xp"), 0.0), 4),
                  "points": round(_num(level.get("points"), 0.0), 2)},
        "collect": {"available": available, "multiplier": mult,
                    "preview": reward, "hours_today": round(hours, 4)},
        "shop": shop,
        "quiz": {"tier_active": tier_active,
                 "tags": list(quiz_st.get("tags", []) or []),   # so the tags modal opens pre-selected
                 "accuracy": (round(acc, 4) if acc is not None else None),
                 "due_count": due_count,
                 "boss": {"available": _boss_available(state),
                          "active_secs": round(_active_secs(state), 2),
                          "interval_secs": BOSS_INTERVAL_SECS}},
        "stats": {"collects": _int(stats.get("collects"), 0),
                  "tools_credited": _int(stats.get("tools_credited"), 0),
                  "sessions_charged": _int(stats.get("sessions_charged"), 0)},
    }


# --------------------------------------------------------------------------- #
#  Public API — server.py threads the external signals in; idlegame owns validation
#  and the single JSON write path. GET is a read (persists only on checkpoint); the
#  three mutations commit pending first, then act, then persist.
# --------------------------------------------------------------------------- #
def get_view(signals=None, commit=True) -> dict:
    """GET /api/game/state. Recomputes the display every poll; PERSISTS only on the coarse
    CHECKPOINT_SECS checkpoint (bounding write amplification), otherwise returns a
    display-only snapshot the next poll/mutation reconciles. `commit` is False for an
    UNTRUSTED read (cross-origin / LAN) so it stays purely read-only: only the real
    same-origin loopback HUD advances the accrual anchor, so a foreign page polling fast
    cannot advance it to defeat the MAX_ACCRUAL_SPAN honesty clamp."""
    now = _now()
    with _lock:
        state = load_state()
        anchor = _num((state.get("economy", {}) or {}).get("last_accrual_at"), now)
        if commit and now - anchor >= CHECKPOINT_SECS:
            _commit(state, now, signals or {})
            save_state(state)
        return _build_view(state, now, signals or {})


def collect(hours_today, signals=None):
    """POST /api/game/collect — pay DAILY_BASE × multiplier(hours_today) once per local day.
    Commits pending first (the folded income is kept even if Collect is refused). 409 when
    already collected today; missing a day forfeits nothing."""
    now = _now()
    with _lock:
        state = load_state()
        _commit(state, now, signals or {})
        if not _collect_available(state, now):
            save_state(state)                       # keep the just-folded accrual
            return {"error": "already collected today"}, 409
        mult = _collect_multiplier(hours_today)
        reward = int(round(DAILY_BASE * mult))
        econ = state.setdefault("economy", {})
        econ["tokens"] = _num(econ.get("tokens"), 0.0) + reward
        econ["lifetime_tokens"] = _num(econ.get("lifetime_tokens"), 0.0) + reward
        col = state.setdefault("collect", {})
        col["last_collect_day"] = _local_day(now)
        col["last_collect_at"] = now
        stats = state.setdefault("stats", {})
        stats["collects"] = _int(stats.get("collects"), 0) + 1
        save_state(state)
        return _build_view(state, now, signals or {}), 200


def buy(item_id, signals=None):
    """POST /api/game/buy {id} — spend tokens on a shop upgrade. `item_id` is a REGISTRY KEY:
    unknown -> 400 (never resolved dynamically). Commits pending first, then spends. 400 on
    insufficient tokens; else increments the owned count (the effect derives from it)."""
    now = _now()
    with _lock:
        state = load_state()
        _commit(state, now, signals or {})
        item = _CATALOG_BY_ID.get(item_id) if isinstance(item_id, str) else None
        if item is None:
            save_state(state)                       # keep the folded accrual; reject the buy
            return {"error": "unknown item"}, 400
        owned = _owned(state, item["id"])
        price = _price(item, owned)
        econ = state.setdefault("economy", {})
        if _num(econ.get("tokens"), 0.0) < price:
            save_state(state)
            return {"error": "insufficient tokens"}, 400
        econ["tokens"] = _num(econ.get("tokens"), 0.0) - price
        state.setdefault("upgrades", {})[item["id"]] = owned + 1
        # Refresh the caps so a shield_max/hp_max buy lifts the ceiling immediately.
        defense = state.setdefault("defense", {})
        defense["shield_max"], defense["hp_max"] = _derive_maxes(state)
        save_state(state)
        return _build_view(state, now, signals or {}), 200


def toggle(enabled, signals=None):
    """POST /api/game/toggle {enabled: bool} — enable/disable the game. Non-bool -> 400 with
    no state change. Commits pending under the OLD enabled state first (so nothing accrued is
    lost or invented), then flips the flag."""
    if not isinstance(enabled, bool):
        return {"error": "enabled must be a boolean"}, 400
    now = _now()
    with _lock:
        state = load_state()
        _commit(state, now, signals or {})
        state.setdefault("config", {})["enabled"] = enabled
        save_state(state)
        return _build_view(state, now, signals or {}), 200


# =========================================================================== #
#  F2 — quiz / boss / knowledge / prestige. server.py threads the BANK in the
#  same way it threads the Hub/focus signals (idlegame never reads bank.json),
#  and flips the generation in_flight guard here while quizbank does the Gemini
#  work with no lock held. Every mutation commits pending accrual FIRST.
# =========================================================================== #
def _find(bank, qid):
    """The bank question with this id, or None (server passes the whole live bank)."""
    for q in bank or []:
        if isinstance(q, dict) and q.get("id") == qid:
            return q
    return None


def _clean_tags(tags):
    """Validate an UNTRUSTED tag list -> (cleaned, ok). Rejects (ok=False) a non-list,
    more than TAGS_MAX entries, a non-string entry, or an over-long tag. Otherwise
    returns the stripped, de-duplicated, order-preserved non-empty tags."""
    if not isinstance(tags, list) or len(tags) > TAGS_MAX:
        return [], False
    out = []
    for t in tags:
        if not isinstance(t, str):
            return [], False
        s = t.strip()
        if len(s) > TAG_MAXLEN:
            return [], False
        if s and s not in out:
            out.append(s)
    return out, True


def _served_question(progress, question, now) -> dict:
    """The play-time payload for one question: {qid, tier, box, stem, options, due}.
    Options are shuffled + re-labelled by quiz.shuffle_options and carry ONLY
    {key, text} — the correct key / why-wrong / misconception never leave here."""
    qid = question.get("id")
    prog = quiz._prog(progress, qid)
    seed = quiz.option_seed(qid, _int(prog.get("seen"), 0))
    served, _key_map = quiz.shuffle_options(question, seed)
    due = _num(prog.get("due"), now) if prog.get("due") is not None else now
    return {"qid": qid, "tier": _int(question.get("tier"), 1),
            "box": _int(prog.get("box"), 1), "stem": question.get("stem", ""),
            "options": served, "due": due}


def _grade(question, qid, progress, served_choice, now):
    """Grade a returned SERVED label against the bank. Returns (correct, feedback) or
    None when the label is not one this serving offered. The shuffle is reproduced
    from the STABLE (qid, seen) seed — the same layout the serve produced — so no
    per-serve state is stored; feedback speaks in SERVED labels for the client."""
    seed = quiz.option_seed(qid, _int(quiz._prog(progress, qid).get("seen"), 0))
    served, key_map = quiz.shuffle_options(question, seed)
    if served_choice not in key_map:
        return None
    correct, ck_stored, chosen_why, expl, rej_stored = quiz.grade(question, key_map[served_choice])
    reverse = {stored: label for label, stored in key_map.items()}
    feedback = {
        "correct": bool(correct),
        "correct_key": reverse.get(ck_stored),
        "chosen_why_wrong": chosen_why,
        "explanation": expl,
        "rejections": [{"key": reverse.get(r["key"]),
                        "misconception": r["misconception"],
                        "why_wrong": r["why_wrong"]} for r in rej_stored],
    }
    return bool(correct), feedback


def _award_learning(state, prog_before, new_prog, matured_now) -> float:
    """Knowledge ONLY from learning: the FIRST time a question matures to box 5
    (once ever per question — gated on the `matured` flag), award KNOW_PER_BOX5.
    Never from work / Collect. Returns the amount awarded (0 if not the first)."""
    already = isinstance(prog_before, dict) and bool(prog_before.get("matured"))
    if matured_now and not already:
        new_prog["matured"] = True
        econ = state.setdefault("economy", {})
        econ["knowledge"] = _num(econ.get("knowledge"), 0.0) + KNOW_PER_BOX5
        econ["lifetime_knowledge"] = _num(econ.get("lifetime_knowledge"), 0.0) + KNOW_PER_BOX5
        return KNOW_PER_BOX5
    return 0.0


def quiz_next(bank, signals=None) -> dict:
    """GET /api/game/quiz/next — the next due question, or {empty:true} when the
    tag-matched pool is empty. A pure READ: it never mutates progress or advances
    the accrual anchor (only the /state checkpoint and mutations do)."""
    now = _now()
    with _lock:
        state = load_state()
        quiz_st = _quiz(state)
        progress = quiz_st.setdefault("progress", {})
        q = quiz.select_next(bank, progress, quiz_st.get("tags", []),
                             _int(quiz_st.get("tier_active"), 1), now)
        if q is None:
            return {"empty": True}
        return _served_question(progress, q, now)


def quiz_answer(qid, choice, question, signals=None):
    """POST /api/game/quiz/answer {qid, choice}. Commits pending first, then grades.

    HONESTY (§1): the ONLY quiz->defense coupling is POSITIVE — a CORRECT answer adds
    QUIZ_SHIELD_REFILL; a WRONG answer leaves `shield` numerically UNCHANGED (there is
    no subtraction anywhere on this path). Knowledge comes only from a first box-5
    maturation. `qid`/`choice` are untrusted: an unknown/retired question or a choice
    this serving never offered is a 400."""
    now = _now()
    with _lock:
        state = load_state()
        _commit(state, now, signals or {})
        if not isinstance(qid, str) or not isinstance(question, dict) \
                or question.get("id") != qid or question.get("retired"):
            save_state(state)
            return {"error": "unknown question"}, 400
        quiz_st = _quiz(state)
        progress = quiz_st.setdefault("progress", {})
        graded = _grade(question, qid, progress, choice, now)
        if graded is None:
            save_state(state)
            return {"error": "invalid choice"}, 400
        correct, feedback = graded

        prog_before = quiz._prog(progress, qid)
        new_prog, matured_now = quiz.apply_result(prog_before, correct, now)
        _award_learning(state, prog_before, new_prog, matured_now)
        progress[qid] = new_prog

        # Ratchet input — NORMAL answers only (boss answers never feed `recent`).
        recent = quiz_st.setdefault("recent", [])
        recent.append({"tier": _int(question.get("tier"), 1), "ok": bool(correct)})
        if len(recent) > RECENT_CAP:
            del recent[:-RECENT_CAP]
        if quiz.ratchet(recent, _int(quiz_st.get("tier_active"), 1)).get("raise_tier"):
            quiz_st["tier_active"] = min(_int(quiz_st.get("tier_active"), 1) + 1, QUIZ_MAX_TIER)

        # POSITIVE-only shield coupling: add on success, DO NOTHING on failure.
        defense = state.setdefault("defense", {})
        if correct:
            shield_max, _hm = _derive_maxes(state)
            defense["shield"] = min(float(shield_max),
                                    _num(defense.get("shield"), 0.0) + QUIZ_SHIELD_REFILL)

        econ = state.get("economy", {}) or {}
        resp = dict(feedback)
        resp["box"] = _int(new_prog.get("box"), 1)
        resp["due"] = _num(new_prog.get("due"), now)
        resp["shield"] = int(_num(defense.get("shield"), 0.0))
        resp["knowledge"] = round(_num(econ.get("knowledge"), 0.0), 2)
        save_state(state)
        return resp, 200


def boss_start(bank, signals=None):
    """POST /api/game/boss/start — assemble a 20-question boss (fewer if the pool is
    small) when the active-time cadence allows it, else 409 `boss not ready`."""
    now = _now()
    with _lock:
        state = load_state()
        _commit(state, now, signals or {})
        if not _boss_available(state):
            save_state(state)
            return {"error": "boss not ready"}, 409
        quiz_st = _quiz(state)
        progress = quiz_st.setdefault("progress", {})
        qs = quiz.assemble_boss(bank, progress, _int(quiz_st.get("tier_active"), 1), now)
        if not qs:
            save_state(state)
            return {"error": "boss not ready"}, 409       # cadence met but no questions
        qids = [q["id"] for q in qs]
        session = {"id": "boss_" + os.urandom(4).hex(), "qids": qids, "idx": 0,
                   "correct": 0, "answered": 0, "total": len(qids), "started_at": now}
        quiz_st.setdefault("boss", {})["session"] = session
        first = _served_question(progress, qs[0], now)
        save_state(state)
        return {"session_id": session["id"], "total": len(qids), "first": first}, 200


def boss_answer(bank, choice, signals=None):
    """POST /api/game/boss/answer {choice}. Grades the current boss question and
    serves the next, or finishes the run. FORGIVING (§4.5): a fail NEVER zeroes
    progress — every miss is simply rescheduled to box 1 by apply_result — and the
    cadence resets on completion (pass OR fail) so a boss is not farmable by retry.
    Boss answers do NOT feed the ratchet and do NOT refill shield per-answer; a PASS
    (> BOSS_PASS_FRACTION) grants BOSS_SHIELD_REFILL + KNOW_PER_BOSS once at the end."""
    now = _now()
    with _lock:
        state = load_state()
        _commit(state, now, signals or {})
        quiz_st = _quiz(state)
        boss = quiz_st.setdefault("boss", {})
        session = boss.get("session")
        if not isinstance(session, dict):
            save_state(state)
            return {"error": "no boss in progress"}, 409
        idx = _int(session.get("idx"), 0)
        qids = session.get("qids") or []
        total = _int(session.get("total"), len(qids))
        if idx >= len(qids):
            boss["session"] = None
            save_state(state)
            return {"error": "no boss in progress"}, 409

        qid = qids[idx]
        progress = quiz_st.setdefault("progress", {})
        question = _find(bank, qid)
        if question is None:
            # Retired/removed mid-boss: count it answered (not correct), no feedback.
            correct = False
            feedback = {"correct": False, "correct_key": None, "chosen_why_wrong": "",
                        "explanation": "", "rejections": []}
            new_box, new_due = _int(quiz._prog(progress, qid).get("box"), 1), now
        else:
            graded = _grade(question, qid, progress, choice, now)
            if graded is None:
                save_state(state)
                return {"error": "invalid choice"}, 400
            correct, feedback = graded
            prog_before = quiz._prog(progress, qid)
            new_prog, matured_now = quiz.apply_result(prog_before, correct, now)
            _award_learning(state, prog_before, new_prog, matured_now)   # box-5 knowledge counts on any path
            progress[qid] = new_prog
            new_box = _int(new_prog.get("box"), 1)
            new_due = _num(new_prog.get("due"), now)

        session["answered"] = _int(session.get("answered"), 0) + 1
        if correct:
            session["correct"] = _int(session.get("correct"), 0) + 1
        session["idx"] = idx + 1

        resp = dict(feedback)
        resp["box"], resp["due"] = new_box, new_due
        resp["idx"], resp["total"] = session["idx"], total

        if session["idx"] >= total:
            score = (session["correct"] / total) if total else 0.0
            passed = score >= quiz.BOSS_PASS_FRACTION
            rewards = {"shield_refill": 0.0, "knowledge": 0.0}
            if passed:
                shield_max, _hm = _derive_maxes(state)
                defense = state.setdefault("defense", {})
                defense["shield"] = min(float(shield_max),
                                        _num(defense.get("shield"), 0.0) + BOSS_SHIELD_REFILL)
                econ = state.setdefault("economy", {})
                econ["knowledge"] = _num(econ.get("knowledge"), 0.0) + KNOW_PER_BOSS
                econ["lifetime_knowledge"] = _num(econ.get("lifetime_knowledge"), 0.0) + KNOW_PER_BOSS
                rewards = {"shield_refill": BOSS_SHIELD_REFILL, "knowledge": KNOW_PER_BOSS}
            boss["last_boss_at"] = _active_secs(state)     # reset cadence (forgiving, not farmable)
            boss["session"] = None
            resp["done"], resp["passed"], resp["score"], resp["rewards"] = True, bool(passed), round(score, 4), rewards
        else:
            resp["done"] = False
            nxt = _find(bank, qids[session["idx"]])
            resp["next"] = _served_question(progress, nxt, now) if nxt is not None else None
        save_state(state)
        return resp, 200


def boss_cancel():
    """POST /api/game/boss/cancel — abandon an in-progress boss run (the client closed
    the modal without answering every question). Clears the session so it stops blocking
    boss availability; grants NOTHING (misses already answered stay rescheduled to box 1)
    and does NOT touch the cadence, so the boss is available again as soon as
    active_secs >= interval. Idempotent: no active session -> still {ok:true}."""
    with _lock:
        state = load_state()
        _quiz(state).setdefault("boss", {})["session"] = None
        save_state(state)
        return {"ok": True}, 200


def prestige(signals=None):
    """POST /api/game/prestige — cash in knowledge for a permanent income multiplier
    and rank+1. One locked read-modify-write; commits pending FIRST. 400 when
    knowledge < prestige_cost(rank). Resets BASE stats (tokens + all upgrades ->
    maxes derive back to base). KEEPS lifetime_tokens, lifetime_knowledge, the
    knowledge remainder, the RPG Level (read from signals, never stored here) and
    ALL quiz progress/tags/tier. The one in-app irreversible action — user-initiated,
    atomic (§ Risks)."""
    now = _now()
    with _lock:
        state = load_state()
        _commit(state, now, signals or {})
        prest = state.setdefault("prestige", {})
        econ = state.setdefault("economy", {})
        rank = _int(prest.get("rank"), 0)
        cost = prestige_cost(rank)
        if _num(econ.get("knowledge"), 0.0) < cost:
            save_state(state)
            return {"error": "insufficient knowledge"}, 400
        lifetime = _num(econ.get("lifetime_tokens"), 0.0)     # gain uses lifetime BEFORE reset (kept)
        econ["knowledge"] = _num(econ.get("knowledge"), 0.0) - cost
        prest["rank"] = rank + 1
        prest["mult"] = _prestige_mult(state) + prestige_gain(lifetime)
        # Reset base stats: tokens + every owned upgrade (defense caps derive back to base).
        econ["tokens"] = 0.0
        econ["last_accrual_at"] = now
        state["upgrades"] = {item["id"]: 0 for item in SHOP_CATALOG}
        defense = state.setdefault("defense", {})
        smax, hmax = _derive_maxes(state)
        defense["shield"], defense["shield_max"] = 0.0, smax
        defense["hp"], defense["hp_max"] = float(hmax), hmax
        defense["last_defense_at"] = now
        save_state(state)
        return _build_view(state, now, signals or {}), 200


def set_tags(tags, signals=None):
    """POST /api/game/quiz/tags {tags:[...]} — set the quiz tag selection. Untrusted:
    a bad/oversized list is a 400 with no change. Returns {ok, tags, changed}; the
    route regenerates only when `changed` (gen.tags_key tracks the selection, so an
    unchanged Save never spends quota)."""
    cleaned, ok = _clean_tags(tags)
    if not ok:
        return {"error": "invalid tags"}, 400
    now = _now()
    with _lock:
        state = load_state()
        quiz_st = _quiz(state)
        quiz_st["tags"] = cleaned
        gen = quiz_st.setdefault("gen", {})
        new_key = ",".join(sorted(cleaned))
        changed = (new_key != gen.get("tags_key"))
        gen["tags_key"] = new_key
        save_state(state)
        return {"ok": True, "tags": cleaned, "changed": changed}, 200


def claim_generation(tags=None, tier=None):
    """Atomically CLAIM the generation slot -> (should_run, eff_tags, eff_tier).
    Returns (False, None, None) when a generation is already in_flight (no pile-ups).
    Flips in_flight True under THIS lock and releases; the caller runs Gemini with no
    lock held, then calls finish_generation(). eff_tags/eff_tier default to the active
    selection when not explicitly (and validly) passed."""
    now = _now()
    with _lock:
        state = load_state()
        quiz_st = _quiz(state)
        gen = quiz_st.setdefault("gen", {})
        if gen.get("in_flight"):
            return False, None, None
        gen["in_flight"] = True
        gen["last_gen_at"] = now
        cleaned, ok = _clean_tags(tags) if tags is not None else ([], False)
        eff_tags = cleaned if (ok and cleaned) else list(quiz_st.get("tags", []) or [])
        eff_tier = _int(quiz_st.get("tier_active"), 1)
        if isinstance(tier, (int, float)) and not isinstance(tier, bool) and int(tier) >= 1:
            eff_tier = int(tier)
        save_state(state)
        return True, eff_tags, eff_tier


def finish_generation():
    """Clear the in_flight guard after a generation attempt (success OR failure).
    Re-acquires this lock only after Gemini + the bank write are done (quizbank holds
    its OWN lock for the write), so the two locks are never held together."""
    now = _now()
    with _lock:
        state = load_state()
        gen = _quiz(state).setdefault("gen", {})
        gen["in_flight"] = False
        gen["last_gen_at"] = now
        save_state(state)


def quiz_cursor() -> dict:
    """A read-only snapshot the server uses to decide a background top-up: the active
    tags/tier, whether a generation is in flight, the enabled flag, and a COPY of the
    progress map (so the server can count unseen via quizbank without holding a lock)."""
    with _lock:
        state = load_state()
        quiz_st = _quiz(state)
        return {"tags": list(quiz_st.get("tags", []) or []),
                "tier_active": _int(quiz_st.get("tier_active"), 1),
                "in_flight": bool((quiz_st.get("gen", {}) or {}).get("in_flight")),
                "enabled": _enabled(state),
                "progress": copy.deepcopy(quiz_st.get("progress", {}) or {})}
