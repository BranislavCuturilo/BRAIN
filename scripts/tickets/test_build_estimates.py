#!/usr/bin/env python3
"""Offline tests for build_estimates.py - the columns, which tickets earn a row,
and byte-identical regeneration. Temp dirs throughout; the real store is never
opened. Run: python test_build_estimates.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_estimates  # noqa: E402
import worklog  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def _measure(root, module, ticket, start, end):
    for ev, at in (("start", start), ("end", end)):
        worklog.append_event(root, {"ev": ev, "work_id": "w-" + ticket, "module": module,
                                    "ticket": ticket, "at": at, "share": 1.0,
                                    "kind": "claude", "device": "desktop",
                                    "auto_closed": False})


def _store(root):
    tickets = {
        # AI estimate wins over the helpdesk field; measured; closed locally
        "1": {"triage": {"ai_estimate": {"hours": 3.5, "at": "2026-08-18T09:00:00",
                                         "by": "gemini"}},
              "helpdesk": {"estimated_time": 9, "is_closed": True},
              "reading": {"scope": "logic", "complexity": "srednja"},
              "closed": {"at": "2026-08-18T15:00:00"}},
        # only the helpdesk estimate, no measurement, closed only on the helpdesk
        "2": {"helpdesk": {"estimated_time": 2, "is_closed": True},
              "analysis": {"scope": "ui"}},
        # measured but never estimated -> still a row (that is the calibration case)
        "3": {"helpdesk": {"estimated_time": None, "is_closed": False}},
        # neither estimated nor measured -> no row
        "4": {"helpdesk": {"estimated_time": 0, "is_closed": False}},
        "junk": "not a dict",
    }
    d = {"project": {"name": "INV"}, "tickets": tickets, "rev": 0}
    (Path(root) / "INV.json").write_text(json.dumps(d), encoding="utf-8")
    # the catalogs are not module files and must not become rows
    (Path(root) / "modules.json").write_text(json.dumps({"modules": {}}), encoding="utf-8")
    (Path(root) / "ai_log.json").write_text(json.dumps({"entries": []}), encoding="utf-8")


def _rows(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.split(",") for line in lines]


def test_columns_and_row_selection():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        _measure(root, "INV", "1", "2026-08-18T09:00:00", "2026-08-18T13:00:00")
        _measure(root, "INV", "3", "2026-08-18T09:00:00", "2026-08-18T09:30:00")
        fp = build_estimates.build(root)
        rows = _rows(fp)
        ck("csv: the header is the agreed contract",
           rows[0] == list(build_estimates.HEADER))
        ck("csv: a ticket with neither estimate nor measurement is skipped",
           [r[1] for r in rows[1:]] == ["1", "2", "3"])
        by_id = {r[1]: r for r in rows[1:]}
        ck("csv: the AI estimate wins over the helpdesk field",
           by_id["1"][4] == "3.50")
        ck("csv: measured hours land in actual_h", by_id["1"][6] == "4.00")
        ck("csv: raw_h carries the model's uncalibrated number when the AI estimate has one, else empty",
           by_id["1"][5] in ("", "7.00") and by_id["2"][5] == "")
        ck("csv: complexity/scope come from the reading",
           (by_id["1"][2], by_id["1"][3]) == ("srednja", "logic"))
        ck("csv: estimated_at and by come with the AI estimate",
           (by_id["1"][7], by_id["1"][9]) == ("2026-08-18T09:00:00", "gemini"))
        ck("csv: closed_at is the local close timestamp",
           by_id["1"][8] == "2026-08-18T15:00:00")
        ck("csv: the helpdesk estimate is attributed to the helpdesk",
           (by_id["2"][4], by_id["2"][9]) == ("2.00", "helpdesk"))
        ck("csv: scope falls back to the older analysis block", by_id["2"][3] == "ui")
        ck("csv: an unmeasured ticket has an empty actual_h", by_id["2"][6] == "")
        ck("csv: a helpdesk-only close is marked without inventing a time",
           by_id["2"][8] == "closed")
        ck("csv: a measured ticket with no estimate still gets a row",
           (by_id["3"][4], by_id["3"][6]) == ("", "0.50"))
        ck("csv: an open ticket has an empty closed_at", by_id["3"][8] == "")
        ck("csv: the catalogs contribute no rows",
           all(r[0] == "INV" for r in rows[1:]))


def test_regeneration_is_byte_identical():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        _measure(root, "INV", "1", "2026-08-18T09:00:00", "2026-08-18T13:00:00")
        first = build_estimates.build(root).read_bytes()
        second = build_estimates.build(root).read_bytes()
        ck("idempotent: the same store produces the same bytes", first == second)
        ck("idempotent: unix line endings (no platform churn in git)",
           b"\r\n" not in first)


def test_an_auto_closed_measurement_does_not_reach_the_csv():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        worklog.append_event(root, {"ev": "start", "work_id": "w3", "module": "INV",
                                    "ticket": "3", "at": "2026-08-18T09:00:00",
                                    "share": 1.0, "kind": "claude", "device": "d"})
        worklog.append_event(root, {"ev": "end", "work_id": "w3", "module": "INV",
                                    "ticket": "3", "at": "2026-08-18T13:00:00",
                                    "share": 1.0, "kind": "claude", "device": "d",
                                    "auto_closed": True})
        rows = _rows(build_estimates.build(root))
        ck("auto_closed: the guessed interval earns no row",
           [r[1] for r in rows[1:]] == ["1", "2"])


def test_an_empty_store_still_writes_a_header():
    with tempfile.TemporaryDirectory() as root:
        fp = build_estimates.build(root)
        ck("empty: only the header",
           fp.read_text(encoding="utf-8") == ",".join(build_estimates.HEADER) + "\n")
        ck("empty: the file sits at the store root and is a csv",
           fp.name == "estimates.csv" and fp.parent == Path(root))
        ck("empty: no temp file is left behind",
           not (Path(root) / "estimates.csv.tmp").exists())


def main() -> int:
    for fn in (test_columns_and_row_selection, test_regeneration_is_byte_identical,
               test_an_auto_closed_measurement_does_not_reach_the_csv,
               test_an_empty_store_still_writes_a_header):
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            ck(f"{fn.__name__} (raised): {type(exc).__name__}: {exc}", False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
