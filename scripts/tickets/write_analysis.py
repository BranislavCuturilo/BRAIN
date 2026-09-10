#!/usr/bin/env python3
"""Persist an AI-triage analysis into ONE ticket's `analysis` block — the write
half of `/brain:tickets triage`.

The ticket-reader agent READS and returns a reading; it does not write. This
script does the write, through the SAME store lock + rev as every other
tiketi.json writer, so a triage write can never clobber a concurrent browser
triage edit or a sync. The analysis JSON object is read from stdin and MERGED
into `tickets[<id>].analysis` (provided keys win; others are kept).

  echo '{"means":"...","plan":["..."],"suggested_agents":["dj-service"]}' \
    | write_analysis.py --project popis --id 94313
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run(root, project, ticket_id, analysis: dict, key: str = "analysis",
        replace: bool = False) -> str:
    """Write `analysis` into tickets[<id>][key] under the store lock, bumping rev.
    `key="analysis"` (default) is the triage block, `key="reading"` the reader's
    "predlog upita" block. Merge by default (provided keys win, others kept);
    `replace=True` overwrites the whole block (a re-read replaces the old one)."""
    fp = store.resolve(root, project)
    if fp is None or not fp.is_file():
        raise SystemExit(f"no tiketi.json for project {project!r} (sync first)")
    tid = str(ticket_id)
    with store.filelock(fp):
        d = store.load(fp)
        if not isinstance(d, dict):
            raise SystemExit("tiketi.json is unreadable")
        tickets = d.get("tickets")
        if not isinstance(tickets, dict) or tid not in tickets:
            raise SystemExit(f"ticket {tid} not in {project}")
        t = tickets[tid]
        cur = t.get(key) if (isinstance(t.get(key), dict) and not replace) else {}
        cur.update(analysis)                     # merge: provided keys win
        cur["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        t[key] = cur
        d["rev"] = (d.get("rev") if isinstance(d.get("rev"), int) else 0) + 1
        store.atomic_write_json(fp, d)
        return f"{key} written to {project}#{tid} (rev {d['rev']})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("TICKETS_STORE") or str(store.default_store()))
    ap.add_argument("--module", required=True, dest="project", help="module key = <MODULE>.json")
    ap.add_argument("--id", required=True, dest="ticket_id")
    args = ap.parse_args()

    raw = sys.stdin.read()
    try:
        analysis = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        print(f"analysis stdin is not JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(analysis, dict):
        print("analysis must be a JSON object", file=sys.stderr)
        return 2

    print(run(args.root, args.project, args.ticket_id, analysis))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
