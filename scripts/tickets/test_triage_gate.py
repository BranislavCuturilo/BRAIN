#!/usr/bin/env python3
"""The quota gate on Gemini triage — which tickets a rescan actually re-reads.

The operator's Rescan used to analyse EVERY active ticket on every press: 20
Gemini calls with nothing new to read. It now runs the same gate the boot pass
uses, extended so "nothing new" is actually true — a far-side comment that
landed after the analysis makes the ticket stale again. Run: python
test_triage_gate.py"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze_gemini  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def _t(analysis_at="2026-08-30T12:00:00", comments=(), closed=False,
       closed_seen=True, created="2026-08-01T08:00:00+02:00"):
    return {
        "original": {"created": created},
        "helpdesk": {"is_closed": closed},
        "analysis": ({"updated_at": analysis_at, "closed_seen": closed_seen}
                     if analysis_at is not None else None),
        "comments": list(comments),
    }


def _c(role, at):
    return {"id": 1, "author": "X", "author_role": role, "at": at, "body": "b"}


# TIMEZONE-PROOF FIXTURES. `rounds.parse_dt` interprets a NAIVE stamp in the
# machine's local zone (correctly -- the store writes local wall-clock) and an
# offset stamp at its offset, then compares in UTC. So a naive analysis at
# 12:00 and an aware comment at 14:00+02:00 are two hours apart in CET and the
# SAME INSTANT on a UTC machine, where `cdt > since` is then False.
#
# Measured 2026-08-31: these two assertions passed on the author's desktop and
# failed on every CI runner, on both Linux and Windows -- the first time this
# suite had ever run outside the timezone it was written in.
#
# So the gap between a naive stamp and an aware one must exceed any real UTC
# offset (+14 to -12). A different DAY is the cheap way to guarantee it; two
# hours is not.
AFTER = "2026-08-31T14:00:00+02:00"      # a day later than the analysis
BEFORE = "2026-08-29T09:00:00+02:00"     # a day earlier


def test_unchanged_is_skipped():
    ck("unchanged: not re-analysed", analyze_gemini._needs(_t(), None) is False)


def test_never_analysed_is_taken():
    ck("unanalysed: re-analysed", analyze_gemini._needs(_t(analysis_at=None), None) is True)


def test_a_far_side_comment_after_the_analysis_makes_it_stale():
    """The case the old gate missed entirely: a dopuna landed and the stored
    analysis went on describing the ticket as it was before."""
    for role in ("customer", "other"):
        t = _t(comments=[_c(role, AFTER)])
        ck("far-side comment (%s): re-analysed" % role,
           analyze_gemini._needs(t, None) is True)


def test_our_own_comment_does_not_make_it_stale():
    """Posting the before/after pictures does not change what is being asked —
    re-triaging on it would spend a call to re-read our own message."""
    t = _t(comments=[_c("engineer", AFTER)])
    ck("own comment: not re-analysed", analyze_gemini._needs(t, None) is False)


def test_a_comment_predating_the_analysis_does_not():
    t = _t(comments=[_c("other", BEFORE)])
    ck("older comment: not re-analysed", analyze_gemini._needs(t, None) is False)


def test_an_undateable_analysis_is_redone_once():
    """Legacy blocks with no `updated_at` come back stamped, so this self-heals
    rather than re-firing every run."""
    t = _t()
    t["analysis"].pop("updated_at")
    ck("undateable: re-analysed once", analyze_gemini._needs(t, None) is True)


def test_newly_closed_still_taken():
    ck("newly closed: re-analysed",
       analyze_gemini._needs(_t(closed=True, closed_seen=False), None) is True)


def test_created_since_the_last_run_still_taken():
    t = _t(created="2026-08-30T10:00:00+02:00")
    ck("created since: re-analysed",
       analyze_gemini._needs(t, "2026-08-29T00:00:00") is True)


def test_force_bypasses_the_gate():
    """`run(force=True)` must not consult `_needs` at all — the guard is pinned
    on the source because the alternative is a test that really calls Gemini."""
    import inspect
    src = inspect.getsource(analyze_gemini.run)
    ck("run: the gate is always on unless forced",
       "if not force and not _needs(t, since):" in src)
    ck("run: takes force, not the old incremental flag",
       "force=False" in inspect.signature(analyze_gemini.run).__str__()
       or "force" in inspect.signature(analyze_gemini.run).parameters)
    ck("run: no leftover `incremental` parameter",
       "incremental" not in inspect.signature(analyze_gemini.run).parameters)


def main():
    for fn in (test_unchanged_is_skipped, test_never_analysed_is_taken,
               test_a_far_side_comment_after_the_analysis_makes_it_stale,
               test_our_own_comment_does_not_make_it_stale,
               test_a_comment_predating_the_analysis_does_not,
               test_an_undateable_analysis_is_redone_once,
               test_newly_closed_still_taken,
               test_created_since_the_last_run_still_taken,
               test_force_bypasses_the_gate):
        fn()
    print(f"\n{len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
