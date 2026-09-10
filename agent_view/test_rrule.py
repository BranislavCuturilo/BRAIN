#!/usr/bin/env python3
"""Offline unit tests for the pure-stdlib RRULE expander (rrule.py).

Run with the kontrola venv python and UTF-8 IO to avoid the Windows cp1252
console trap (this file's output is ASCII regardless):

    PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m unittest test_rrule -v

Weekday anchors used below (verified): 2026-01-01 is a Thursday, so
2026-01-05 is a Monday, 2026-01-06 the 1st Tuesday, 2026-01-13 the 2nd Tuesday,
2026-01-30 the last Friday of January 2026.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rrule  # noqa: E402


def dt(y, mo, d, h=9, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s)


class DailyTests(unittest.TestCase):
    def test_every_day(self):
        start = dt(2026, 1, 1)
        occ = rrule.expand(start, "FREQ=DAILY",
                           dt(2026, 1, 1, 0), dt(2026, 1, 10, 23, 59, 59))
        self.assertEqual(len(occ), 10)
        self.assertEqual(occ[0], start)
        self.assertEqual(occ[-1], dt(2026, 1, 10))
        self.assertEqual(occ, sorted(occ))

    def test_count_boundary(self):
        occ = rrule.expand(dt(2026, 1, 1), "FREQ=DAILY;COUNT=3",
                           dt(2026, 1, 1, 0), dt(2026, 1, 31, 23, 59))
        self.assertEqual(occ, [dt(2026, 1, 1), dt(2026, 1, 2), dt(2026, 1, 3)])

    def test_until_boundary_inclusive(self):
        # Last occurrence (Jan 5 09:00) equals UNTIL exactly -> included; the
        # next day is excluded.
        occ = rrule.expand(dt(2026, 1, 1), "FREQ=DAILY;UNTIL=20260105T090000Z",
                           dt(2026, 1, 1, 0), dt(2026, 3, 1))
        self.assertEqual(len(occ), 5)
        self.assertEqual(occ[-1], dt(2026, 1, 5))
        self.assertNotIn(dt(2026, 1, 6), occ)


class WeeklyTests(unittest.TestCase):
    def test_byday_mo_we_until(self):
        start = dt(2026, 1, 5)  # Monday
        occ = rrule.expand(start,
                           "FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20260119T235959Z",
                           dt(2026, 1, 1, 0), dt(2026, 3, 1))
        self.assertEqual(occ, [dt(2026, 1, 5), dt(2026, 1, 7),
                               dt(2026, 1, 12), dt(2026, 1, 14),
                               dt(2026, 1, 19)])
        # Weekdays are exactly Monday(0) and Wednesday(2).
        self.assertTrue(all(o.weekday() in (0, 2) for o in occ))

    def test_interval2_weekly(self):
        start = dt(2026, 1, 5)  # Monday
        occ = rrule.expand(start, "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO",
                           dt(2026, 1, 1, 0), dt(2026, 3, 1))
        self.assertEqual(occ, [dt(2026, 1, 5), dt(2026, 1, 19),
                               dt(2026, 2, 2), dt(2026, 2, 16)])
        # Consecutive occurrences are exactly 14 days apart.
        gaps = {(occ[i + 1] - occ[i]).days for i in range(len(occ) - 1)}
        self.assertEqual(gaps, {14})


class MonthlyTests(unittest.TestCase):
    def test_bymonthday_15(self):
        occ = rrule.expand(dt(2026, 1, 15), "FREQ=MONTHLY;BYMONTHDAY=15",
                           dt(2026, 1, 1, 0), dt(2026, 6, 30, 23, 59))
        self.assertEqual([o.month for o in occ], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(o.day == 15 for o in occ))

    def test_bymonthday_31_skips_short_months(self):
        occ = rrule.expand(dt(2026, 1, 31), "FREQ=MONTHLY;BYMONTHDAY=31",
                           dt(2026, 1, 1, 0), dt(2026, 12, 31, 23, 59))
        # Only months that HAVE a 31st: Jan, Mar, May, Jul, Aug, Oct, Dec.
        self.assertEqual([o.month for o in occ], [1, 3, 5, 7, 8, 10, 12])
        self.assertTrue(all(o.day == 31 for o in occ))
        # Feb and Apr are skipped, never rolled over to the 1st / 2nd / 3rd.
        self.assertFalse(any(o.month == 2 for o in occ))
        self.assertFalse(any(o.month == 4 for o in occ))

    def test_bymonthday_negative_last_day(self):
        occ = rrule.expand(dt(2026, 1, 31), "FREQ=MONTHLY;BYMONTHDAY=-1",
                           dt(2026, 1, 1, 0), dt(2026, 4, 30, 23, 59))
        self.assertEqual(occ, [dt(2026, 1, 31), dt(2026, 2, 28),
                               dt(2026, 3, 31), dt(2026, 4, 30)])

    def test_byday_second_tuesday(self):
        start = dt(2026, 1, 13, 10)  # 2nd Tuesday of Jan 2026
        occ = rrule.expand(start, "FREQ=MONTHLY;BYDAY=2TU",
                           dt(2026, 1, 1, 0), dt(2026, 3, 31, 23, 59))
        self.assertEqual(occ, [dt(2026, 1, 13, 10), dt(2026, 2, 10, 10),
                               dt(2026, 3, 10, 10)])
        self.assertTrue(all(o.weekday() == 1 for o in occ))       # Tuesday
        self.assertTrue(all(8 <= o.day <= 14 for o in occ))       # the 2nd one

    def test_byday_last_friday(self):
        start = dt(2026, 1, 30)  # last Friday of Jan 2026
        occ = rrule.expand(start, "FREQ=MONTHLY;BYDAY=-1FR",
                           dt(2026, 1, 1, 0), dt(2026, 3, 31, 23, 59))
        self.assertEqual(occ, [dt(2026, 1, 30), dt(2026, 2, 27),
                               dt(2026, 3, 27)])
        self.assertTrue(all(o.weekday() == 4 for o in occ))       # Friday


class YearlyTests(unittest.TestCase):
    def test_yearly(self):
        start = dt(2026, 6, 15)
        occ = rrule.expand(start, "FREQ=YEARLY",
                           dt(2026, 1, 1, 0), dt(2030, 12, 31, 23, 59))
        self.assertEqual([o.year for o in occ], [2026, 2027, 2028, 2029, 2030])
        self.assertTrue(all(o.month == 6 and o.day == 15 for o in occ))


class ExdateTests(unittest.TestCase):
    def test_exdates_subtracted(self):
        # One exact datetime EXDATE and one date-only ISO EXDATE.
        occ = rrule.expand(
            dt(2026, 1, 1), "FREQ=DAILY",
            dt(2026, 1, 1, 0), dt(2026, 1, 5, 23, 59),
            exdates=[dt(2026, 1, 3), "2026-01-05"])
        self.assertEqual(occ, [dt(2026, 1, 1), dt(2026, 1, 2), dt(2026, 1, 4)])


class SafetyTests(unittest.TestCase):
    def test_open_ended_rule_is_capped(self):
        # DAILY with no COUNT / no UNTIL over a 74-year window would be ~27000
        # occurrences; the hard cap stops it at _MAX_OCCURRENCES and it returns.
        start = dt(2026, 1, 1)
        occ = rrule.expand(start, "FREQ=DAILY",
                           dt(2026, 1, 1, 0), dt(2100, 12, 31, 23, 59))
        self.assertEqual(len(occ), rrule._MAX_OCCURRENCES)
        self.assertEqual(occ[0], start)
        self.assertEqual(occ, sorted(occ))

    def test_malformed_rule_returns_empty(self):
        win_a, win_b = dt(2026, 1, 1, 0), dt(2026, 12, 31)
        self.assertEqual(rrule.expand(dt(2026, 1, 1), "garbage", win_a, win_b), [])
        self.assertEqual(rrule.expand(dt(2026, 1, 1), "", win_a, win_b), [])
        # A bad value on a KNOWN key is structural -> [] (not a silent default).
        self.assertEqual(
            rrule.expand(dt(2026, 1, 1), "FREQ=WEEKLY;INTERVAL=abc", win_a, win_b), [])
        # Unknown FREQ.
        self.assertEqual(
            rrule.expand(dt(2026, 1, 1), "FREQ=FORTNIGHTLY", win_a, win_b), [])
        # None input must not crash.
        self.assertEqual(rrule.expand(dt(2026, 1, 1), None, win_a, win_b), [])


class ParseRoundtripTests(unittest.TestCase):
    def test_unknown_keys_ignored(self):
        rule = rrule.parse("FREQ=DAILY;FOO=BAR;COUNT=2")
        self.assertEqual(rule.get("FREQ"), "DAILY")
        self.assertEqual(rule.get("COUNT"), 2)
        self.assertNotIn("FOO", rule)
        self.assertNotIn("_error", rule)
        occ = rrule.expand(dt(2026, 1, 1), "FREQ=DAILY;FOO=BAR;COUNT=2",
                           dt(2026, 1, 1, 0), dt(2026, 1, 31))
        self.assertEqual(occ, [dt(2026, 1, 1), dt(2026, 1, 2)])

    def test_roundtrip_stable(self):
        src = "FREQ=MONTHLY;INTERVAL=2;BYDAY=2TU,-1FR;BYMONTHDAY=15;COUNT=5;WKST=SU"
        rule = rrule.parse(src)
        self.assertNotIn("_error", rule)
        again = rrule.parse(rrule.to_str(rule))
        self.assertEqual(again, rule)

    def test_until_roundtrip(self):
        rule = rrule.parse("FREQ=DAILY;UNTIL=20261231T235959Z")
        self.assertEqual(rule["UNTIL"], datetime(2026, 12, 31, 23, 59, 59))
        self.assertEqual(rrule.to_str(rule), "FREQ=DAILY;UNTIL=20261231T235959Z")
        self.assertEqual(rrule.parse(rrule.to_str(rule)), rule)

    def test_date_only_until_inclusive_of_day(self):
        rule = rrule.parse("FREQ=DAILY;UNTIL=20261231")
        self.assertEqual(rule["UNTIL"], datetime(2026, 12, 31, 23, 59, 59))


class HelperTests(unittest.TestCase):
    def test_resolve_monthday(self):
        from datetime import date
        self.assertEqual(rrule._resolve_monthday(2026, 1, 31), date(2026, 1, 31))
        self.assertIsNone(rrule._resolve_monthday(2026, 2, 31))   # no Feb 31
        self.assertEqual(rrule._resolve_monthday(2026, 2, -1), date(2026, 2, 28))

    def test_nth_weekday(self):
        from datetime import date
        # 2nd Tuesday (wd=1) of Jan 2026 is the 13th; last Friday (wd=4) the 30th.
        self.assertEqual(rrule._nth_weekday(2026, 1, 1, 2), date(2026, 1, 13))
        self.assertEqual(rrule._nth_weekday(2026, 1, 4, -1), date(2026, 1, 30))
        self.assertIsNone(rrule._nth_weekday(2026, 1, 1, 6))       # no 6th Tuesday


if __name__ == "__main__":
    unittest.main()
