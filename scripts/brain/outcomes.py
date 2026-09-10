#!/usr/bin/env python3
"""What the work actually produced -- measured from records that already exist.

**The hole this fills.** `score.py` asks whether a SKILL helped, and the answer
is typed in by hand. `usage.py` counts how often something was invoked. Neither
says whether the WORK went well: whether a ticket came back, whether the plan
survived contact, whether anything was learned from it.

Nothing here is recorded on purpose. Every number comes from a file that is
written as a side effect of working:

  tickets_store/<MODULE>.json   the analysis, the plan, the close, the reopen
  tickets_store/worklog/*.jsonl start/touch events, so elapsed time is real
  git log                        `capture:` commits and `Brain-Agents:` trailers

That matters more than the numbers themselves. A metric that needs a step to
maintain it stops being maintained on the first busy week, and then reads as
zero rather than as unknown.

**Leading** indicators predict; **lagging** ones report. Both are shown, because
a queue that is analysed fast and reopened constantly is not a queue that is
going well, and either number alone says it is.

  outcomes.py              the report
  outcomes.py --json       machine-readable, for the dashboard
  outcomes.py --module VEZ one module only
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
STORE = ROOT / "tickets_store"
SKIP_FILES = {"modules.json", "ai_log.json", "estimates.csv"}


def _dt(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for cut in (None, 19, 10):
        try:
            return datetime.fromisoformat(text if cut is None else text[:cut])
        except ValueError:
            continue
    return None


def _hours(a, b) -> float | None:
    da, db = _dt(a), _dt(b)
    if not da or not db:
        return None
    # Mixed awareness is normal here: the helpdesk stamps with a zone, the
    # local writer does not. Compare naively rather than dropping the pair.
    if (da.tzinfo is None) != (db.tzinfo is None):
        da, db = da.replace(tzinfo=None), db.replace(tzinfo=None)
    delta = (db - da).total_seconds() / 3600
    return delta if delta >= 0 else None


def load_tickets(only: str | None) -> list[tuple[str, str, dict]]:
    out = []
    for path in sorted(STORE.glob("*.json")):
        if path.name in SKIP_FILES:
            continue
        module = path.stem
        if only and module.upper() != only.upper():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for tid, t in (data.get("tickets") or {}).items():
            if isinstance(t, dict):
                out.append((module, tid, t))
    return out


def load_worklog() -> dict[str, list[dict]]:
    """ticket id -> its events, so elapsed time is measured and not estimated."""
    by_ticket: dict[str, list[dict]] = {}
    ids: dict[str, str] = {}
    for path in sorted((STORE / "worklog").glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            wid = rec.get("work_id")
            if rec.get("ev") == "start" and wid and rec.get("ticket"):
                ids[wid] = str(rec["ticket"])
            tid = rec.get("ticket") or ids.get(wid)
            if tid:
                by_ticket.setdefault(str(tid), []).append(rec)
    return by_ticket


def git_log(n: int = 400) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", f"-{n}", "--format=%s%n%b%n<<>>"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [c.strip() for c in out.split("<<>>") if c.strip()]


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def collect(only: str | None) -> dict:
    tickets = load_tickets(only)
    work = load_worklog()
    commits = git_log()

    total = len(tickets)
    analysed = [t for _m, _i, t in tickets if (t.get("analysis") or {}).get("plan")]
    closed = [(m, i, t) for m, i, t in tickets if t.get("closed")]
    done = [t for _m, _i, t in tickets if t.get("status") == "done"]

    # --- leading -----------------------------------------------------------
    to_analysis, to_close, worked_hours = [], [], []
    for _m, tid, t in tickets:
        a, o = t.get("analysis") or {}, t.get("original") or {}
        h = _hours(o.get("created"), a.get("updated_at"))
        if h is not None:
            to_analysis.append(h)
        c = t.get("closed") or {}
        h = _hours(a.get("updated_at"), c.get("at") if isinstance(c, dict) else None)
        if h is not None:
            to_close.append(h)
        evs = work.get(str(tid)) or []
        stamps = sorted(x for x in (_dt(e.get("at")) for e in evs) if x)
        if len(stamps) >= 2:
            worked_hours.append((stamps[-1] - stamps[0]).total_seconds() / 3600)

    by_brain: dict[str, int] = {}
    for _m, _i, t in tickets:
        a = t.get("analysis") or {}
        if a.get("plan"):
            by_brain[a.get("by") or "unrecorded"] = by_brain.get(a.get("by") or "unrecorded", 0) + 1

    # --- lagging -----------------------------------------------------------
    reopened = [(m, i) for m, i, t in tickets if t.get("reopened") or t.get("reopen")]

    # Resolution discipline: a `done` ticket with no customer-facing resolution
    # stays OPEN on the helpdesk. The store already reports this as
    # `needs_resolution`; counting it makes the backlog visible before the sync.
    missing_resolution = [
        (m, i) for m, i, t in tickets
        if t.get("status") == "done"
        and not ((t.get("triage") or {}).get("resolution") or "").strip()
        and not ((t.get("closed") or {}).get("resolution") or "").strip()
        if isinstance(t.get("closed") or {}, dict)
    ]

    # Plan adherence: did the work use the agents the analysis named? The
    # commit trailers are the only record that survives; a ticket with neither
    # is unknown, not zero -- and is reported as such.
    ticket_ids = {str(i) for _m, i, _t in tickets}
    capture_commits = [c for c in commits if re.match(r"^capture[(:]", c)]
    trailered = [c for c in commits if "Brain-Agents:" in c]
    tickets_in_captures = {
        tid for c in capture_commits for tid in re.findall(r"#(\d{4,6})", c)
    } & ticket_ids

    return {
        "module": only or "all",
        "tickets": total,
        "leading": {
            "analysed": len(analysed),
            "analysis_coverage": round(100 * len(analysed) / total, 1) if total else None,
            "median_h_to_analysis": median(to_analysis),
            "analysed_by": by_brain,
        },
        "lagging": {
            "closed": len(closed),
            "done": len(done),
            "median_h_analysis_to_close": median(to_close),
            "median_h_worked": median(worked_hours),
            "reopened": len(reopened),
            "reopen_rate": round(100 * len(reopened) / len(closed), 1) if closed else None,
            "missing_resolution": len(missing_resolution),
            "capture_commits": len(capture_commits),
            "tickets_that_produced_a_rule": len(tickets_in_captures),
            "capture_rate": round(100 * len(tickets_in_captures) / len(closed), 1) if closed else None,
            "commits_with_agent_trailers": len(trailered),
        },
        "_detail": {
            "reopened": reopened[:10],
            "missing_resolution": missing_resolution[:10],
        },
    }


def main() -> int:
    only = None
    if "--module" in sys.argv:
        i = sys.argv.index("--module")
        only = sys.argv[i + 1] if i + 1 < len(sys.argv) else None

    if not STORE.is_dir():
        print("no tickets_store/ -- nothing to measure")
        return 0

    r = collect(only)
    if "--json" in sys.argv:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0

    lead, lag = r["leading"], r["lagging"]

    def num(v, unit=""):
        return "—" if v is None else f"{v}{unit}"

    print("=" * 68)
    print(f"OUTCOMES  ({r['module']}, {r['tickets']} tickets)")
    print("=" * 68)
    print("\nLEADING -- these predict, and you can still change them\n")
    print(f"  analysis before work        {lead['analysed']}/{r['tickets']}"
          f"  ({num(lead['analysis_coverage'], '%')})")
    print(f"  median time to analysis     {num(lead['median_h_to_analysis'], ' h')}")
    print(f"  analysed by                 "
          f"{', '.join(f'{k} {v}' for k, v in sorted(lead['analysed_by'].items())) or '—'}")

    print("\nLAGGING -- these report what already happened\n")
    print(f"  closed                      {lag['closed']}   (done locally: {lag['done']})")
    print(f"  median analysis -> close    {num(lag['median_h_analysis_to_close'], ' h')}")
    print(f"  median hours actually on it {num(lag['median_h_worked'], ' h')}")
    print(f"  REWORK: reopened            {lag['reopened']}   ({num(lag['reopen_rate'], '%')} of closed)")
    print(f"  done without a resolution   {lag['missing_resolution']}"
          f"   (these stay OPEN on the helpdesk)")
    print(f"  produced a durable rule     {lag['tickets_that_produced_a_rule']}"
          f"   ({num(lag['capture_rate'], '%')} of closed, {lag['capture_commits']} capture commits)")
    print(f"  commits naming their agents {lag['commits_with_agent_trailers']}")

    for label, rows in (("reopened", r["_detail"]["reopened"]),
                        ("done with no resolution", r["_detail"]["missing_resolution"])):
        if rows:
            print(f"\n  {label}: " + ", ".join(f"{m}#{i}" for m, i in rows))

    print("\nA number here is only worth acting on next to its pair: fast analysis")
    print("with a rising reopen rate is not speed, it is rework being paid later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
