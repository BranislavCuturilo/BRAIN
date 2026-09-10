#!/usr/bin/env python3
"""Offline tests for the new-mail notifier in server.py — no network, no IMAP.

Drives the pure high-water-mark detector with successive UID snapshots, and the
wired sync observer with HUB.broadcast captured. Importing server is offline (its
only import-time work is the local ticket store). Run:  python test_mail_notify.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server      # noqa: E402


# --------------------------------------------------------------------------- #
#  Tiny assertion harness (same shape as the other agent_view tests)
# --------------------------------------------------------------------------- #
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


def _reset():
    with server._mail_hwm_lock:
        server._mail_hwm.clear()


# --------------------------------------------------------------------------- #
#  The pure detector
# --------------------------------------------------------------------------- #
def test_baseline_is_silent():
    _reset()
    n = server.note_folder_snapshot("a", "INBOX", 1, [1, 2, 3])
    check("baseline: first snapshot returns None (silent)", n is None, repr(n))


def test_increase_counts():
    _reset()
    server.note_folder_snapshot("a", "INBOX", 1, [1, 2, 3])            # baseline
    n = server.note_folder_snapshot("a", "INBOX", 1, [1, 2, 3, 4, 5])
    check("increase: two new messages counted", n == 2, repr(n))


def test_no_change_is_zero():
    _reset()
    server.note_folder_snapshot("a", "INBOX", 1, [1, 2, 3])
    n = server.note_folder_snapshot("a", "INBOX", 1, [1, 2, 3])
    check("no change: zero new", n == 0, repr(n))


def test_deletion_holds_the_mark():
    _reset()
    server.note_folder_snapshot("a", "INBOX", 1, [1, 2, 3, 4, 5])      # hwm = 5
    n1 = server.note_folder_snapshot("a", "INBOX", 1, [4, 5])          # some deleted
    check("deletion: nothing new counted", n1 == 0, repr(n1))
    n2 = server.note_folder_snapshot("a", "INBOX", 1, [4, 5, 6])       # one truly new
    check("deletion: hwm stayed at 5 -> only uid 6 is new", n2 == 1, repr(n2))


def test_new_uidvalidity_rebaselines():
    _reset()
    server.note_folder_snapshot("a", "INBOX", 1, [1, 2, 3])
    n = server.note_folder_snapshot("a", "INBOX", 2, [10, 11])         # server renumber
    check("uidvalidity change: fresh baseline (silent), no spurious event",
          n is None, repr(n))


def test_per_account_isolation():
    _reset()
    server.note_folder_snapshot("a", "INBOX", 1, [1, 2, 3])            # a baseline
    n = server.note_folder_snapshot("b", "INBOX", 1, [1, 2, 3, 4])     # b's FIRST
    check("isolation: another account's first snapshot is its own baseline",
          n is None, repr(n))


# --------------------------------------------------------------------------- #
#  The wired observer (mailstore -> server.HUB.broadcast)
# --------------------------------------------------------------------------- #
def _capture_broadcasts():
    sent = []
    orig = server.HUB.broadcast
    server.HUB.broadcast = lambda msg: sent.append(msg)
    return sent, orig


def test_observer_broadcasts_only_on_increase():
    _reset()
    sent, orig = _capture_broadcasts()
    try:
        server._on_folder_synced("acc1", "INBOX", 1, [1, 2, 3])        # baseline
        check("observer: no broadcast on baseline", sent == [], repr(sent))
        server._on_folder_synced("acc1", "INBOX", 1, [1, 2, 3, 4])     # +1
        check("observer: one broadcast on increase", len(sent) == 1, repr(sent))
        m = sent[0] if sent else {}
        check("observer: event type mail-new", m.get("type") == "mail-new", repr(m))
        check("observer: carries the acct", m.get("acct") == "acc1", repr(m))
        check("observer: folder INBOX", m.get("folder") == "INBOX", repr(m))
        check("observer: count is 1", m.get("count") == 1, repr(m))
    finally:
        server.HUB.broadcast = orig


def test_observer_ignores_non_inbox():
    _reset()
    sent, orig = _capture_broadcasts()
    try:
        server._on_folder_synced("acc1", "Sent", 1, [1, 2, 3])
        server._on_folder_synced("acc1", "Sent", 1, [1, 2, 3, 4, 5])
        check("observer: non-INBOX never broadcasts", sent == [], repr(sent))
    finally:
        server.HUB.broadcast = orig


def test_observer_registered_with_mailstore():
    # The wiring the sync loop performs: after registration, a mailstore folder
    # sync notification reaches the server's observer.
    import mailstore
    _reset()
    mailstore.add_sync_observer(server._on_folder_synced)
    mailstore.add_sync_observer(server._on_folder_synced)   # idempotent
    check("wiring: observer registered exactly once (idempotent)",
          mailstore._sync_observers.count(server._on_folder_synced) == 1,
          str(mailstore._sync_observers))
    sent, orig = _capture_broadcasts()
    try:
        mailstore._notify_sync_observers("accX", "INBOX", 1, [1, 2])   # baseline
        mailstore._notify_sync_observers("accX", "INBOX", 1, [1, 2, 3])  # +1
        check("wiring: notify path drives a mail-new event",
              len(sent) == 1 and sent[0].get("count") == 1, repr(sent))
    finally:
        server.HUB.broadcast = orig


def main():
    for fn in (test_baseline_is_silent, test_increase_counts, test_no_change_is_zero,
               test_deletion_holds_the_mark, test_new_uidvalidity_rebaselines,
               test_per_account_isolation, test_observer_broadcasts_only_on_increase,
               test_observer_ignores_non_inbox, test_observer_registered_with_mailstore):
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
