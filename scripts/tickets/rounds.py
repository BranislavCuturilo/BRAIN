#!/usr/bin/env python3
"""The `rounds` ledger — the ONE owner of a ticket's reopen history.

A ticket the operator closed (with a resolution) can be REOPENED by an admin or
the client on the helpdesk (its status flips back to Active and a "dopuna"
comment arrives). This module is where that reopen is DETECTED and where the
durable, append-only `rounds` ledger — the record of every close→reopen→re-close
cycle — is written. `sync.py` calls the detection/open/close helpers during a
pull; a launched Claude session calls the `verdict` CLI door (mirroring
`write_analysis.py`) to classify a reopen into branch A/B/C.

Three keys, and every writer must agree on their shape (the data contract):

  * `t["rounds"]`   append-only ledger. Index 0 is the ORIGINAL close,
      reconstructed lazily at first reopen — `{closed_at, resolution, closed_by}`;
      every later element is a reopen round —
      `{reopened_at, trigger:{comment_id, author, author_role},
        branch: None|"A"|"B"|"C", resolution: None|str,
        closed_at: None|str, closed_by?}`.
  * `t["reopened"]` current-reopen marker `{at, by_role, trigger_comment_id}`.
      Lane membership = this marker present AND the current round is not closed.
  * `t["reopen"]`   the current round's AI working block (Gemini predlog; the
      Claude verdict: branch/confidence/legitimate/questions/suggested_reply).
      Ledger FACTS live in `rounds`; only prose lives here. Reset per round.

All three keys are additive and every reader is `.get`-tolerant: the store is
shared across machines, so an older reader meets these keys on a ticket a newer
one wrote, and a legacy ticket has none of them.

Two invariants make the round-aware re-close in `sync.close_out` safe:

  * OPENING A ROUND CLEARS `t["closed"]`. A reopened ticket is no longer closed
    in the store — the round-1 close facts are preserved in `rounds[0]`, so
    nothing is lost, and `writeback.run`'s already-closed guard (which reads
    `t["closed"]`) then lets the branch-B re-close actually fire instead of
    reporting a close that never happened.
  * CLOSING A ROUND RE-STAMPS `t["closed"]`. So the next reopen is detectable
    again (the LOCKED predicate reads `isinstance(t["closed"], dict)`), and every
    reader of the queue — `status`, `triage.state`, `helpdesk.is_closed` — agrees
    the ticket is done (the same multi-reader discipline `_closed_stamp` keeps).
  * OPENING A ROUND ALSO RESETS THE WORKFLOW STATE. `status` goes back to
    "active" and `triage.state` is cleared, so `store.queue_status` no longer
    reports the reopened ticket as done. This restores the SECOND re-close gate:
    the branch-B round-aware close in `sync.close_out` requires the operator to
    make a FRESH done-transition THIS round (not inherit round-1's leftover
    done state, which made the `queue_status=="done"` leg of the gate a tautology
    and could have sent an in-progress resolution note to the customer and closed
    the ticket irreversibly with no deliberate second gate).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: The three branches a reopen can be classified into (locked decision #2):
#: A — the customer self-closes on the helpdesk; B — a real fix, re-closes like a
#: normal solve; C — cannot reproduce, waits for the operator to re-branch to B.
VALID_BRANCHES = ("A", "B", "C")


def _now() -> str:
    """The store's naive-local timestamp — same format every other writer uses."""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------- #
#  Pure predicates over ONE ticket dict (tolerant of legacy tickets).
# --------------------------------------------------------------------------- #
def current_round(t: dict) -> dict | None:
    """The last element of `t["rounds"]`, or None for a ticket that has never
    carried a round (no `rounds` key, or an empty/non-list value)."""
    rounds = t.get("rounds") if isinstance(t, dict) else None
    return rounds[-1] if isinstance(rounds, list) and rounds else None


def is_reopened_open(t: dict) -> bool:
    """True iff this ticket is in an OPEN reopen round — the `reopened` marker is
    present AND the current round has not been closed. This is lane membership:
    the HUD renders exactly these tickets in the Vraćeni/Dopune lane, and the
    round-aware close paths key off it."""
    if not (isinstance(t, dict) and t.get("reopened")):
        return False
    cur = current_round(t)
    return bool(cur) and not cur.get("closed_at")


