#!/usr/bin/env python3
"""Offline tests for /api/mail/list sorting + paging (Feature 4).

Exercises the pure server.mail_list_page / server._sort_mail_rows over a fake mail
backend — no HTTP, no IMAP. Run:  python test_mail_list.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server      # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# Newest-first, as the cache returns them (uid 5 .. 1). Distinct name/subject/seen
# so each sort orders differently from the others.
_ROWS = [
    {"uid": "5", "from_name": "Zoran", "from_email": "zoran@x.me", "subject": "Apple", "seen": True},
    {"uid": "4", "from_name": "Ana", "from_email": "ana@example.com", "subject": "Zulu", "seen": False},
    {"uid": "3", "from_name": "Marko", "from_email": "marko@x.me", "subject": "Bravo", "seen": True},
    {"uid": "2", "from_name": "Bojan", "from_email": "bojan@example.com", "subject": "Yankee", "seen": False},
    {"uid": "1", "from_name": "Ceca", "from_email": "ceca@x.me", "subject": "Charlie", "seen": True},
]


class FakeMS:
    def __init__(self, rows):
        self._rows = rows
        self.last_limit = None

    def messages(self, acct, folder, limit, q=None):
        self.last_limit = limit
        return [dict(r) for r in self._rows[:limit]]


class FakeAI:
    def detect_bulk(self, row):
        return (row.get("from_email") or "").startswith("zoran")


def _uids(page):
    return [r["uid"] for r in page]


def test_limit_clamps_to_500():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 10000, 0, "date")
    check("limit: clamps to 500", out["limit"] == 500, str(out["limit"]))
    check("limit: cache asked for the clamped ceiling (<=500)",
          ms.last_limit == 500, str(ms.last_limit))
    check("limit: all rows returned in date order",
          _uids(out["messages"]) == ["5", "4", "3", "2", "1"])


def test_bad_limit_falls_back_to_50():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, "not-a-number", 0, "date")
    check("limit: non-numeric falls back to 50", out["limit"] == 50, str(out["limit"]))


def test_offset_skips():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 2, 2, "date")
    check("offset: skips the first two, returns the next two",
          _uids(out["messages"]) == ["3", "2"], repr(_uids(out["messages"])))
    check("offset: reported back", out["offset"] == 2, str(out["offset"]))
    check("offset: fetched only offset+limit rows", ms.last_limit == 4, str(ms.last_limit))


def test_negative_offset_clamps_to_zero():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 3, -5, "date")
    check("offset: negative clamps to 0", out["offset"] == 0, str(out["offset"]))
    check("offset: page starts at the newest", _uids(out["messages"]) == ["5", "4", "3"])


def test_sort_date_is_newest_first_default():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 10, 0, "date")
    check("sort date: newest-first preserved",
          _uids(out["messages"]) == ["5", "4", "3", "2", "1"])


def test_sort_from_alphabetical():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 10, 0, "from")
    # Ana, Bojan, Ceca, Marko, Zoran
    check("sort from: case-insensitive alphabetical by name",
          _uids(out["messages"]) == ["4", "2", "1", "3", "5"], repr(_uids(out["messages"])))


def test_sort_subject_alphabetical():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 10, 0, "subject")
    # Apple, Bravo, Charlie, Yankee, Zulu
    check("sort subject: case-insensitive alphabetical",
          _uids(out["messages"]) == ["5", "3", "1", "2", "4"], repr(_uids(out["messages"])))


def test_sort_unread_first():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 10, 0, "unread")
    # unseen (uid4, uid2) first in newest-first order, then seen (5,3,1)
    check("sort unread: unseen first, date order preserved within each group",
          _uids(out["messages"]) == ["4", "2", "5", "3", "1"], repr(_uids(out["messages"])))


def test_bad_sort_falls_back_to_date():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 10, 0, "nonsense")
    check("sort: unknown sort falls back to date", out["sort"] == "date", out["sort"])
    check("sort: date order returned",
          _uids(out["messages"]) == ["5", "4", "3", "2", "1"])


def test_bulk_annotation_applied_on_page():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 10, 0, "date", ai=FakeAI())
    have_flag = all("bulk" in r for r in out["messages"])
    check("bulk: every returned row is annotated when ai is present", have_flag)
    zoran = next(r for r in out["messages"] if r["uid"] == "5")
    check("bulk: the zoran row is flagged bulk", zoran.get("bulk") is True)
    ana = next(r for r in out["messages"] if r["uid"] == "4")
    check("bulk: a normal row is not bulk", ana.get("bulk") is False)


def test_no_ai_leaves_rows_unannotated():
    ms = FakeMS(_ROWS)
    out = server.mail_list_page(ms, "a", "INBOX", None, 10, 0, "date", ai=None)
    check("bulk: no ai -> no bulk key added",
          all("bulk" not in r for r in out["messages"]))


def main():
    for fn in (test_limit_clamps_to_500, test_bad_limit_falls_back_to_50,
               test_offset_skips, test_negative_offset_clamps_to_zero,
               test_sort_date_is_newest_first_default, test_sort_from_alphabetical,
               test_sort_subject_alphabetical, test_sort_unread_first,
               test_bad_sort_falls_back_to_date, test_bulk_annotation_applied_on_page,
               test_no_ai_leaves_rows_unannotated):
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
