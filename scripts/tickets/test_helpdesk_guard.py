#!/usr/bin/env python3
"""The outbound-write guard: nothing that smells of a test may reach the
helpdesk.

WHY (measured, 2026-08-20, twice in one hour): a subagent's test shelled out to
`writeback.py --close`; the child inherited HELPDESK_URL/HELPDESK_TOKEN and
posted a real comment on live ticket #08597 (id 599). An hour later a
"let me check the guard works" call to `add_comment` posted another (id 600).
Both had to be deleted from the production database by hand.

HOW THIS TEST STAYS HONEST: `urllib.request.urlopen` is replaced by a spy that
RAISES if it is ever reached, so this file cannot repeat the incident it exists
to prevent — and the last case proves the spy would have caught a leak (a clean
process does reach the wire; the guard is the only thing that stops a test).

  python test_helpdesk_guard.py
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from adapters import acme_helpdesk as hd  # noqa: E402

FAILS = []
ATTEMPTS = []


def ck(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  -- " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _spy(*a, **k):
    ATTEMPTS.append(a)
    raise AssertionError("a real HTTP request was attempted")


def _clear_env():
    for var in ("BRAIN_HELPDESK_READONLY", "BRAIN_NO_SEND", "PYTEST_CURRENT_TEST"):
        os.environ.pop(var, None)


def main() -> int:
    urllib.request.urlopen = _spy                       # nothing leaves this process
    os.environ["HELPDESK_URL"] = "https://example.invalid"
    os.environ["HELPDESK_TOKEN"] = "not-a-real-token"
    adapter = hd.AcmeHelpdesk()
    writes = (("add_comment", lambda: adapter.add_comment("1", "x")),
              ("close", lambda: adapter.close("1", "x")),
              ("set_estimate", lambda: adapter.set_estimate("1", 2.0)))

    # 1. every switch blocks every write door
    for var, val in (("BRAIN_HELPDESK_READONLY", "1"), ("BRAIN_NO_SEND", "1"),
                     ("PYTEST_CURRENT_TEST", "test_x (call)")):
        _clear_env()
        os.environ[var] = val
        ck("%s: reason reported" % var, bool(hd.writes_blocked()), hd.writes_blocked())
        for name, op in writes:
            try:
                op()
                ck("%s blocks %s" % (var, name), False, "the call went through")
            except hd.HelpdeskError as exc:
                ck("%s blocks %s" % (var, name), "refused" in str(exc), str(exc))
            except AssertionError:
                ck("%s blocks %s" % (var, name), False, "IT REACHED THE WIRE")

    # 2. a falsy value is not a switch (0/false/empty must not block real work).
    #    argv[0] has to be neutralised here: THIS file is a test_*.py, which the
    #    guard blocks on its own — correctly, and it would mask the check.
    argv_keep, sys.argv = sys.argv, ["writeback.py"]
    for val in ("", "0", "false", "no"):
        _clear_env()
        os.environ["BRAIN_HELPDESK_READONLY"] = val
        ck("BRAIN_HELPDESK_READONLY=%r does not block" % val, hd.writes_blocked() == "")
    sys.argv = argv_keep
    ck("the guard blocks THIS test file on its own name alone",
       bool(hd.writes_blocked()) and "test_helpdesk_guard" in hd.writes_blocked(),
       hd.writes_blocked())

    # 3. this repo's own runner convention: argv[0] is a test_*.py
    _clear_env()
    keep, sys.argv = sys.argv, ["scripts/tickets/test_something.py"]
    ck("a test_*.py caller is blocked", bool(hd.writes_blocked()), hd.writes_blocked())
    sys.argv = ["writeback.py"]
    ck("a normal caller is NOT blocked (real work must still work)", hd.writes_blocked() == "")

    # 4. the positive twin: with no switch the call really does reach the wire,
    #    so the checks above prove the GUARD stops it, not a broken adapter.
    before = len(ATTEMPTS)
    try:
        adapter.add_comment("1", "x")
        ck("no switch: the call reaches the transport", False, "nothing attempted")
    except AssertionError:
        ck("no switch: the call reaches the transport (guard is the only stopper)",
           len(ATTEMPTS) == before + 1)
    except hd.HelpdeskError as exc:
        ck("no switch: the call reaches the transport", False, "blocked instead: %s" % exc)
    sys.argv = keep

    # 5. reads are never blocked - the guard is about WRITES only
    _clear_env()
    os.environ["BRAIN_HELPDESK_READONLY"] = "1"
    before = len(ATTEMPTS)
    try:
        adapter.get_detail("1")
    except AssertionError:
        pass                                            # reached the transport, as it should
    except Exception:                                   # noqa: BLE001
        pass
    ck("a READ is not blocked by the guard", len(ATTEMPTS) == before + 1)
    _clear_env()

    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
