#!/usr/bin/env python3
"""Regenerate `<root>/estimates.csv` - the estimated-vs-actual table the hour
estimator calibrates against.

The file is DERIVED and never a source of truth: every column is read back out
of the module files (the estimate, the reading's scope/complexity, the close)
and the worklog (the measured hours), so deleting it loses nothing and a stale
copy is fixed by re-running this. That is the point - a ticket closed on the
other machine before its worklog was pulled would otherwise be permanently
half-recorded in a hand-maintained file.

    build_estimates.py [--root <tickets_store>]

Columns: module, ticket, complexity, scope, estimated_h, actual_h, estimated_at,
closed_at, by.

  * estimated_h - `triage.ai_estimate.hours` when this brain estimated the
    ticket, else the helpdesk's own `estimated_time`; empty when neither exists.
  * actual_h    - summed COMPLETE worklog intervals (auto-closed ones excluded;
    see worklog.hours_by_ticket). Empty when the ticket was never measured.
  * closed_at   - `closed.at`, or the literal "closed" when the helpdesk says
    the ticket is closed but no local timestamp was recorded.
  * by          - who produced the estimate: `ai_estimate.by`, or "helpdesk".

A row exists only for a ticket that has an estimate OR a measurement; a ticket
with neither teaches the calibration nothing and would only dilute the file.

Idempotent by construction: the same store always produces byte-identical
output (sorted rows, fixed 2-decimal numbers, `\\n` line endings), so a
regeneration that changed nothing shows up as no git diff. Written atomically
(temp + os.replace) like every other store file; no lock is needed because two
concurrent runs write the same bytes.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import store  # noqa: E402
import worklog  # noqa: E402

FILE_NAME = "estimates.csv"
HEADER = ("module", "ticket", "complexity", "scope", "estimated_h", "raw_h", "actual_h",
          "estimated_at", "closed_at", "by")
TEXT_CHARS = 60          # complexity/scope are labels, not prose


def default_root() -> Path:
    return Path(os.environ.get("TICKETS_STORE") or store.default_store())


def _num(value) -> str:
    """Fixed 2 decimals, or empty. One formatting rule so a re-run of the same
    store cannot produce a different byte."""
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def _label(value) -> str:
    return " ".join(str(value or "").split())[:TEXT_CHARS]


def reading_label(t: dict, key: str) -> str:
    """`reading` is the current reader's output and `analysis` the older block;
    the fresher one wins, and a ticket carrying neither yields "".

    Public because the hour estimator reads the SAME scope/complexity labels for
    its prompt and its calibration buckets; two copies of this precedence would
    put a ticket in one bucket here and another there."""
    for block in (t.get("reading"), t.get("analysis")):
        if isinstance(block, dict):
            v = block.get(key)
            if v not in (None, ""):
                return _label(v)
    return ""


def has_estimate(raw) -> bool:
    """True when a helpdesk `estimated_time` value carries a REAL estimate.

    The helpdesk spells "no estimate" as null OR 0, and its API serialises a
    DecimalField(6,2), so the value arrives as the STRING "0.00" as often as the
    number 0 — a plain truthiness test would treat "0.00" as estimated. Anything
    that will not parse as a number counts AS an estimate: unreadable means do
    not overwrite (fail closed). The ONE definition; estimate.py imports it."""
    if raw is None or raw == "":
        return False
    try:
        return float(raw) > 0
    except (TypeError, ValueError):
        return True


def _estimate(t: dict):
    """(written_hours, raw_model_hours|None, at, by) from the local AI estimate,
    else the helpdesk's own field. `raw` is the model's UNCALIBRATED number
    (ai_estimate.raw) — calibration must learn actual/raw, never actual/written,
    or the loop corrects an already-corrected number and converges to the square
    root of the needed factor (reviewer 2026-08-19). Returns (None, None, "", "")
    when the ticket has no estimate at all."""
    tri = t.get("triage") if isinstance(t.get("triage"), dict) else {}
    ai = tri.get("ai_estimate") if isinstance(tri.get("ai_estimate"), dict) else None
    if ai and ai.get("hours") not in (None, ""):
        return (ai.get("hours"), ai.get("raw"),
                str(ai.get("at") or ""), str(ai.get("by") or ""))
    hd = t.get("helpdesk") if isinstance(t.get("helpdesk"), dict) else {}
    if has_estimate(hd.get("estimated_time")):
        return hd.get("estimated_time"), None, "", "helpdesk"
    return None, None, "", ""


def _closed_at(t: dict) -> str:
    closed = t.get("closed") if isinstance(t.get("closed"), dict) else None
    if closed and closed.get("at"):
        return str(closed.get("at"))
    hd = t.get("helpdesk") if isinstance(t.get("helpdesk"), dict) else {}
    return "closed" if hd.get("is_closed") else ""


def _sort_key(row):
    """Numeric ticket ids sort as numbers, anything else after them as text -
    both branches carry a leading int so the tuples stay comparable."""
    module, ticket = row[0], row[1]
    return (module, (0, int(ticket), "") if ticket.isdigit() else (1, 0, ticket))


def rows(root) -> list:
    """The CSV rows (no header) as lists, WITHOUT writing anything. Public
    because the HUD's read-only estimates route needs the same table on a GET,
    and a GET must never write the file."""
    root = Path(root)
    hours = worklog.hours_by_ticket(root)
    out = []
    for fp in sorted(root.glob("*.json")):        # top level only: the module files
        if not store.is_module_file(fp):
            continue
        d = store.load(fp)
        tickets = d.get("tickets") if isinstance(d, dict) else None
        if not isinstance(tickets, dict):
            continue
        module = fp.stem
        for tid, t in tickets.items():
            if not isinstance(t, dict):
                continue
            ticket = str(tid)
            est, raw, est_at, by = _estimate(t)
            actual = hours.get((module, ticket))
            if est is None and actual is None:
                continue                          # nothing to learn from
            out.append([module, ticket, reading_label(t, "complexity"),
                        reading_label(t, "scope"), _num(est), _num(raw), _num(actual),
                        est_at, _closed_at(t), by])
    out.sort(key=_sort_key)
    return out


def build(root=None) -> Path:
    """Write `<root>/estimates.csv` and return its path."""
    root = Path(root or default_root())
    table = rows(root)
    fp = root / FILE_NAME
    tmp = fp.with_name(fp.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(HEADER)
        w.writerows(table)
    os.replace(tmp, fp)
    return fp


def main() -> int:
    ap = argparse.ArgumentParser(description="regenerate estimates.csv from the store + worklog")
    ap.add_argument("--root", default=str(default_root()))
    args = ap.parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(f"no such tickets store: {root}", file=sys.stderr)
        return 2
    fp = build(root)
    rows = max(fp.read_text(encoding="utf-8").count("\n") - 1, 0)   # minus the header
    print(f"wrote {fp} ({rows} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
