#!/usr/bin/env python3
"""Offline unit tests for testparse.py, over realistic captured samples for each
runner (a passing AND a failing case) plus a handful of streaming progress
lines. Pure — no server, no network. Run:  python test_testparse.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import testparse      # noqa: E402


# --------------------------------------------------------------------------- #
#  Captured samples
# --------------------------------------------------------------------------- #
DJANGO_PASS = """\
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
........
----------------------------------------------------------------------
Ran 8 tests in 0.123s

OK
Destroying test database for alias 'default'...
"""

DJANGO_PASS_SKIP = """\
Creating test database for alias 'default'...
....ss
----------------------------------------------------------------------
Ran 6 tests in 0.050s

OK (skipped=2)
Destroying test database for alias 'default'...
"""

DJANGO_FAIL = """\
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
..F.E...
======================================================================
FAIL: test_addition (myapp.tests.MathTests.test_addition)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/app/myapp/tests.py", line 12, in test_addition
    self.assertEqual(add(2, 2), 5)
AssertionError: 4 != 5
======================================================================
ERROR: test_divide (myapp.tests.MathTests.test_divide)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/app/myapp/tests.py", line 18, in test_divide
    divide(1, 0)
ZeroDivisionError: division by zero
----------------------------------------------------------------------
Ran 8 tests in 0.150s

FAILED (failures=1, errors=1)
Destroying test database for alias 'default'...
"""

UNITTEST_PASS = """\
...
----------------------------------------------------------------------
Ran 3 tests in 0.001s

OK
"""

PYTEST_PASS = """\
============================= test session starts ==============================
platform linux -- Python 3.12.0, pytest-8.0.0
collected 5 items

tests/test_math.py .....                                                 [100%]

============================== 5 passed in 0.03s ===============================
"""

PYTEST_FAIL = """\
============================= test session starts ==============================
collected 6 items

tests/test_math.py ..F.F.                                                [100%]

=================================== FAILURES ===================================
______________________________ test_add _______________________________________
    def test_add():
>       assert add(2, 2) == 5
E       assert 4 == 5
=========================== short test summary info ============================
FAILED tests/test_math.py::test_add - assert 4 == 5
FAILED tests/test_math.py::test_sub - assert 1 == 0
========================= 2 failed, 4 passed in 0.12s ==========================
"""

PYTEST_SKIP = """\
collected 4 items
tests/test_x.py .s.s                                                      [100%]
======================== 2 passed, 2 skipped in 0.04s =========================
"""

JEST_PASS = """\
PASS  src/sum.test.js
  ✓ adds 1 + 2 (3 ms)
  ✓ adds 0 + 0

Test Suites: 1 passed, 1 total
Tests:       2 passed, 2 total
Snapshots:   0 total
Time:        1.2 s
"""

JEST_FAIL = """\
FAIL  src/sum.test.js
  ✓ adds 1 + 2 (3 ms)
  ✕ adds 2 + 2 (5 ms)

  ● sum › adds 2 + 2

    expect(received).toBe(expected)

Test Suites: 1 failed, 1 total
Tests:       1 failed, 1 passed, 2 total
Snapshots:   0 total
Time:        1.5 s
"""

GO_PASS = """\
=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestSub
--- PASS: TestSub (0.00s)
PASS
ok  \tgithub.com/x/y\t0.008s
"""

GO_PASS_NONVERBOSE = "ok  \tgithub.com/x/y\t0.012s\n"

GO_FAIL = """\
=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestSub
--- FAIL: TestSub (0.00s)
    sub_test.go:15: expected 1, got 2
