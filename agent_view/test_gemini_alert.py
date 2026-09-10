#!/usr/bin/env python3
"""Offline tests for the quota-threshold owner alert (Feature 2).

Two halves, matching the layering: gemini_client's _count fires an optional hook
at most once per Pacific day (no mail import there), and server's registered
_gemini_threshold_email sends the mail via the first account. No network — the
usage counter points at a throwaway file and mailstore is faked in sys.modules.
Run:  python test_gemini_alert.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server                    # noqa: E402  (also puts scripts/tickets on sys.path)
import gemini_client as gc       # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Half 1 — gemini_client._count fires the hook once per day
# --------------------------------------------------------------------------- #
def _setup_gc():
    tmp = Path(tempfile.mkdtemp(prefix="gcalert_"))
    gc._USAGE_FILE = tmp / ".gemini_usage.json"
    gc._last_call.clear()
    gc.on_threshold_crossed = None
    os.environ.pop("GEMINI_API_KEYS", None)
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"    # one key → cap == DAILY_CAP


def test_count_fires_hook_once_at_threshold():
    _setup_gc()
    gc.ALERT_AT = 3
    fired = []
    gc.on_threshold_crossed = lambda used, cap: fired.append((used, cap))
    for _ in range(3):
        gc._count("k1")                           # 1, 2, 3 → crosses on the 3rd
    check("count: hook fired exactly once at the threshold", len(fired) == 1, repr(fired))
    check("count: hook got (used, cap)", fired == [(3, gc.DAILY_CAP)], repr(fired))
    check("count: the day is marked alerted in the usage file",
          gc._load_usage().get("alerted") is True)


def test_count_does_not_refire_same_day():
    _setup_gc()
    gc.ALERT_AT = 3
    fired = []
    gc.on_threshold_crossed = lambda used, cap: fired.append((used, cap))
    for _ in range(6):                            # cross, then keep going same day
        gc._count("k1")
    check("count: a second crossing the same day does NOT re-fire",
          len(fired) == 1, repr(fired))


def test_count_below_threshold_never_fires():
    _setup_gc()
    gc.ALERT_AT = 100
    fired = []
    gc.on_threshold_crossed = lambda used, cap: fired.append((used, cap))
    for _ in range(5):
        gc._count("k1")
    check("count: below the threshold the hook never fires", fired == [], repr(fired))


def test_count_hook_exception_is_swallowed():
    _setup_gc()
    gc.ALERT_AT = 1

    def _boom(used, cap):
        raise RuntimeError("hook blew up")

    gc.on_threshold_crossed = _boom
    try:
        gc._count("k1")                           # crosses immediately; hook raises
        check("count: a raising hook never breaks the counted call", True)
    except Exception as exc:
        check("count: a raising hook never breaks the counted call", False,
              f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
#  Half 2 — server._gemini_threshold_email sends to both owners
# --------------------------------------------------------------------------- #
class FakeMailstore:
    def __init__(self, accounts=None, raise_on_send=False):
        self._accounts = (accounts if accounts is not None
                          else [{"id": "acc1", "name": "Primary", "email": "a@x"}])
        self.raise_on_send = raise_on_send
        self.sent = []

    def accounts_public(self):
        return self._accounts

    def send(self, acct_id, to, cc, subject, body, attachments):
        if self.raise_on_send:
            raise RuntimeError("smtp down")
        self.sent.append({"acct": acct_id, "to": to, "cc": cc,
                          "subject": subject, "body": body})


def _with_fake_mailstore(fake, fn):
    prev = sys.modules.get("mailstore")
    sys.modules["mailstore"] = fake
    try:
        return fn()
    finally:
        if prev is not None:
            sys.modules["mailstore"] = prev
        else:
            sys.modules.pop("mailstore", None)


def test_email_sends_to_both_owners():
    fake = FakeMailstore()
    _with_fake_mailstore(fake, lambda: server._gemini_threshold_email(485, 500))
    check("email: exactly one send attempted", len(fake.sent) == 1, repr(fake.sent))
    sent = fake.sent[0] if fake.sent else {}
    check("email: sent from the first configured account", sent.get("acct") == "acc1")
    check("email: addressed to BOTH owner addresses",
          set(sent.get("to") or []) == set(server.GEMINI_ALERT_TO), repr(sent.get("to")))
    check("email: subject/body are English and mention the threshold",
          "quota" in (sent.get("subject", "") + sent.get("body", "")).lower()
          and "485" in sent.get("body", ""))


def test_email_no_account_is_a_silent_noop():
    fake = FakeMailstore(accounts=[])
    ok = {"raised": False}

    def _run():
        try:
            server._gemini_threshold_email(485, 500)
        except Exception:
            ok["raised"] = True

    _with_fake_mailstore(fake, _run)
    check("email: no account -> no send, no raise",
          fake.sent == [] and not ok["raised"])


def test_email_send_failure_is_swallowed():
    fake = FakeMailstore(raise_on_send=True)
    ok = {"raised": False}

    def _run():
        try:
            server._gemini_threshold_email(485, 500)
        except Exception:
            ok["raised"] = True

    _with_fake_mailstore(fake, _run)
    check("email: a failing send is swallowed (never breaks the AI call)",
          not ok["raised"])


def main():
    for fn in (test_count_fires_hook_once_at_threshold,
               test_count_does_not_refire_same_day,
               test_count_below_threshold_never_fires,
               test_count_hook_exception_is_swallowed,
               test_email_sends_to_both_owners,
               test_email_no_account_is_a_silent_noop,
               test_email_send_failure_is_swallowed):
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
