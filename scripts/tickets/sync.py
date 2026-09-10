#!/usr/bin/env python3
"""Deterministic helpdesk pull -> <repo>/.claude/tiketi.json.

No model tokens: plain urllib + a file merge, run by the `/tickets-pull`
command. Pulls the engineer's active tickets for one project, fetches each
ticket's detail (description + comments), and MERGES into the queue file:

  * source layers  (original on first sight, comments/url/priority/helpdesk)
    are refreshed from the pull;
  * human layers   (title, status, notes, consider_for, triage, outbox) are
    preserved untouched;
  * a ticket that is in the queue here but NO LONGER active on the helpdesk was
    closed there — it is closed here too (status done, helpdesk.is_closed), so
    the view stops showing it. Only when the module id resolved: an unfiltered
    pull is not evidence of anything being closed.

The reverse direction — a ticket the human closed in triage (solved_manually /
nonsense / done) that is STILL active on the helpdesk — is `close_out()`: it
posts the outbox and closes it on the helpdesk through writeback.py, which
holds the irreversibility rules. `rescan_all()` runs both for every module and
is what the view's Rescan button and `sync.py --all` call.

  sync.py --project popis                 # pull popis's module into its file
  sync.py --project popis --module POPIS  # force the module name
  sync.py --project popis --dry-run       # show what WOULD change, write nothing

Credentials come from the environment (HELPDESK_URL / HELPDESK_TOKEN).
Exit non-zero on a configuration or network failure so the command surfaces it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))               # store.py + adapters/ live beside us

import rounds                                # noqa: E402  the reopen ledger owner
import store                                 # noqa: E402
from adapters import get_adapter             # noqa: E402
# From the REGISTRY, never from one adapter. Importing the concrete adapter's
# exception made the whole pipeline depend on that adapter existing: a tree
# built without it could not import sync.py at all, and five test files that
# never mention an adapter went red.
from adapters import TicketSourceError as HelpdeskError  # noqa: E402

# Ticket titles are Serbian; the Windows console is cp1252 and raises MID-PRINT
# on c/c/s/z/d with diacritics. Force a tolerant stdout before printing (same
# guard as board.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def skeleton(module: str, repo: str) -> dict:
    """A fresh <MODULE>.json for a module synced for the first time."""
    return {
        "project": {
            "name": module,
            "repo": repo or "",          # where this module's work happens
            "url": "",
            "helpdesk_module": module,
            "last_sync": None,
        },
        "statuses": {},
        "tickets": {},
        "rev": 0,
    }


def merge(existing: dict, records: list) -> tuple[dict, dict]:
    """Merge pulled records into `existing`, preserving every human layer.
    Returns (existing, {"added": [...], "updated": [...], "reopened": [...]})."""
    tickets = existing.setdefault("tickets", {})
    added, updated, reopened = [], [], []
    for rec in records:
        tid = rec.get("id")
        if not tid:
            continue
        if isinstance(tickets.get(tid), dict):
            t = tickets[tid]
            t.setdefault("original", rec["original"])   # evidence: set once
            t["comments"] = rec["comments"]              # the point of a re-sync
            t["url"] = rec["url"]
            # Reopen detection runs on OLD-vs-FRESH, BEFORE the helpdesk block is
            # overwritten: the OLD block still carries round-1's closed_at (the
            # trigger anchor) and is_closed=True, the FRESH record says is_closed
            # =False. open_round seeds the ledger and clears t["closed"].
            if rounds.detect_reopen(t, rec) and not rounds.is_reopened_open(t):
                rounds.open_round(t, rounds.pick_trigger(t, rec))
                reopened.append(tid)
            t["helpdesk"] = rec["helpdesk"]
            if rec["helpdesk"].get("priority"):          # customer's urgency
                t["priority"] = rec["helpdesk"]["priority"]
            updated.append(tid)
        else:
            tickets[tid] = {
                "title": rec["original"]["title"],       # seed; the human edits
                "priority": rec["helpdesk"].get("priority") or "minor",
                "status": "active",                       # enters the queue
                "notes": "",
                "consider_for": "",
                "original": rec["original"],
                "comments": rec["comments"],
                "url": rec["url"],
                "helpdesk": rec["helpdesk"],
            }
            added.append(tid)
    return existing, {"added": added, "updated": updated, "reopened": reopened}


def _closed_stamp(t: dict, by: str, resolution: str = "") -> None:
    """Mark one ticket closed IN THE STORE (not on the helpdesk): every reader
    of the queue — legacy `status`, the view's `triage.state`, the raw
    `helpdesk` block — must agree, or the ticket keeps showing somewhere."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    t["status"] = "done"
    tri = t.get("triage") if isinstance(t.get("triage"), dict) else {}
    tri["state"] = "done"
    tri["updated_at"] = now
    t["triage"] = tri
    hd = t.get("helpdesk") if isinstance(t.get("helpdesk"), dict) else {}
    hd["is_closed"] = True
    t["helpdesk"] = hd
    if not isinstance(t.get("closed"), dict):
        t["closed"] = {"at": now, "by": by, "resolution": resolution}