FAIL
exit status 1
FAIL\tgithub.com/x/y\t0.010s
"""


# --------------------------------------------------------------------------- #
class TestDjango(unittest.TestCase):
    def test_pass(self):
        r = testparse.parse_final(DJANGO_PASS)
        self.assertEqual(r["runner"], "django")
        self.assertEqual(r["status"], "passed")
        self.assertEqual((r["total"], r["passed"], r["failed"], r["skipped"]), (8, 8, 0, 0))
        self.assertEqual(r["fails"], [])

    def test_pass_with_skips(self):
        r = testparse.parse_final(DJANGO_PASS_SKIP)
        self.assertEqual(r["status"], "passed")
        self.assertEqual((r["total"], r["passed"], r["skipped"]), (6, 4, 2))

    def test_fail(self):
        r = testparse.parse_final(DJANGO_FAIL)
        self.assertEqual(r["runner"], "django")
        self.assertEqual(r["status"], "failed")
        self.assertEqual((r["total"], r["passed"], r["failed"]), (8, 6, 2))
        self.assertEqual(len(r["fails"]), 2)
        self.assertIn("test_addition", r["fails"][0])
        self.assertIn("AssertionError: 4 != 5", r["fails"][0])
        self.assertIn("test_divide", r["fails"][1])
        self.assertIn("ZeroDivisionError: division by zero", r["fails"][1])
        self.assertIn(" — ", r["fails"][0])          # "<name> — <reason>"


class TestUnittest(unittest.TestCase):
    def test_pass(self):
        r = testparse.parse_final(UNITTEST_PASS)
        self.assertEqual(r["runner"], "unittest")        # no Django db markers
        self.assertEqual(r["status"], "passed")
        self.assertEqual((r["total"], r["passed"]), (3, 3))


class TestPytest(unittest.TestCase):
    def test_pass(self):
        r = testparse.parse_final(PYTEST_PASS)
        self.assertEqual(r["runner"], "pytest")
        self.assertEqual(r["status"], "passed")
        self.assertEqual((r["total"], r["passed"], r["failed"]), (5, 5, 0))
        self.assertEqual(r["fails"], [])

    def test_fail(self):
        r = testparse.parse_final(PYTEST_FAIL)
        self.assertEqual(r["runner"], "pytest")
        self.assertEqual(r["status"], "failed")
        self.assertEqual((r["passed"], r["failed"]), (4, 2))
        self.assertEqual(r["total"], 6)
        self.assertEqual(len(r["fails"]), 2)
        self.assertIn("test_math.py::test_add", r["fails"][0])
        self.assertIn("assert 4 == 5", r["fails"][0])

    def test_skips(self):
        r = testparse.parse_final(PYTEST_SKIP)
        self.assertEqual(r["status"], "passed")
        self.assertEqual((r["passed"], r["skipped"]), (2, 2))
        self.assertEqual(r["total"], 4)


class TestJest(unittest.TestCase):
    def test_pass(self):
        r = testparse.parse_final(JEST_PASS)
        self.assertEqual(r["runner"], "jest")
        self.assertEqual(r["status"], "passed")
        self.assertEqual((r["total"], r["passed"], r["failed"]), (2, 2, 0))

    def test_fail(self):
        r = testparse.parse_final(JEST_FAIL)
        self.assertEqual(r["runner"], "jest")
        self.assertEqual(r["status"], "failed")
        self.assertEqual((r["total"], r["passed"], r["failed"]), (2, 1, 1))
        self.assertTrue(any("adds 2 + 2" in f for f in r["fails"]))


class TestGo(unittest.TestCase):
    def test_pass_verbose(self):
        r = testparse.parse_final(GO_PASS)
        self.assertEqual(r["runner"], "go")
        self.assertEqual(r["status"], "passed")
        self.assertEqual((r["total"], r["passed"], r["failed"]), (2, 2, 0))

    def test_pass_nonverbose(self):
        r = testparse.parse_final(GO_PASS_NONVERBOSE)
        self.assertEqual(r["runner"], "go")
        self.assertEqual(r["status"], "passed")

    def test_fail(self):
        r = testparse.parse_final(GO_FAIL)
        self.assertEqual(r["runner"], "go")
        self.assertEqual(r["status"], "failed")
        self.assertEqual((r["passed"], r["failed"]), (1, 1))
        self.assertEqual(len(r["fails"]), 1)
        self.assertIn("TestSub", r["fails"][0])
        self.assertIn("expected 1, got 2", r["fails"][0])


class TestUnknownAndEdges(unittest.TestCase):
    def test_unknown_output(self):
        r = testparse.parse_final("just some build output\nnothing to see here")
        self.assertEqual(r["runner"], "unknown")
        self.assertEqual(r["status"], "unknown")
        self.assertEqual((r["total"], r["passed"], r["failed"]), (0, 0, 0))
        self.assertEqual(r["fails"], [])

    def test_empty_and_none_never_raise(self):
        for bad in ("", "   ", None, 123, [], {}):
            r = testparse.parse_final(bad)
            self.assertEqual(r["status"], "unknown")
            self.assertEqual(r["runner"], "unknown")


class TestProgress(unittest.TestCase):
    def test_pytest_collected_total(self):
        self.assertEqual(testparse.parse_progress("collected 12 items"), {"total": 12})

    def test_pytest_per_test(self):
        self.assertEqual(
            testparse.parse_progress("tests/test_a.py::test_one PASSED   [ 10%]"),
            {"done": 1, "passed": 1})
        self.assertEqual(
            testparse.parse_progress("tests/test_a.py::test_two FAILED   [ 20%]"),
            {"done": 1, "failed": 1})

    def test_go_per_test(self):
        self.assertEqual(testparse.parse_progress("--- PASS: TestAdd (0.00s)"),
                         {"done": 1, "passed": 1})
        self.assertEqual(testparse.parse_progress("--- FAIL: TestSub (0.00s)"),
                         {"done": 1, "failed": 1})

    def test_jest_bullets(self):
        self.assertEqual(testparse.parse_progress("  ✓ adds 1 + 2 (3 ms)"),
                         {"done": 1, "passed": 1})
        self.assertEqual(testparse.parse_progress("  ✕ adds 2 + 2 (5 ms)"),
                         {"done": 1, "failed": 1})

    def test_jest_tests_line_yields_total_only(self):
        # the Tests: line is the WHOLE-run summary; only `total` is surfaced so
        # the wrapper's per-bullet increments are not double-counted.
        self.assertEqual(testparse.parse_progress("Tests:       1 failed, 1 passed, 2 total"),
                         {"total": 2})

    def test_unittest_verbose(self):
        self.assertEqual(testparse.parse_progress("test_x (mod.Cls.test_x) ... ok"),
                         {"done": 1, "passed": 1})
        self.assertEqual(testparse.parse_progress("test_y (mod.Cls.test_y) ... FAIL"),
                         {"done": 1, "failed": 1})

    def test_django_progress_dots(self):
        self.assertEqual(testparse.parse_progress("...F"),
                         {"done": 4, "passed": 3, "failed": 1})

    def test_noise_is_none(self):
        for line in ("", "   ", None, "some random log line", "Installing deps..."):
            self.assertIsNone(testparse.parse_progress(line))


if __name__ == "__main__":
    unittest.main(verbosity=2)
