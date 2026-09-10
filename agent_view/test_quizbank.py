#!/usr/bin/env python3
"""Offline tests for quizbank.py — the bank store + Gemini generation. The model is
NEVER called for real: gemini_client.call is monkeypatched to a fake that returns a
canned (possibly malformed) batch, and asserts that NO lock is held while it runs (the
deadlock rule: never hold either lock across a Gemini call). No network, no key.

Output is ASCII (Windows cp1252 console). Run:  python test_quizbank.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts" / "tickets"))   # gemini_client lives here

import quiz       # noqa: E402
import quizbank   # noqa: E402
import idlegame   # noqa: E402
import gemini_client  # noqa: E402

_results = []
_ORIG_CALL = gemini_client.call
_ORIG_BANK = quizbank.BANK_PATH
_ORIG_STATE = idlegame.STATE_PATH


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


def _good_item(n):
    return {"stem": f"Question number {n}?",
            "options": [
                {"key": "A", "text": f"correct-{n}", "correct": True},
                {"key": "B", "text": f"b-{n}", "misconception": "m-b", "why_wrong": "because b"},
                {"key": "C", "text": f"c-{n}", "misconception": "m-c", "why_wrong": "because c"},
                {"key": "D", "text": f"d-{n}", "misconception": "m-d", "why_wrong": "because d"}],
            "explanation": f"explanation {n}", "learn_more": None}


def _fake_returning(batch, assert_locks_free=True):
    """A fake gemini_client.call that returns `batch` and (optionally) asserts that
    neither the bank lock nor the idlegame lock is held while it runs."""
    def fake(prompt, **kwargs):
        if assert_locks_free:
            got_bank = quizbank._bank_lock.acquire(blocking=False)
            if got_bank:
                quizbank._bank_lock.release()
            got_ig = idlegame._lock.acquire(blocking=False)
            if got_ig:
                idlegame._lock.release()
            fake.locks_free = bool(got_bank and got_ig)
        return batch
    fake.locks_free = None
    return fake


def _env():
    d = tempfile.mkdtemp(prefix="quizbank_test_")
    quizbank.BANK_PATH = Path(d) / "quiz_bank" / "bank.json"
    idlegame.STATE_PATH = Path(d) / "game_state.json"
    return d


def _cleanup(d):
    gemini_client.call = _ORIG_CALL
    quizbank.BANK_PATH = _ORIG_BANK
    idlegame.STATE_PATH = _ORIG_STATE
    shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Persistence
# --------------------------------------------------------------------------- #
def test_load_missing_and_corrupt_degrade_to_empty():
    d = _env()
    try:
        check("bank: a missing file loads as an empty, well-shaped bank",
              quizbank.load_bank() == {"version": quizbank.BANK_VERSION, "questions": []})
        quizbank.BANK_PATH.parent.mkdir(parents=True, exist_ok=True)
        quizbank.BANK_PATH.write_text("{ not valid json", encoding="utf-8")
        check("bank: a corrupt file degrades to empty (never raises)",
              quizbank.load_bank()["questions"] == [], str(quizbank.load_bank()))
    finally:
        _cleanup(d)


def test_save_load_roundtrip_atomic():
    d = _env()
    try:
        bank = {"version": 1, "questions": [_good_item(1)]}
        bank["questions"][0]["id"] = "q_x"
        check("bank: save -> 200-ish (True)", quizbank.save_bank(bank) is True)
        check("bank: reload round-trips the question", quizbank.load_bank()["questions"][0]["id"] == "q_x")
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Generation — untrusted output, drop-malformed, dedup-on-persist, locks free
# --------------------------------------------------------------------------- #
def test_generate_parses_batch_and_assigns_ids():
    d = _env()
    try:
        fake = _fake_returning([_good_item(i) for i in range(3)])
        gemini_client.call = fake
        res = quizbank.generate(["networking"], 2, key="fake")
        qs = quizbank.questions()
        check("gen: all 3 valid items added", res["added"] == 3 and len(qs) == 3, str(res))
        check("gen: locks were FREE during the Gemini call (deadlock rule)", fake.locks_free is True)
        check("gen: each stored question got an id + stem_norm + gemini provenance",
              all(q["id"] and q["stem_norm"] and q["by"] == "gemini" for q in qs), str(qs[:1]))
        check("gen: keys are re-stamped A-D and exactly one is correct",
              all([o["key"] for o in q["options"]] == list(quiz._OPT_KEYS)
                  and sum(1 for o in q["options"] if o.get("correct")) == 1 for q in qs), str(qs[:1]))
        check("gen: tags/tier come from the request", qs[0]["tags"] == ["networking"] and qs[0]["tier"] == 2,
              str((qs[0]["tags"], qs[0]["tier"])))
    finally:
        _cleanup(d)


def test_generate_drops_malformed_items_without_aborting():
    d = _env()
    try:
        batch = [
            _good_item(1),
            {"stem": "no options"},                                   # missing options
            {"stem": "three options", "options": _good_item(2)["options"][:3],
             "explanation": "x"},                                     # != 4 options
            _bad_two_correct(),                                       # two correct
            _bad_distractor_missing_why(),                           # distractor lacks why_wrong
            {"options": _good_item(3)["options"], "explanation": "no stem"},  # missing stem
            _good_item(9),
        ]
        gemini_client.call = _fake_returning(batch)
        res = quizbank.generate(["db"], 1, key="fake")
        check("gen: only the 2 valid items survive", res["added"] == 2, str(res))
        check("gen: the 5 malformed items are dropped (batch never aborts)", res["dropped"] == 5, str(res))
        check("gen: the bank holds exactly the 2 valid items", len(quizbank.questions()) == 2)
    finally:
        _cleanup(d)


def test_generate_dedups_on_persist():
    d = _env()
    try:
        gemini_client.call = _fake_returning([_good_item(1), _good_item(2)])
        quizbank.generate(["x"], 1, key="fake")
        # a second batch repeats item 1 (same stem) + adds item 3
        gemini_client.call = _fake_returning([_good_item(1), _good_item(3)])
        res = quizbank.generate(["x"], 1, key="fake")
        check("gen: a repeated stem is deduped on persist", res["added"] == 1 and res["duplicates"] == 1,
              str(res))
        stems = {q["stem_norm"] for q in quizbank.questions()}
        check("gen: three distinct stems total", len(quizbank.questions()) == 3 and len(stems) == 3,
              str(stems))
    finally:
        _cleanup(d)


def test_generate_handles_object_wrapper_and_junk():
    d = _env()
    try:
        gemini_client.call = _fake_returning({"questions": [_good_item(1)]})
        check("gen: a {questions:[...]} wrapper is accepted", quizbank.generate(["x"], 1, key="f")["added"] == 1)
        gemini_client.call = _fake_returning("not a list or object")
        res = quizbank.generate(["x"], 1, key="f")
        check("gen: junk (non-list/non-object) yields nothing, never raises", res["added"] == 0, str(res))
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  Retire (§4.7) + serving read helpers
# --------------------------------------------------------------------------- #
def test_retire_excludes_from_live_and_dedup():
    d = _env()
    try:
        gemini_client.call = _fake_returning([_good_item(1), _good_item(2)])
        quizbank.generate(["x"], 1, key="f")
        qid = quizbank.questions()[0]["id"]
        check("retire: an unknown id -> False", quizbank.retire("q_nope") is False)
        check("retire: a real id -> True", quizbank.retire(qid) is True)
        check("retire: the question is kept for audit but flagged",
              quizbank.get_question(qid)["retired"] is True)
        check("retire: excluded from the live (servable) set",
              qid not in {q["id"] for q in quizbank.live_questions()})
        # a retired stem no longer blocks re-generation of the same question
        gemini_client.call = _fake_returning([_good_item(1)])
        res = quizbank.generate(["x"], 1, key="f")
        check("retire: a retired stem is not treated as a live duplicate", res["added"] == 1, str(res))
    finally:
        _cleanup(d)


def test_unseen_count_respects_tags_tier_and_progress():
    d = _env()
    try:
        gemini_client.call = _fake_returning([_good_item(i) for i in range(4)])
        quizbank.generate(["net"], 1, key="f")
        qs = quizbank.questions()
        check("unseen: all four count as unseen at tier 1 / matching tag",
              quizbank.unseen_count({}, ["net"], 1) == 4, str(quizbank.unseen_count({}, ["net"], 1)))
        seen_one = {qs[0]["id"]: {"box": 2, "seen": 3}}
        check("unseen: a seen question drops out of the count",
              quizbank.unseen_count(seen_one, ["net"], 1) == 3)
        check("unseen: a non-matching tag -> zero", quizbank.unseen_count({}, ["other"], 1) == 0)
        check("unseen: a different tier -> zero", quizbank.unseen_count({}, ["net"], 2) == 0)
    finally:
        _cleanup(d)


# --------------------------------------------------------------------------- #
#  in_flight guard (idlegame owns it; server flips it around generate)
# --------------------------------------------------------------------------- #
def test_in_flight_guard_prevents_pileups():
    d = _env()
    try:
        first = idlegame.claim_generation(["x"], 1)
        check("in_flight: the first claim runs", first[0] is True, str(first))
        second = idlegame.claim_generation(["x"], 1)
        check("in_flight: a second claim is refused while one is in flight",
              second == (False, None, None), str(second))
        idlegame.finish_generation()
        third = idlegame.claim_generation(["x"], 1)
        check("in_flight: after finish, a new claim runs again", third[0] is True, str(third))
        idlegame.finish_generation()
    finally:
        _cleanup(d)


def _bad_two_correct():
    it = _good_item(50)
    it["options"][1]["correct"] = True         # now A and B both correct
    return it


def _bad_distractor_missing_why():
    it = _good_item(51)
    it["options"][1].pop("why_wrong")          # a distractor with no why-wrong
    return it


def main():
    fns = [test_load_missing_and_corrupt_degrade_to_empty, test_save_load_roundtrip_atomic,
           test_generate_parses_batch_and_assigns_ids,
           test_generate_drops_malformed_items_without_aborting,
           test_generate_dedups_on_persist, test_generate_handles_object_wrapper_and_junk,
           test_retire_excludes_from_live_and_dedup,
           test_unseen_count_respects_tags_tier_and_progress,
           test_in_flight_guard_prevents_pileups]
    for fn in fns:
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