def detect_reopen(t: dict, fresh_rec: dict) -> bool:
    """The LOCKED reopen predicate (decision #1): a ticket we CLOSED (the top-level
    `closed` marker is a dict) whose FRESH pull says the helpdesk block is open
    again (`is_closed` is False).

    NOT `status:done` — in a full rescan the pull+merge runs before `close_out`
    in the same pass, so a ticket just marked done (round 1, awaiting write-back)
    has `status:done` but NO `closed` marker and a fresh block still
    `is_closed:false`; the loose predicate would false-positive on the very
    rescan about to close it. The `closed` marker covers both an operator close
    and a `close_missing` (by:helpdesk) close."""
    if not (isinstance(t, dict) and isinstance(t.get("closed"), dict)):
        return False
    hd = (fresh_rec or {}).get("helpdesk")
    if not isinstance(hd, dict):
        return False
    # Affirmatively open: `to_record` always writes a real bool, so a missing or
    # non-bool `is_closed` is NOT treated as reopened (that would false-positive
    # on a legacy/thin record that simply never carried the field).
    if hd.get("is_closed") is not False:
        return False
    # A reopen is the FAR SIDE saying the work is not done. Our OWN comment is
    # not that — and on this helpdesk posting one re-opens the ticket, so the
    # before/after pictures every visual ticket ships (the `visual-diff` flow)
    # would reopen the very ticket they are reporting as finished. Measured on
    # DEMO#76082: trigger comment 624, author_role "engineer", body "Slike
    # ekrana pre i posle izmene" — our own. The round that opened cleared
    # `triage.resolution`, reset the ticket to active so the close phase would
    # no longer close it, and queued a Gemini adjudication of our own message.
    #
    # Only comments POSTDATING the close count, and only from the far side. A
    # bare status flip with no new comment at all is still a genuine reopen (an
    # admin reopening without a word), so the suppression needs at least one
    # new comment, all of them ours.
    fresh = comments_after_close(t, fresh_rec)
    if fresh and all(is_own_comment(c) for c in fresh):
        return False
    return True


# --------------------------------------------------------------------------- #
#  Trigger-comment matching — which comment reopened the ticket.
# --------------------------------------------------------------------------- #
def parse_dt(value):
    """Parse an ISO-ish timestamp to a naive-UTC datetime, or None. Tolerant of a
    trailing 'Z', an explicit offset, a space instead of 'T', and a naive stamp.
    A value we cannot parse yields None and simply does not participate in the
    match — detection never depends on finding the trigger.

    EVERY return is naive-UTC so `pick_trigger` compares like-with-like. A naive
    stamp (the store's `closed.at`/re-stamped `helpdesk.closed_at` are naive
    LOCAL wall-clock) is interpreted in the machine's local zone and converted to
    UTC — never treated as-if-UTC against a tz-aware comment time, which would
    mis-order the trigger by the local UTC offset."""
    s = str(value or "").strip()
    if not s:
        return None
    s = s.replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.astimezone()          # pin naive local wall-clock to the local zone
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


#: The private name this module has always used internally. ONE implementation —
#: `analyze_gemini` needs the same tolerant parser to compare an analysis stamp
#: (naive local) against a comment time (tz-aware), and a second copy would drift
#: by exactly the local UTC offset.
_parse_dt = parse_dt


def _close_anchor(t: dict) -> str | None:
    """When round 1 closed — for seeding `rounds[0]` and dating the trigger.
    Prefer the API's tz-aware `helpdesk.closed_at` (captured in Phase 1) over the
    store's naive `closed.at`. Read at merge time, BEFORE the helpdesk block is
    overwritten, so `helpdesk.closed_at` here still carries round 1's close."""
    hd = t.get("helpdesk") if isinstance(t.get("helpdesk"), dict) else {}
    if hd.get("closed_at"):
        return hd.get("closed_at")
    closed = t.get("closed") if isinstance(t.get("closed"), dict) else {}
    return closed.get("at")


#: Comment roles that are OUR OWN side and therefore can never be a "dopuna".
#: `role_of` in adapters/acme_helpdesk.py labels a comment "engineer" when its
#: author is the ticket's assigned engineer; the far side arrives as "customer"
#: (the registered customer) or "other" (anyone else, which is how most of this
#: queue's real dopune actually land). ONE definition — the predicate and the
#: trigger matcher both read it, so they can never disagree about who reopened
#: a ticket.
OWN_SIDE_ROLES = ("engineer",)


def is_own_comment(c) -> bool:
    """True when WE wrote this comment. Unknown/missing role is NOT ours — a
    thin record must never silently suppress a real customer dopuna."""
    if not isinstance(c, dict):
        return False
    return str(c.get("author_role") or "").strip().lower() in OWN_SIDE_ROLES


def comments_after_close(t: dict, fresh_rec: dict) -> list:
    """Every FRESH comment posted after round 1's close, newest last. A comment
    whose timestamp will not parse is left out — the same tolerance
    `pick_trigger` has always had, and detection never depends on it."""
    anchor = _parse_dt(_close_anchor(t))
    out = []
    for c in ((fresh_rec or {}).get("comments") or []):
        if not isinstance(c, dict):
            continue
        cdt = _parse_dt(c.get("at"))
        if cdt is None:
            continue
        if anchor is not None and cdt <= anchor:
            continue
        out.append((cdt, c))
    out.sort(key=lambda pair: pair[0])
    return [c for _dt, c in out]


