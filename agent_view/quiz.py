#!/usr/bin/env python3
"""The quiz SCHEDULER — pure functions, NO I/O and NO Gemini (F2).

This is the retrieval-practice core the whole F2 layer is built to justify (see
PLAN_IDLE_GAME.md §0/§4): serving is deterministic Python — Leitner due-selection,
tier routing, boss assembly, grading and serve-time option shuffling — with no
model at play time (cheap, offline, instant). Content GENERATION (the one strong-
Gemini prompt) lives in quizbank.py; state (progress, knowledge, prestige) lives
in idlegame.py. This module holds neither: every function is pure over the dicts
it is handed, so it is trivially testable with a frozen clock and no files.

Two dict shapes flow through here (both owned elsewhere):
  * a BANK QUESTION (quizbank.py owns bank.json): id, tags, tier, stem, options
    [{key, text, correct?, misconception, why_wrong}], explanation, learn_more,
    stem_norm, retired, ...  Options are stored UN-shuffled with the correct one
    flagged; the flag NEVER leaves the server (see shuffle_options / grade).
  * a PROGRESS record (idlegame.py owns it inside game_state.json), per qid:
    {box, due, seen, correct, wrong, last_result, matured}.  `matured` is the
    once-ever box-5 knowledge gate — apply_result reports the transition, idlegame
    flips the flag, so the knowledge award is unfarmable.

HONESTY BOUNDARY: a served question carries ONLY {key, text} per option — no
`correct`, no `why_wrong`, no `misconception`. Options are re-labelled A/B/C/D by
SHUFFLED position; the mapping back to the stored keys stays server-side, so the
answer is never in the payload and grading happens here against the bank.
"""
from __future__ import annotations

import hashlib
import random
import re

# --------------------------------------------------------------------------- #
#  Tunable constants — the quiz-mechanics half of the "one tunable block" idea
#  (the economy half lives in idlegame). F2 seeds plausible values; F3 tunes.
# --------------------------------------------------------------------------- #
LEITNER = (1, 3, 7, 16, 35)     # box 1..5 review interval in DAYS (§4.1)
DAY_SECS = 86400.0
WRONG_BOX = 1                   # a miss drops to box 1 (re-surfaces tomorrow)
MAX_BOX = 5

RATCHET_WINDOW = 20            # rolling accuracy over the last ~20 at the active tier
RATCHET_MIN = 5               # need this many answers before the ratchet acts (no early swing)
RATCHET_RAISE = 0.82         # >= -> raise the ceiling + signal "gen next tier" (§4.2)
RATCHET_FREEZE = 0.60        # <  -> freeze injection. Separate up/down = hysteresis; band ~80%.

BOSS_SIZE = 20               # a boss run is 20 questions (§4.5)
BOSS_DUE = 10                # ~10 due / recently-missed
BOSS_UNSEEN = 6             # ~6 current-tier unseen
BOSS_MATURE = 4            # ~4 mature (box 5). Backfilled across buckets if short.
BOSS_PASS_FRACTION = 0.70   # pass = > 70% correct (idlegame turns this into rewards)

_OPT_KEYS = ("A", "B", "C", "D")


# --------------------------------------------------------------------------- #
#  Small tolerant readers (a hand-edited / model-authored dict must never crash
#  a pure function). Local copies on purpose: importing idlegame's would couple
#  this pure module to the state layer it is meant to sit beneath.
# --------------------------------------------------------------------------- #
def _as_int(v, default):
    if isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v, default):
    if isinstance(v, bool):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
#  Normalization + dedup — the ONE normalization, shared by generation (quizbank)
#  and retire, so "same question" means the same thing on both paths.
# --------------------------------------------------------------------------- #
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_stem(s) -> str:
    """A stem's canonical form for dedup: lowercase, non-alphanumerics collapsed to
    single spaces, stripped. 'What does TCP provide that UDP does not?' ->
    'what does tcp provide that udp does not'."""
    return _NON_ALNUM.sub(" ", str(s or "").lower()).strip()


def dedup(new_items, bank) -> list:
    """Return the subset of `new_items` whose normalized stem is not already in
    `bank` (pass the LIVE, non-retired questions) AND not repeated earlier in the
    same batch. Order-preserving; the first occurrence of a stem wins."""
    existing = set()
    for q in bank or []:
        sn = (q.get("stem_norm") if isinstance(q, dict) else "") or normalize_stem(
            (q or {}).get("stem", "") if isinstance(q, dict) else "")
        if sn:
            existing.add(sn)
    out = []
    for it in new_items or []:
        if not isinstance(it, dict):
            continue
        sn = it.get("stem_norm") or normalize_stem(it.get("stem", ""))
        if not sn or sn in existing:
            continue
        existing.add(sn)
        out.append(it)
    return out


# --------------------------------------------------------------------------- #
#  Progress accessors (a missing/partial record reads as a fresh, unseen box-1).
# --------------------------------------------------------------------------- #
def _prog(progress, qid) -> dict:
    p = (progress or {}).get(qid)
    return p if isinstance(p, dict) else {}


