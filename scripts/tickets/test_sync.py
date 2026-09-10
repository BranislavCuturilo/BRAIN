#!/usr/bin/env python3
"""Offline tests for sync.py's two-way close and store.py's machine-independent
repo paths — no network, no real store. A fake adapter stands in for the
helpdesk (shaped like adapters/acme_helpdesk.AcmeHelpdesk's public
surface); a temp dir stands in for tickets_store. Run: python test_sync.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rounds  # noqa: E402  the reopen ledger owner
import store  # noqa: E402
import sync  # noqa: E402
from adapters.acme_helpdesk import HelpdeskError  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def _rec(tid, title="t"):
    """The shape adapter.to_record returns (see acme_helpdesk.to_record)."""
    return {"id": tid, "original": {"title": title}, "comments": [], "url": "",
            "helpdesk": {"priority": "minor", "is_closed": False}}


class FakeAdapter:
    """Records writes; never touches the network. Same method names/returns as
    AcmeHelpdesk (list_modules/list_active/get_detail/to_record/add_comment/close)."""
    def __init__(self, active=()):
        self.active = list(active)
        self.closed = []            # (tid, resolution)
        self.comments = []          # (tid, text)
        self.fail_close = set()

    def list_modules(self):
        return [{"id": 12, "name": "INV"}]

    def list_active(self, module=None, **_):
        return [{"ticket_id": t} for t in self.active]

    def get_detail(self, tid):
        return {"ticket_id": tid}

    def to_record(self, detail):
        return _rec(detail["ticket_id"])

    def add_comment(self, tid, text):
        self.comments.append((tid, text))

    def close(self, tid, resolution):
        if tid in self.fail_close:
            raise HelpdeskError("PATCH close -> HTTP 400")
        self.closed.append((tid, resolution))


def _write_store(root, tickets):
    d = {"project": {"name": "INV", "repo": "inv", "helpdesk_module": "INV"},
         "statuses": {}, "tickets": tickets, "rev": 0}
    (Path(root) / "INV.json").write_text(json.dumps(d), encoding="utf-8")
    return d


def _read(root):
    return json.loads((Path(root) / "INV.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- close_missing
def test_close_missing():
    tickets = {
        "1": {"status": "active", "helpdesk": {}},                          # still live
        "2": {"status": "active", "helpdesk": {}},                          # gone -> close
        "3": {"status": "active", "triage": {"state": "working_today"}, "helpdesk": {}},  # gone -> close
        "4": {"status": "done", "helpdesk": {}},                            # already done -> untouched
        "5": {"status": "active", "helpdesk": {"is_closed": True}},         # already closed -> untouched
    }
    d = {"tickets": tickets}
    closed = sync.close_missing(d, [_rec("1")])
    ck("close_missing: closes exactly the live-absent open tickets", sorted(closed) == ["2", "3"])
    ck("close_missing: status done", tickets["2"]["status"] == "done")
    ck("close_missing: triage.state done (view agrees)", tickets["3"]["triage"]["state"] == "done")
    ck("close_missing: helpdesk.is_closed set", tickets["2"]["helpdesk"]["is_closed"] is True)
    ck("close_missing: closed.by = helpdesk", tickets["2"]["closed"]["by"] == "helpdesk")
    ck("close_missing: queue_status now done", store.queue_status(tickets["3"]) == "done")
    ck("close_missing: live ticket untouched (presence twin)", tickets["1"]["status"] == "active"
       and "closed" not in tickets["1"])
    ck("close_missing: already-done untouched", "closed" not in tickets["4"])
    ck("close_missing: already-closed untouched", "closed" not in tickets["5"])


# ------------------------------------------------------------------- merge()
def test_merge_carries_the_rating_block_through():
    """F8: `to_record`'s helpdesk.rating/rating_comment/rated_at ride the SAME
    full-block replace merge() already does for helpdesk.* — pins that no
    per-field allowlist was added that would silently drop them."""
    existing = {"tickets": {"1": {"title": "old", "priority": "minor", "status": "active",
                                  "notes": "", "consider_for": "", "original": {"title": "old"},
                                  "comments": [], "url": "", "helpdesk": {"is_closed": False}}}}
    rec = _rec("1")
    rec["helpdesk"]["rating"] = 4
    rec["helpdesk"]["rating_comment"] = "Dobro"
    rec["helpdesk"]["rated_at"] = "2026-08-15T09:00:00"
    merged, report = sync.merge(existing, [rec])
    hd = merged["tickets"]["1"]["helpdesk"]
    ck("merge: rating rides the refreshed helpdesk block", hd.get("rating") == 4)
    ck("merge: rating_comment rides through", hd.get("rating_comment") == "Dobro")
    ck("merge: rated_at rides through", hd.get("rated_at") == "2026-08-15T09:00:00")

    # a brand-new ticket (not previously in the queue) is seeded from the SAME
    # record, so its helpdesk block carries the rating too.
    merged2, _ = sync.merge({"tickets": {}}, [rec])
    ck("merge: a brand-new ticket's seeded helpdesk block carries the rating too",
       merged2["tickets"]["1"]["helpdesk"].get("rating") == 4)


def test_merge_carries_the_resolution_steps_and_closed_at_through():
    """Row 2 twin of the rating test above: `to_record`'s new
    helpdesk.resolution_steps/closed_at (Phase 1, adapter capture) ride the SAME
    full-block replace merge() already does for helpdesk.* - pins that no
    per-field allowlist was added that would silently drop them."""
    existing = {"tickets": {"1": {"title": "old", "priority": "minor", "status": "active",
                                  "notes": "", "consider_for": "", "original": {"title": "old"},
                                  "comments": [], "url": "", "helpdesk": {"is_closed": False}}}}
    rec = _rec("1")
    rec["helpdesk"]["resolution_steps"] = "Restartovan servis."
    rec["helpdesk"]["closed_at"] = "2026-08-20T10:00:00"
    merged, report = sync.merge(existing, [rec])
    hd = merged["tickets"]["1"]["helpdesk"]
    ck("merge: resolution_steps rides the refreshed helpdesk block", hd.get("resolution_steps") == "Restartovan servis.")
    ck("merge: closed_at rides through", hd.get("closed_at") == "2026-08-20T10:00:00")

    # a brand-new ticket is seeded from the SAME record, so its helpdesk block
    # carries both new fields too.
    merged2, _ = sync.merge({"tickets": {}}, [rec])
    hd2 = merged2["tickets"]["1"]["helpdesk"]
    ck("merge: a brand-new ticket's seeded helpdesk block carries resolution_steps too",
       hd2.get("resolution_steps") == "Restartovan servis.")
    ck("merge: a brand-new ticket's seeded helpdesk block carries closed_at too",
       hd2.get("closed_at") == "2026-08-20T10:00:00")


# ---------------------------------------------------- reopen ledger fixtures
def _closed_ticket(resolution="Popravljeno.", closed_at="2026-08-10T08:00:00"):
    """A ticket in the store that WE closed - the shape `detect_reopen` keys off."""
    return {
        "status": "done",
        "closed": {"at": closed_at, "by": "operator", "resolution": resolution},
        "triage": {"state": "done", "resolution": ""},
        "helpdesk": {"is_closed": True, "closed_at": closed_at},
        "comments": [],
    }


def _fresh_open_rec(tid, comments=None):
    """The pull-side record for a ticket that is OPEN again on the helpdesk."""
    rec = _rec(tid)
    if comments is not None:
        rec["comments"] = comments
    return rec


def _reopened_ticket(branch=None, fresh_resolution="", round0_resolution="Prvi popravak.", state=""):
    """A ticket already IN an open reopen round, as `rounds.open_round` would
    leave it: `closed` cleared, `reopened` marker set, round 1 unclassified
    unless `branch` is given, and the WORKFLOW STATE reset to active — a reopened
    ticket is no longer `done` (`status="active"`, `triage.state=""`), so
    `store.queue_status` reports it active and re-closing needs a FRESH
    done-transition this round. Pass `state="done"` to model the operator making
    that fresh done-transition; the top-level `status` is kept coherent with it
    (queue_status reads `triage.state` first)."""
    return {
        "status": "done" if state == "done" else "active",
        "triage": {"state": state, "resolution": fresh_resolution},
        "helpdesk": {"is_closed": False},
        "rounds": [
            {"closed_at": "2026-08-10T08:00:00", "resolution": round0_resolution, "closed_by": "operator"},
            {"reopened_at": "2026-08-11T09:00:00",
             "trigger": {"comment_id": 501, "author": "Klijent", "author_role": "customer"},
             "branch": branch, "resolution": None, "closed_at": None},
        ],
        "reopened": {"at": "2026-08-11T09:00:00", "by_role": "customer", "trigger_comment_id": 501},
        "reopen": {},
    }


# ------------------------------------------------- reopen: our own comment
def _comment(cid, role, at="2026-08-11T09:00:00+02:00", body="tekst"):
    return {"id": cid, "author": "X", "author_role": role, "at": at, "body": body}


def test_our_own_comment_is_never_a_dopuna():
    """DEMO#76082. Posting the before/after pictures re-opens the ticket on this
    helpdesk, so the comment that REPORTS the work as finished used to be read
    as the customer asking for more. It cleared the resolution, pushed the
    ticket back to active (so the close phase stopped closing it) and queued a
    Gemini adjudication of our own message."""
    t = _closed_ticket()
    rec = _fresh_open_rec("1", [_comment(624, "engineer",
                                         body="Slike ekrana pre i posle izmene")])
    ck("own comment: not a reopen", rounds.detect_reopen(t, rec) is False)
    ck("own comment: never picked as the trigger",
       rounds.pick_trigger(t, rec) is None)


def test_a_real_customer_comment_still_reopens():
    """The guard must not cost us the case it exists for. Both roles the far
    side actually arrives as are checked - this queue's real dopune came in as
    `other`, not `customer`."""
    for role in ("customer", "other"):
        t = _closed_ticket()
        rec = _fresh_open_rec("1", [_comment(700, role)])
        ck("far side (%s): still a reopen" % role,
           rounds.detect_reopen(t, rec) is True)
        picked = rounds.pick_trigger(t, rec)
        ck("far side (%s): it is the trigger" % role,
           isinstance(picked, dict) and picked.get("id") == 700)


def test_a_customer_comment_wins_over_our_later_one():
    """We routinely answer a dopuna. The trigger must stay the CUSTOMER's
    comment even when ours is newer, or round 1 gets adjudicated against our
    own reply."""
    t = _closed_ticket()
    rec = _fresh_open_rec("1", [
        _comment(700, "other", at="2026-08-11T09:00:00+02:00"),
        _comment(701, "engineer", at="2026-08-11T10:00:00+02:00"),
    ])
    ck("mixed: still a reopen", rounds.detect_reopen(t, rec) is True)
    picked = rounds.pick_trigger(t, rec)
    ck("mixed: the customer's comment is the trigger",
       isinstance(picked, dict) and picked.get("id") == 700)


def test_a_bare_status_flip_is_still_a_reopen():
    """An admin reopening WITHOUT a word is a genuine reopen - the suppression
    needs new comments that are all ours, not merely no far-side comment."""
    t = _closed_ticket()
    ck("bare flip: still a reopen",
       rounds.detect_reopen(t, _fresh_open_rec("1", [])) is True)


def test_comments_predating_the_close_do_not_reopen():
    """Our own comment posted BEFORE the close (the normal writeback order:
    drafts first, then close) must not even reach the own-comment rule."""
    t = _closed_ticket()
    rec = _fresh_open_rec("1", [_comment(600, "other", at="2026-08-01T08:00:00+02:00")])
    ck("stale comment: not counted as fresh",
       rounds.comments_after_close(t, rec) == [])
    ck("stale comment: bare flip semantics -> still a reopen",
       rounds.detect_reopen(t, rec) is True)


# ------------------------------------------------------------- reopen: #1/#2
def test_reopen_detected_on_merge_and_rounds_seeded():
    """Scenario 1: closed-marker + fresh-open pull -> `reopened` marker set,
    `rounds` seeded (round 0 = the original close, round 1 = this reopen),
    trigger comment matched."""
    t = _closed_ticket(resolution="Popravljeno, radi.", closed_at="2026-08-10T08:00:00")
    existing = {"tickets": {"1": t}}
    trigger_comment = {"id": 501, "author": "Klijent", "author_role": "customer",
                       "at": "2026-08-11T09:00:00", "body": "Opet se javlja greska."}
    rec = _fresh_open_rec("1", comments=[trigger_comment])
    merged, report = sync.merge(existing, [rec])
    tt = merged["tickets"]["1"]
    ck("reopen: merge() reports the ticket as reopened", report["reopened"] == ["1"])
    ck("reopen: the marker is set", isinstance(tt.get("reopened"), dict))
    ck("reopen: rounds seeded to 2 entries (round 0 + this reopen)", len(tt.get("rounds") or []) == 2)
    ck("reopen: round 0 carries the original resolution", tt["rounds"][0]["resolution"] == "Popravljeno, radi.")
    ck("reopen: round 0 carries the original close timestamp", tt["rounds"][0]["closed_at"] == "2026-08-10T08:00:00")
    ck("reopen: round 1 branch starts unclassified", tt["rounds"][1]["branch"] is None)
    ck("reopen: trigger comment matched", tt["rounds"][1]["trigger"]["comment_id"] == 501)
    ck("reopen: marker trigger_comment_id matches", tt["reopened"]["trigger_comment_id"] == 501)
    ck("reopen: is_reopened_open is True", rounds.is_reopened_open(tt) is True)
    ck("reopen: top-level closed marker is cleared", "closed" not in tt)
    # The workflow state is reset so no reader still calls the ticket done — the
    # tautology that made the branch-B re-close gate unsafe (reviewer HIGH).
    ck("reopen: queue_status is no longer done (back in the queue)",
       store.queue_status(tt) != "done")
    ck("reopen: legacy status no longer done", tt.get("status") != "done")
    ck("reopen: triage.state is no longer a done-transition",
       tt.get("triage", {}).get("state") != "done")


def test_reopen_not_detected_without_closed_marker():
    """Scenario 2 (R1's false-positive guard): status:done during the
    done-awaiting-writeback window (before close_out ran in the same rescan) has
    NO `closed` marker yet - the loose `status:done` predicate would
    false-positive on the very rescan about to close it. Locked predicate: the
    `closed` marker is required."""
    t = {"status": "done", "triage": {"state": "done", "resolution": "Popravljeno."},
         "helpdesk": {"is_closed": False}, "comments": []}
    existing = {"tickets": {"1": t}}
    rec = _fresh_open_rec("1")
    merged, report = sync.merge(existing, [rec])
    tt = merged["tickets"]["1"]
    ck("no-marker: NOT reported as reopened", report["reopened"] == [])
    ck("no-marker: no reopened marker written", "reopened" not in tt)
    ck("no-marker: no rounds ledger written", "rounds" not in tt)
    ck("no-marker: setup really is the done-awaiting-writeback shape (presence twin)",
       tt["status"] == "done" and "closed" not in tt)


# ------------------------------------------------------------- reopen: #6/#7
def test_close_missing_closes_open_round_when_absent():
    """Scenario 6: a reopened-open ticket ABSENT from the (module-filtered) pull
    was closed AGAIN on the helpdesk (the branch-A self-close) - close_missing
    closes the round so the card leaves the lane instead of ghosting there."""
    tickets = {"1": _reopened_ticket(branch=None), "9": {"status": "active", "helpdesk": {}}}
    d = {"tickets": tickets}
    closed = sync.close_missing(d, [_rec("9")])   # "1" is absent from the pull
    ck("close_missing/reopen: the open round is closed", "1" in closed)
    t = tickets["1"]
    ck("close_missing/reopen: round closed_by helpdesk", t["rounds"][-1]["closed_by"] == "helpdesk")
    ck("close_missing/reopen: round closed_at stamped", t["rounds"][-1]["closed_at"] is not None)
    ck("close_missing/reopen: lane membership now false", rounds.is_reopened_open(t) is False)
    ck("close_missing/reopen: queue agrees done", store.queue_status(t) == "done")
    ck("close_missing/reopen: helpdesk.is_closed set", t["helpdesk"]["is_closed"] is True)
    ck("close_missing/reopen: still-active ticket untouched (presence twin)", tickets["9"]["status"] == "active")


def test_reopen_reclose_reopen_appends_third_round():
    """Scenario 7: reopen -> reclose -> a SECOND reopen appends a THIRD ledger
    entry - the closed-marker predicate keeps firing every time the ticket is
    genuinely re-closed and then genuinely reopened again."""
    t = _closed_ticket(resolution="Prvi popravak.", closed_at="2026-08-10T08:00:00")
    existing = {"tickets": {"1": t}}

    rec1 = _fresh_open_rec("1", comments=[
        {"id": 1, "author": "Klijent", "author_role": "customer",
         "at": "2026-08-11T09:00:00", "body": "opet ne radi"}])
    merged, report = sync.merge(existing, [rec1])
    ck("reopen x2: first reopen detected", report["reopened"] == ["1"])
    ck("reopen x2: rounds at 2 after the first reopen", len(merged["tickets"]["1"]["rounds"]) == 2)

    closed = sync.close_missing(merged, [])          # absent from the pull -> re-closed on the helpdesk
    ck("reopen x2: close_missing closed round 1", closed == ["1"])
    ck("reopen x2: rounds still 2 after the reclose", len(merged["tickets"]["1"]["rounds"]) == 2)
    ck("reopen x2: top-level closed re-stamped (detectable again)",
       isinstance(merged["tickets"]["1"].get("closed"), dict))

    rec2 = _fresh_open_rec("1", comments=[
        {"id": 2, "author": "Klijent", "author_role": "customer",
         "at": "2026-08-12T09:00:00", "body": "treci put"}])
    merged, report = sync.merge(merged, [rec2])
    ck("reopen x2: second reopen detected", report["reopened"] == ["1"])
    ck("reopen x2: rounds now has 3 entries", len(merged["tickets"]["1"]["rounds"]) == 3)
    ck("reopen x2: round 2 is unclassified/open, same as any fresh round",
       merged["tickets"]["1"]["rounds"][2]["branch"] is None
       and merged["tickets"]["1"]["rounds"][2]["closed_at"] is None)


# ---------------------------------------------------------- reopen: #3/#4/#5
def test_close_out_does_not_reclose_with_stale_resolution_only(root):
    """Scenario 3 (R2, the RESOLUTION gate): branch B, the operator made a fresh
    done-transition (state="done") but wrote NO fresh resolution this round - the
    only resolution text is the STALE round-0 one `open_round` archived and
    cleared. The re-close gate must key off `triage.resolution` alone and never
    fall back to the stale round text. (Twin of the done-transition gate test.)"""
    _write_store(root, {"1": _reopened_ticket(branch="B", fresh_resolution="", round0_resolution="Prvi popravak.", state="done")})
    ad = FakeAdapter()
    out = sync.close_out(root, "INV", [_rec("1")], ad, dry_run=False)
    ck("stale-resolution: close_out does not close it", "1" not in out["closed"])
    ck("stale-resolution: nothing reaches the helpdesk", ad.closed == [])
    ck("stale-resolution: reported as needs_resolution instead", out["needs_resolution"] == ["1"])
    d = _read(root)
    ck("stale-resolution: round stays open on disk", d["tickets"]["1"]["rounds"][-1]["closed_at"] is None)


def test_close_out_recloses_branch_b_with_fresh_resolution(root):
    """Scenario 4: branch B + BOTH fresh gates satisfied THIS round - the operator
    made a fresh done-transition (state="done") AND wrote a fresh
    triage.resolution -> close_out re-closes through the same writeback door and
    stamps the round closed."""
    _write_store(root, {"1": _reopened_ticket(branch="B", fresh_resolution="Ponovo popravljeno, radi.", state="done")})
    ad = FakeAdapter()
    out = sync.close_out(root, "INV", [_rec("1")], ad, dry_run=False)
    ck("branch-B fresh: close_out closes it", out["closed"] == ["1"])
    ck("branch-B fresh: fired on the helpdesk with the fresh text",
       dict(ad.closed).get("1") == "Ponovo popravljeno, radi.")
    d = _read(root)
    t = d["tickets"]["1"]
    ck("branch-B fresh: the round is stamped closed", t["rounds"][-1]["closed_at"] is not None)
    ck("branch-B fresh: round closed_by operator", t["rounds"][-1]["closed_by"] == "operator")
    ck("branch-B fresh: lane membership now false", rounds.is_reopened_open(t) is False)
    ck("branch-B fresh: top-level closed marker re-stamped (next reopen detectable)",
       isinstance(t.get("closed"), dict))


def test_close_out_never_autocloses_branch_a_or_c(root):
    """Scenario 5: branch A (customer self-closes) and branch C (waiting on a
    fix) NEVER auto-close through close_out, even when BOTH fresh gates are
    satisfied (fresh done-transition state="done" + fresh resolution) so they
    reach the branch check and are rejected THERE, not earlier."""
    _write_store(root, {
        "1": _reopened_ticket(branch="A", fresh_resolution="Zatvoricu sam.", state="done"),
        "2": _reopened_ticket(branch="C", fresh_resolution="Ne mogu da reprodukujem.", state="done"),
    })
    ad = FakeAdapter()
    out = sync.close_out(root, "INV", [_rec("1"), _rec("2")], ad, dry_run=False)
    ck("branch A/C: neither is closed", out["closed"] == [])
    ck("branch A/C: nothing reaches the helpdesk", ad.closed == [])
    ck("branch A/C: neither is even reported as needs_resolution (skipped before that check)",
       out["needs_resolution"] == [])
    d = _read(root)
    ck("branch A/C: both rounds remain open on disk",
       d["tickets"]["1"]["rounds"][-1]["closed_at"] is None
       and d["tickets"]["2"]["rounds"][-1]["closed_at"] is None)


# ------------------------------------------------ reopen: restored done-gate
def test_close_out_needs_fresh_done_transition(root):
    """The RESTORED second re-close gate (reviewer HIGH). Drives the REAL
    open_round (via sync.merge), then models the operator classifying B and
    writing a resolution but making NO fresh done-transition (triage.state stays
    what open_round left it). close_out must NOT re-close - the done-transition
    gate is missing. Goes RED if open_round's state reset is reverted: a
    round-1 done-leftover makes queue_status=='done' a tautology and would re-send
    the in-progress note to the customer and close irreversibly."""
    # 1. a closed ticket, reopened through the real merge/open_round path.
    t = _closed_ticket(resolution="Prvi popravak.", closed_at="2026-08-10T08:00:00")
    existing = {"tickets": {"1": t}}
    rec = _fresh_open_rec("1", comments=[
        {"id": 7, "author": "Klijent", "author_role": "customer",
         "at": "2026-08-11T09:00:00", "body": "opet"}])
    merged, _ = sync.merge(existing, [rec])
    tt = merged["tickets"]["1"]
    ck("done-gate: after open_round queue_status is not done",
       store.queue_status(tt) != "done")
    # 2. operator classifies B and writes a resolution but makes NO done-transition
    #    (an in-progress note, triage.state stays what open_round left it).
    tt["rounds"][-1]["branch"] = "B"
    tt["triage"]["resolution"] = "Probajte ovako pa javite."
    _write_store(root, merged["tickets"])
    # 3. close_out must NOT re-close: the done-transition gate is missing.
    ad = FakeAdapter()
    out = sync.close_out(root, "INV", [_rec("1")], ad, dry_run=False)
    ck("done-gate: close_out does NOT re-close without a done-transition", out["closed"] == [])
    ck("done-gate: nothing reaches the helpdesk", ad.closed == [])
    d = _read(root)
    ck("done-gate: the round stays open on disk", d["tickets"]["1"]["rounds"][-1]["closed_at"] is None)


# ------------------------------------------------------------------ reopen: #8
def test_dry_run_reopen_detection_mutates_nothing(root):
    """Scenario 8: sync.run's dry-run path deep-copies before detecting a reopen
    - the report says the ticket WOULD be reopened, but nothing on disk moves."""
    _write_store(root, {"1": _closed_ticket(resolution="Popravljeno.", closed_at="2026-08-10T08:00:00")})
    before = _read(root)
    ad = FakeAdapter(active=["1"])
    sync.get_adapter = lambda name: (lambda: ad)   # noqa: E731
    report, n, dry = sync.run(root, "INV", dry_run=True)
    ck("dry-run reopen: report says reopened", report.get("reopened") == ["1"])
    after = _read(root)
    ck("dry-run reopen: the file on disk is unchanged", after == before)
    ck("dry-run reopen: no reopened marker written to disk", "reopened" not in after["tickets"]["1"])
    ck("dry-run reopen: no rounds ledger written to disk", "rounds" not in after["tickets"]["1"])
    ck("dry-run reopen: closed marker on disk still the original (presence twin)",
       after["tickets"]["1"]["closed"]["resolution"] == "Popravljeno.")


# --------------------------------------------------------- rounds.py: unit
def test_detect_reopen_locked_predicate():
    """rounds.detect_reopen: locked to the `closed` MARKER, never bare
    status:done (R1's false-positive guard) - the pure-predicate level twin of
    the merge()-level scenario 1/2 tests above."""
    closed_t = {"closed": {"at": "x", "by": "operator", "resolution": "r"}}
    fresh_open = {"helpdesk": {"is_closed": False}}
    fresh_still_closed = {"helpdesk": {"is_closed": True}}
    ck("detect_reopen: closed marker + fresh open -> True",
       rounds.detect_reopen(closed_t, fresh_open) is True)
    ck("detect_reopen: closed marker + fresh STILL closed -> False",
       rounds.detect_reopen(closed_t, fresh_still_closed) is False)
    ck("detect_reopen: NOT triggered by bare status:done (no closed marker) -> False",
       rounds.detect_reopen({"status": "done"}, fresh_open) is False)
    ck("detect_reopen: closed marker but fresh helpdesk block missing -> False",
       rounds.detect_reopen(closed_t, {}) is False)
    ck("detect_reopen: closed marker but is_closed missing/non-bool -> not treated as reopened",
       rounds.detect_reopen(closed_t, {"helpdesk": {}}) is False)


def test_record_verdict_writes_branch_and_bumps_rev(root):
    """rounds.record_verdict: stamps `rounds[cur].branch` AND the `reopen`
    working block together (so the two can never disagree), bumps rev, and
    rejects an unknown branch before touching the file."""
    _write_store(root, {"1": _reopened_ticket(branch=None)})
    msg = rounds.record_verdict(root, "INV", "1", {"branch": "b", "confidence": 0.8, "legitimate": True})
    ck("record_verdict: returns a confirmation naming the branch", "B" in msg)
    d = _read(root)
    t = d["tickets"]["1"]
    ck("record_verdict: rounds[cur].branch written (case-normalised)", t["rounds"][-1]["branch"] == "B")
    ck("record_verdict: reopen.branch agrees", t["reopen"]["branch"] == "B")
    ck("record_verdict: reopen.confidence carried", t["reopen"]["confidence"] == 0.8)
    ck("record_verdict: rev bumped", d["rev"] == 1)

    try:
        rounds.record_verdict(root, "INV", "1", {"branch": "Q"})
        ck("record_verdict: unknown branch raises", False)
    except SystemExit:
        ck("record_verdict: unknown branch raises SystemExit", True)


def test_verdict_cli_exit_codes(root):
    """rounds.py verdict CLI smoke: valid branch on stdin -> 0; unknown branch ->
    nonzero; malformed JSON on stdin -> nonzero. A subprocess, so credentials are
    scrubbed from its environment even though this module never calls the
    helpdesk (craft-testing: a shelled-out test inherits the caller's env)."""
    _write_store(root, {"1": _reopened_ticket(branch=None)})
    script = str(HERE / "rounds.py")
    env = {k: v for k, v in os.environ.items()
           if k not in ("HELPDESK_URL", "HELPDESK_TOKEN")}
    env["PYTHONIOENCODING"] = "utf-8"

    def run_cli(stdin_text):
        return subprocess.run(
            [sys.executable, script, "verdict", "--root", root, "--module", "INV", "--id", "1"],
            input=stdin_text, capture_output=True, text=True, env=env, timeout=30)

    r_ok = run_cli(json.dumps({"branch": "B"}))
    ck("verdict CLI: valid branch exits 0", r_ok.returncode == 0)

    r_bad_branch = run_cli(json.dumps({"branch": "Z"}))
    ck("verdict CLI: unknown branch exits nonzero", r_bad_branch.returncode != 0)

    r_bad_json = run_cli("{not json")
    ck("verdict CLI: malformed JSON exits nonzero", r_bad_json.returncode != 0)


# --------------------------------------------------------------- run(): guard
def test_run_never_closes_on_unfiltered_pull(root):
    """Module id unresolved -> pull is unfiltered -> absence proves nothing."""
    _write_store(root, {"9": {"status": "active", "helpdesk": {}}})

    class NoModules(FakeAdapter):
        def list_modules(self):
            return []                       # INV not found -> module_id None

    ad = NoModules(active=["1"])
    sync.get_adapter = lambda name: (lambda: ad)   # noqa: E731
    report, n, dry = sync.run(root, "INV", dry_run=True)
    ck("run: unfiltered pull closes nothing", report["closed"] == [])
    ck("run: unfiltered pull still adds", report["added"] == ["1"])


def test_run_filtered_pull_closes(root):
    _write_store(root, {"9": {"status": "active", "helpdesk": {}}})
    ad = FakeAdapter(active=["1"])
    sync.get_adapter = lambda name: (lambda: ad)   # noqa: E731
    report, n, dry = sync.run(root, "INV", dry_run=False)
    ck("run: filtered pull closes the absent ticket", report["closed"] == ["9"])
    d = _read(root)
    ck("run: closed state persisted", d["tickets"]["9"]["status"] == "done")
    ck("run: new ticket persisted as active", d["tickets"].get("1", {}).get("status") == "active")
    ck("run: repo stays machine-independent", d["project"]["repo"] == "inv")


# ---------------------------------------------------------------- close_out
def test_close_out(root):
    # 2026-08-19: ONLY `triage.resolution` (customer-facing) closes a ticket on
    # the helpdesk. `report` is internal and a technical one is refused by the
    # writer; a done ticket with no resolution is NOT closed, it is reported.
    _write_store(root, {
        "1": {"status": "active", "helpdesk": {}},                                       # open here -> no
        "2": {"status": "done", "triage": {"state": "solved_manually", "resolution": "Popravljeno, radi.",
                                           "report": "commit abc1234 test_x.py"}, "helpdesk": {}},  # -> close with the RESOLUTION
        "3": {"status": "done", "triage": {"state": "nonsense", "report": "Fixed X"}, "helpdesk": {}},  # report only -> NOT closed
        "4": {"status": "done", "closed": {"at": "x"}, "helpdesk": {}},                    # already closed -> no
        "5": {"status": "done", "closing_at": "x", "helpdesk": {}},                        # claimed -> no
        "6": {"status": "done", "helpdesk": {}},                                            # not live -> no
        "7": {"status": "done", "triage": {"state": "done", "resolution": "Gotovo."}, "helpdesk": {},
              "outbox": [{"id": "d1", "body": "komentar"}]},                                # posts then closes
        "9": {"status": "done", "triage": {"state": "done",
                                           "resolution": "DONE (commit 87f3a0d) test_data_mismatch.py"}, "helpdesk": {}},  # technical -> refused
    })
    live = [_rec(t) for t in ("1", "2", "3", "4", "5", "7", "9")]
    ad = FakeAdapter()
    out = sync.close_out(root, "INV", live, ad, dry_run=True)
    ck("close_out dry-run: would close 2,7,9 only (3 has no resolution)", sorted(out["would_close"]) == ["2", "7", "9"])
    ck("close_out dry-run: 3 is reported as needs_resolution", out["needs_resolution"] == ["3"])
    ck("close_out dry-run: nothing fired", ad.closed == [] and ad.comments == [])

    out = sync.close_out(root, "INV", live, ad, dry_run=False)
    ck("close_out: closed 2,7", sorted(out["closed"]) == ["2", "7"])
    by = dict(ad.closed)
    ck("close_out: fired exactly those on the helpdesk", sorted(by) == ["2", "7"])
    ck("close_out: resolution from triage.resolution, never the report", by.get("2") == "Popravljeno, radi.")
    ck("close_out: a done ticket without a resolution stays open and is listed", out["needs_resolution"] == ["3"]
       and "3" not in by)
    ck("close_out: a TECHNICAL resolution is refused by the writer (9 failed, not closed)",
       "9" not in by and any(f["id"] == "9" for f in out["failed"]))
    ck("close_out: outbox posted before close", ad.comments == [("7", "komentar")])
    d = _read(root)
    ck("close_out: closed marker persisted", isinstance(d["tickets"]["2"].get("closed"), dict))
    ck("close_out: open ticket untouched", d["tickets"]["1"]["status"] == "active"
       and "closed" not in d["tickets"]["1"])

    # a failed close is reported, not raised, and leaves the claim for the operator
    _write_store(root, {"8": {"status": "done", "triage": {"state": "done", "resolution": "Gotovo."}, "helpdesk": {}}})
    ad = FakeAdapter()
    ad.fail_close.add("8")
    out = sync.close_out(root, "INV", [_rec("8")], ad)
    ck("close_out: failed close reported", [f["id"] for f in out["failed"]] == ["8"])
    ck("close_out: failed close not counted as closed", out["closed"] == [])
    d = _read(root)
    ck("close_out: failed close keeps closing_at (ambiguous next run)",
       bool(d["tickets"]["8"].get("closing_at")))


# ------------------------------------------------- rescan discovers new module
def test_rescan_discovers_new_module(root):
    """Rescan must visit EVERY helpdesk module, not only those with a local
    <MODULE>.json. A module whose first ticket just arrived (TIKET here) has no
    file yet; enumerating the store glob alone skips it forever. Pins the
    discover-from-source fix in rescan_all()."""
    _write_store(root, {"9": {"status": "active", "helpdesk": {}}})   # only INV.json on disk

    class TwoModules(FakeAdapter):
        def list_modules(self):
            return [{"id": 12, "name": "INV"}, {"id": 34, "name": "TIKET"}]

    ad = TwoModules(active=["1"])
    sync.get_adapter = lambda name: (lambda: ad)   # noqa: E731
    summ = sync.rescan_all(root, dry_run=True)
    ck("rescan: visits the locally-known module (presence twin)", "INV" in summ["modules"])
    ck("rescan: discovers a helpdesk module with no local file yet", "TIKET" in summ["modules"])


# ------------------------------------------------------------- repo paths
def test_repo_paths(root):
    proj = Path(root) / "PROJ"
    os.environ["PROJECTS_ROOT"] = str(proj)
    (proj / "inv").mkdir(parents=True)
    ck("repo_path: relative under PROJECTS_ROOT", Path(store.repo_path("inv")) == proj / "inv")
    # The legacy path must be one that provably does NOT exist. `C:/projects/popis`
    # was the original choice, and it passed everywhere except the one machine
    # the store was born on -- where it exists, so `p.exists()` won, the real
    # path came back, and the assertion failed only for its own author.
    ck("repo_path: legacy absolute re-rooted when missing",
       Path(store.repo_path(store.LEGACY_PROJECTS_ROOT + "/__no_such_repo__"))
       == proj / "__no_such_repo__")
    ck("repo_path: existing absolute kept", Path(store.repo_path(str(proj / "inv"))) == proj / "inv")
    ck("repo_path: empty stays empty", store.repo_path("") == "")
    ck("repo_name: strips PROJECTS_ROOT", store.repo_name(str(proj / "sub" / "x")) == "sub/x")
    ck("repo_name: strips legacy root", store.repo_name("C:/projects/acme-audit") == "acme-audit")
    ck("repo_name: relative kept", store.repo_name("inv") == "inv")
    ck("repo_name: foreign absolute kept",
       store.repo_name("D:/elsewhere/x").replace("\\", "/") == "D:/elsewhere/x")
    (Path(root) / store.LOCAL_OVERRIDES).write_text(
        json.dumps({"modules": {"ODS": "NSGAS-Telefon-app/ODS"}}), encoding="utf-8")
    ck("module_repo: local override wins",
       Path(store.module_repo(root, "ODS", "TelefonPrijavaKvarova")) == proj / "NSGAS-Telefon-app" / "ODS")
    ck("module_repo: falls back to recorded",
       Path(store.module_repo(root, "INV", "inv")) == proj / "inv")
    ck("module_repo: unmapped is empty", store.module_repo(root, "X", "") == "")


def main():
    saved_adapter = sync.get_adapter
    saved_env = os.environ.get("PROJECTS_ROOT")
    try:
        test_close_missing()
        test_merge_carries_the_rating_block_through()
        test_merge_carries_the_resolution_steps_and_closed_at_through()
        test_our_own_comment_is_never_a_dopuna()
        test_a_real_customer_comment_still_reopens()
        test_a_customer_comment_wins_over_our_later_one()
        test_a_bare_status_flip_is_still_a_reopen()
        test_comments_predating_the_close_do_not_reopen()
        test_reopen_detected_on_merge_and_rounds_seeded()
        test_reopen_not_detected_without_closed_marker()
        test_close_missing_closes_open_round_when_absent()
        test_reopen_reclose_reopen_appends_third_round()
        test_detect_reopen_locked_predicate()
        for fn in (test_run_never_closes_on_unfiltered_pull, test_run_filtered_pull_closes,
                   test_close_out, test_rescan_discovers_new_module,
                   test_close_out_does_not_reclose_with_stale_resolution_only,
                   test_close_out_recloses_branch_b_with_fresh_resolution,
                   test_close_out_never_autocloses_branch_a_or_c,
                   test_close_out_needs_fresh_done_transition,
                   test_dry_run_reopen_detection_mutates_nothing,
                   test_record_verdict_writes_branch_and_bumps_rev,
                   test_verdict_cli_exit_codes,
                   test_repo_paths):
            with tempfile.TemporaryDirectory() as td:
                fn(td)
    finally:
        sync.get_adapter = saved_adapter
        if saved_env is None:
            os.environ.pop("PROJECTS_ROOT", None)
        else:
            os.environ["PROJECTS_ROOT"] = saved_env
    print(f"\n{len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
