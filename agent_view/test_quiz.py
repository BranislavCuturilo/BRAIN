#!/usr/bin/env python3
"""Offline tests for quiz.py — the PURE F2 scheduler (Leitner / ratchet / boss /
grade / shuffle / dedup). No clock monkeypatch is needed: every function takes `now`
as a parameter, so a fixed epoch is enough. No I/O, no Gemini, no network.

Output is ASCII (the Windows cp1252 console cannot encode a non-ASCII char and would
kill the run mid-way). Run:  python test_quiz.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import quiz  # noqa: E402

_results = []
NOW = 1_700_000_000.0
DAY = quiz.DAY_SECS


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


def _approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def _q(qid, tier=1, tags=None, retired=False, correct_key="A"):
    """A minimal bank question with a flagged correct option and three distractors
    that each carry a named misconception + why-wrong (the stored, un-shuffled shape)."""
    opts = []
    for k in quiz._OPT_KEYS:
        if k == correct_key:
            opts.append({"key": k, "text": f"{qid}-{k}-correct", "correct": True})
        else:
            opts.append({"key": k, "text": f"{qid}-{k}", "misconception": f"m-{k}",
                         "why_wrong": f"why-{k}"})
    return {"id": qid, "tags": tags or ["t"], "tier": tier, "stem": f"stem {qid}?",
            "options": opts, "explanation": f"expl {qid}", "learn_more": None,
            "stem_norm": quiz.normalize_stem(f"stem {qid}?"), "retired": retired}


# --------------------------------------------------------------------------- #
#  Leitner transitions + box-5 maturation
# --------------------------------------------------------------------------- #
def test_apply_result_correct_advances_box_and_due():
    prog = {"box": 1, "seen": 0}
    new, matured = quiz.apply_result(prog, True, NOW)
    check("leitner: correct box 1 -> 2", new["box"] == 2, str(new))
    check("leitner: due = now + interval[box2] (3d)", _approx(new["due"], NOW + 3 * DAY), str(new["due"]))
    check("leitner: seen/correct bumped, last_result correct",
          new["seen"] == 1 and new["correct"] == 1 and new["last_result"] == "correct", str(new))
    check("leitner: box<5 is not maturation", matured is False, str(matured))


def test_apply_result_wrong_resets_to_box1_next_day():
    prog = {"box": 4, "seen": 5, "correct": 4}
    new, matured = quiz.apply_result(prog, False, NOW)
    check("leitner: wrong -> box 1", new["box"] == 1, str(new))
    check("leitner: wrong due = now + 1d", _approx(new["due"], NOW + 1 * DAY), str(new["due"]))
    check("leitner: wrong bumps wrong count + last_result", new["wrong"] == 1
          and new["last_result"] == "wrong", str(new))
    check("leitner: a wrong answer never matures", matured is False, str(matured))


def test_box5_maturation_reported_only_on_4_to_5():
    new, matured = quiz.apply_result({"box": 4}, True, NOW)
    check("maturation: box 4 -> 5 reports matured_now", matured is True and new["box"] == 5, str(new))
    # a correct answer already at box 5 stays 5 and is NOT a new maturation
    new2, matured2 = quiz.apply_result({"box": 5}, True, NOW)
    check("maturation: correct at box 5 stays 5, not a new maturation",
          new2["box"] == 5 and matured2 is False, str((new2["box"], matured2)))


def test_apply_result_carries_matured_flag_unchanged():
    # the pure scheduler NEVER decides the economy: it carries `matured` through so
    # idlegame's once-ever gate is the only place it flips.
    new, _m = quiz.apply_result({"box": 5, "matured": True}, False, NOW)
    check("maturation: apply_result preserves the matured flag", new.get("matured") is True, str(new))


# --------------------------------------------------------------------------- #
#  Ratchet — hysteresis, target band, insufficient data
# --------------------------------------------------------------------------- #
def test_ratchet_raises_on_high_accuracy():
    recent = [{"tier": 1, "ok": True} for _ in range(10)]
    r = quiz.ratchet(recent, 1)
    check("ratchet: >=82% raises the tier ceiling", r["raise_tier"] is True and r["freeze"] is False,
          str(r))


def test_ratchet_freezes_on_low_accuracy():
    recent = [{"tier": 1, "ok": i < 3} for i in range(10)]   # 3/10 = 30%
    r = quiz.ratchet(recent, 1)
    check("ratchet: <60% freezes injection", r["freeze"] is True and r["raise_tier"] is False, str(r))


def test_ratchet_target_band_neither():
    recent = [{"tier": 1, "ok": i < 8} for i in range(10)]   # 8/10 = 80% (target band)
    r = quiz.ratchet(recent, 1)
    check("ratchet: ~80% is the target band (neither fires)",
          r["raise_tier"] is False and r["freeze"] is False, str(r))


def test_ratchet_only_counts_active_tier_and_needs_minimum():
    check("ratchet: no data -> accuracy None, neither fires",
          quiz.ratchet([], 1) == {"accuracy": None, "n": 0, "raise_tier": False, "freeze": False})
    # perfect but only 2 answers at the tier -> below RATCHET_MIN, does not raise
    r = quiz.ratchet([{"tier": 1, "ok": True}, {"tier": 1, "ok": True},
                      {"tier": 2, "ok": False}], 1)
    check("ratchet: below the minimum sample never fires", r["raise_tier"] is False and r["n"] == 2, str(r))


# --------------------------------------------------------------------------- #
#  Selection — due oldest-first, never nothing, retired + tag filtering
# --------------------------------------------------------------------------- #
def test_select_next_prefers_oldest_due():
    bank = [_q("q_a"), _q("q_b")]
    progress = {"q_a": {"box": 2, "due": NOW - 100, "seen": 1},
                "q_b": {"box": 2, "due": NOW - 500, "seen": 1}}
    got = quiz.select_next(bank, progress, [], 1, NOW)
    check("select: the oldest-due question wins", got["id"] == "q_b", str(got and got["id"]))


def test_select_next_serves_soonest_when_none_due():
    bank = [_q("q_a"), _q("q_b")]
    progress = {"q_a": {"box": 3, "due": NOW + 5000, "seen": 1},
                "q_b": {"box": 3, "due": NOW + 200, "seen": 1}}
    got = quiz.select_next(bank, progress, [], 1, NOW)
    check("select: none due -> the soonest-due (never nothing)", got["id"] == "q_b", str(got and got["id"]))


def test_select_next_never_nothing_when_bank_nonempty():
    bank = [_q("q_a"), _q("q_b"), _q("q_c")]
    got = quiz.select_next(bank, {}, [], 1, NOW)     # all unseen (due now), empty tags = match all
    check("select: a non-empty bank always yields a question", got is not None, str(got))


def test_select_next_excludes_retired_and_filters_tags():
    bank = [_q("q_a", tags=["net"], retired=True), _q("q_b", tags=["net"])]
    got = quiz.select_next(bank, {}, ["net"], 1, NOW)
    check("select: retired is excluded", got is not None and got["id"] == "q_b", str(got))
    none = quiz.select_next([_q("q_a", tags=["db"])], {}, ["net"], 1, NOW)
    check("select: a tag filter matching nothing -> None", none is None, str(none))
    # empty selection matches everything (so a fresh user with no tags still plays)
    any_q = quiz.select_next([_q("q_a", tags=["db"])], {}, [], 1, NOW)
    check("select: empty tag selection matches all", any_q is not None, str(any_q))


# --------------------------------------------------------------------------- #
#  Boss assembly — buckets, backfill, scale-down, no duplicates
# --------------------------------------------------------------------------- #
def test_assemble_boss_size_and_no_duplicates():
    bank = [_q(f"q_{i:02d}", tier=1) for i in range(30)]
    progress = {}
    # make 12 due/missed, leave the rest unseen at tier 1
    for i in range(12):
        progress[f"q_{i:02d}"] = {"box": 2, "due": NOW - 100, "seen": 1, "last_result": "wrong"}
    boss = quiz.assemble_boss(bank, progress, 1, NOW)
    ids = [q["id"] for q in boss]
    check("boss: exactly BOSS_SIZE when the pool is large", len(boss) == quiz.BOSS_SIZE, str(len(boss)))
    check("boss: no duplicate questions", len(ids) == len(set(ids)), str(ids))


def test_assemble_boss_scales_down_when_pool_small():
    bank = [_q(f"q_{i}", tier=1) for i in range(7)]
    boss = quiz.assemble_boss(bank, {}, 1, NOW)
    ids = [q["id"] for q in boss]
    check("boss: fewer than BOSS_SIZE when the pool is small", len(boss) == 7, str(len(boss)))
    check("boss: still no duplicates when scaled down", len(ids) == len(set(ids)), str(ids))


def test_assemble_boss_draws_from_each_bucket():
    bank = []
    progress = {}
    for i in range(6):                                # due/missed
        qid = f"due_{i}"; bank.append(_q(qid, tier=1))
        progress[qid] = {"box": 2, "due": NOW - 10, "seen": 1, "last_result": "wrong"}
    for i in range(6):                                # current-tier unseen
        bank.append(_q(f"uns_{i}", tier=1))
    for i in range(6):                                # mature (box 5)
        qid = f"mat_{i}"; bank.append(_q(qid, tier=1))
        progress[qid] = {"box": 5, "due": NOW + 9 * DAY, "seen": 6, "matured": True}
    boss = quiz.assemble_boss(bank, progress, 1, NOW)
    ids = {q["id"] for q in boss}
    check("boss: draws due/missed", any(i.startswith("due_") for i in ids), str(ids))
    check("boss: draws current-tier unseen", any(i.startswith("uns_") for i in ids), str(ids))
    check("boss: draws mature", any(i.startswith("mat_") for i in ids), str(ids))


# --------------------------------------------------------------------------- #
#  Grading + the honesty boundary (no answer in the served payload)
# --------------------------------------------------------------------------- #
def test_grade_correct_and_incorrect():
    q = _q("q_a", correct_key="C")
    ok, ck, why, expl, rej = quiz.grade(q, "C")
    check("grade: the correct key is correct", ok is True and ck == "C" and why == "", str((ok, ck)))
    check("grade: explanation is returned", expl == "expl q_a", str(expl))
    ok2, ck2, why2, _e, rej2 = quiz.grade(q, "A")
    check("grade: a distractor is wrong + carries its why-wrong", ok2 is False and why2 == "why-A",
          str((ok2, why2)))
    check("grade: three rejections (one per distractor)", len(rej) == 3 and len(rej2) == 3, str(len(rej)))
    check("grade: a rejection carries key/misconception/why_wrong",
          all(set(r) == {"key", "misconception", "why_wrong"} for r in rej), str(rej[:1]))


def test_shuffle_hides_answer_and_maps_back_server_side():
    q = _q("q_a", correct_key="B")
    seed = quiz.option_seed("q_a", 0)
    served, key_map = quiz.shuffle_options(q, seed)
    check("shuffle: four served options", len(served) == 4, str(served))
    check("shuffle: served labels are A-D by position",
          [o["key"] for o in served] == list(quiz._OPT_KEYS), str([o["key"] for o in served]))
    check("shuffle: NO answer info in the payload (only key+text)",
          all(set(o) == {"key", "text"} for o in served), str(served[:1]))
    # the correct key is recoverable ONLY server-side, via the key_map -> stored key
    served_correct = next(lbl for lbl, stored in key_map.items() if stored == "B")
    _ok, ck, _w, _e, _r = quiz.grade(q, key_map[served_correct])
    check("shuffle: key_map maps a served label back to the stored correct key", ck == "B", str(ck))


def test_shuffle_is_deterministic_per_seed():
    q = _q("q_a")
    s = quiz.option_seed("q_a", 0)
    a, _ = quiz.shuffle_options(q, s)
    b, _ = quiz.shuffle_options(q, s)
    check("shuffle: same seed -> identical layout (serve == grade)",
          [o["text"] for o in a] == [o["text"] for o in b], str((a, b)))
    check("shuffle: option_seed is stable across calls", quiz.option_seed("q_a", 0) == s)


# --------------------------------------------------------------------------- #
#  Normalization + dedup (shared by generation and retire)
# --------------------------------------------------------------------------- #
def test_normalize_stem():
    check("normalize: lowercases, strips punctuation + whitespace",
          quiz.normalize_stem("  What does TCP provide, that UDP does NOT? ")
          == "what does tcp provide that udp does not",
          quiz.normalize_stem("  What does TCP provide, that UDP does NOT? "))


def test_dedup_against_bank_and_within_batch():
    bank = [_q("q_a")]                                 # stem "stem q_a?"
    bank[0]["stem"] = "What is TCP?"
    bank[0]["stem_norm"] = quiz.normalize_stem("What is TCP?")
    new = [{"stem": "what is tcp"},                    # dup of the bank
           {"stem": "What is UDP?"},
           {"stem": "what   is   udp"}]                # dup within the batch
    kept = quiz.dedup(new, bank)
    check("dedup: a stem already in the bank is dropped",
          all(quiz.normalize_stem(k["stem"]) != "what is tcp" for k in kept), str(kept))
    check("dedup: within-batch duplicates collapse to one", len(kept) == 1, str(kept))


def main():
    fns = [test_apply_result_correct_advances_box_and_due,
           test_apply_result_wrong_resets_to_box1_next_day,
           test_box5_maturation_reported_only_on_4_to_5,
           test_apply_result_carries_matured_flag_unchanged,
           test_ratchet_raises_on_high_accuracy, test_ratchet_freezes_on_low_accuracy,
           test_ratchet_target_band_neither, test_ratchet_only_counts_active_tier_and_needs_minimum,
           test_select_next_prefers_oldest_due, test_select_next_serves_soonest_when_none_due,
           test_select_next_never_nothing_when_bank_nonempty,
           test_select_next_excludes_retired_and_filters_tags,
           test_assemble_boss_size_and_no_duplicates, test_assemble_boss_scales_down_when_pool_small,
           test_assemble_boss_draws_from_each_bucket,
           test_grade_correct_and_incorrect, test_shuffle_hides_answer_and_maps_back_server_side,
           test_shuffle_is_deterministic_per_seed, test_normalize_stem,
           test_dedup_against_bank_and_within_batch]
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
