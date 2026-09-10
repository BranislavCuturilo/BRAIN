#!/usr/bin/env python3
"""Pure-stdlib iCal RRULE recurrence expander for the agent_view calendar.

Only the Python standard library is used (`datetime`, `calendar`, `re`) — the
project chose stdlib-only, no `dateutil`.

Public surface consumed by the calendar service:

    expand(dtstart, rrule_str, win_start, win_end, exdates=None) -> list[datetime]
        A SORTED list of occurrence `datetime`s inside [win_start, win_end]
        (both inclusive). `dtstart` is the series start; its time-of-day is the
        time of every occurrence. Never raises: a structurally bad rule, an
        unparseable value or any internal error yields [] so a bad rule can
        never crash the caller. Open-ended rules are hard-capped (see below).

    parse(rrule_str) -> dict          # tolerant; unknown keys ignored
    to_str(rule_dict) -> str          # canonical RRULE string

Supported: FREQ (DAILY/WEEKLY/MONTHLY/YEARLY), INTERVAL, BYDAY (MO..SU plus
ordinal forms 2MO / -1FR for MONTHLY/YEARLY), BYMONTHDAY (incl. negative, -1 =
last day), BYMONTH, COUNT, UNTIL (inclusive; trailing Z and date-only handled),
WKST (default MO).

Constraint the code cannot show: datetimes are treated as naive wall-clock. A
trailing `Z` on UNTIL is stripped, not converted — this app is single-region and
never mixes tz-aware and naive datetimes on one calendar.

Safety: the expansion is bounded on two axes so a malformed / open-ended rule can
never hang or exhaust memory (a self-FK / DoS guard) — at most _MAX_OCCURRENCES
results are returned and at most _MAX_PERIODS periods are ever walked.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

__all__ = ["expand", "parse", "to_str"]

_FREQS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_BYDAY_RE = re.compile(r"^([+-]?\d{1,3})?(MO|TU|WE|TH|FR|SA|SU)$")

# Hard caps — the whole point is that an open-ended rule terminates.
_MAX_OCCURRENCES = 2000     # most results ever returned
_MAX_PERIODS = 200000       # most periods ever walked (absolute loop backstop)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse(rrule_str):
    """Parse an RRULE string into a dict. Never raises.

    Unknown keys are ignored. A KNOWN key with an invalid value sets
    ``rule['_error']`` so :func:`expand` can reject the rule wholesale rather
    than silently guessing. Typed values: FREQ/WKST str, INTERVAL/COUNT int,
    UNTIL datetime, BYDAY list[(ordinal:int, day:str)], BYMONTHDAY/BYMONTH
    list[int].
    """
    rule = {}
    if not isinstance(rrule_str, str):
        rule["_error"] = "not a string"
        return rule
    for token in rrule_str.strip().split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip().upper()
        value = value.strip()
        if not key or not value:
            continue
        try:
            if key == "FREQ":
                rule["FREQ"] = value.upper()
            elif key == "INTERVAL":
                rule["INTERVAL"] = int(value)
            elif key == "COUNT":
                rule["COUNT"] = int(value)
            elif key == "UNTIL":
                parsed = _parse_dt_token(value)
                if parsed is None:
                    raise ValueError("bad UNTIL")
                has_time, dt = parsed
                # A date-only UNTIL is inclusive of the whole day.
                rule["UNTIL"] = dt if has_time else dt.replace(
                    hour=23, minute=59, second=59)
            elif key == "WKST":
                v = value.upper()
                rule["WKST"] = v if v in _WEEKDAYS else "MO"
            elif key == "BYDAY":
                rule["BYDAY"] = _parse_byday(value)
            elif key == "BYMONTHDAY":
                rule["BYMONTHDAY"] = _parse_int_list(
                    value, lo=-31, hi=31, nonzero=True)
            elif key == "BYMONTH":
                rule["BYMONTH"] = _parse_int_list(value, lo=1, hi=12)
            # else: unknown key -> ignore
        except (ValueError, TypeError):
            rule["_error"] = "invalid " + key
    return rule


def to_str(rule):
    """Render a parsed rule dict back to a canonical RRULE string.

    Meta keys (leading underscore) are skipped. FREQ leads, per RFC 5545.
    """
    if not isinstance(rule, dict):
        return ""
    parts = []
    if rule.get("FREQ"):
        parts.append("FREQ=" + str(rule["FREQ"]))
    if "INTERVAL" in rule:
        parts.append("INTERVAL=" + str(rule["INTERVAL"]))
    if "WKST" in rule:
        parts.append("WKST=" + str(rule["WKST"]))
    if "BYMONTH" in rule:
        parts.append("BYMONTH=" + ",".join(str(n) for n in rule["BYMONTH"]))
    if "BYMONTHDAY" in rule:
        parts.append("BYMONTHDAY=" + ",".join(str(n) for n in rule["BYMONTHDAY"]))
    if "BYDAY" in rule:
        parts.append("BYDAY=" + ",".join(
            (str(o) if o else "") + c for (o, c) in rule["BYDAY"]))
    if "COUNT" in rule:
        parts.append("COUNT=" + str(rule["COUNT"]))
    if "UNTIL" in rule:
        parts.append("UNTIL=" + rule["UNTIL"].strftime("%Y%m%dT%H%M%SZ"))
    return ";".join(parts)


def _parse_byday(value):
    out = []
    for part in value.split(","):
        part = part.strip().upper()
        if not part:
            continue
        m = _BYDAY_RE.match(part)
        if not m:
            raise ValueError("bad BYDAY: " + part)
        ordinal = int(m.group(1)) if m.group(1) else 0
        out.append((ordinal, m.group(2)))
    if not out:
        raise ValueError("empty BYDAY")
    return out


def _parse_int_list(value, lo, hi, nonzero=False):
    out = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        n = int(part)
        if n < lo or n > hi or (nonzero and n == 0):
            raise ValueError("out of range")
        out.append(n)
    if not out:
        raise ValueError("empty list")
    return out


def _parse_dt_token(s):
    """Parse an iCal/ISO date or datetime. Returns (has_time, datetime) or None.

    A date-only value returns time 00:00:00; the caller decides how to treat it.
    """
    s = s.strip()
    if not s:
        return None
    core = s[:-1] if s.endswith("Z") else s
    for fmt, has_time in (
        ("%Y%m%dT%H%M%S", True),
        ("%Y-%m-%dT%H:%M:%S", True),
        ("%Y-%m-%d %H:%M:%S", True),
        ("%Y%m%d", False),
        ("%Y-%m-%d", False),
    ):
        try:
            return has_time, datetime.strptime(core, fmt)
        except ValueError:
            continue
    try:
        return True, datetime.fromisoformat(core).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Date arithmetic helpers (pure, unit-testable)
# --------------------------------------------------------------------------- #
def _days_in_month(y, m):
    return calendar.monthrange(y, m)[1]


def _resolve_monthday(y, m, md):
    """A BYMONTHDAY value -> a real date in that month, or None if it does not
    exist. md>0 counts from the 1st; md<0 counts from the last day (-1 = last).
    A month with no 31st returns None for md=31 (it SKIPS, never rolls over)."""
    dim = _days_in_month(y, m)
    if md > 0:
        return date(y, m, md) if md <= dim else None
    if md < 0:
        day = dim + 1 + md
        return date(y, m, day) if 1 <= day <= dim else None
    return None


def _resolve_monthdays(y, m, mds):
    out = set()
    for md in mds:
        r = _resolve_monthday(y, m, md)
        if r:
            out.add(r)
    return out


def _nth_weekday(y, m, wd, n):
    """Date of the n-th weekday `wd` (0=Mon) in month, n>0 from start, n<0 from
    end (-1 = last). None if that occurrence does not exist in the month."""
    dim = _days_in_month(y, m)
    if n > 0:
        first_wd = date(y, m, 1).weekday()
        day = 1 + (wd - first_wd) % 7 + (n - 1) * 7
        return date(y, m, day) if day <= dim else None
    if n < 0:
        last_wd = date(y, m, dim).weekday()
        day = dim - (last_wd - wd) % 7 - (abs(n) - 1) * 7
        return date(y, m, day) if day >= 1 else None
    return None


def _weekdays_in_month(y, m, wd):
    out = []
    n = 1
    while True:
        r = _nth_weekday(y, m, wd, n)
        if r is None:
            break
        out.append(r)
        n += 1
    return out


def _week_start(d, wkst):
    return d - timedelta(days=(d.weekday() - wkst) % 7)


def _add_months(y, m, delta):
    total = y * 12 + (m - 1) + delta
    return total // 12, total % 12 + 1


def _month_candidates(y, m, byday, bymonthday, default_day):
    """Sorted occurrence dates within one month, applying the BY* rules.

    Also the per-month unit for YEARLY (called once per selected month).
    """
    if bymonthday is not None:
        days = _resolve_monthdays(y, m, bymonthday)
        if byday is not None:  # both present -> BYDAY narrows BYMONTHDAY
            allowed = {_WEEKDAYS[c] for (_o, c) in byday}
            days = {d for d in days if d.weekday() in allowed}
        return sorted(days)
    if byday is not None:
        out = set()
        for ordinal, code in byday:
            wd = _WEEKDAYS[code]
            if ordinal == 0:
                out.update(_weekdays_in_month(y, m, wd))
            else:
                r = _nth_weekday(y, m, wd, ordinal)
                if r:
                    out.add(r)
        return sorted(out)
    r = _resolve_monthday(y, m, default_day)  # skips months too short for the day
    return [r] if r else []


# --------------------------------------------------------------------------- #
# Expansion
# --------------------------------------------------------------------------- #
def expand(dtstart, rrule_str, win_start, win_end, exdates=None):
    """Occurrences of an RRULE inside [win_start, win_end], inclusive, sorted.

    See the module docstring for the full contract. Never raises: returns [] on
    a structurally invalid rule or any internal error.
    """
    try:
        rule = parse(rrule_str)
    except Exception:
        return []
    if not isinstance(rule, dict) or rule.get("_error"):
        return []
    freq = rule.get("FREQ")
    if freq not in _FREQS:
        return []
    try:
        return _expand_core(dtstart, rule, freq, win_start, win_end, exdates)
    except Exception:
        # Outermost boundary for the calendar service: the contract is "never
        # raise". A wrong input becomes an empty result, not a 500.
        return []


def _expand_core(dtstart, rule, freq, win_start, win_end, exdates):
    interval = rule.get("INTERVAL", 1)
    if not isinstance(interval, int) or interval < 1:
        return []
    count = rule.get("COUNT")
    if count is not None and (not isinstance(count, int) or count < 1):
        return []
    until = rule.get("UNTIL")
    wkst = _WEEKDAYS.get(rule.get("WKST", "MO"), 0)
    byday = rule.get("BYDAY")
    bymonthday = rule.get("BYMONTHDAY")
    bymonth = rule.get("BYMONTH")

    tod = dtstart.time()
    dstart = dtstart.date()
    exact_ex, date_ex = _normalize_exdates(exdates)
    win_end_date = win_end.date()

    def period(i):
        """(period_reference_date, sorted candidate dates) for period index i.

        The reference date is the EARLIEST date the period can contain, so once
        it passes win_end no later period can contribute — the loop stops. A
        period that yields no candidates (e.g. BYMONTHDAY=31 in February) does
        NOT stop the loop; only the reference date passing the bound does.
        """
        step = i * interval
        if freq == "DAILY":
            d = dstart + timedelta(days=step)
            cands = [d]
            if byday is not None:
                if d.weekday() not in {_WEEKDAYS[c] for (_o, c) in byday}:
                    cands = []
            if cands and bymonthday is not None:
                if d not in _resolve_monthdays(d.year, d.month, bymonthday):
                    cands = []
            return d, cands
        if freq == "WEEKLY":
            ws = _week_start(dstart, wkst) + timedelta(days=step * 7)
            if byday is not None:
                days = [ws + timedelta(days=(_WEEKDAYS[c] - wkst) % 7)
                        for (_o, c) in byday]
            else:
                days = [ws + timedelta(days=(dstart.weekday() - wkst) % 7)]
            if bymonthday is not None:
                days = [d for d in days
                        if d in _resolve_monthdays(d.year, d.month, bymonthday)]
            return ws, sorted(set(days))
        if freq == "MONTHLY":
            y, m = _add_months(dstart.year, dstart.month, step)
            return date(y, m, 1), _month_candidates(
                y, m, byday, bymonthday, dstart.day)
        # YEARLY
        y = dstart.year + step
        months = bymonth if bymonth else [dstart.month]
        days = []
        for mo in months:
            days += _month_candidates(y, mo, byday, bymonthday, dstart.day)
        return date(y, 1, 1), sorted(set(days))

    results = []
    generated = 0  # RRULE-level count (BEFORE exdate removal), per RFC 5545
    for i in range(_MAX_PERIODS):
        try:
            ref, cands = period(i)
        except (ValueError, OverflowError):
            break  # ran off the end of representable dates near the horizon
        if ref > win_end_date:
            break
        for d in cands:
            dt = datetime.combine(d, tod)
            if dt < dtstart:
                continue  # recurrence set starts at dtstart
            if until is not None and dt > until:
                return sorted(results)
            generated += 1
            if count is not None and generated > count:
                return sorted(results)
            if win_start <= dt <= win_end and not _is_excluded(
                    dt, exact_ex, date_ex):
                results.append(dt)
                if len(results) >= _MAX_OCCURRENCES:
                    return sorted(results)
    return sorted(results)


def _normalize_exdates(exdates):
    """Split EXDATE inputs into (exact-datetime set, date-only set).

    datetime / datetime-string -> exact match; date / date-only string -> match
    any occurrence on that calendar day (EXDATEs commonly carry no time).
    """
    exact, days = set(), set()
    if not exdates:
        return exact, days
    for x in exdates:
        if isinstance(x, datetime):        # must precede date: datetime is-a date
            exact.add(x)
        elif isinstance(x, date):
            days.add(x)
        elif isinstance(x, str):
            parsed = _parse_dt_token(x)
            if parsed is None:
                continue
            has_time, dt = parsed
            (exact.add(dt) if has_time else days.add(dt.date()))
    return exact, days


def _is_excluded(dt, exact, days):
    return dt in exact or dt.date() in days
