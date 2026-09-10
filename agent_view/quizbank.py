#!/usr/bin/env python3
"""The quiz CONTENT BANK + generation (F2). Serving is pure code (quiz.py); this
module owns the persisted question pool and the ONE strong-Gemini prompt that
authors it — mirroring the batched-and-persisted pattern of ticket_profiles /
mail_profiles (mail_ai.load_profiles/save_profiles): whole-file read, atomic
tmp + os.replace, its OWN threading.Lock.

TWO invariants make this safe to run behind a game:

  * MODEL OUTPUT IS UNTRUSTED. Gemini can hallucinate a malformed item or a wrong
    answer. Every item is validated (a stem, exactly 4 options, exactly one
    `correct`, every distractor a named misconception + a one-line why-wrong) and
    a bad item is DROPPED — one bad item never aborts the batch. New stems are
    deduped against the live bank before they are assigned ids and persisted.
    Human "obriši/prijavi" (retire) is the last-line hallucination guard (§4.7).

  * NEVER HOLD A LOCK ACROSS A GEMINI CALL. generate() calls the model with NO
    lock held, then takes the bank lock ONLY for the load/append/save. The
    in_flight guard that prevents pile-ups lives in idlegame's game_state (a
    different lock); server.py flips it under idlegame's lock, releases, calls
    generate() here, then clears it — so the two locks are never held together
    and neither is ever held across the network call (deadlock-free).

Serving never calls a model: at the daily cap, play is unaffected — only new
question generation warns and pauses (gemini_client is warn-not-block).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# gemini_client lives in scripts/tickets (the one Gemini door). Put it on the path
# the same way server.py does, so importing quizbank alone (e.g. in a test that
# monkeypatches gemini_client.call) resolves it without importing the whole server.
sys.path.insert(0, str(HERE.parent / "scripts" / "tickets"))

import quiz  # noqa: E402  (pure scheduler: normalize_stem / dedup)

#: The bank file, in a gitignored dir beside ticket_profiles/. A module global so a
#: test can point it at a temp path (mirrors idlegame.STATE_PATH / focus.STATE_PATH).
BANK_PATH = HERE / "quiz_bank" / "bank.json"
BANK_VERSION = 1

QUIZ_BATCH_N = 16               # one strong-Gemini call authors ~this many MCQs (§4.6)
GEN_TEMPERATURE = 0.4           # a little spread for variety, low enough to stay on-spec
_MAX_OPTIONS = 4

# One writer per process (generation is serialised by idlegame's in_flight guard),
# so a single plain lock around the whole-file read-modify-write — the focus/idlegame
# discipline, NOT the tickets store's cross-process lock.
_bank_lock = threading.Lock()


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
#  Persistence — whole-file, atomic, tolerant. Never raises on a read.
# --------------------------------------------------------------------------- #
def _empty_bank() -> dict:
    return {"version": BANK_VERSION, "questions": []}


def load_bank() -> dict:
    """The whole bank, or a fresh empty skeleton. Always well-shaped (a `questions`
    list), so callers never guard for missing keys; a corrupt file degrades to
    empty rather than crashing a serve/generate."""
    d = _empty_bank()
    try:
        loaded = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return d
    except Exception:
        return d
    if isinstance(loaded, dict) and isinstance(loaded.get("questions"), list):
        d["questions"] = [q for q in loaded["questions"] if isinstance(q, dict)]
        d["version"] = loaded.get("version", BANK_VERSION)
    return d


def save_bank(bank: dict) -> bool:
    """Atomic whole-file write (temp + os.replace). Creates the gitignored dir on
    first save. Returns False on a disk hiccup instead of raising — an optional
    overlay must never crash the server."""
    try:
        BANK_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = BANK_PATH.with_name(BANK_PATH.name + ".tmp")
        tmp.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, BANK_PATH)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Read helpers used by serving (server threads these into idlegame like it
#  threads the Hub/focus signals — idlegame never reads the bank itself).
# --------------------------------------------------------------------------- #
def questions() -> list:
    """The full stored question list (including retired ones — quiz.py filters
    retired at serve/dedup time; keeping them here preserves the audit trail)."""
    return load_bank().get("questions", [])


def live_questions() -> list:
    return [q for q in questions() if not q.get("retired")]


def get_question(qid):
    """The bank question with this id, or None. Used to grade an answer server-side
    (the served payload never carried the answer)."""
    if not isinstance(qid, str) or not qid:
        return None
    for q in questions():
        if q.get("id") == qid:
            return q
    return None


def unseen_count(progress, tags, tier) -> int:
    """How many eligible (non-retired, tag-matched) questions at `tier` have never
    been answered — the low-water signal that decides a background top-up (§4.6).
    `progress` is idlegame's per-qid record map, threaded in by the caller."""
    n = 0
    for q in live_questions():
        if quiz._as_int(q.get("tier"), 1) != quiz._as_int(tier, 1):
            continue
        if not quiz._tags_match(q, tags):
            continue
        if quiz.is_unseen(progress, q.get("id")):
            n += 1
    return n