def close_missing(existing: dict, records: list) -> list:
    """Tickets active HERE that the (module-filtered) pull did not return were
    closed on the helpdesk → close them here. Returns the ids closed."""
    live = {str(r.get("id")) for r in records if r.get("id")}
    closed = []
    for tid, t in (existing.get("tickets") or {}).items():
        if not isinstance(t, dict) or str(tid) in live:
            continue
        if rounds.is_reopened_open(t):
            # An OPEN reopen round whose ticket vanished from the (module-filtered)
            # active pull was closed AGAIN on the helpdesk by the customer — the
            # branch-A self-close. Stamp the round closed (which sets status/
            # triage.state/helpdesk.is_closed the same multi-reader way) so the
            # card leaves the lane instead of ghosting there.
            rounds.close_current_round(t, resolution=None, by="helpdesk")
            closed.append(str(tid))
            continue
        if store.queue_status(t) == "done" or (t.get("helpdesk") or {}).get("is_closed"):
            continue                                 # already out of the queue
        _closed_stamp(t, by="helpdesk")
        closed.append(str(tid))
    return closed


def _resolution_for(t: dict) -> str:
    """The CUSTOMER-FACING close text: `triage.resolution` ONLY. `triage.report`
    is the engineer's internal record (commits, files, open items) and `context`
    is the operator's note to the AI — neither may reach the helpdesk (2026-08-19:
    #08597 and #93164 were closed with the report, a changelog dump, because this
    used to fall back to it). Empty string when nothing customer-facing was
    written — the caller then does NOT close and says so."""
    tri = t.get("triage") if isinstance(t.get("triage"), dict) else {}
    v = str(tri.get("resolution") or "").strip()
    return v[:2000]


def close_out(root, module, records: list, adapter, dry_run=False) -> dict:
    """The REVERSE direction: tickets the human closed in triage that are still
    active on the helpdesk get closed THERE (outbox posted first) — through
    writeback.run, which owns the claim/ambiguity rules. Returns
    {"closed": [...], "failed": [{"id", "error"}], "would_close": [...]}."""
    import writeback                                 # sibling module
    fp = store.resolve(root, module)
    d = store.load(fp) or {}
    live = {str(r.get("id")) for r in records if r.get("id")}
    out = {"closed": [], "failed": [], "would_close": [], "needs_resolution": []}
    for tid, t in (d.get("tickets") or {}).items():
        if not isinstance(t, dict) or str(tid) not in live:
            continue
        if store.queue_status(t) != "done":
            continue                                 # still open here too
        if t.get("closing_at"):
            continue                                 # a close is claimed / in flight
        reopened_b = False
        if rounds.is_reopened_open(t):
            # A ticket in an OPEN reopen round. ONLY a branch-B ticket the operator
            # has re-solved may auto-close, through the SAME writeback door as any
            # other close. Branch A (customer self-closes) and C (waiting on a fix)
            # NEVER auto-close here; an unclassified round is left for the operator.
            # The fresh-resolution gate below is what R2 needs: open_round cleared
            # triage.resolution, so a non-empty one now was written THIS round.
            if (rounds.current_round(t) or {}).get("branch") != "B":
                continue
            reopened_b = True
        elif isinstance(t.get("closed"), dict):
            continue                                 # already closed (non-reopened)
        resolution = _resolution_for(t)
        if not resolution:
            # Done here, but nobody wrote what the CUSTOMER should read. Leave it
            # open on the helpdesk and surface it - never close with a default or
            # with the internal report.
            out["needs_resolution"].append(str(tid))
            continue
        if dry_run:
            out["would_close"].append(str(tid))
            continue
        try:
            rep = writeback.run(root, module, tid, close_resolution=resolution,
                                adapter=adapter)
        except SystemExit as exc:                    # writeback's own refusals
            out["failed"].append({"id": str(tid), "error": str(exc)})
            continue
        if rep.get("closed") or rep.get("already_closed"):
            out["closed"].append(str(tid))
            if reopened_b:
                # writeback closed on the helpdesk and re-stamped t["closed"], but
                # it knows nothing of the ledger — stamp the reopen round closed
                # under a fresh lock so is_reopened_open() goes False and the card
                # leaves the lane (else it re-closes as by:helpdesk next pass).
                _stamp_round_closed(fp, tid, resolution)
        else:
            out["failed"].append({"id": str(tid),
                                  "error": rep.get("close_error") or "comments not all posted"})
    return out