def pick_trigger(t: dict, fresh_rec: dict) -> dict | None:
    """The newest FRESH comment posted after the close anchor BY THE FAR SIDE —
    the reopen's "dopuna". Returns the comment dict (id/author/author_role/at/
    body) or None when nothing qualifies (nothing postdates the close, no
    timestamp parses, or every new comment is our own). tz is parsed
    tolerantly; the reopen marker stays valid with `trigger_comment_id: null`."""
    fresh = [c for c in comments_after_close(t, fresh_rec) if not is_own_comment(c)]
    return fresh[-1] if fresh else None


# --------------------------------------------------------------------------- #
#  Ledger writers (pure dict-in/dict-out — the file lock is the caller's).
# --------------------------------------------------------------------------- #
def _seed_round_zero(t: dict) -> dict:
    """Round 1 (index 0), reconstructed lazily at first reopen from the top-level
    `closed` marker + the customer-facing `triage.resolution` + the helpdesk's own
    `resolution_steps` — no store-wide migration. The declared shape
    `{closed_at, resolution, closed_by}`."""
    closed = t.get("closed") if isinstance(t.get("closed"), dict) else {}
    tri = t.get("triage") if isinstance(t.get("triage"), dict) else {}
    hd = t.get("helpdesk") if isinstance(t.get("helpdesk"), dict) else {}
    resolution = (str(tri.get("resolution") or "").strip()
                  or str(closed.get("resolution") or "").strip()
                  or str(hd.get("resolution_steps") or "").strip())
    return {
        "closed_at": _close_anchor(t),
        "resolution": resolution or None,
        "closed_by": closed.get("by") or "operator",
    }


def open_round(t: dict, trigger: dict | None) -> dict:
    """Open a NEW reopen round on `t`. Seeds `rounds[0]` from the original close on
    first sight, appends the reopen round, sets the `reopened` marker, ARCHIVES
    and CLEARS `triage.resolution`, RESETS the workflow state so the ticket is no
    longer done (`status`→"active", `triage.state`→""), CLEARS `t["closed"]` (the
    ticket is no longer closed in the store) and RESETS the AI working block.

    `trigger` is the reopen comment dict (from `pick_trigger`) or None — a None
    trigger yields `comment_id: null`, still a valid marker."""
    rounds = t.get("rounds")
    if not (isinstance(rounds, list) and rounds):
        rounds = [_seed_round_zero(t)]

    now = _now()

    # A reopened ticket is NO LONGER done. Archive the round's customer-facing
    # resolution into rounds[0] (only when it would otherwise be lost — never
    # overwriting an earlier round's text), then RESET the whole workflow state:
    #   * clear triage.resolution — so "a resolution is present" means "written
    #     THIS round" (the first R2 gate);
    #   * clear triage.state and set status back to "active" — so queue_status(t)
    #     is no longer "done" and the SECOND re-close gate is restored: re-closing
    #     a branch-B round needs a FRESH done-transition THIS round, not round-1's
    #     leftover done (which made close_out's queue_status=="done" leg a
    #     tautology). "" is the value queue_status maps to "active" — "cleared in
    #     the view → back in the queue". close_current_round re-stamps status/
    #     triage.state/closed on the actual re-close, so a later reopen stays
    #     detectable via the `closed` dict.
    tri = t.get("triage") if isinstance(t.get("triage"), dict) else {}
    archived = str(tri.get("resolution") or "").strip()
    if archived and not str(rounds[0].get("resolution") or "").strip():
        rounds[0]["resolution"] = archived
    tri["resolution"] = ""
    tri["state"] = ""
    tri["updated_at"] = now
    t["triage"] = tri
    t["status"] = "active"

    tc = trigger if isinstance(trigger, dict) else {}
    rounds.append({
        "reopened_at": now,
        "trigger": {
            "comment_id": tc.get("id"),
            "author": tc.get("author"),
            "author_role": tc.get("author_role"),
        },
        "branch": None,
        "resolution": None,
        "closed_at": None,
    })
    t["rounds"] = rounds
    t["reopened"] = {
        "at": now,
        "by_role": tc.get("author_role"),
        "trigger_comment_id": tc.get("id"),
    }
    # A reopened ticket is not closed anymore — clearing this is what lets the
    # branch-B re-close fire through writeback.run (its guard reads t["closed"]).
    t.pop("closed", None)
    # The AI working block belongs to ONE round; the new round starts empty.
    t["reopen"] = {}
    return t