# --------------------------------------------------------------------------- #
#  Retire (§4.7) — the human hallucination guard. Idempotent; unknown id -> False.
# --------------------------------------------------------------------------- #
def retire(qid) -> bool:
    """Mark a question retired:true (kept for audit, excluded from serve + dedup).
    Returns True when a matching id was found and (re)tired, else False so the
    route can 400 an unknown/typo id."""
    if not isinstance(qid, str) or not qid:
        return False
    with _bank_lock:
        bank = load_bank()
        found = False
        for q in bank["questions"]:
            if q.get("id") == qid:
                q["retired"] = True
                found = True
        if found:
            save_bank(bank)
        return found


# --------------------------------------------------------------------------- #
#  Generation prompt (§4.4) — one call authors a whole batch. Encodes the named-
#  misconception distractors, the per-distractor why-wrong, a pre-authored
#  explanation, and the anti-tell checklist.
# --------------------------------------------------------------------------- #
def _build_prompt(tags, tier, n) -> str:
    topic = ", ".join(t for t in (tags or []) if t) or "general IT and programming"
    return (
        "You are an expert IT/programming instructor writing multiple-choice "
        "questions for a spaced-repetition trainer used by a professional developer. "
        f"Write EXACTLY {n} questions on: {topic}.\n"
        f"Target difficulty tier: {tier} (1 = fundamentals, higher = deeper/edge-case).\n\n"
        "Hard requirements for EVERY question:\n"
        "- A clear, self-contained `stem` (the question). No 'which of the following' "
        "filler; ask something a working developer should know.\n"
        "- EXACTLY 4 options with keys \"A\",\"B\",\"C\",\"D\".\n"
        "- EXACTLY ONE option is correct: it carries \"correct\": true.\n"
        "- Each of the other THREE options is a plausible DISTRACTOR that encodes a "
        "NAMED misconception: give it \"misconception\" (a short kebab-case tag, e.g. "
        "\"confuses-tcp-udp\") and \"why_wrong\" (one sentence explaining the error).\n"
        "- A pre-authored \"explanation\" (2-3 sentences) of why the correct answer is "
        "right — this is shown after answering, so make it teach.\n"
        "- Set \"learn_more\": null.\n\n"
        "Anti-tell checklist (an LLM leaks the key — avoid ALL of these):\n"
        "- Do NOT make the correct option noticeably longer or more qualified.\n"
        "- Do NOT use 'all of the above' / 'none of the above'.\n"
        "- Avoid absolutes ('always', 'never') that flag a distractor.\n"
        "- Keep all four options parallel in length and grammar; put the correct "
        "answer in a random position, not always A.\n\n"
        "Return ONLY a JSON array (no prose, no markdown fences). Each element:\n"
        '{"stem": "...", "options": ['
        '{"key":"A","text":"...","correct":true},'
        '{"key":"B","text":"...","misconception":"...","why_wrong":"..."},'
        '{"key":"C","text":"...","misconception":"...","why_wrong":"..."},'
        '{"key":"D","text":"...","misconception":"...","why_wrong":"..."}],'
        '"explanation":"...","learn_more":null}\n'
    )


# --------------------------------------------------------------------------- #
#  Untrusted-output validation — one bad item is dropped, never fatal.
# --------------------------------------------------------------------------- #
def _clean_text(v):
    return v.strip() if isinstance(v, str) else None


