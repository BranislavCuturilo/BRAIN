"""A run may not be deleted while the customer has nothing.

Pins the incident of 2026-08-28 on DEMO#43417: both pairs were decided, no
shots draft was ever written, `_shots_undelivered` returned 0, `shots_finished`
said "finished", the folder was promoted and REMOVED — and the ticket had no
comment and no images. `sent_log` for it carries `estimate` and `close` and no
`comment` at all. Operator: "poslati snimci u komentar tiketa nisu otisli a
obrisali su se iz snimci pre/posle sekcije".

The count could never have caught it: `undelivered == 0` means BOTH "the send
landed" (the posted draft stays in the outbox) and "no send was ever made"
(there is no draft). Only the presence of a draft tells them apart.

Run: python agent_view/test_shots_finished.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server                                                  # noqa: E402


def _man(decisions, ticket="DEMO#43417"):
    return {"ticket": ticket, "version": 1,
            "pairs": [{"page_id": "p%d" % i, "drop": False, "decision": d}
                      for i, d in enumerate(decisions)]}


class _Base(unittest.TestCase):
    """Every test drives the real functions against a temp ticket store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._orig = server._resolve_ticket_file
        server._resolve_ticket_file = lambda mod: self.fp
        self.addCleanup(lambda: setattr(server, "_resolve_ticket_file", self._orig))
        self.fp = self.root / "DEMO.json"
        self._write(outbox=None)

    def _write(self, outbox):
        t = {"status": "done"}
        if outbox is not None:
            t["outbox"] = outbox
        self.fp.write_text(json.dumps(
            {"project": {}, "tickets": {"43417": t}, "rev": 1}), encoding="utf-8")


class NeverSentTests(_Base):
    def test_approved_with_no_draft_is_NOT_finished(self):
        """THE incident. Nothing pending, because nothing was ever drafted."""
        man = _man(["approved", "approved"])
        self.assertEqual(server._shots_undelivered("w1", man=man), 0)
        self.assertFalse(server._shots_ever_drafted("w1", man=man))
        finished, reason = server.shots_finished("w1", man=man)
        self.assertFalse(finished, "a run whose pictures never left was deleted")
        self.assertIn("never sent", reason)

    def test_the_operator_is_told_why_and_how(self):
        stay = server.shots_stay("w1", man=_man(["approved"]))
        self.assertFalse(stay["finished"])
        self.assertFalse(stay["blocked"], "the way out is send, not discard")
        self.assertIn("pošalji", stay["reason"])


class DeliveredTests(_Base):
    def test_a_posted_draft_finishes_the_run(self):
        self._write(outbox=[{"kind": "shots", "work_id": "w1", "posted": True}])
        man = _man(["approved", "approved"])
        self.assertEqual(server._shots_undelivered("w1", man=man), 0)
        self.assertTrue(server._shots_ever_drafted("w1", man=man))
        finished, reason = server.shots_finished("w1", man=man)
        self.assertTrue(finished, reason)

    def test_a_pending_draft_still_holds_the_run(self):
        self._write(outbox=[{"kind": "shots", "work_id": "w1", "posted": False}])
        finished, reason = server.shots_finished("w1", man=_man(["approved"]))
        self.assertFalse(finished)
        self.assertIn("deliver", reason)

    def test_a_draft_of_ANOTHER_work_does_not_count(self):
        self._write(outbox=[{"kind": "shots", "work_id": "other", "posted": True}])
        self.assertFalse(server._shots_ever_drafted("w1", man=_man(["approved"])))


class RejectedTests(_Base):
    def test_all_rejected_finishes_without_any_draft(self):
        """Rejecting IS the decision not to send; nothing is owed."""
        finished, reason = server.shots_finished(
            "w1", man=_man(["rejected", "rejected"]))
        self.assertTrue(finished, reason)

    def test_one_approval_among_rejections_still_needs_the_send(self):
        finished, _ = server.shots_finished(
            "w1", man=_man(["rejected", "approved"]))
        self.assertFalse(finished)


class UndecidedTests(_Base):
    def test_an_undecided_pair_still_holds_the_run(self):
        finished, reason = server.shots_finished("w1", man=_man(["approved", ""]))
        self.assertFalse(finished)
        self.assertIn("not decided", reason)


class DecidePersistsWithoutSendingTests(unittest.TestCase):
    """A decision reaches DISK when it is made, not when it is sent.

    The review used to be browser-local until the send button, so deciding a
    batch and leaving threw it away (operator, 2026-08-28: "kada odaberem
    odobri/odbaci na svim parovima i ne pošaljem već izađem, obriše se ono što
    sam uradio, ne čuva se odabir").
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name) / "w9"
        (self.d / "before").mkdir(parents=True)
        man = {"ticket": "DEMO#43417", "version": 1, "pairs": [
            {"pair_id": "a", "page_id": "pa", "drop": False},
            {"pair_id": "b", "page_id": "pb", "drop": False},
            {"pair_id": "c", "page_id": "pc", "drop": False}]}
        (self.d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        self._orig = server._shots_dir
        server._shots_dir = lambda wid: self.d if wid == "w9" else None
        self.addCleanup(lambda: setattr(server, "_shots_dir", self._orig))

    def _man(self):
        return json.loads((self.d / "manifest.json").read_text(encoding="utf-8"))

    def _decisions(self):
        return {p["pair_id"]: p.get("decision") for p in self._man()["pairs"]}

    def test_all_three_states_are_written_to_disk(self):
        res, code = server.shots_decide(
            "w9", "DEMO#43417",
            pairs=[{"pair_id": "a", "caption": "prva", "mode": "both"}],
            drop=["c"], reject=["b"])
        self.assertEqual(code, 200, res)
        self.assertEqual(self._decisions(), {"a": "approved", "b": "rejected", "c": ""})
        self.assertEqual(self._man()["pairs"][0]["caption"], "prva")

    def test_it_writes_no_draft_and_sends_nothing(self):
        """Deciding is not a promise to send: the outbox must stay untouched."""
        calls = []
        orig = server.shots_draft
        server.shots_draft = lambda *a, **k: calls.append(a) or {}
        self.addCleanup(lambda: setattr(server, "shots_draft", orig))
        server.shots_decide("w9", "DEMO#43417",
                            pairs=[{"pair_id": "a"}], drop=[], reject=[])
        self.assertEqual(calls, [], "decide must not build a draft")

    def test_a_decision_survives_being_re_read(self):
        server.shots_decide("w9", "DEMO#43417", pairs=[], drop=[],
                            reject=["a", "b", "c"])
        self.assertEqual(set(self._decisions().values()), {"rejected"})
        # A second call with the same body is idempotent, not additive.
        server.shots_decide("w9", "DEMO#43417", pairs=[], drop=[],
                            reject=["a", "b", "c"])
        self.assertEqual(set(self._decisions().values()), {"rejected"})

    def test_an_unknown_work_is_refused(self):
        _res, code = server.shots_decide("nope", "DEMO#43417", [], [], [])
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
