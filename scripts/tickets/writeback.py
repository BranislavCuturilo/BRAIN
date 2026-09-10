#!/usr/bin/env python3
"""Write-back to the helpdesk — the IRREVERSIBLE half of `/brain:tickets close`.

Posts the ticket's drafted `outbox` comments, then (optionally) closes it with a
resolution. Two hard rules, because a helpdesk comment has no idempotency key and
a close fires notifications/webhooks that cannot be recalled:

  * CLAIM BEFORE CALL. A draft is marked `posting_at` under the lock BEFORE the
    POST. If the process dies mid-flight, the re-run sees a claimed-but-unposted
    draft and refuses to repost it (it may already be live) — it reports it as
    AMBIGUOUS for the human to check on the helpdesk, rather than double-posting.
  * ONE DRAFT IS 1..N COMMENTS. The helpdesk caps how many files a single
    comment may carry, so a draft with more pictures than fit is split
    (`plan_chunks`) and EVERY comment is claimed the same way, recorded on the
    draft with the attachment names it carried. A retry resumes at the first
    comment that did not land and reposts none of the ones that did.
  * NEVER CLOSE ON A PARTIAL POST. If any comment fails or is ambiguous, the
    close does not run — a ticket closed with half its comments missing is worse
    than one left open.

Network calls happen OUTSIDE the store lock (they are slow); only the claim and
the result-recording take it, so a browser triage edit is never blocked on the
network.

EVERY call that leaves this module — comment, close, estimate, success AND
HelpdeskError — is recorded in `sent_log` (per-device JSONL in the store) with
the approver, a 120-char preview and, for an estimate, the value it replaced. A
send cannot be recalled, so "what did we tell this customer, and who approved
it" has to be answerable from the tracked store months later.

  echo "Rešeno: ispravljen save() na formi." | \
    writeback.py --project popis --id 94313 --close
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sent_log  # noqa: E402
import store  # noqa: E402
from adapters import get_adapter  # noqa: E402
from adapters.acme_helpdesk import HelpdeskError  # noqa: E402

#: The helpdesk field is a bare DecimalField(6,2) with no bounds, it feeds the
#: customer-visible day planner, and 0 is how the helpdesk spells "no estimate"
#: (ticket/filters.py:160) — so 0 would ERASE the ticket from the unestimated
#: queue while telling nobody anything. Both estimate doors REFUSE anything outside (0, 40] (reject, not clamp).
MAX_ESTIMATE_HOURS = 40.0

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _ticket(d, tid):
    tickets = d.get("tickets") if isinstance(d, dict) else None
    if not isinstance(tickets, dict) or tid not in tickets or not isinstance(tickets[tid], dict):
        raise SystemExit(f"ticket {tid} not found")
    return tickets[tid]


def _bump(d):
    d["rev"] = (d.get("rev") if isinstance(d.get("rev"), int) else 0) + 1


def _validate_hours(hours) -> float:
    """The ONE bounds check both estimate doors use. Raises ValueError; NaN
    fails `> 0` on its own, which is why the test is written that way round."""
    try:
        h = float(hours)
    except (TypeError, ValueError):
        raise ValueError(f"estimate must be a number, got {hours!r}") from None
    if not h > 0 or h > MAX_ESTIMATE_HOURS:
        raise ValueError(f"estimate must be within (0, {MAX_ESTIMATE_HOURS}] hours, got {h}")
    return h


def _http_outcome(exc) -> str:
    """Map a HelpdeskError to the sent-log `http` value. The ADAPTER is the only
    layer that knows whether the request left this machine, and it says so on the
    exception: `ambiguous` is True for a transport or parse failure after the
    send, False for a real HTTP status (the server answered: the write did NOT
    happen) and for every pre-flight refusal (nothing was opened). This module
    treats the ambiguous case as AMBIGUOUS (claim left in place); the log must
    not flatten it into a definite "fail" (reviewer 2026-08-19).

    Read the FLAG, never the message. The message carried "HTTP" for exactly one
    of the two definite cases, so every pre-flight refusal - the attachment caps,
    the write guard, a missing credential - was logged "unknown", which in this
    project means "the request may have landed, go check the helpdesk". It sent
    the operator looking for a comment that could not exist (2026-08-21,
    DEMO#05513: "too many attachments (13 > 5)", logged as unknown)."""
    return "unknown" if getattr(exc, "ambiguous", False) else "fail"


#: One helpdesk comment takes a bounded number of attachments (the adapter's
#: `attachment_limits`), and an approved set of before/after screenshots
#: routinely exceeds it: three approved pairs on DEMO#05513 are 13 files against a
#: cap of 5, and the whole comment was refused - the customer got nothing. So ONE
#: draft is delivered as as many comments as it needs. They arrive as SEPARATE
#: comments in the customer's thread, which is why each one has to read on its
#: own and say where it sits in the sequence.
CHUNK_TAIL_SR = "(1. od {n} — ostale slike su u sledećim komentarima.)"
CHUNK_CONT_SR = "{head} — nastavak ({i}. od {n}).\nOpis izmena je u prvom komentaru."
#: Only for a draft whose body is empty; normally a continuation repeats the
#: draft's OWN first line (see `_chunk_head`).
CHUNK_CONT_HEAD_SR = "Slike ekrana"
CHUNK_HEAD_MAX = 120

def _att_group(att) -> str:
    """Which before/after PAIR an attachment belongs to. Chunk boundaries follow
    these groups wherever they fit, so one screen's PRE, POSLE and zoomed details
    stay in the same comment.

    The PRODUCER states it (`agent_view/server.py::shots_draft` writes `pair` on
    every attachment); this never parses it back out of the file name. That name
    is customer-facing Serbian and is expected to keep changing — a display
    string makes a bad primary key, and the first version of it changed the day
    after it shipped. An attachment with no `pair` (a hand-made draft, or one
    written before the key existed) falls into a single unnamed group, which
    packs file by file: a duller comment boundary, never a wrong or missing
    picture."""
    return str((att or {}).get("pair") or "")


def _chunk_head(body) -> str:
    """The lead line a continuation comment repeats, taken from the draft's OWN
    first line. The gallery writes three different heads (with a baseline,
    without one, text-only), so a continuation that invented its own could
    promise a "before" picture that does not exist."""
    for line in str(body or "").splitlines():
        line = line.strip().rstrip(":").strip()
        if line:
            return line[:CHUNK_HEAD_MAX]
    return CHUNK_CONT_HEAD_SR


def plan_chunks(body, attachments, adapter):
    """Split ONE outbox draft into the comments the helpdesk will actually take.

    Returns `(chunks, error)`. `chunks` is `[{"body", "files", "names"}]` in the
    order the customer reads them; `error` refuses the WHOLE draft, and then
    nothing is sent at all.

    Three rules, in this order:

    * an attachment the helpdesk can never accept (wrong type, unreadable, over
      the per-file cap) refuses the draft HERE, before the first comment goes
      out. Dropping it silently would deliver a set with a hole nobody can see;
      discovering it at comment 3 of 4 leaves the customer a torn set.
    * the files of one before/after pair are never split while they fit in a
      comment of their own - the customer reads one screen's pictures together.
      A pair too big to fit anywhere is split file by file, because refusing it
      would deliver nothing.
    * both caps bind: the file COUNT (what bit on DEMO#05513) and the byte budget
      for one request.

    A draft with no attachments is one comment with the body unchanged - the
    text path is untouched by any of this.
    """
    files = [a for a in (attachments or []) if isinstance(a, dict)]
    if not files:
        return [{"body": str(body or ""), "files": [], "names": []}], ""

    limits = adapter.attachment_limits() or {}
    max_files = int(limits.get("max_files") or 0) or len(files)
    max_req = int(limits.get("max_request_bytes") or 0)

    sized, refused = [], []
    for a in files:
        size, reason = adapter.attachment_check(a)
        if reason:
            refused.append(reason)
        sized.append((a, int(size or 0)))
    if refused:
        return [], "; ".join(sorted(set(refused))[:3])

    groups = []                                    # [(group key, [(att, size)])]
    for a, size in sized:
        key = _att_group(a)
        if groups and groups[-1][0] == key:
            groups[-1][1].append((a, size))
        else:
            groups.append((key, [(a, size)]))

    packed, cur, cur_bytes = [], [], 0
    for _key, items in groups:
        whole = (len(items) <= max_files
                 and (not max_req or sum(s for _a, s in items) <= max_req))
        # The unit we place: the whole pair when it fits an empty comment, else
        # one file at a time. One greedy fill loop serves both.
        for unit in ([items] if whole else [[it] for it in items]):
            n, b = len(unit), sum(s for _a, s in unit)
            if cur and (len(cur) + n > max_files or (max_req and cur_bytes + b > max_req)):
                packed.append(cur)
                cur, cur_bytes = [], 0
            cur.extend(unit)
            cur_bytes += b
    if cur:
        packed.append(cur)

    total = len(packed)
    head = _chunk_head(body)
    chunks = []
    for i, unit in enumerate(packed):
        if total == 1:
            text = str(body or "")
        elif i == 0:
            text = (str(body or "").rstrip() + "\n\n" + CHUNK_TAIL_SR.format(n=total)).strip()
        else:
            text = CHUNK_CONT_SR.format(head=head, i=i + 1, n=total)
        chunks.append({"body": text, "files": [a for a, _s in unit],
                       "names": [str(a.get("name") or a.get("path") or "") for a, _s in unit]})
    return chunks, ""


def _draft(t, draft_id):
    """The outbox draft with this id, or None (it can be removed mid-send)."""
    box = t.get("outbox") if isinstance(t.get("outbox"), list) else []
    return next((dr for dr in box
                 if isinstance(dr, dict) and dr.get("id") == draft_id), None)


def _claim_chunk(fp, tid, draft_id, index, chunks) -> str:
    """Claim comment `index` of a chunked draft under the lock, BEFORE the POST.

    The draft-level claim in `run()` stops a crash from double-posting a draft;
    this is the same rule one level down, because one draft is now N irreversible
    sends and a retry must repost none of the ones that landed. The claimed plan
    is written out with each comment's attachment NAMES, so the store itself
    answers "which pictures does the customer already have".

    Returns "ok" (claimed - send it), "posted" (an earlier run delivered it -
    skip), "ambiguous" (an earlier run claimed it and never reported back: it may
    be live, so it is never re-sent), "changed" (the split no longer matches what
    was already sent) or "gone" (the draft was removed).
    """
    with store.filelock(fp):
        d = store.load(fp)
        dr = _draft(_ticket(d, tid), draft_id)
        if dr is None:
            return "gone"
        plan = [c["names"] for c in chunks]
        state = dr.get("chunks") if isinstance(dr.get("chunks"), list) else []
        if len(state) != len(plan) or any(
                (s.get("names") if isinstance(s, dict) else None) != p
                for s, p in zip(state, plan)):
            if any(isinstance(s, dict) and (s.get("posted") or s.get("posting_at"))
                   for s in state):
                # Part of this draft went out under a DIFFERENT split. Which
                # comment carried which picture is no longer knowable from here,
                # so nothing further is sent.
                return "changed"
            state = [{"names": p, "posted": False} for p in plan]
        s = state[index]
        if s.get("posted"):
            return "posted"
        if s.get("posting_at"):
            return "ambiguous"
        s["posting_at"] = _now()
        s.pop("error", None)
        dr["chunks"] = state
        _bump(d)
        store.atomic_write_json(fp, d)
        return "ok"


def _record_chunk(fp, tid, draft_id, index, *, posted=False, error=None, ambiguous=False):
    """Record ONE comment's outcome under the lock, straight after the call.

    A DEFINITE failure releases the claim, so a re-run sends that same comment
    again (it did not land). An AMBIGUOUS one leaves `posting_at` in place: the
    send may be live, and the next run must report it rather than repeat it.
    """
    with store.filelock(fp):
        d = store.load(fp)
        dr = _draft(_ticket(d, tid), draft_id)
        state = (dr or {}).get("chunks") if isinstance((dr or {}).get("chunks"), list) else []
        if dr is None or index >= len(state) or not isinstance(state[index], dict):
            return                                 # the draft moved under us
        s = state[index]
        if posted:
            s.pop("posting_at", None)
            s.pop("error", None)
            s["posted"] = True
            s["posted_at"] = _now()
        else:
            if not ambiguous:
                s.pop("posting_at", None)
            s["error"] = str(error or "")
        dr["chunks"] = state
        _bump(d)
        store.atomic_write_json(fp, d)


def _post_draft(root, fp, tid, draft_id, body, atts, adapter, approved_by, report):
    """Deliver ONE draft as the 1..N comments it needs. Returns
    `(status, error, posted, total)` with status "ok" | "fail" | "ambiguous".

    Stops at the FIRST comment that does not land, and says how far it got. A
    re-run resumes at exactly that comment: the ones before it are recorded as
    posted and are never sent twice. Posting past a hole would leave the customer
    a set numbered "1 of 3" then "3 of 3" with the middle missing, which reads as
    a bug rather than as an interrupted send.
    """
    chunks, err = plan_chunks(body, atts, adapter)
    if err:
        # Pre-flight: nothing was sent, and it is logged as the definite failure
        # it is - never as an ambiguous one.
        _log_sent(root, fp, tid, "comment", body, "fail", err,
                  report=report, approved_by=approved_by)
        return "fail", err, 0, 0
    total, posted = len(chunks), 0
    for i, ch in enumerate(chunks):
        claim = _claim_chunk(fp, tid, draft_id, i, chunks)
        if claim == "posted":
            posted += 1
            continue
        if claim in ("ambiguous", "changed"):
            return "ambiguous", (
                f"comment {i + 1} of {total} was claimed by an earlier run and may "
                "already be live" if claim == "ambiguous" else
                "the attachment set changed after part of it was sent"), posted, total
        if claim == "gone":
            return "fail", "the draft was removed mid-send", posted, total
        preview = ch["body"] + (f"  [+{len(ch['files'])} priloga]" if ch["files"] else "")
        try:
            (adapter.add_comment(tid, ch["body"], files=ch["files"]) if ch["files"]
             else adapter.add_comment(tid, ch["body"]))
        except HelpdeskError as exc:
            amb = bool(getattr(exc, "ambiguous", False))
            _record_chunk(fp, tid, draft_id, i, error=exc, ambiguous=amb)
            _log_sent(root, fp, tid, "comment", preview, _http_outcome(exc), str(exc),
                      report=report, approved_by=approved_by)
            return ("ambiguous" if amb else "fail"), str(exc), posted, total
        _record_chunk(fp, tid, draft_id, i, posted=True)
        posted += 1
        _log_sent(root, fp, tid, "comment", preview, "ok",
                  report=report, approved_by=approved_by)
    return "ok", None, posted, total


def _log_sent(root, fp, tid, kind, preview, http, error=None,
              approved_by="operator", pre_image=None, report=None) -> bool:
    """One send -> one row in the per-device sent log. Called straight after the
    adapter returns (before the store write), so the record of an irreversible
    call exists even if the local mirror write then fails. Keyed by the module
    FILE STEM (`fp.stem`), never by the caller's `--module` argument: on Windows
    `store.resolve` accepts "popis" for INV.json, and a row keyed "popis"
    would never match the HUD's `(fp.stem, id)` lookup. A failed append is
    printed and counted in `report["sent_log_failed"]` - the send already went
    out, so the miss must be visible, never silent."""
    ok = sent_log.append(root, {"module": Path(fp).stem, "ticket": tid, "kind": kind,
                                "preview": preview, "approved_by": approved_by,
                                "http": http, "error": error,
                                "pre_image": pre_image})
    if not ok:
        print(f"  ! sent_log write failed for {kind} on #{tid} - the send DID go out; "
              f"record it by hand", file=sys.stderr)
        if isinstance(report, dict):
            report["sent_log_failed"] = int(report.get("sent_log_failed") or 0) + 1
    return ok


def run(root, project, ticket_id, close_resolution=None, estimate=None,
        source="acme_helpdesk", adapter=None, *, approved_by="operator",
        allow_technical=False, only_drafts=None):
    """Post this ticket's unposted outbox drafts and, with a resolution, close it.

    `close_resolution=None` posts and does NOT close — the ticket's status is
    never read and never written on that path, so a comment can go out on an open
    ticket and on a closed one alike.

    `only_drafts` names the draft ids this call may deliver; every other draft is
    left exactly as it was, unclaimed. It exists because delivery now has two
    doors — the CLI close, which sends everything the ticket has waiting, and the
    HUD gallery's send button, which sends the ONE draft the operator just
    confirmed by pair and picture count. A confirmation that names three pictures
    and then also posts a hand-written draft the operator forgot about is a lie
    about an irreversible act. `None` means "everything unposted", which is what
    the close path has always meant.
    """
    fp = store.resolve(root, project)
    if fp is None or not fp.is_file():
        raise SystemExit(f"no tiketi.json for project {project!r} (sync first)")
    tid = str(ticket_id)
    adapter = adapter or get_adapter(source)()
    # `posted`/`failed`/`ambiguous` are draft ids; `comments` is the per-draft
    # delivery count ({"draft", "posted", "total", "status"}), because a draft
    # that got 2 of its 3 comments out is neither a success nor a total failure
    # and must not be reported as either.
    report = {"posted": [], "failed": [], "ambiguous": [], "closed": False,
              "comments": []}
    # The helpdesk is the customer's screen: a resolution that reads like an
    # engineering changelog is refused HERE, in the one door, so no caller (CLI,
    # sync.close_out, the HUD) can send it by another path (2026-08-19: #08597
    # was closed through close_out with the internal report).
    if close_resolution is not None and not allow_technical:
        hit = looks_technical(close_resolution)
        if hit:
            report["close_error"] = f"resolution reads technical (matched {hit!r}); refused"
            report["close_refused_technical"] = True
            return report
    # Validate the estimate FIRST: an out-of-range value must be refused before
    # any irreversible comment/close goes out, not discovered after the close.
    hours = None
    if estimate is not None:
        try:
            hours = _validate_hours(estimate)          # same rule as set_estimate_only
        except ValueError as exc:
            report["estimate_error"] = str(exc)
            return report

    # -- A. claim unposted drafts BY STABLE ID, under the lock -------------
    # By id, not index: the user can add/remove drafts in the view during the
    # network window, which shifts positions — reconciling by index would then
    # mark the wrong (unsent) draft as posted.
    claimed = []                                   # [(draft_id, body, attachments)]
    only = None if only_drafts is None else {str(x) for x in only_drafts}
    with store.filelock(fp):
        d = store.load(fp)
        t = _ticket(d, tid)
        outbox = t.get("outbox") if isinstance(t.get("outbox"), list) else []
        changed = False
        for dr in outbox:
            if not isinstance(dr, dict) or dr.get("posted"):
                continue
            if not dr.get("id"):                   # older drafts predate the id
                dr["id"] = uuid.uuid4().hex[:12]
                changed = True
            if only is not None and str(dr.get("id")) not in only:
                # Not this call's draft: not claimed, not reported, not touched.
                # The filter sits AFTER the id backfill so a draft that predates
                # ids can still be named by a caller that has just written it.
                continue
            if dr.get("posting_at"):               # a prior run claimed it; unknown
                report["ambiguous"].append(dr["id"])
                continue
            dr["posting_at"] = _now()
            changed = True
            claimed.append((dr["id"], dr.get("body", ""),
                            [a for a in (dr.get("attachments") or []) if isinstance(a, dict)]))
        if changed:
            t["outbox"] = outbox
            _bump(d)
            store.atomic_write_json(fp, d)

    if report["ambiguous"]:
        print(f"  ! {len(report['ambiguous'])} draft(s) were claimed by a prior "
              f"run and may already be posted — check the helpdesk; not reposting.")

    # -- B. deliver each claimed draft OUTSIDE the lock; stop on first failure --
    # The shots draft (visual-diff) carries before/after images and may need more
    # than one comment to hold them; every other draft has none and comes out of
    # `_post_draft` as the single plain-text comment it always was.
    results = {}                                   # draft_id -> (status, err)
    for cid, body, atts in claimed:
        status, err, posted_n, total_n = _post_draft(root, fp, tid, cid, body, atts,
                                                     adapter, approved_by, report)
        results[cid] = (status, err)
        report["comments"].append({"draft": cid, "posted": posted_n, "total": total_n,
                                   "status": status, "error": err})
        if status != "ok":
            break

    # -- C. reconcile BY ID under the lock ---------------------------------
    if claimed:
        with store.filelock(fp):
            d = store.load(fp)
            t = _ticket(d, tid)
            outbox = t.get("outbox") if isinstance(t.get("outbox"), list) else []
            by_id = {dr.get("id"): dr for dr in outbox if isinstance(dr, dict)}
            for cid, _body, _atts in claimed:
                dr = by_id.get(cid)
                if dr is None:                     # draft removed meanwhile
                    continue
                status = results.get(cid)
                if status is None:                 # claimed but never attempted
                    dr.pop("posting_at", None)      # (we broke first) — release it
                elif status[0] == "ok":
                    dr.pop("posting_at", None)
                    dr.pop("error", None)
                    dr["posted"] = True
                    dr["posted_at"] = _now()
                    report["posted"].append(cid)
                elif status[0] == "ambiguous":
                    # One of its comments may be live on the helpdesk. The
                    # draft-level claim STAYS, so the next run reports it instead
                    # of sending anything again; `dr["chunks"]` says exactly how
                    # far the delivery got.
                    dr["error"] = status[1]
                    report["ambiguous"].append(cid)
                else:
                    dr.pop("posting_at", None)      # definite: nothing landed here
                    dr["error"] = status[1]
                    report["failed"].append(cid)
            t["outbox"] = outbox
            _bump(d)
            store.atomic_write_json(fp, d)

    # -- D. close (irreversible) only if EVERY comment landed --------------
    if report["failed"] or report["ambiguous"]:
        print("  ! comments did not all post — NOT closing.")
        return report

    if close_resolution is not None:
        # Claim the close the same way: check the local closed-state and a prior
        # claim under the lock BEFORE firing, so a re-run or a lost close-response
        # never re-fires the irreversible close (duplicate notifications/webhooks).
        do_close = False
        with store.filelock(fp):
            d = store.load(fp)
            t = _ticket(d, tid)
            if t.get("closed") or (t.get("helpdesk") or {}).get("is_closed"):
                report["already_closed"] = True
            elif t.get("closing_at"):
                report["close_ambiguous"] = True
            else:
                t["closing_at"] = _now()
                _bump(d)
                store.atomic_write_json(fp, d)
                do_close = True
        if do_close:
            try:
                adapter.close(tid, close_resolution)
                report["closed"] = True
                _log_sent(root, fp, tid, "close", close_resolution, "ok", report=report,
                          approved_by=approved_by)
                with store.filelock(fp):
                    d = store.load(fp)
                    t = _ticket(d, tid)
                    t.pop("closing_at", None)
                    t["status"] = "done"
                    hd = t.get("helpdesk") if isinstance(t.get("helpdesk"), dict) else {}
                    hd["is_closed"] = True
                    t["helpdesk"] = hd
                    # Same three-way stamp sync._closed_stamp writes: without the
                    # triage state, store.queue_status() (which prefers it) kept
                    # calling a closed ticket "active" (reviewer 2026-08-19).
                    tri = t.get("triage") if isinstance(t.get("triage"), dict) else {}
                    tri["state"] = "done"
                    t["triage"] = tri
                    t["closed"] = {"at": _now(), "resolution": close_resolution}
                    _bump(d)
                    store.atomic_write_json(fp, d)
            except HelpdeskError as exc:
                # Leave closing_at set: a lost response is now AMBIGUOUS on the
                # next run, not silently re-fired. The operator checks the helpdesk.
                report["close_error"] = str(exc)
                _log_sent(root, fp, tid, "close", close_resolution, _http_outcome(exc),
                          str(exc), approved_by=approved_by, report=report)

    if hours is not None:
        try:
            adapter.set_estimate(tid, hours)
            report["estimate_set"] = hours
            _log_sent(root, fp, tid, "estimate", hours, "ok",
                      approved_by=approved_by, report=report)
        except HelpdeskError as exc:
            report["estimate_error"] = str(exc)
            _log_sent(root, fp, tid, "estimate", hours, _http_outcome(exc), str(exc),
                      approved_by=approved_by, report=report)

    return report


def set_estimate_only(root, project, ticket_id, hours, *, pre_image=None,
                      approved_by="gemini", source="acme_helpdesk", adapter=None,
                      extra=None) -> dict:
    """Set ONLY the time estimate on the helpdesk and mirror it locally.
    Returns {"estimate_set": hours} or {"estimate_error": "..."} (also for an
    out-of-range/invalid `hours` - never raises, so a batch caller skips ONE
    ticket instead of aborting the pass); may add "store_error" when the PATCH
    landed but the local mirror write failed.

    A separate door from `run` because the estimate write has neither of run's
    two problems and must not drag its machinery along: PATCH estimate carries
    the value, so re-sending the same number leaves the same state — no claim is
    taken, and none should be added. It stays IN this module because writeback
    is the only door to the helpdesk (test_outbound_door.py enforces that).

    `pre_image` is the value read from the helpdesk immediately before the call
    (F3 reads it fresh, never from the local mirror) and is kept in the sent log,
    so "what did this overwrite" stays answerable.

    The local `triage.ai_estimate` mirror is written ONLY after the helpdesk
    accepted the value — a mirror that claims an estimate the helpdesk never got
    would stop the next pass from retrying it.

    `extra` is merged INTO that mirror (the estimator's raw model number, the
    calibration factor, its basis and the one-line why). It is carried here
    rather than written by the caller afterwards because a second write would
    need its own lock round and could land while the helpdesk value and the
    mirror disagree; `hours`/`at`/`by` always win over anything `extra` repeats.
    """
    try:
        h = _validate_hours(hours)             # refused before anything is sent
    except ValueError as exc:
        return {"estimate_error": str(exc)}    # per-ticket, so a batch caller skips one
    fp = store.resolve(root, project)
    if fp is None or not fp.is_file():
        raise SystemExit(f"no tiketi.json for project {project!r} (sync first)")
    tid = str(ticket_id)
    _ticket(store.load(fp), tid)               # refuse an unknown ticket BEFORE the call
    adapter = adapter or get_adapter(source)()

    report = {}
    try:
        adapter.set_estimate(tid, h)
    except HelpdeskError as exc:
        _log_sent(root, fp, tid, "estimate", h, _http_outcome(exc), str(exc),
                  approved_by=approved_by, pre_image=pre_image, report=report)
        report["estimate_error"] = str(exc)
        return report
    _log_sent(root, fp, tid, "estimate", h, "ok",
              approved_by=approved_by, pre_image=pre_image, report=report)
    report["estimate_set"] = h
    try:
        with store.filelock(fp):
            d = store.load(fp)
            t = _ticket(d, tid)
            tri = t.get("triage") if isinstance(t.get("triage"), dict) else {}
            ai = dict(extra) if isinstance(extra, dict) else {}
            ai.update({"hours": h, "at": _now(), "by": approved_by})
            tri["ai_estimate"] = ai
            t["triage"] = tri
            _bump(d)
            store.atomic_write_json(fp, d)
    except (SystemExit, OSError, TimeoutError, ValueError) as exc:
        # The PATCH landed; only the local mirror missed. Reported, never
        # swallowed — the sent log already holds the authoritative record.
        report["store_error"] = str(exc)
    return report


def create_ticket_only(root, project, payload, *, approved_by="operator",
                       source="acme_helpdesk", adapter=None) -> dict:
    """Create a new ticket on the helpdesk (F10, voice `create_ticket` step) and
    mirror it into the module file. Returns {"created": True, "ticket_id": ...}
    or {"create_error": "..."} - never raises, so a batch/voice caller reports
    the one failure instead of aborting.

    NOT idempotent - the API has no dedup key for a create, same class of
    problem `run()`'s claim-before-post solves for comments; here the caller
    (voice.py's single-use approval token) is what stops a double-click from
    double-creating.

    The mirror write reuses `sync.merge` - the SAME merge a pull applies - on
    the ONE record the create response gives us, under the file lock; this is
    the smaller of the two options the brief named (triggering a full
    `sync.run` would re-list and re-fetch every ticket already in the queue
    over the network, just to pick up the one we already have in hand).
    """
    fp = store.resolve(root, project)
    if fp is None or not fp.is_file():
        raise SystemExit(f"no tiketi.json for project {project!r} (sync first)")
    adapter = adapter or get_adapter(source)()
    body = {
        "ticket_title": payload.get("title") or "",
        "ticket_description": payload.get("description") or "",
        "module": payload.get("module") or "",
        "category": payload.get("category") or "",
        "priority": payload.get("priority") or "Minor",
        "assign_to_me": bool(payload.get("assign_to_me")),
    }
    preview = body["ticket_title"]
    try:
        detail = adapter.create_ticket(body)
    except HelpdeskError as exc:
        _log_sent(root, fp, "", "create", preview, _http_outcome(exc), str(exc),
                  approved_by=approved_by)
        return {"create_error": str(exc)}
    rec = adapter.to_record(detail if isinstance(detail, dict) else {})
    tid = rec.get("id") or ""
    _log_sent(root, fp, tid, "create", preview, "ok", approved_by=approved_by)
    if not tid:
        return {"created": True, "ticket_id": "", "store_error": "no ticket id in the response"}
    try:
        import sync as ticket_sync          # lazy: same reason as `import writeback` in estimate.py -
                                              # keep this module's own stdout reconfigure the only side effect
        with store.filelock(fp):
            d = store.load(fp) or {}
            merged, _report = ticket_sync.merge(d, [rec])
            _bump(merged)
            store.atomic_write_json(fp, merged)
    except (OSError, TimeoutError, ValueError) as exc:
        # The POST landed and is logged above; only the local mirror missed -
        # the next sync of this module picks it up.
        return {"created": True, "ticket_id": tid, "store_error": str(exc)}
    return {"created": True, "ticket_id": tid}


_EDIT_FIELDS = ("title", "description", "module", "category", "priority")
#: voice.py's payload keys -> the API's own field names (mirrors create's body).
_EDIT_API_KEYS = {"title": "ticket_title", "description": "ticket_description"}


def edit_ticket_only(root, project, ticket_id, fields, *, approved_by="operator",
                     source="acme_helpdesk", adapter=None) -> dict:
    """PATCH a subset of title/description/module/category/priority on an
    existing ticket (F10, voice `edit_ticket` step). Returns
    {"edited": True, "fields": {...}} or {"edit_error": "..."} - never raises.

    Does NOT refresh the local mirror: unlike create's 201 (the full detail),
    the edit PATCH's response shape is not documented as the full ticket, so
    there is no record here safe to merge - the next sync (or Rescan) picks
    the change up, same as any other field sync.py already owns.
    """
    fp = store.resolve(root, project)
    if fp is None or not fp.is_file():
        raise SystemExit(f"no tiketi.json for project {project!r} (sync first)")
    tid = str(ticket_id)
    _ticket(store.load(fp), tid)             # refuse an unknown ticket BEFORE the call
    body = {k: v for k, v in (fields or {}).items() if k in _EDIT_FIELDS}
    if not body:
        return {"edit_error": "no fields to edit"}
    adapter = adapter or get_adapter(source)()
    api_body = {_EDIT_API_KEYS.get(k, k): v for k, v in body.items()}
    preview = ", ".join(f"{k}={v}" for k, v in body.items())
    try:
        adapter.edit_ticket(tid, api_body)
    except HelpdeskError as exc:
        _log_sent(root, fp, tid, "edit", preview, _http_outcome(exc), str(exc),
                  approved_by=approved_by)
        return {"edit_error": str(exc)}
    _log_sent(root, fp, tid, "edit", preview, "ok", approved_by=approved_by)
    return {"edited": True, "fields": body}


#: What a CUSTOMER must never read in a resolution or a comment: commit hashes,
#: source paths/test names, our internal decisions and open engineering items.
#: The helpdesk is the customer's screen; those belong in the store's `report`
#: / `notes` (2026-08-19: #93164 was closed with a changelog dump, #51571 got
#: an internal musing posted as a comment).
_TECH_RE = re.compile(
    r"(?xi)"
    r"\bcommit\b|\b[0-9a-f]{7,40}\b(?=.*\b(commit|on\s+main|main\)))"   # commit hashes
    r"|[\w/.-]+\.(py|js|html|css|json|ts)\b"                            # source paths / files
    r"|\btest_\w+"                                                    # test names
    r"|\bDONE\s*\(|\bOTVORENO\s*\(|\bOperater\s+izabrao\b|\bvan\s+obima\b"
    r"|\bX_FRAME_OPTIONS\b|\bxhtml2pdf\b|\b@font-face\b|\bmsgctxt\b|\biframe\b")


def looks_technical(text) -> str:
    """The first match that makes `text` unfit for the customer, else ""."""
    m = _TECH_RE.search(str(text or ""))
    return m.group(0) if m else ""


def _unposted(fp, tid) -> list:
    """The outbox draft RECORDS `run()` would send. One definition of "unposted",
    so the CLI's several questions about pending drafts (what will be posted, do
    any of them carry pictures) can never disagree about the set."""
    d = store.load(fp)
    t = _ticket(d, tid)
    return [dr for dr in (t.get("outbox") if isinstance(t.get("outbox"), list) else [])
            if isinstance(dr, dict) and not dr.get("posted")]


def unposted_drafts(fp, tid) -> list:
    """[(id, body)] of outbox drafts not yet posted - what `run()` WOULD send."""
    return [(dr.get("id") or "?", str(dr.get("body") or "")) for dr in _unposted(fp, tid)]


def draft_progress(dr) -> tuple:
    """`(delivered, planned)` comments of a draft that is PARTLY on the helpdesk
    already, else `(0, 0)`. A draft only counts as posted when every one of its
    comments landed, so a partly delivered one is still "unposted" - and neither
    the operator's post/drop question nor the report may present it as if the
    customer had seen nothing."""
    state = (dr or {}).get("chunks") if isinstance((dr or {}).get("chunks"), list) else []
    done = sum(1 for s in state if isinstance(s, dict) and s.get("posted"))
    return (done, len(state)) if done else (0, 0)


def unposted_shots_pairs(fp, tid) -> int:
    """How many before/after PAIRS ride along in the unposted `kind:"shots"`
    drafts the HUD gallery wrote (0 when there are none). The close routine asks
    this so the customer-facing resolution can point at the pictures."""
    return sum(int(dr.get("pair_count") or 0) or 1
               for dr in _unposted(fp, tid) if dr.get("kind") == "shots")


def drop_unposted_drafts(fp, tid) -> int:
    """Remove every unposted draft (the operator decided they are internal)."""
    with store.filelock(fp):
        d = store.load(fp)
        t = _ticket(d, tid)
        box = [dr for dr in (t.get("outbox") if isinstance(t.get("outbox"), list) else [])
               if not (isinstance(dr, dict) and not dr.get("posted"))]
        n = len(t.get("outbox") or []) - len(box)
        if n:
            t["outbox"] = box
            _bump(d)
            store.atomic_write_json(fp, d)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("TICKETS_STORE") or str(store.default_store()))
    ap.add_argument("--module", required=True, dest="project", help="module key = <MODULE>.json")
    ap.add_argument("--id", required=True, dest="ticket_id")
    ap.add_argument("--source", default="acme_helpdesk")
    ap.add_argument("--close", action="store_true",
                    help="close the ticket; the resolution text is read from stdin")
    ap.add_argument("--estimate", type=float, help="set the time estimate (hours)")
    ap.add_argument("--post-outbox", action="store_true",
                    help="with --close: POST the unposted outbox drafts too (they are customer-facing)")
    ap.add_argument("--drop-outbox", action="store_true",
                    help="with --close: DROP the unposted outbox drafts (they were internal notes)")
    ap.add_argument("--strict-shots", action="store_true",
                    help="with --close: REFUSE a resolution that does not mention the "
                         "pictures when a shots draft is about to be posted (default: warn)")
    ap.add_argument("--allow-technical", action="store_true",
                    help="send even though the text reads like an engineering changelog")
    args = ap.parse_args()

    fp = store.resolve(args.root, args.project)
    if fp is None or not fp.is_file():
        print(f"no tiketi.json for project {args.project!r} (sync first)", file=sys.stderr)
        return 2
    resolution = None
    if args.close:
        # The shell may hand us cp1252 on Windows; read the bytes as UTF-8 so
        # c/c/s/z/dj survive into the helpdesk (the API is UTF-8 end to end).
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                              # noqa: BLE001
            pass
        resolution = sys.stdin.read().strip()
        if not resolution:
            print("--close needs a resolution on stdin", file=sys.stderr)
            return 2
        hit = looks_technical(resolution)
        if hit and not args.allow_technical:
            print("  ! the resolution reads like an engineering changelog "
                  f"(matched: {hit!r}). The customer reads this. Rewrite it in their "
                  "language (what they see, how to use it); keep commits/files/open "
                  "items in the ticket report. Or pass --allow-technical on purpose.",
                  file=sys.stderr)
            return 3
        # Unposted drafts are about to be POSTED by the close. They are often
        # internal musings (#51571: "demo podaci, ne unositi") - the operator
        # must decide, explicitly, per close.
        drafts = unposted_drafts(fp, args.ticket_id)
        if drafts and not (args.post_outbox or args.drop_outbox):
            print(f"  ! {len(drafts)} unposted draft(s) in the outbox would be POSTED by this close:",
                  file=sys.stderr)
            for dr in _unposted(fp, args.ticket_id):
                flat = " ".join(str(dr.get("body") or "").split())
                done, total = draft_progress(dr)
                mark = f" [{done} of {total} comment(s) ALREADY on the helpdesk]" if done else ""
                print(f"      [{dr.get('id') or '?'}]{mark} {flat[:160]}", file=sys.stderr)
            print("    decide: --post-outbox (customer-facing) or --drop-outbox (internal notes).",
                  file=sys.stderr)
            return 4
        if drafts and args.drop_outbox:
            for dr in _unposted(fp, args.ticket_id):
                done, total = draft_progress(dr)
                if done:
                    print(f"  ! draft [{dr.get('id') or '?'}] already delivered {done} of {total} "
                          "comment(s) TO THE CUSTOMER; dropping it removes only our record of that.",
                          file=sys.stderr)
            n = drop_unposted_drafts(fp, args.ticket_id)
            print(f"  dropped {n} internal draft(s)")
        elif drafts:
            for did, body in drafts:
                hit = looks_technical(body)
                if hit and not args.allow_technical:
                    print(f"  ! draft [{did}] reads technical (matched: {hit!r}) - drop it, rewrite it, "
                          "or pass --allow-technical.", file=sys.stderr)
                    return 3
            # A shots draft (the HUD gallery's before/after pictures) is about to
            # be POSTED. The customer reads the RESOLUTION first and the comment
            # second, so a resolution that never mentions the comment leaves the
            # pictures unfound. Warn by default; --strict-shots makes it a refusal.
            pairs = unposted_shots_pairs(fp, args.ticket_id)
            if pairs and "komentar" not in resolution.lower():
                print(f"  ! {pairs} before/after pair(s) will be posted as a comment, but the "
                      'resolution never mentions it. Add: "Slike pre i posle su u komentaru."',
                      file=sys.stderr)
                if args.strict_shots:
                    return 5

    try:
        report = run(args.root, args.project, args.ticket_id, resolution,
                     args.estimate, args.source, allow_technical=args.allow_technical)
    except HelpdeskError as exc:
        print(f"helpdesk not reachable: {exc}", file=sys.stderr)
        return 2

    # Honest arithmetic: a draft that needed three comments and got two out is
    # neither "posted 1" nor "failed 1" - it is 2 of 3, and the operator has to
    # be able to see that without opening the store.
    parts = report.get("comments") or []
    sent = sum(int(c.get("posted") or 0) for c in parts)
    want = sum(int(c.get("total") or 0) for c in parts)
    print(f"posted {sent} of {want} comment(s) from {len(parts)} draft(s); "
          f"drafts failed {len(report['failed'])}; ambiguous {len(report['ambiguous'])}; "
          f"closed={report['closed']}")
    for c in parts:
        if c.get("status") == "ok":
            if int(c.get("total") or 0) > 1:
                print(f"  draft {c['draft']}: the pictures needed {c['total']} comments - all sent.")
            continue
        print(f"  ! draft {c['draft']}: posted {c.get('posted')} of {c.get('total')} comment(s)"
              f" - {c.get('error') or c.get('status')}")
        if c.get("posted"):
            print("    re-running sends ONLY the remainder; nothing already delivered is reposted.")
    for k in ("close_error", "estimate_error"):
        if report.get(k):
            print(f"  ! {k}: {report[k]}")

    if report.get("closed"):
        # F4: the paid half of customer-profile learning — the HUD has no close
        # ROUTE (closing happens via this CLI, `/brain:tickets close`), so this is
        # where the hook lives instead. Best-effort and silent without a key: a
        # close must never fail, or even print a warning, because of this.
        try:
            import profile_learn
            # Same gate as the HUD: the "customer_profiles" toggle AND a key
            # (bridged from agent_view.config.json when the env has none);
            # api_key="" -> gemini_client rotates across every configured key.
            on = profile_learn.enabled_by_config() and profile_learn.ensure_gemini_env()
            rep = profile_learn.learn_on_close(args.root, args.project, args.ticket_id,
                                               enabled=on, api_key="")
            if rep.get("ok"):
                print(f"  profile: learned from {rep.get('creator')!r}")
        except Exception:                              # noqa: BLE001 — never sink the close
            pass
    return 0 if not (report["failed"] or report.get("close_error")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