def _validate_item(raw, tags, tier):
    """Coerce ONE untrusted model item into a clean bank question, or return None to
    DROP it. Enforces: a stem, an explanation, exactly 4 options each with text,
    exactly one `correct`, and every distractor carrying misconception + why_wrong.
    Keys are RE-STAMPED A/B/C/D by position so a model that mislabels can't corrupt
    grading; the stored options remain un-shuffled (quiz.shuffle_options serves)."""
    if not isinstance(raw, dict):
        return None
    stem = _clean_text(raw.get("stem"))
    explanation = _clean_text(raw.get("explanation"))
    if not stem or not explanation:
        return None
    opts_in = raw.get("options")
    if not isinstance(opts_in, list) or len(opts_in) != _MAX_OPTIONS:
        return None
    options, n_correct = [], 0
    for pos, o in enumerate(opts_in):
        if not isinstance(o, dict):
            return None
        text = _clean_text(o.get("text"))
        if not text:
            return None
        key = quiz._OPT_KEYS[pos]
        if o.get("correct") is True:
            n_correct += 1
            options.append({"key": key, "text": text, "correct": True})
        else:
            misc = _clean_text(o.get("misconception"))
            why = _clean_text(o.get("why_wrong"))
            if not misc or not why:        # a distractor MUST teach why it is wrong
                return None
            options.append({"key": key, "text": text,
                            "misconception": misc, "why_wrong": why})
    if n_correct != 1:                      # exactly one correct, no more no less
        return None
    return {
        "id": None,                          # assigned at persist, after dedup
        "tags": [t for t in (tags or []) if isinstance(t, str) and t],
        "tier": quiz._as_int(tier, 1),
        "stem": stem,
        "options": options,
        "explanation": explanation,
        "learn_more": None,
        "stem_norm": quiz.normalize_stem(stem),
        "retired": False,
        "by": "gemini",
        "created_at": _now(),
    }


def _parse_batch(raw, tags, tier) -> list:
    """Turn an untrusted model reply (a JSON array, or {'questions': [...]}) into a
    list of validated items, dropping every malformed one."""
    items = raw
    if isinstance(raw, dict):
        items = raw.get("questions") or raw.get("items") or []
    if not isinstance(items, list):
        return []
    out = []
    for el in items:
        try:
            v = _validate_item(el, tags, tier)
        except Exception:
            v = None                         # a weird element is dropped, never fatal
        if v is not None:
            out.append(v)
    return out


def _new_id(existing) -> str:
    """A short, collision-checked question id (q_ + 6 hex)."""
    while True:
        qid = "q_" + os.urandom(3).hex()
        if qid not in existing:
            return qid


def _call_gemini(tags, tier, n, key):
    """The ONE model call. Strong workhorse (gemini_client.MODEL_PRO), json_out so
    gemini_client parses + raises GeminiError on non-JSON. NO lock is held here."""
    import gemini_client
    prompt = _build_prompt(tags, tier, n)
    return gemini_client.call(prompt, json_out=True, model=gemini_client.MODEL_PRO,
                              temperature=GEN_TEMPERATURE, api_key=key)


def generate(tags, tier, n=QUIZ_BATCH_N, key="") -> dict:
    """Author one batch and merge it into the bank. Returns
    {added, dropped, duplicates}. The Gemini call happens with NO lock held; the
    bank lock is taken ONLY for the load/dedup/append/save so it is never held
    across the network call (see the module docstring's lock rule).

    Raises GeminiError (from gemini_client) on a model/transport failure — the
    daemon wrapper in server.py swallows it; play is unaffected."""
    raw = _call_gemini(tags, tier, n, key)          # <-- outside every lock
    parsed = _parse_batch(raw, tags, tier)          # untrusted -> validated (pure)
    dropped = max(0, _reply_len(raw) - len(parsed))
    with _bank_lock:
        bank = load_bank()
        live = [q for q in bank["questions"] if not q.get("retired")]
        fresh = quiz.dedup(parsed, live)            # vs live bank + within the batch
        duplicates = len(parsed) - len(fresh)
        existing_ids = {q.get("id") for q in bank["questions"]}
        for it in fresh:
            it["id"] = _new_id(existing_ids)
            existing_ids.add(it["id"])
        bank["questions"].extend(fresh)
        if fresh:
            save_bank(bank)
        return {"added": len(fresh), "dropped": dropped, "duplicates": duplicates}


def _reply_len(raw) -> int:
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        items = raw.get("questions") or raw.get("items") or []
        return len(items) if isinstance(items, list) else 0
    return 0