def close_current_round(t: dict, resolution, by: str) -> dict:
    """Stamp the current round closed — `resolution` (when given), `closed_at`,
    `closed_by` — and keep every reader of the queue agreeing the ticket is done:
    `status`, `triage.state`, `helpdesk.is_closed` and a re-stamped top-level
    `closed` marker (the same multi-reader discipline `sync._closed_stamp` and
    `writeback.run`'s close path keep). Re-stamping `closed` is what makes a
    LATER reopen detectable again. Tolerant of a ticket with no current round
    (a no-op)."""
    cur = current_round(t)
    if cur is None:
        return t
    now = _now()
    if resolution is not None:
        cur["resolution"] = resolution
    cur["closed_at"] = now
    cur["closed_by"] = by
    t["status"] = "done"
    tri = t.get("triage") if isinstance(t.get("triage"), dict) else {}
    tri["state"] = "done"
    tri["updated_at"] = now
    t["triage"] = tri
    hd = t.get("helpdesk") if isinstance(t.get("helpdesk"), dict) else {}
    hd["is_closed"] = True
    # Also stamp the API-first close anchor: `_close_anchor` PREFERS
    # helpdesk.closed_at, so without this a reclose-without-pull would silently
    # fall back to the naive-local `closed.at`. This is the store's local stamp
    # until the next real pull refreshes the whole helpdesk block from the API;
    # `_parse_dt` normalises it (naive local → UTC) like any tz-aware value.
    hd["closed_at"] = now
    t["helpdesk"] = hd
    # Re-stamp the top-level marker so it reflects THIS close (and the next
    # reopen's LOCKED predicate finds a `closed` dict again). Keep the round's own
    # resolution when the caller passed none (a branch-A helpdesk self-close).
    prev = t.get("closed") if isinstance(t.get("closed"), dict) else {}
    t["closed"] = {"at": now, "by": by,
                   "resolution": resolution if resolution is not None
                   else (cur.get("resolution") or prev.get("resolution") or "")}
    return t


def record_verdict(root, module, tid, verdict: dict) -> str:
    """Write a Claude verdict for the current reopen round under the store lock:
    the ledger FACT `rounds[cur].branch` AND the AI working block's verdict fields
    (branch/confidence/legitimate/questions/suggested_reply) together, so the two
    can never disagree. Rejects an unknown branch before taking the lock. Bumps
    `rev`. This is the door a launched Claude session uses (via `main()`)."""
    branch = str((verdict or {}).get("branch") or "").strip().upper()
    if branch not in VALID_BRANCHES:
        raise SystemExit(f"unknown branch {branch!r}: must be one of {VALID_BRANCHES}")
    fp = store.resolve(root, module)
    if fp is None or not fp.is_file():
        raise SystemExit(f"no tiketi.json for module {module!r} (sync first)")
    tid = str(tid)
    with store.filelock(fp):
        d = store.load(fp)
        if not isinstance(d, dict):
            raise SystemExit("tiketi.json is unreadable")
        tickets = d.get("tickets")
        if not isinstance(tickets, dict) or tid not in tickets:
            raise SystemExit(f"ticket {tid} not in {module}")
        t = tickets[tid]
        cur = current_round(t)
        if cur is None or "reopened_at" not in cur:
            raise SystemExit(f"ticket {tid} has no open reopen round to classify")
        cur["branch"] = branch
        block = t.get("reopen") if isinstance(t.get("reopen"), dict) else {}
        block["branch"] = branch
        for k in ("confidence", "legitimate", "questions", "suggested_reply"):
            if k in verdict:
                block[k] = verdict[k]
        block["updated_at"] = _now()
        t["reopen"] = block
        d["rev"] = (d.get("rev") if isinstance(d.get("rev"), int) else 0) + 1
        store.atomic_write_json(fp, d)
        return f"verdict {branch} written to {module}#{tid} (rev {d['rev']})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["verdict"],
                    help="verdict: classify the current reopen round (JSON on stdin)")
    ap.add_argument("--root", default=os.environ.get("TICKETS_STORE") or str(store.default_store()))
    ap.add_argument("--module", required=True, help="module key = <MODULE>.json")
    ap.add_argument("--id", required=True, dest="ticket_id")
    args = ap.parse_args()

    raw = sys.stdin.read()
    try:
        verdict = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        print(f"verdict stdin is not JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(verdict, dict):
        print("verdict must be a JSON object", file=sys.stderr)
        return 2

    # record_verdict raises SystemExit for a bad branch / unknown ticket; letting
    # it propagate exits non-zero (same idiom as write_analysis.py).
    print(record_verdict(args.root, args.module, args.ticket_id, verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
