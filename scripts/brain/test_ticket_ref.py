"""The producer and the gallery must apply ONE ticket-reference rule.

Pins the defect of 2026-08-28 (DEMO#43417, and it had happened before): the
capture engine accepted `--ticket 43417`, wrote correct PNGs, printed
`2 pair(s), 2 shown` — and the HUD gallery filtered the work out for ever,
because a bare number splits into `("", "43417")` and its key is empty.
Nothing warned at either end.

Run: python scripts/brain/test_ticket_ref.py
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRAIN = HERE.parent.parent
sys.path.insert(0, str(HERE))

import ticket_ref                                              # noqa: E402


class ParseTests(unittest.TestCase):
    def test_both_spellings_are_one_ticket(self):
        self.assertEqual(ticket_ref.key("DEMO#43417"), "DEMO#43417")
        self.assertEqual(ticket_ref.key("DEMO43417"), "DEMO#43417")
        self.assertEqual(ticket_ref.key("demo#43417"), "DEMO#43417")

    def test_leading_zeros_do_not_split_a_ticket_in_two(self):
        self.assertEqual(ticket_ref.key("DEMO#05513"),
                         ticket_ref.key("DEMO#5513"))

    def test_a_bare_number_names_no_ticket(self):
        """THE defect. A bare id has no module, so nothing can file it."""
        for raw in ("43417", "05123", " 43417 "):
            with self.subTest(raw=raw):
                self.assertIsNone(ticket_ref.parse(raw))
                self.assertEqual(ticket_ref.key(raw), "")
                self.assertFalse(ticket_ref.names_a_ticket(raw))

    def test_empty_and_moduleless_references_name_no_ticket(self):
        for raw in ("", None, "   ", "#43417", "DEMO#", "DEMO"):
            with self.subTest(raw=raw):
                self.assertFalse(ticket_ref.names_a_ticket(raw))

    def test_modules_for_repo_never_raises(self):
        self.assertEqual(ticket_ref.modules_for_repo(None), [])
        self.assertEqual(
            ticket_ref.modules_for_repo(r"Z:\no\such\repo\anywhere"), [])


class OneRuleBothEndsTests(unittest.TestCase):
    """The producer and the consumer must agree on every reference.

    Two copies of this rule is what caused the defect; these two tests fail if
    either end grows its own again.
    """

    CASES = ["DEMO#43417", "DEMO43417", "43417", "", "#5", "INV#94313",
             "demo#05513", "LEDGER#12", "12IFRS", "x"]

    def test_gallery_key_matches_the_shared_rule(self):
        sys.path.insert(0, str(BRAIN / "agent_view"))
        import server                                          # noqa: PLC0415
        for raw in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(server._shots_ticket_key(raw),
                                 ticket_ref.key(raw))
                self.assertEqual(server._shots_ticket_ref(raw),
                                 ticket_ref.parse(raw))

    def test_capture_engine_refuses_what_the_gallery_would_hide(self):
        """`shoot.py` must reject exactly the references whose key is empty —
        otherwise it produces pairs the gallery cannot show."""
        sys.path.insert(0, str(BRAIN / "scripts"))
        from visual import isolate as visolate                 # noqa: PLC0415
        shared = visolate.load_module(
            "brain_ticket_ref_probe", BRAIN / "scripts" / "brain" / "ticket_ref.py")
        for raw in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(shared.names_a_ticket(raw),
                                 bool(ticket_ref.key(raw)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
