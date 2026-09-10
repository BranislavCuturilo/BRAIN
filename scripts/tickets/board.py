#!/usr/bin/env python3
"""What is in the helpdesk queue, across every project, in one command.

Answers "what should I work on" without an agent opening five tiketi.json
files and reasoning about them. The agent reads this output and decides;
it does not do the counting.

  board.py                     every project, active work only
  board.py --root C:/projects     scan somewhere else
  board.py --all               include done and skipped
  board.py --project popis     one project
  board.py --json              machine-readable, for another script

Exit 1 if no queue file was found at all (wrong root, most likely).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402  — queue_status: the view's triage.state maps here

# Ticket titles are Serbian. The Windows console is cp1252 and cannot encode
# c/c/s/z/d with diacritics -- and the encoder raises MID-PRINT, so half a
# board would render and the rest would vanish behind a traceback. Force a
# tolerant stdout before printing anything.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MARK = {"active": "*", "skip_session": "o", "skip_consider": "~", "done": "+"}
RANK = {"critical": 0, "major": 1, "minor": 2}
HIDDEN = {"done", "skip_session"}


def find(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("*.json") if store.is_module_file(p))


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ! unreadable: {path} ({exc.__class__.__name__})")
        return None


def rows(data: dict, show_all: bool) -> list[dict]:
    out = []
    for tid, t in (data.get("tickets") or {}).items():
        status = store.queue_status(t)      # view's triage.state wins over legacy status
        if not show_all and status in HIDDEN:
            continue
        out.append({
            "id": tid,
            "title": (t.get("title") or t.get("original", {}).get("title") or "").strip(),
            "priority": (t.get("priority") or "minor").lower(),
            "status": status,
            "customer": (t.get("original", {}) or {}).get("customer", ""),
            "waiting": (t.get("consider_for") or "").strip(),
        })
    out.sort(key=lambda r: (RANK.get(r["priority"], 3), r["id"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.environ.get("TICKETS_STORE") or str(store.default_store()))
    ap.add_argument("--project", help="filter to one module key (file stem)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    root = Path(args.root)
    files = find(root)
    if args.project:
        files = [p for p in files if p.stem.lower() == args.project.lower()]
    if not files:
        print(f"no tiketi.json found under {root}", file=sys.stderr)
        return 1

    collected, totals = {}, {"critical": 0, "major": 0, "minor": 0}
    for path in files:
        data = load(path)
        if data is None:
            continue
        name = (data.get("project") or {}).get("name") or path.stem
        items = rows(data, args.all)
        collected[name] = {
            "path": str(path),
            "last_sync": (data.get("project") or {}).get("last_sync"),
            "tickets": items,
        }
        for r in items:
            if r["status"] == "active":
                totals[r["priority"]] = totals.get(r["priority"], 0) + 1

    if args.as_json:
        print(json.dumps(collected, indent=2, ensure_ascii=False))
        return 0

    print("=" * 74)
    print(f"HELPDESK BOARD   critical {totals['critical']} | "
          f"major {totals['major']} | minor {totals['minor']}   "
          f"({len(collected)} projects)")
    print("=" * 74)

    for name, block in sorted(collected.items()):
        if not block["tickets"]:
            continue
        print(f"\n{name}   (synced {block['last_sync'] or 'never'})")
        for r in block["tickets"]:
            title = r["title"][:52]
            who = f"  <{r['customer'][:22]}>" if r["customer"] else ""
            wait = f"  -> {r['waiting'][:34]}" if r["waiting"] else ""
            print(f"  {MARK.get(r['status'], '?')} {r['priority'][:4]:<5} "
                  f"#{r['id']:<7} {title:<52}{who}{wait}")

    empty = [n for n, b in collected.items() if not b["tickets"]]
    if empty:
        print(f"\nnothing open: {', '.join(sorted(empty))}")
    print("\nlegend: * active  ~ waiting  o skipped today  + done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
