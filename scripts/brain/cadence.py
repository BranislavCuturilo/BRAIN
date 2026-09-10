#!/usr/bin/env python3
"""The periodic reviews, and whether any of them is overdue.

**Why a ledger and not a calendar.** `ops-maintain` describes a monthly cycle,
`ops-scoring` a periodic score review, `brain-keeper` an audit "run
periodically". None of them says when it last happened, so in practice each one
runs when something feels wrong -- which is after the cost, not before it.

**Why it reports through health.py rather than nagging on its own.** A reminder
that fires every session is noise, and noise is why the fourteen reminder hooks
produced zero skill invocations in a day. This is silent until something is
genuinely overdue, and then it appears exactly where the session-start check
already looks.

Recording is one command, and it is the only manual step:

  cadence.py                     what is due
  cadence.py did brain-keeper    record that it just ran
  cadence.py --json              for the dashboard
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER = ROOT / "journal" / "cadence.json"

#: name -> (days, what it is, how to run it)
REVIEWS = {
    "brain-keeper": (
        30, "audit the brain for duplication, contradiction and stale references",
        "Agent: brain-keeper"),
    "score-review": (
        30, "which skills and agents earned their place; what to stop preloading",
        "python scripts/brain/score.py review"),
    "delegation": (
        14, "is it still a queue -- fan-out width, nesting, agents never chosen",
        "python scripts/brain/delegation.py"),
    "coverage": (
        30, "agents nothing names -- reachability when the case finally arrives",
        "python scripts/brain/coverage.py --weak"),
    "outcomes": (
        14, "rework rate, tickets closed without a resolution, what produced a rule",
        "python scripts/brain/outcomes.py"),
    "chain-gaps": (
        14, "closed tickets whose chain of artefacts is broken",
        "python scripts/tickets/chain.py --gaps --done"),
    "evals-behaviour": (
        30, "the paid tier: does a rule still change the OUTCOME, not just fire",
        "python scripts/brain/evals.py --behaviour"),
    "upstream": (
        21, "what Claude Code shipped that a skill was working around",
        "python scripts/brain/upstream.py"),
    "ebr-review": (
        14, "what the extension's tickets say about docs/pages: lies, gaps, what to write next",
        "python scripts/tickets/ebr_review.py"),
    "ops-maintain": (
        90, "the full periodic cycle",
        "/brain:ops-maintain"),
}


def load() -> dict:
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def save(data: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def due() -> list[tuple[str, int | None, int, str, str]]:
    """[(name, days_since_or_None, period, what, how)] for everything overdue."""
    data = load()
    today = date.today()
    out = []
    for name, (period, what, how) in REVIEWS.items():
        last = data.get(name, {}).get("at")
        if last:
            try:
                age = (today - datetime.fromisoformat(str(last)[:10]).date()).days
            except ValueError:
                age = None
        else:
            age = None
        if age is None or age >= period:
            out.append((name, age, period, what, how))
    return out


def main() -> int:
    args = sys.argv[1:]

    if args and args[0] == "did":
        if len(args) < 2 or args[1] not in REVIEWS:
            print(f"which one? {', '.join(sorted(REVIEWS))}")
            return 1
        data = load()
        data[args[1]] = {"at": date.today().isoformat()}
        save(data)
        print(f"recorded: {args[1]} ran {date.today().isoformat()}")
        return 0

    overdue = due()
    if "--json" in args:
        print(json.dumps([{"name": n, "age_days": a, "period_days": p}
                          for n, a, p, _w, _h in overdue], indent=2))
        return 0

    if not overdue:
        data = load()
        print("Nothing due. Last run:")
        for name in sorted(REVIEWS):
            print(f"  {name:<18} {data.get(name, {}).get('at', 'never')}")
        return 0

    print(f"{len(overdue)} review(s) due:\n")
    for name, age, period, what, how in overdue:
        when = "never run" if age is None else f"{age}d ago (every {period}d)"
        print(f"  {name:<18} {when}")
        print(f"    {what}")
        print(f"    {how}\n")
    print("Record one after running it:  python scripts/brain/cadence.py did <name>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