def box_of(progress, qid) -> int:
    return _as_int(_prog(progress, qid).get("box"), 1)


def due_of(progress, qid, now) -> float:
    """A question's due time: its scheduled `due`, or `now` for an unseen one (a
    never-answered question is available immediately, not in the future)."""
    p = _prog(progress, qid)
    return _as_float(p.get("due"), now) if p.get("due") is not None else now


def is_unseen(progress, qid) -> bool:
    return _as_int(_prog(progress, qid).get("seen"), 0) <= 0


def is_mature(progress, qid) -> bool:
    p = _prog(progress, qid)
    return _as_int(p.get("box"), 1) >= MAX_BOX or bool(p.get("matured"))


# --------------------------------------------------------------------------- #
#  Leitner scheduling — the review engine (§4.1).
# --------------------------------------------------------------------------- #
def interval_secs(box: int) -> float:
    """The review interval for `box` (1..5), in seconds."""
    b = min(max(1, _as_int(box, 1)), MAX_BOX)
    return LEITNER[b - 1] * DAY_SECS


def apply_result(prog, correct: bool, now: float):
    """Advance a progress record for one answer -> (new_prog, matured_now).

    correct -> box = min(box+1, 5), due = now + interval[new box].
    wrong   -> box = 1,            due = now + 1 day (auto re-surfaces the miss).
    Always bumps seen and correct|wrong and records last_result.

    `matured_now` is the box-4 -> box-5 transition — the knowledge trigger. It is
    reported on EVERY such transition (a question can fall to box 1 and climb
    again); the once-ever gate lives in idlegame via the `matured` flag, which
    this function carries through UNCHANGED so the pure scheduler never decides
    the economy."""
    p = dict(prog) if isinstance(prog, dict) else {}
    old_box = _as_int(p.get("box"), 1)
    if correct:
        new_box = min(old_box + 1, MAX_BOX)
        due = now + interval_secs(new_box)
    else:
        new_box = WRONG_BOX
        due = now + interval_secs(WRONG_BOX)
    matured_now = bool(correct and old_box == MAX_BOX - 1 and new_box == MAX_BOX)
    p["box"] = new_box
    p["due"] = due
    p["seen"] = _as_int(p.get("seen"), 0) + 1
    if correct:
        p["correct"] = _as_int(p.get("correct"), 0) + 1
    else:
        p["wrong"] = _as_int(p.get("wrong"), 0) + 1
    p["last_result"] = "correct" if correct else "wrong"
    p.setdefault("matured", bool(prog.get("matured")) if isinstance(prog, dict) else False)
    return p, matured_now


# --------------------------------------------------------------------------- #
#  Serving — tag filter + due-selection (§4.1). Deterministic and never empty
#  while the tag-matched pool is non-empty.
# --------------------------------------------------------------------------- #
def _tags_match(question, tags) -> bool:
    """A question matches when the user has selected no tags (match everything) or
    when it carries at least one selected tag."""
    if not tags:
        return True
    qtags = question.get("tags") or []
    sel = set(tags)
    return any(t in sel for t in qtags)


def eligible(bank, tags) -> list:
    """The servable pool: non-retired and tag-matched, order preserved."""
    return [q for q in (bank or [])
            if isinstance(q, dict) and q.get("id") and not q.get("retired")
            and _tags_match(q, tags)]


def select_next(bank, progress, tags, tier_active, now):
    """The next question to serve, or None when the tag-matched pool is empty.

    Among the eligible (non-retired, tag-matched) pool: every question with
    due <= now, OLDEST-due first; if none are due, the single soonest-due one — so
    a non-empty pool ALWAYS yields a question (never show nothing, §4.1). `tier_active`
    is accepted for signature stability with the ratchet/boss selectors; due-order
    is the play-time driver and does not gate on tier (a due review outranks tier)."""
    pool = eligible(bank, tags)
    if not pool:
        return None
    due_ready = [q for q in pool if due_of(progress, q["id"], now) <= now]
    if due_ready:
        due_ready.sort(key=lambda q: (due_of(progress, q["id"], now), q["id"]))
        return due_ready[0]
    pool.sort(key=lambda q: (due_of(progress, q["id"], now), q["id"]))
    return pool[0]


# --------------------------------------------------------------------------- #
#  Difficulty ratchet (§4.2) — a flow controller, not a score. Hysteresis via
#  separate raise/freeze thresholds; boss answers are NOT fed in (idlegame keeps
#  them out of `recent`).
# --------------------------------------------------------------------------- #
def ratchet(recent, tier_active) -> dict:
    """Rolling accuracy over the last ~RATCHET_WINDOW answers AT `tier_active`.
    Returns {accuracy, n, raise_tier, freeze}:
      * accuracy = correct fraction (None until any at-tier answer exists),
      * raise_tier when accuracy >= RATCHET_RAISE (unlock + gen the next tier),
      * freeze     when accuracy <  RATCHET_FREEZE (stop injecting harder items).
    Below RATCHET_MIN at-tier answers neither fires — too little signal to move."""
    at_tier = [r for r in (recent or [])
               if isinstance(r, dict) and _as_int(r.get("tier"), 0) == _as_int(tier_active, 0)]
    window = at_tier[-RATCHET_WINDOW:]
    n = len(window)
    if n == 0:
        return {"accuracy": None, "n": 0, "raise_tier": False, "freeze": False}
    acc = sum(1 for r in window if r.get("ok")) / n
    enough = n >= RATCHET_MIN
    return {"accuracy": acc, "n": n,
            "raise_tier": bool(enough and acc >= RATCHET_RAISE),
            "freeze": bool(enough and acc < RATCHET_FREEZE)}


