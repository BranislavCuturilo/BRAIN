#!/usr/bin/env python3
"""Offline tests for adapters/acme_helpdesk.py's `to_record` — no network, no
credentials. Covers F8 (ticket rating): the mapping is TOLERANT of an API that
does not carry the rating fields yet (pre-F8 helpdesk), and passes them through
unchanged when it does.
Run: python test_acme_helpdesk.py"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from adapters.acme_helpdesk import AcmeHelpdesk  # noqa: E402

FAILS = []


def ck(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label
          + (("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(label)


def _detail(**extra):
    base = {"ticket_id": 12345, "ticket_title": "t", "ticket_description": "d",
           "created_on": "2026-08-01T10:00:00", "customer": {"id": 1, "full_name": "Ana"},
           "engineer": {"id": 2, "full_name": "Bob"}, "category": {"name": "Bug"},
           "module": {"id": 9, "name": "INV"}, "priority": "Minor", "status": "Open",
           "is_closed": True, "comments": []}
    base.update(extra)
    return base


def test_to_record_carries_the_rating_fields_when_present():
    ad = AcmeHelpdesk(url="https://helpdesk.example.com", token="x")
    rec = ad.to_record(_detail(rating=5, rating_comment="Brzo i tacno", rated_at="2026-08-15T09:00:00"))
    hd = rec["helpdesk"]
    ck("rating: value passes through", hd.get("rating") == 5, hd)
    ck("rating: comment passes through", hd.get("rating_comment") == "Brzo i tacno", hd)
    ck("rating: rated_at passes through", hd.get("rated_at") == "2026-08-15T09:00:00", hd)


def test_to_record_is_tolerant_of_a_pre_f8_api_with_no_rating_fields():
    ad = AcmeHelpdesk(url="https://helpdesk.example.com", token="x")
    rec = ad.to_record(_detail())          # no rating/rating_comment/rated_at keys at all
    hd = rec["helpdesk"]
    ck("no rating: value is None, not a KeyError", hd.get("rating") is None)
    ck("no rating: comment normalises to empty string", hd.get("rating_comment") == "", hd)
    ck("no rating: rated_at normalises to empty string", hd.get("rated_at") == "", hd)


def test_to_record_tolerates_an_explicit_null_comment_and_date():
    ad = AcmeHelpdesk(url="https://helpdesk.example.com", token="x")
    rec = ad.to_record(_detail(rating=None, rating_comment=None, rated_at=None))
    hd = rec["helpdesk"]
    ck("explicit null: value stays None", hd.get("rating") is None, hd)
    ck("explicit null: comment normalises to empty string", hd.get("rating_comment") == "", hd)
    ck("explicit null: rated_at normalises to empty string", hd.get("rated_at") == "", hd)


def test_to_record_maps_f9_ratings_list_and_prefers_the_customer():
    ad = AcmeHelpdesk(url="https://helpdesk.example.com", token="x")
    rec = ad.to_record(_detail(
        rating=None, rating_comment=None, rated_at=None,           # dropped by the F9 API
        ratings=[{"role": "tester", "rater": "Mika", "rating": 4,
                  "comment": "Brzo", "rated_at": "2026-08-14T09:00:00"},
                 {"role": "customer", "rater": "Ana", "rating": 5,
                  "comment": "Odlicno", "rated_at": "2026-08-15T09:00:00"}],
        rating_avg=4.5))
    hd = rec["helpdesk"]
    ck("f9: ratings list passes through in full", len(hd.get("ratings") or []) == 2, hd)
    ck("f9: rating_avg passes through", hd.get("rating_avg") == 4.5, hd)
    ck("f9: back-compat `rating` is the CUSTOMER's own rating, not the average",
       hd.get("rating") == 5, hd)


def test_to_record_falls_back_to_the_average_with_no_customer_rating():
    ad = AcmeHelpdesk(url="https://helpdesk.example.com", token="x")
    rec = ad.to_record(_detail(
        rating=None, rating_comment=None, rated_at=None,
        ratings=[{"role": "tester", "rater": "Mika", "rating": 4, "comment": "", "rated_at": ""}],
        rating_avg=4.0))
    hd = rec["helpdesk"]
    ck("f9: no customer rating yet -> back-compat `rating` falls back to the average",
       hd.get("rating") == 4.0, hd)


def test_to_record_tolerates_an_empty_f9_ratings_list():
    ad = AcmeHelpdesk(url="https://helpdesk.example.com", token="x")
    rec = ad.to_record(_detail(rating=None, rating_comment=None, rated_at=None,
                               ratings=[], rating_avg=None))
    hd = rec["helpdesk"]
    ck("f9: an empty ratings list is [], not a KeyError", hd.get("ratings") == [], hd)
    ck("f9: rating stays None with nobody rated", hd.get("rating") is None, hd)


def test_to_record_carries_resolution_steps_and_closed_at_when_present():
    """Phase 1 (reopen ledger): `resolution_steps`/`closed_at` are captured so
    rounds.py can seed round 0 and anchor the trigger-comment match."""
    ad = AcmeHelpdesk(url="https://helpdesk.example.com", token="x")
    rec = ad.to_record(_detail(resolution_steps="Restartovan servis, radi.",
                               closed_at="2026-08-20T10:00:00"))
    hd = rec["helpdesk"]
    ck("resolution_steps: value passes through", hd.get("resolution_steps") == "Restartovan servis, radi.", hd)
    ck("closed_at: value passes through", hd.get("closed_at") == "2026-08-20T10:00:00", hd)


def test_to_record_tolerates_absence_of_resolution_steps_and_closed_at():
    """A legacy/thin detail dict (older helpdesk, or a field genuinely never set)
    must not KeyError - `resolution_steps` normalises to "", `closed_at` stays
    None."""
    ad = AcmeHelpdesk(url="https://helpdesk.example.com", token="x")
    rec = ad.to_record(_detail())          # no resolution_steps/closed_at keys at all
    hd = rec["helpdesk"]
    ck("no resolution_steps: normalises to empty string, not a KeyError", hd.get("resolution_steps") == "", hd)
    ck("no closed_at: stays None, not a KeyError", hd.get("closed_at") is None, hd)


def main() -> int:
    for fn in (test_to_record_carries_the_rating_fields_when_present,
               test_to_record_is_tolerant_of_a_pre_f8_api_with_no_rating_fields,
               test_to_record_tolerates_an_explicit_null_comment_and_date,
               test_to_record_maps_f9_ratings_list_and_prefers_the_customer,
               test_to_record_falls_back_to_the_average_with_no_customer_rating,
               test_to_record_tolerates_an_empty_f9_ratings_list,
               test_to_record_carries_resolution_steps_and_closed_at_when_present,
               test_to_record_tolerates_absence_of_resolution_steps_and_closed_at):
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