def _stamp_round_closed(fp, tid, resolution) -> None:
    """Close the current reopen round in the store after writeback re-closed the
    ticket on the helpdesk. A fresh lock+reload (writeback wrote under its own
    lock) keeps the ledger stamp from racing its close write."""
    with store.filelock(fp):
        d = store.load(fp)
        t = (d.get("tickets") or {}).get(str(tid)) if isinstance(d, dict) else None
        if not isinstance(t, dict):
            return
        rounds.close_current_round(t, resolution=resolution, by="operator")
        d["rev"] = (d.get("rev") if isinstance(d.get("rev"), int) else 0) + 1
        store.atomic_write_json(fp, d)


def _resolve_module_id(adapter, module_name: str):
    """The active-tickets filter wants a module ID; the project file carries a
    NAME. Returns (id_or_None, warning_or_None)."""
    if not module_name:
        return None, None
    try:
        for m in adapter.list_modules():
            if str(m.get("name", "")).lower() == module_name.lower():
                return m.get("id"), None
    except HelpdeskError as exc:
        return None, f"could not list modules ({exc}); pulling ALL active tickets"
    return None, (f"module {module_name!r} not found on the helpdesk; "
                  f"pulling ALL active tickets")


def pull_records(adapter, module_id) -> list:
    """List active tickets, then fetch each one's detail. The list shape is thin
    (no description/comments), so the detail call per ticket is required."""
    records = []
    for summary in adapter.list_active(module=module_id):
        tid = summary.get("ticket_id")
        if not tid:
            continue
        records.append(adapter.to_record(adapter.get_detail(tid)))
    return records


def run(root, module, repo=None, source="acme_helpdesk", dry_run=False):
    fp = store.resolve(root, module)
    if fp is None:
        raise SystemExit(f"invalid module key: {module!r}")

    existing = store.load(fp) or {}
    repo = repo or (existing.get("project") or {}).get("repo") or ""

    adapter = get_adapter(source)()          # reads env creds; may raise HelpdeskError
    module_id, warn = _resolve_module_id(adapter, module)  # the file key IS the module
    if warn:
        print(f"  ! {warn}")

    records = pull_records(adapter, module_id)   # NETWORK — no lock held here

    if dry_run:
        base = json.loads(json.dumps(existing or skeleton(module, repo)))
        _, report = merge(base, records)
        report["closed"] = close_missing(base, records) if module_id is not None else []
        report["records"] = records
        return report, len(records), True

    fp.parent.mkdir(parents=True, exist_ok=True)
    with store.filelock(fp):                 # re-read + write under the lock
        current = store.load(fp) or skeleton(module, repo)
        merged, report = merge(current, records)
        # absent from a MODULE-FILTERED pull = closed on the helpdesk. An
        # unfiltered pull (module id unresolved) proves nothing -> never close.
        report["closed"] = close_missing(merged, records) if module_id is not None else []
        report["records"] = records
        proj = merged.setdefault("project", {})
        proj["last_sync"] = time.strftime("%Y-%m-%d")
        proj["helpdesk_module"] = module
        if repo:
            proj["repo"] = store.repo_name(repo)   # machine-independent (PROJECTS_ROOT)
        merged["rev"] = (merged.get("rev") if isinstance(merged.get("rev"), int) else 0) + 1
        store.atomic_write_json(fp, merged)
    return report, len(records), False