# --------------------------------------------------------------------------- #
#  Boss assembly (§4.5) — 20 = ~10 due/missed + ~6 current-tier unseen + ~4 mature,
#  backfilled across buckets, scaled down when fewer than 20 are eligible.
# --------------------------------------------------------------------------- #
def assemble_boss(bank, progress, tier_active, now) -> list:
    """A boss set of up to BOSS_SIZE distinct questions from the non-retired pool,
    drawn from three buckets with per-bucket caps then backfilled to size. Returns
    fewer than BOSS_SIZE only when the pool itself is smaller (never duplicates)."""
    pool = [q for q in (bank or [])
            if isinstance(q, dict) and q.get("id") and not q.get("retired")]
    tier = _as_int(tier_active, 1)

    due_missed = sorted(
        [q for q in pool
         if due_of(progress, q["id"], now) <= now
         or _prog(progress, q["id"]).get("last_result") == "wrong"],
        key=lambda q: (due_of(progress, q["id"], now), q["id"]))
    unseen = sorted(
        [q for q in pool if _as_int(q.get("tier"), 1) == tier and is_unseen(progress, q["id"])],
        key=lambda q: q["id"])
    mature = sorted(
        [q for q in pool if is_mature(progress, q["id"])],
        key=lambda q: q["id"])

    picked, seen_ids = [], set()

    def take(cands, cap):
        added = 0
        for q in cands:
            if len(picked) >= BOSS_SIZE or added >= cap:
                break
            if q["id"] in seen_ids:
                continue
            picked.append(q)
            seen_ids.add(q["id"])
            added += 1

    take(due_missed, BOSS_DUE)
    take(unseen, BOSS_UNSEEN)
    take(mature, BOSS_MATURE)
    take(pool, BOSS_SIZE)          # backfill from anything left to reach BOSS_SIZE
    return picked


# --------------------------------------------------------------------------- #
#  Serve-time option shuffle + grading — the honesty boundary. The correct key
#  is NEVER put in a payload; options are re-labelled A/B/C/D by shuffled position
#  and the mapping stays server-side.
# --------------------------------------------------------------------------- #
def option_seed(qid, seen) -> int:
    """A process-STABLE shuffle seed for (qid, seen). Stable so the same serving and
    its later grading produce the identical A/B/C/D layout WITHOUT any stored serve
    state; folding in `seen` reshuffles across attempts. Python's salted hash() is
    per-process, so a real digest is used instead (single machine, determinism)."""
    h = hashlib.sha1(f"{qid}:{_as_int(seen, 0)}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def shuffle_options(question, seed):
    """(served_options, key_map) for a question at a given seed.

      * served_options = [{"key": "A", "text": ...}, ...] — the options in a
        seed-determined order, RE-LABELLED A/B/C/D by position. No `correct`, no
        `why_wrong`, no `misconception`: this is the exact payload the client sees.
      * key_map = {served_label -> stored option key}, kept server-side so grading
        can map a returned label back to the stored (flagged) option.

    Deterministic in `seed`, so serve and grade agree with nothing persisted."""
    opts = [o for o in (question.get("options") or []) if isinstance(o, dict)]
    order = list(range(len(opts)))
    random.Random(seed).shuffle(order)
    served, key_map = [], {}
    for label, idx in zip(_OPT_KEYS, order):
        o = opts[idx]
        served.append({"key": label, "text": o.get("text", "")})
        key_map[label] = o.get("key")
    return served, key_map


def grade(question, choice):
    """Grade a STORED-key `choice` against the bank question. Returns
    (correct, correct_key, chosen_why_wrong, explanation, rejections) where keys are
    STORED keys; idlegame translates them to served labels for the response.
    `rejections` is one entry per distractor: {key, misconception, why_wrong}."""
    opts = [o for o in (question.get("options") or []) if isinstance(o, dict)]
    correct_key = next((o.get("key") for o in opts if o.get("correct")), None)
    correct = choice is not None and choice == correct_key
    chosen_why_wrong = ""
    for o in opts:
        if o.get("key") == choice and not o.get("correct"):
            chosen_why_wrong = o.get("why_wrong", "") or ""
            break
    rejections = [{"key": o.get("key"),
                   "misconception": o.get("misconception", "") or "",
                   "why_wrong": o.get("why_wrong", "") or ""}
                  for o in opts if not o.get("correct")]
    return correct, correct_key, chosen_why_wrong, (question.get("explanation", "") or ""), rejections
