#!/usr/bin/env python3
"""Offline tests for the writeback -> sent_log trail and the estimate-only door.

A fake adapter stands in for the helpdesk (same public surface as
adapters.acme_helpdesk.AcmeHelpdesk) and records every call; a temp dir
stands in for tickets_store. NO network, no real store.
Run: python test_writeback_sentlog.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sent_log  # noqa: E402
import writeback  # noqa: E402
from adapters.acme_helpdesk import AcmeHelpdesk, HelpdeskError  # noqa: E402

FAILS = []


def ck(label, cond, detail=""):
    """Same signature as the sibling test modules: the optional third argument is
    printed ONLY on a failure, so a red check says what it actually saw."""
    print(("PASS " if cond else "FAIL ") + label
          + (("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(label)


class FakeAdapter:
    """Records writes; never touches the network. `fail` names the methods that
    raise HelpdeskError, so a failure path is exercised with the same object.

    `fail_after` makes the N-th comment fail (`ambiguous` picks a transport-style
    failure over an HTTP one) - that is how a draft delivered as several comments
    is tested part-way through.
    """

    #: The REAL caps and the REAL per-file check - both are pure functions of the
    #: file, so a chunking test cannot pass against limits the helpdesk does not
    #: have. Same reason `to_record` below reuses the real mapping.
    _real = AcmeHelpdesk(url="https://tiket.example", token="x")

    def __init__(self, fail=(), fail_after=None, ambiguous=False):
        self.fail = set(fail)
        self.fail_after = fail_after
        self.ambiguous = ambiguous
        self.comments = []
        self.sent_files = []                  # [[customer-facing name, ...], ...]
        self.closed = []
        self.estimates = []
        self.created = []
        self.edited = []

    def attachment_limits(self):
        return self._real.attachment_limits()

    def attachment_check(self, f):
        return self._real.attachment_check(f)

    def add_comment(self, tid, text, files=None):
        if "add_comment" in self.fail:
            raise HelpdeskError("POST comment -> HTTP 500")
        if self.fail_after is not None and len(self.comments) >= self.fail_after:
            raise HelpdeskError("POST comment failed: TimeoutError" if self.ambiguous
                                else "POST comment -> HTTP 500", ambiguous=self.ambiguous)
        if files:
            # The server refuses an over-cap comment; so does this, or a chunking
            # bug would "pass" here and fail only on the live helpdesk.
            lim = self.attachment_limits()
            if len(files) > lim["max_files"]:
                raise HelpdeskError(f"too many attachments ({len(files)} > {lim['max_files']})")
            total = 0
            for f in files:
                size, reason = self.attachment_check(f)
                if reason:
                    raise HelpdeskError(reason)
                total += size
            if total > lim["max_request_bytes"]:
                raise HelpdeskError("attachments too large for one comment")
        self.comments.append((tid, text))
        self.sent_files.append([str((f or {}).get("name") or "") for f in (files or [])])

    def close(self, tid, resolution):
        if "close" in self.fail:
            raise HelpdeskError("PATCH close -> HTTP 400")
        self.closed.append((tid, resolution))

    def set_estimate(self, tid, hours):
        if "set_estimate" in self.fail:
            raise HelpdeskError("PATCH estimate -> HTTP 400")
        self.estimates.append((tid, hours))

    def create_ticket(self, payload):
        if "create_ticket" in self.fail:
            raise HelpdeskError("POST ticket -> HTTP 400")
        self.created.append(payload)
        return {"ticket_id": 555, "ticket_title": payload.get("ticket_title"),
                "ticket_description": payload.get("ticket_description"),
                "created_on": "2026-08-19T10:00:00",
                "customer": {"id": 1, "full_name": "Ana"}, "engineer": {},
                "category": {"name": payload.get("category")},
                "module": {"id": 9, "name": payload.get("module")},
                "priority": payload.get("priority"), "status": "Open",
                "is_closed": False, "comments": []}

    def edit_ticket(self, tid, fields):
        if "edit_ticket" in self.fail:
            raise HelpdeskError("PATCH edit -> HTTP 400")
        self.edited.append((tid, fields))

    def to_record(self, detail):
        # Reuses the REAL mapping (adapters.acme_helpdesk.to_record is a pure
        # function of `detail`) rather than a second, hand-rolled shape here.
        return AcmeHelpdesk(url="https://tiket.example", token="x").to_record(detail)


def _store(root, ticket=None):
    """One module file with one ticket carrying one unposted draft."""
    t = ticket if ticket is not None else {
        "status": "active",
        "helpdesk": {"is_closed": False, "estimated_time": 2},
        "outbox": [{"id": "d1", "body": "Popravljeno u formi."}],
    }
    d = {"project": {"name": "INV", "helpdesk_module": "INV"},
         "tickets": {"1": t}, "rev": 0}
    (Path(root) / "INV.json").write_text(json.dumps(d), encoding="utf-8")
    return d


def _read_store(root):
    return json.loads((Path(root) / "INV.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ comments
def test_a_posted_comment_leaves_an_ok_row():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        writeback.run(root, "INV", "1", adapter=fake)
        rows = sent_log.read(root)
        ck("comment: exactly one row", len(rows) == 1)
        ck("comment: kind=comment http=ok",
           rows[0]["kind"] == "comment" and rows[0]["http"] == "ok")
        ck("comment: no error on an ok row", rows[0]["error"] is None)
        ck("comment: the preview is the body",
           rows[0]["preview"] == "Popravljeno u formi.")
        ck("comment: module/ticket identify the ticket",
           (rows[0]["module"], rows[0]["ticket"]) == ("INV", "1"))
        ck("comment: approved_by defaults to the operator",
           rows[0]["approved_by"] == "operator")
        ck("comment: counts see it", sent_log.counts(root)[("INV", "1")]["comment"] == 1)


def test_a_failed_comment_leaves_a_fail_row_with_the_error():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter(fail=["add_comment"])
        report = writeback.run(root, "INV", "1", close_resolution="Gotovo.",
                               adapter=fake)
        rows = sent_log.read(root)
        ck("fail: one row for the attempt", len(rows) == 1)
        ck("fail: http=fail", rows[0]["http"] == "fail")
        ck("fail: the helpdesk error is recorded",
           "HTTP 500" in str(rows[0]["error"]))
        ck("fail: counts ignore it", sent_log.counts(root) == {})
        ck("fail: the close never ran (unchanged rule)",
           fake.closed == [] and report["closed"] is False)


def test_a_close_leaves_its_own_row():
    with tempfile.TemporaryDirectory() as root:
        _store(root, {"status": "active", "helpdesk": {"is_closed": False}, "outbox": []})
        fake = FakeAdapter()
        writeback.run(root, "INV", "1", close_resolution="Ispravljen save().",
                      adapter=fake, approved_by="operator")
        rows = sent_log.read(root)
        ck("close: one row, kind=close, ok",
           len(rows) == 1 and rows[0]["kind"] == "close" and rows[0]["http"] == "ok")
        ck("close: the resolution is the preview",
           rows[0]["preview"] == "Ispravljen save().")


def test_a_failed_close_leaves_a_fail_row():
    with tempfile.TemporaryDirectory() as root:
        _store(root, {"status": "active", "helpdesk": {"is_closed": False}, "outbox": []})
        fake = FakeAdapter(fail=["close"])
        report = writeback.run(root, "INV", "1", close_resolution="Gotovo.",
                               adapter=fake)
        rows = sent_log.read(root)
        ck("close fail: reported to the caller", bool(report.get("close_error")))
        ck("close fail: logged as fail with the error",
           len(rows) == 1 and rows[0]["http"] == "fail" and "HTTP 400" in str(rows[0]["error"]))


def test_approved_by_is_threaded_through():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        writeback.run(root, "INV", "1", adapter=FakeAdapter(), approved_by="gemini")
        ck("approved_by: reaches the log row",
           sent_log.read(root)[0]["approved_by"] == "gemini")


# ------------------------------------------------------------ estimate door
def test_set_estimate_only_refuses_zero_and_over_the_cap():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        for bad in (0, 41, -3, "x"):
            rep = writeback.set_estimate_only(root, "INV", "1", bad, adapter=fake)
            ck(f"refuse: {bad!r} -> estimate_error (returned, never raised)",
               isinstance(rep, dict) and "estimate_error" in rep and "estimate_set" not in rep)
        ck("refuse: nothing was sent", fake.estimates == [])
        ck("refuse: nothing was logged", sent_log.read(root) == [])
        ck("refuse: 40 is still allowed",
           writeback.set_estimate_only(root, "INV", "1", 40,
                                       adapter=fake).get("estimate_set") == 40.0)


def test_set_estimate_only_writes_the_mirror_and_logs_the_pre_image():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        report = writeback.set_estimate_only(root, "INV", "1", 3.5, pre_image=2,
                                             approved_by="gemini", adapter=fake)
        ck("estimate: reported as set", report == {"estimate_set": 3.5})
        ck("estimate: the adapter got the hours", fake.estimates == [("1", 3.5)])
        row = sent_log.read(root)[0]
        ck("estimate: logged kind=estimate http=ok",
           row["kind"] == "estimate" and row["http"] == "ok")
        ck("estimate: the pre_image is kept", row["pre_image"] == 2)
        ck("estimate: approved_by is the approver", row["approved_by"] == "gemini")
        tri = _read_store(root)["tickets"]["1"]["triage"]["ai_estimate"]
        ck("estimate: the local mirror carries hours/at/by",
           tri["hours"] == 3.5 and tri["by"] == "gemini" and bool(tri["at"]))
        ck("estimate: the file rev was bumped", _read_store(root)["rev"] == 1)


def test_set_estimate_only_does_not_mirror_a_failed_call():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter(fail=["set_estimate"])
        report = writeback.set_estimate_only(root, "INV", "1", 3.5, pre_image=2,
                                             adapter=fake)
        ck("estimate fail: reported as an error", "estimate_error" in report)
        row = sent_log.read(root)[0]
        ck("estimate fail: logged http=fail with the error",
           row["http"] == "fail" and "HTTP 400" in str(row["error"]))
        ck("estimate fail: the pre_image is still recorded", row["pre_image"] == 2)
        ck("estimate fail: NO local ai_estimate was written",
           "ai_estimate" not in (_read_store(root)["tickets"]["1"].get("triage") or {}))
        ck("estimate fail: counts stay empty", sent_log.counts(root) == {})


def test_an_unknown_ticket_is_refused_before_anything_is_sent():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        try:
            writeback.set_estimate_only(root, "INV", "999", 2, adapter=fake)
            ck("unknown: refused before the call", False)
        except SystemExit:
            ck("unknown: refused before the call", True)
        ck("unknown: nothing was sent or logged",
           fake.estimates == [] and sent_log.read(root) == [])


def test_the_run_estimate_path_clamps_the_same_way():
    with tempfile.TemporaryDirectory() as root:
        _store(root, {"status": "active", "helpdesk": {"is_closed": False}, "outbox": []})
        fake = FakeAdapter()
        report = writeback.run(root, "INV", "1", estimate=0, adapter=fake)
        ck("run: a 0-hour estimate is refused, not sent",
           "estimate_error" in report and fake.estimates == [])
        report = writeback.run(root, "INV", "1", estimate=2, adapter=fake)
        ck("run: a valid estimate is sent and logged",
           report.get("estimate_set") == 2.0 and fake.estimates == [("1", 2.0)])
        ck("run: the estimate row is in the log",
           sent_log.counts(root)[("INV", "1")]["estimate"] == 1)


class TransportFailAdapter(FakeAdapter):
    """A transport failure (timeout after the request left) — the adapter marks
    it `ambiguous`, because the helpdesk may have processed it; writeback must
    log that as AMBIGUOUS, never as a definite fail."""
    def close(self, tid, resolution):
        raise HelpdeskError("PATCH /close failed: TimeoutError", ambiguous=True)


def test_a_transport_failure_is_logged_unknown_not_fail():
    with tempfile.TemporaryDirectory() as root:
        _store(root, {"status": "active", "helpdesk": {"is_closed": False}, "outbox": []})
        report = writeback.run(root, "INV", "1", close_resolution="x", adapter=TransportFailAdapter())
        rows = sent_log.read(root)
        ck("ambiguous: close_error reported", bool(report.get("close_error")))
        ck("ambiguous: http=unknown (not fail)", len(rows) == 1 and rows[0]["http"] == "unknown")
        ck("ambiguous: not counted as sent", sent_log.counts(root) == {})


def test_the_log_is_keyed_by_the_file_stem_not_the_caller_argument():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        # store.resolve accepts a differently-cased module on Windows; the row
        # must still be keyed exactly like the HUD looks it up: (fp.stem, id).
        import store as _st
        if _st.resolve(root, "inv") is None:
            ck("stem: (case-sensitive FS, skipped)", True)
            return
        writeback.run(root, "inv", "1", adapter=fake)
        ck("stem: row keyed INV", ("INV", "1") in sent_log.counts(root))


def test_an_out_of_range_estimate_is_refused_before_the_close():
    with tempfile.TemporaryDirectory() as root:
        _store(root, {"status": "active", "helpdesk": {"is_closed": False}, "outbox": []})
        fake = FakeAdapter()
        report = writeback.run(root, "INV", "1", close_resolution="done", estimate=0, adapter=fake)
        ck("order: estimate refused", "estimate_error" in report)
        ck("order: the close did NOT fire", fake.closed == [] and not report.get("closed"))


def test_a_failed_log_write_is_visible_in_the_report():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        orig = sent_log.append
        sent_log.append = lambda *a, **k: False
        try:
            report = writeback.run(root, "INV", "1", adapter=fake)
        finally:
            sent_log.append = orig
        ck("logfail: comment still posted", fake.comments and report["posted"])
        ck("logfail: report counts the miss", report.get("sent_log_failed") == 1)


# --------------------------------------------------- one draft, many comments
#: What the HUD's gallery writes (agent_view/server.py::shots_draft): the
#: customer-facing name, and the PAIR the picture belongs to stated on the
#: attachment itself. This is the DEMO#05513 set that was refused whole - 3 pairs,
#: 13 files, against a cap of 5 - in the order and the wording the customer now
#: gets. The `pair` key is what the chunker groups by; the NAME is a label for
#: the customer's eyes and nothing may key off it.
def _pair_files(pair, n, screen, details):
    """One screen's pictures, named the way the gallery names them: the number of
    the description line, then the ROLE (up front, where a truncated file list
    cannot cut it off), then the screen."""
    return ([(pair, "%d-PRE-%s.png" % (n, screen)), (pair, "%d-POSLE-%s.png" % (n, screen))]
            + [(pair, "%d-POSLE-detalj-%d-%s.png" % (n, i, screen))
               for i in range(1, details + 1)])


DEMO05513 = (_pair_files("p1", 1, "karton-kvaliteta", 1)
            + _pair_files("p2", 2, "nova-lokacija", 4)
            + _pair_files("p3", 3, "detalj-lokacije", 2))
DEMO_NAMES = [n for _p, n in DEMO05513]
SHOTS_BODY = ("Slike ekrana pre i posle izmene:\n"
              "1. Karton kvaliteta \u2014 slike: PRE, POSLE, POSLE detalj 1\n"
              "2. Nova lokacija \u2014 slike: PRE, POSLE, POSLE detalj 1, POSLE detalj 2, "
              "POSLE detalj 3, POSLE detalj 4\n"
              "3. Detalj lokacije \u2014 slike: PRE, POSLE, POSLE detalj 1, POSLE detalj 2")


def _pngs(tmp, items, size=64):
    """`[(pair, name)]` -> the attachment records a shots draft carries."""
    out = []
    for pair, n in items:
        fp = Path(tmp) / n
        fp.write_bytes(b"\x89PNG" + b"0" * size)
        out.append({"path": str(fp), "name": n, "pair": pair})
    return out


def _shots_store(root, tmp, names=DEMO05513, body=SHOTS_BODY):
    _store(root, {"status": "active", "helpdesk": {"is_closed": False},
                  "outbox": [{"id": "d1", "kind": "shots", "body": body,
                              "attachments": _pngs(tmp, names), "pair_count": 3}]})


def _draft(root):
    return _read_store(root)["tickets"]["1"]["outbox"][0]


def test_a_draft_that_exceeds_the_attachment_cap_is_split_across_comments():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tmp:
        _shots_store(root, tmp)
        fake = FakeAdapter()
        report = writeback.run(root, "INV", "1", adapter=fake)
        ck("split: every attachment was delivered",
           sorted(n for c in fake.sent_files for n in c) == sorted(DEMO_NAMES),
           str(fake.sent_files))
        ck("split: no comment exceeds the helpdesk cap",
           all(len(c) <= 5 for c in fake.sent_files), str([len(c) for c in fake.sent_files]))
        ck("split: three comments, not one refusal", len(fake.comments) == 3,
           str(len(fake.comments)))
        ck("split: the draft counts as posted only once ALL of them landed",
           report["posted"] == ["d1"] and _draft(root).get("posted") is True)
        ck("split: the report says 3 of 3",
           report["comments"] == [{"draft": "d1", "posted": 3, "total": 3,
                                   "status": "ok", "error": None}], str(report["comments"]))
        ck("split: one sent-log row per comment that went out",
           len(sent_log.read(root)) == 3 and
           sent_log.counts(root)[("INV", "1")]["comment"] == 3)


def test_the_first_comment_carries_the_body_and_the_rest_stand_on_their_own():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tmp:
        _shots_store(root, tmp)
        fake = FakeAdapter()
        writeback.run(root, "INV", "1", adapter=fake)
        first = fake.comments[0][1]
        rest = [t for _tid, t in fake.comments[1:]]
        ck("text: the description is on the FIRST comment", first.startswith(SHOTS_BODY), first)
        ck("text: the first comment says more are coming", "1. od 3" in first, first)
        ck("text: each continuation says where it sits",
           all(("%d. od 3" % i) in t for i, t in enumerate(rest, start=2)), str(rest))
        ck("text: each continuation repeats the draft's own lead line",
           all(t.startswith("Slike ekrana pre i posle izmene") for t in rest), str(rest))
        ck("text: a continuation points back at the description",
           all("Opis izmena je u prvom komentaru." in t for t in rest), str(rest))
        ck("text: nothing customer-facing names a file or a folder",
           not any(".png" in t or "\\" in t or "/" in t for _tid, t in fake.comments),
           str([t for _t, t in fake.comments]))
        ck("text: no continuation reads technical",
           not any(writeback.looks_technical(t) for _tid, t in fake.comments))


def test_chunk_boundaries_follow_the_before_after_pairs_that_fit():
    with tempfile.TemporaryDirectory() as tmp:
        pairs = (_pair_files("a", 1, "prvi", 0) + _pair_files("b", 2, "drugi", 1)
                 + _pair_files("c", 3, "treci", 0))
        names = [n for _p, n in pairs]
        chunks, err = writeback.plan_chunks("Slike:", _pngs(tmp, pairs), FakeAdapter())
        got = [c["names"] for c in chunks]
        ck("pairs: no pair is torn apart when it fits in one comment",
           got == [names[0:5], names[5:7]], str(got))
        ck("pairs: no error", err == "")


def test_the_chunker_groups_by_the_pair_key_and_never_by_the_file_name():
    """THE REGRESSION THIS PINS: grouping used to be recovered by parsing the
    attachment name. The name is a LABEL for the customer, it was rewritten the
    day after it shipped, and a rewrite must not silently turn pair-aware packing
    into file-by-file packing. So the producer states `pair` and only that is
    read - names that look alike must not merge two screens, and names that look
    different must not split one."""
    with tempfile.TemporaryDirectory() as tmp:
        alike = [("p1", "1-PRE-ekran.png"), ("p1", "1-POSLE-ekran.png"),
                 ("p2", "2-PRE-ekran.png"), ("p2", "2-POSLE-ekran.png"),
                 ("p3", "3-PRE-ekran.png"), ("p3", "3-POSLE-ekran.png")]
        chunks, err = writeback.plan_chunks("Slike:", _pngs(tmp, alike), FakeAdapter())
        ck("group: three pairs of two pack as 4+2 - a pair is never torn to fill "
           "the last slot",
           err == "" and [len(c["names"]) for c in chunks] == [4, 2],
           str([c["names"] for c in chunks]))

        one_pair = [("p1", "1-POSLE-detalj-%d-sasvim-drugacije-ime-%d.png" % (i, i))
                    for i in range(1, 7)]
        chunks, err = writeback.plan_chunks("Slike:", _pngs(tmp, one_pair), FakeAdapter())
        ck("group: six pictures of ONE pair are one group, split only because six "
           "will not fit",
           err == "" and [len(c["names"]) for c in chunks] == [5, 1],
           str([c["names"] for c in chunks]))

        no_pair = [("", "prilog-%d.png" % i) for i in range(1, 8)]
        chunks, err = writeback.plan_chunks("Slike:", _pngs(tmp, no_pair), FakeAdapter())
        ck("group: attachments with no pair still deliver, packed file by file",
           err == "" and [len(c["names"]) for c in chunks] == [5, 2],
           str([c["names"] for c in chunks]))


def test_a_pair_too_big_for_one_comment_is_split_rather_than_refused():
    with tempfile.TemporaryDirectory() as tmp:
        big = _pair_files("p", 1, "jedan-ekran", 5)                     # 7 > cap of 5
        chunks, err = writeback.plan_chunks("Slike:", _pngs(tmp, big), FakeAdapter())
        ck("oversized pair: delivered, not refused", err == "" and len(chunks) == 2, err)
        ck("oversized pair: every file still goes",
           sorted(n for c in chunks for n in c["names"]) == sorted(n for _p, n in big))
        ck("oversized pair: each comment is within the cap",
           all(len(c["names"]) <= 5 for c in chunks))


def test_the_byte_budget_splits_a_set_that_fits_by_count():
    with tempfile.TemporaryDirectory() as tmp:
        from adapters import acme_helpdesk as hd
        names = [("p%d" % i, "%d-POSLE-ekran.png" % i) for i in range(3)]
        files = _pngs(tmp, names, size=hd.ATTACH_MAX_REQUEST_BYTES // 2)
        chunks, err = writeback.plan_chunks("Slike:", files, FakeAdapter())
        ck("bytes: three files inside the count cap still split on size",
           err == "" and len(chunks) == 3, "%s %s" % (err, [c["names"] for c in chunks]))


def test_an_attachment_over_the_size_cap_refuses_the_whole_draft_before_any_send():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tmp:
        from adapters import acme_helpdesk as hd
        _shots_store(root, tmp, names=_pair_files("p1", 1, "prvi", 0))
        huge = Path(tmp) / "2-POSLE-drugi.png"
        huge.write_bytes(b"\x89PNG" + b"0" * (hd.ATTACH_MAX_BYTES + 1))
        d = _read_store(root)
        d["tickets"]["1"]["outbox"][0]["attachments"].append(
            {"path": str(huge), "name": "2-POSLE-drugi.png", "pair": "p2"})
        (Path(root) / "INV.json").write_text(json.dumps(d), encoding="utf-8")
        fake = FakeAdapter()
        report = writeback.run(root, "INV", "1", close_resolution="Gotovo.", adapter=fake)
        ck("oversize: NOTHING was sent - not even the files that would fit",
           fake.comments == [], str(fake.comments))
        ck("oversize: reported as a failed draft", report["failed"] == ["d1"])
        ck("oversize: the reason names the file, for the operator",
           "too large" in str(report["comments"][0]["error"]), str(report["comments"]))
        ck("oversize: logged as a DEFINITE failure (nothing left the machine)",
           [r["http"] for r in sent_log.read(root)] == ["fail"], str(sent_log.read(root)))
        ck("oversize: the close never ran", fake.closed == [] and not report["closed"])


def test_only_drafts_delivers_the_named_draft_and_leaves_the_rest_alone():
    """The HUD gallery's send button confirms ONE comment by pair and picture
    count and then posts it. Without this filter it would also post whatever else
    the ticket happened to have waiting - a hand-written draft the operator meant
    for later - which makes the confirmation a lie about an irreversible act.

    The untouched draft must come out completely untouched: not posted, and NOT
    CLAIMED either. A stray `posting_at` would make the next run call it
    ambiguous for ever, and nothing would send it again."""
    with tempfile.TemporaryDirectory() as root:
        _store(root, {"status": "active", "helpdesk": {"is_closed": False},
                      "outbox": [{"id": "mine", "body": "Slike ekrana."},
                                 {"id": "theirs", "body": "Nacrt za kasnije."}]})
        fake = FakeAdapter()
        report = writeback.run(root, "INV", "1", adapter=fake, only_drafts=["mine"])
        ck("only: exactly one comment went out", len(fake.comments) == 1, str(fake.comments))
        ck("only: and it is the named one",
           fake.comments[0][1] == "Slike ekrana.", str(fake.comments))
        box = {d["id"]: d for d in _read_store(root)["tickets"]["1"]["outbox"]}
        ck("only: the named draft is posted", box["mine"].get("posted") is True)
        ck("only: the other draft is untouched - not posted, and not claimed",
           box["theirs"].get("posted") is not True
           and not box["theirs"].get("posting_at")
           and not box["theirs"].get("chunks"), json.dumps(box["theirs"]))
        ck("only: it is not reported either - the caller asked about one draft",
           report["posted"] == ["mine"] and report["failed"] == []
           and report["ambiguous"] == [], str(report))
        ck("only: nothing was closed", fake.closed == [] and not report["closed"])
        # And the default is unchanged: no filter means everything waiting.
        report2 = writeback.run(root, "INV", "1", adapter=fake)
        ck("only: without the filter the remaining draft goes out as it always did",
           report2["posted"] == ["theirs"] and len(fake.comments) == 2, str(report2))


def test_a_partial_delivery_reports_as_partial_and_blocks_the_close():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tmp:
        _shots_store(root, tmp)
        fake = FakeAdapter(fail_after=2)                # comments 1 and 2 land, 3 does not
        report = writeback.run(root, "INV", "1", close_resolution="Gotovo.", adapter=fake)
        ck("partial: two comments are live", len(fake.comments) == 2)
        ck("partial: the report says 2 of 3, not success and not total failure",
           report["comments"] == [{"draft": "d1", "posted": 2, "total": 3, "status": "fail",
                                   "error": "POST comment -> HTTP 500"}], str(report["comments"]))
        ck("partial: the draft is NOT marked posted", not _draft(root).get("posted"))
        ck("partial: it is reported as a failed draft, so nothing reads as done",
           report["failed"] == ["d1"] and report["posted"] == [])
        ck("partial: the close did NOT fire", fake.closed == [] and not report["closed"])
        state = _draft(root)["chunks"]
        ck("partial: the store records which comments landed",
           [bool(s.get("posted")) for s in state] == [True, True, False], str(state))
        ck("partial: the failed comment holds no claim (it did not land)",
           not state[2].get("posting_at") and "HTTP 500" in str(state[2].get("error")))


def test_a_retry_after_a_partial_delivery_sends_only_the_remainder():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tmp:
        _shots_store(root, tmp)
        first = FakeAdapter(fail_after=2)
        writeback.run(root, "INV", "1", adapter=first)
        second = FakeAdapter()
        report = writeback.run(root, "INV", "1", adapter=second)
        ck("retry: only the missing comment is sent", len(second.comments) == 1,
           str(second.comments))
        ck("retry: it is the one that never landed - the last pair, whole",
           second.sent_files == [[n for p, n in DEMO05513 if p == "p3"]],
           str(second.sent_files))
        ck("retry: the customer got each picture exactly once",
           sorted(n for c in (first.sent_files + second.sent_files) for n in c)
           == sorted(DEMO_NAMES))
        ck("retry: the draft is posted now", report["posted"] == ["d1"]
           and _draft(root).get("posted") is True)
        ck("retry: the report counts the whole delivery, not just this run",
           report["comments"] == [{"draft": "d1", "posted": 3, "total": 3,
                                   "status": "ok", "error": None}], str(report["comments"]))


def test_an_ambiguous_comment_is_never_re_sent_by_a_retry():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tmp:
        _shots_store(root, tmp)
        first = FakeAdapter(fail_after=1, ambiguous=True)     # transport died mid-send
        rep1 = writeback.run(root, "INV", "1", adapter=first)
        ck("ambiguous: reported as ambiguous, not failed",
           rep1["ambiguous"] == ["d1"] and rep1["failed"] == [])
        ck("ambiguous: logged http=unknown - go check the helpdesk",
           sorted(r["http"] for r in sent_log.read(root)) == ["ok", "unknown"],
           str([r["http"] for r in sent_log.read(root)]))
        state = _draft(root)["chunks"]
        ck("ambiguous: the claim on comment 2 STAYS", bool(state[1].get("posting_at")), str(state))
        second = FakeAdapter()
        rep2 = writeback.run(root, "INV", "1", close_resolution="Gotovo.", adapter=second)
        ck("ambiguous: a retry sends NOTHING - the comment may already be live",
           second.comments == [], str(second.comments))
        ck("ambiguous: and it stays ambiguous rather than turning into a success",
           rep2["ambiguous"] == ["d1"] and rep2["posted"] == [])
        ck("ambiguous: the close is still blocked", second.closed == [] and not rep2["closed"])


def test_a_text_only_draft_is_untouched_by_the_chunker():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        report = writeback.run(root, "INV", "1", adapter=fake)
        ck("text: one comment, body verbatim",
           fake.comments == [("1", "Popravljeno u formi.")], str(fake.comments))
        state = _draft(root).get("chunks") or []
        ck("text: the single comment is claimed and recorded like every other one",
           len(state) == 1 and state[0].get("posted") is True and state[0].get("names") == [],
           str(state))
        ck("text: reported as 1 of 1", report["comments"][0]["posted"] == 1
           and report["comments"][0]["total"] == 1)


def test_a_partly_delivered_draft_is_flagged_to_the_operator():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tmp:
        _shots_store(root, tmp)
        writeback.run(root, "INV", "1", adapter=FakeAdapter(fail_after=2))
        fp = Path(root) / "INV.json"
        dr = writeback._unposted(fp, "1")[0]
        ck("progress: the operator's list can say 2 of 3 are already live",
           writeback.draft_progress(dr) == (2, 3), str(writeback.draft_progress(dr)))


# ------------------------------------------------------------ create/edit door
def test_create_ticket_only_creates_and_mirrors_into_the_store():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        payload = {"title": "Ne radi export", "description": "Klik na dugme ne radi.",
                   "module": "INV", "category": "Bug", "priority": "Major",
                   "assign_to_me": True}
        rep = writeback.create_ticket_only(root, "INV", payload, adapter=fake)
        ck("create: reported as created with the new id",
           rep.get("created") is True and rep.get("ticket_id") == "555")
        ck("create: the adapter got the API's own field names",
           fake.created[0]["ticket_title"] == "Ne radi export"
           and fake.created[0]["assign_to_me"] is True)
        row = sent_log.read(root)[0]
        ck("create: logged kind=create http=ok, keyed by the new ticket id",
           row["kind"] == "create" and row["http"] == "ok" and row["ticket"] == "555")
        ck("create: the preview is the title", row["preview"] == "Ne radi export")
        d = _read_store(root)
        ck("create: the new ticket is merged into the module file",
           "555" in d["tickets"] and d["tickets"]["555"]["title"] == "Ne radi export")
        ck("create: the existing ticket is untouched", "1" in d["tickets"])
        ck("create: the file rev was bumped", d["rev"] == 1)


def test_create_ticket_only_logs_the_failure_and_never_raises():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter(fail=["create_ticket"])
        rep = writeback.create_ticket_only(root, "INV", {"title": "x", "description": "y",
                                                            "module": "INV", "category": "Bug"},
                                           adapter=fake)
        ck("create fail: reported as an error, not raised", "create_error" in rep)
        row = sent_log.read(root)[0]
        ck("create fail: logged http=fail with no ticket id yet",
           row["http"] == "fail" and row["ticket"] == "" and "HTTP 400" in str(row["error"]))
        ck("create fail: nothing merged into the store", "555" not in _read_store(root)["tickets"])


def test_edit_ticket_only_patches_and_logs():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        rep = writeback.edit_ticket_only(root, "INV", "1",
                                         {"title": "Novi naslov", "priority": "Critical"},
                                         adapter=fake, approved_by="gemini")
        ck("edit: reported as edited with the accepted fields",
           rep == {"edited": True, "fields": {"title": "Novi naslov", "priority": "Critical"}})
        ck("edit: the adapter got the API's own field names",
           fake.edited == [("1", {"ticket_title": "Novi naslov", "priority": "Critical"})])
        row = sent_log.read(root)[0]
        ck("edit: logged kind=edit http=ok, approved_by threaded through",
           row["kind"] == "edit" and row["http"] == "ok" and row["approved_by"] == "gemini")
        ck("edit: an unrecognised field is dropped, not sent",
           writeback.edit_ticket_only(root, "INV", "1", {"nope": "x"}, adapter=fake)
           .get("edit_error") == "no fields to edit")


def test_edit_ticket_only_refuses_an_unknown_ticket():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        try:
            writeback.edit_ticket_only(root, "INV", "999", {"title": "x"}, adapter=fake)
            ck("edit: unknown ticket refused before the call", False)
        except SystemExit:
            ck("edit: unknown ticket refused before the call", True)
        ck("edit: nothing was sent or logged", fake.edited == [] and sent_log.read(root) == [])


def test_edit_ticket_only_refuses_empty_fields_without_sending():
    with tempfile.TemporaryDirectory() as root:
        _store(root)
        fake = FakeAdapter()
        rep = writeback.edit_ticket_only(root, "INV", "1", {}, adapter=fake)
        ck("edit: empty fields -> edit_error, nothing sent", rep.get("edit_error") == "no fields to edit"
           and fake.edited == [] and sent_log.read(root) == [])


def main() -> int:
    for fn in (test_a_posted_comment_leaves_an_ok_row,
               test_a_transport_failure_is_logged_unknown_not_fail,
               test_the_log_is_keyed_by_the_file_stem_not_the_caller_argument,
               test_an_out_of_range_estimate_is_refused_before_the_close,
               test_a_failed_log_write_is_visible_in_the_report,
               test_a_failed_comment_leaves_a_fail_row_with_the_error,
               test_a_close_leaves_its_own_row,
               test_a_failed_close_leaves_a_fail_row,
               test_approved_by_is_threaded_through,
               test_set_estimate_only_refuses_zero_and_over_the_cap,
               test_set_estimate_only_writes_the_mirror_and_logs_the_pre_image,
               test_set_estimate_only_does_not_mirror_a_failed_call,
               test_an_unknown_ticket_is_refused_before_anything_is_sent,
               test_the_run_estimate_path_clamps_the_same_way,
               test_a_draft_that_exceeds_the_attachment_cap_is_split_across_comments,
               test_the_first_comment_carries_the_body_and_the_rest_stand_on_their_own,
               test_chunk_boundaries_follow_the_before_after_pairs_that_fit,
               test_the_chunker_groups_by_the_pair_key_and_never_by_the_file_name,
               test_a_pair_too_big_for_one_comment_is_split_rather_than_refused,
               test_the_byte_budget_splits_a_set_that_fits_by_count,
               test_an_attachment_over_the_size_cap_refuses_the_whole_draft_before_any_send,
               test_only_drafts_delivers_the_named_draft_and_leaves_the_rest_alone,
               test_a_partial_delivery_reports_as_partial_and_blocks_the_close,
               test_a_retry_after_a_partial_delivery_sends_only_the_remainder,
               test_an_ambiguous_comment_is_never_re_sent_by_a_retry,
               test_a_text_only_draft_is_untouched_by_the_chunker,
               test_a_partly_delivered_draft_is_flagged_to_the_operator,
               test_create_ticket_only_creates_and_mirrors_into_the_store,
               test_create_ticket_only_logs_the_failure_and_never_raises,
               test_edit_ticket_only_patches_and_logs,
               test_edit_ticket_only_refuses_an_unknown_ticket,
               test_edit_ticket_only_refuses_empty_fields_without_sending):
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            ck(f"{fn.__name__} (raised): {type(exc).__name__}: {exc}", False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