def rescan_all(root=None, source="acme_helpdesk", dry_run=False, log=print) -> dict:
    """Both directions for EVERY module in the store — what the view's Rescan
    button runs before the Gemini triage:
      1. pull the active queue per module (new/updated tickets in);
      2. close here what the helpdesk closed;
      3. close on the helpdesk what was closed here in triage.
    Never raises for one module's failure — reports it and moves on. Raises
    HelpdeskError only when the adapter cannot be built (no credentials)."""
    root = Path(root or os.environ.get("TICKETS_STORE") or store.default_store())
    adapter = get_adapter(source)()             # raises HelpdeskError: no creds
    summary = {"modules": {}, "added": 0, "updated": 0, "closed_here": 0,
               "closed_helpdesk": 0, "reopened": 0, "needs_resolution": [], "failed": []}
    # The module set is the UNION of what we already track locally AND what the
    # helpdesk reports. Enumerating only local <MODULE>.json files never visits a
    # module whose first ticket just arrived (it has no file yet), so its queue is
    # never pulled -- discover from the source, don't enumerate the cache.
    local = {fp.stem for fp in root.glob("*.json") if store.is_module_file(fp)}
    try:
        discovered = {str(m.get("name")) for m in adapter.list_modules() if m.get("name")}
    except HelpdeskError as exc:
        discovered = set()                      # helpdesk unreachable -> local-only
        log(f"  ! module discovery skipped: {exc}")
    for module in sorted(local | discovered):
        try:
            report, pulled, _dry = run(str(root), module, None, source, dry_run)
            wb = close_out(str(root), module, report.get("records") or [], adapter, dry_run)
        except HelpdeskError as exc:
            summary["failed"].append({"module": module, "error": str(exc)})
            log(f"  ! {module}: {exc}")
            continue
        except SystemExit as exc:
            summary["failed"].append({"module": module, "error": str(exc)})
            continue
        m = {"pulled": pulled, "added": report["added"], "updated": report["updated"],
             "closed_here": report.get("closed", []),
             "closed_helpdesk": wb["closed"] + wb.get("would_close", []),
             "reopened": report.get("reopened", []),
             "needs_resolution": wb.get("needs_resolution", []),
             "failed": wb["failed"]}
        summary["modules"][module] = m
        summary["added"] += len(m["added"])
        summary["updated"] += len(m["updated"])
        summary["closed_here"] += len(m["closed_here"])
        summary["closed_helpdesk"] += len(m["closed_helpdesk"])
        summary["reopened"] += len(m["reopened"])
        summary["needs_resolution"] += [f"{module}#{t}" for t in m["needs_resolution"]]
        summary["failed"].extend({"module": module, **f} for f in wb["failed"])
        log(f"  {module}: pulled {pulled}, +{len(m['added'])} new, "
            f"~{len(m['updated'])} updated, closed here {len(m['closed_here'])}, "
            f"closed on helpdesk {len(m['closed_helpdesk'])}"
            + (f", reopened {len(m['reopened'])}" if m["reopened"] else "")
            + (f", FAILED {len(wb['failed'])}" if wb["failed"] else ""))
    # A close is what completes an estimate/measured PAIR, and both directions of
    # closing pass through the loop above (close_missing inside run(), close_out
    # after it) - so this is the one place that sees every close. The CSV is
    # derived and idempotent, so regenerating it here can only make it fresher.
    if not dry_run:
        try:
            import build_estimates                    # sibling module
            build_estimates.build(str(root))
        except (OSError, ValueError) as exc:
            log(f"  ! estimates.csv not rebuilt: {exc.__class__.__name__}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("TICKETS_STORE") or str(store.default_store()))
    ap.add_argument("--module", help="module key = <MODULE>.json in the store")
    ap.add_argument("--all", action="store_true",
                    help="every module in the store, both directions (what Rescan does)")
    ap.add_argument("--repo", help="repo path where this module's work happens (recorded once)")
    ap.add_argument("--source", default="acme_helpdesk")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = ap.parse_args()
    if not args.module and not args.all:
        ap.error("--module <KEY> or --all")

    try:
        if args.all:
            tag = " (dry-run, nothing written)" if args.dry_run else ""
            print(f"rescan: every module{tag}")
            summ = rescan_all(args.root, args.source, args.dry_run)
            print(f"total: +{summ['added']} new, ~{summ['updated']} updated, "
                  f"closed here {summ['closed_here']}, closed on helpdesk "
                  f"{summ['closed_helpdesk']}, reopened {summ['reopened']}, "
                  f"failed {len(summ['failed'])}")
            for f in summ["failed"]:
                print(f"  ! {f}")
            return 1 if summ["failed"] else 0
        report, pulled, dry = run(args.root, args.module, args.repo,
                                  args.source, args.dry_run)
    except HelpdeskError as exc:
        print(f"helpdesk not reachable: {exc}", file=sys.stderr)
        print("  set HELPDESK_URL and HELPDESK_TOKEN in the environment.",
              file=sys.stderr)
        return 2

    tag = "(dry-run, nothing written)" if dry else ""
    print(f"pulled {pulled} active ticket(s) {tag}")
    print(f"  + {len(report['added'])} new: {', '.join(report['added']) or '-'}")
    print(f"  ~ {len(report['updated'])} updated: {', '.join(report['updated']) or '-'}")
    print(f"  x {len(report['closed'])} closed on helpdesk: {', '.join(report['closed']) or '-'}")
    reop = report.get("reopened") or []
    print(f"  ^ {len(reop)} reopened: {', '.join(reop) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
