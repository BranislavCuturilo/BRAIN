#!/usr/bin/env python3
"""Offline tests for inbox triage: mail_ai.triage_unread + the /api/mail/triage route.

triage_unread packs a folder's UNREAD messages into ONE Gemini call and maps each
returned index back to a message. Here the mailstore and gemini are fakes injected
via the same seams server.py uses (mail_ai._mailstore / mail_ai._gemini); the fake
gemini echoes one item per [n] label it sees in the prompt, so index->uid mapping
is checked directly. The route is driven in-process over loopback to prove the
loopback + same-origin CSRF gate. No network, no credentials, no quota. Run:
python test_mail_triage.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_TMP = Path(tempfile.mkdtemp(prefix="mailtriage_"))
os.environ["AGENT_VIEW_MAIL_PROFILES"] = str(_TMP / "mail_profiles")
os.environ["AGENT_VIEW_MAIL_CACHE"] = str(_TMP / "mail_cache.sqlite")

import mail_ai      # noqa: E402


# --------------------------------------------------------------------------- #
#  Fakes
# --------------------------------------------------------------------------- #
class FakeGemini:
    """Echoes one item per '[n]' label found in the prompt, so index->message
    mapping can be asserted for any unread-set size. important = odd index."""

    def __init__(self):
        self.calls = []          # (prompt, json_out, api_key)

    def call(self, prompt, *, json_out=True, temperature=0.2, api_key="", files=None):
        self.calls.append((prompt, json_out, api_key))
        idxs = [int(m) for m in re.findall(r"\[(\d+)\]", prompt)]
        items = [{"index": i, "important": (i % 2 == 1),
                  "category": f"cat{i}", "reason": f"reason{i}"} for i in idxs]
        return {"items": items, "conclusion": "Look at the first one."}


class FakeMS:
    """messages() returns canned summary rows (newest-first) carrying a `seen`
    flag — exactly the shape mailstore.messages/mailcache expose."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []          # (folder, limit)

    def messages(self, acct, folder, limit=50, q=None):
        self.calls.append((folder, limit))
        return [dict(r) for r in self.rows[:limit]]


def _row(uid, seen, subject=None, from_email=None):
    return {"uid": str(uid), "from_name": f"N{uid}",
            "from_email": from_email or f"user{uid}@x.me",
            "subject": subject or f"subj{uid}", "date": "",
            "seen": seen, "has_attach": False, "snippet": f"body of {uid}"}


def _install(rows):
    gem = FakeGemini()
    ms = FakeMS(rows)
    mail_ai._gemini = lambda: gem
    mail_ai._mailstore = lambda: ms
    return gem, ms


# --------------------------------------------------------------------------- #
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
def test_only_unread_and_uid_mapping():
    # Newest-first, mixed read/unread. Unread (newest-first): uid 4, 2, 1.
    rows = [_row(5, True, subject="READ-five"), _row(4, False),
            _row(3, True, subject="READ-three"), _row(2, False), _row(1, False)]
    gem, ms = _install(rows)
    out = mail_ai.triage_unread("acct", "INBOX")

    check("triage: exactly one Gemini call", len(gem.calls) == 1, str(len(gem.calls)))
    check("triage: JSON-mode call", bool(gem.calls) and gem.calls[0][1] is True)
    check("triage: UNPINNED (empty api_key -> rotation)",
          bool(gem.calls) and gem.calls[0][2] == "")

    items = out.get("items") or []
    check("triage: only the three unread are triaged", len(items) == 3, str(len(items)))
    got = [it["uid"] for it in items]
    check("triage: index maps back to unread newest-first (4,2,1)",
          got == ["4", "2", "1"], str(got))
    check("triage: read messages are excluded from items",
          all(it["uid"] not in ("5", "3") for it in items), str(got))
    prompt = gem.calls[0][0] if gem.calls else ""
    check("triage: read subjects never enter the prompt",
          "READ-five" not in prompt and "READ-three" not in prompt)
    check("triage: unread body excerpt is in the prompt", "body of 4" in prompt)
    check("triage: carries the untrusted-data guard", "UNTRUSTED DATA" in prompt)

    check("triage: important flag carried per item (odd index True)",
          items[0]["important"] is True and items[1]["important"] is False
          and items[2]["important"] is True, str([it["important"] for it in items]))
    check("triage: category/reason carried", items[0]["category"] == "cat1"
          and items[0]["reason"] == "reason1", str(items[0]))
    check("triage: from is the sender email", items[0]["from"] == "user4@x.me",
          items[0]["from"])
    check("triage: subject carried", items[0]["subject"] == "subj4", items[0]["subject"])
    check("triage: conclusion returned", bool(out.get("conclusion")))
    check("triage: count is the unread count", out.get("count") == 3, str(out.get("count")))
    check("triage: not truncated at three", out.get("truncated") is False)


def test_truncation_over_40():
    # 45 unread + 5 read, all in one page, NEWEST-FIRST (uid 1044 down to 1000),
    # as mailstore.messages returns them. Only the 40 newest unread go to Gemini.
    rows = ([_row(1044 - i, False) for i in range(45)]
            + [_row(2000 + i, True) for i in range(5)])
    gem, ms = _install(rows)
    out = mail_ai.triage_unread("acct", "INBOX")
    check("triage(trunc): still exactly one Gemini call", len(gem.calls) == 1,
          str(len(gem.calls)))
    check("triage(trunc): truncated flag set", out.get("truncated") is True)
    check("triage(trunc): count capped at TRIAGE_MAX",
          out.get("count") == mail_ai.TRIAGE_MAX, str(out.get("count")))
    check("triage(trunc): no more than TRIAGE_MAX items",
          len(out.get("items") or []) <= mail_ai.TRIAGE_MAX,
          str(len(out.get("items") or [])))
    # the newest unread (uid 1044) must be first; the 41st-newest (uid 1004) dropped
    got = [it["uid"] for it in out.get("items") or []]
    check("triage(trunc): newest unread kept (1044 first)",
          got and got[0] == "1044", str(got[:3]))
    check("triage(trunc): the 41st-oldest unread is dropped", "1004" not in got,
          str("1004" in got))


def test_scan_limit_requested():
    rows = [_row(1, False)]
    gem, ms = _install(rows)
    mail_ai.triage_unread("acct", "INBOX")
    check("triage: scans TRIAGE_SCAN newest rows from the cache",
          ms.calls and ms.calls[0][1] == mail_ai.TRIAGE_SCAN, str(ms.calls))


def test_no_unread_skips_gemini():
    rows = [_row(3, True), _row(2, True), _row(1, True)]
    gem, ms = _install(rows)
    out = mail_ai.triage_unread("acct", "INBOX")
    check("triage(none): ZERO Gemini calls when nothing is unread",
          len(gem.calls) == 0, str(len(gem.calls)))
    check("triage(none): empty items", out.get("items") == [], str(out.get("items")))
    check("triage(none): count 0, not truncated",
          out.get("count") == 0 and out.get("truncated") is False)
    check("triage(none): a conclusion is still present", bool(out.get("conclusion")))


def test_hallucinated_index_dropped():
    rows = [_row(2, False), _row(1, False)]

    class BadGemini:
        def __init__(self):
            self.calls = []

        def call(self, prompt, *, json_out=True, temperature=0.2, api_key="", files=None):
            self.calls.append((prompt, json_out))
            # index 9 does not exist (only 1..2); a non-int index too — both dropped.
            return {"items": [{"index": 1, "important": True, "category": "c",
                               "reason": "r"},
                              {"index": 9, "important": True},
                              {"index": "x", "important": False}],
                    "conclusion": "ok"}

    gem = BadGemini()
    ms = FakeMS(rows)
    mail_ai._gemini = lambda: gem
    mail_ai._mailstore = lambda: ms
    out = mail_ai.triage_unread("acct", "INBOX")
    items = out.get("items") or []
    check("triage(bad-index): out-of-range/non-int indices dropped, valid kept",
          len(items) == 1 and items[0]["uid"] == "2", str(items))


# --------------------------------------------------------------------------- #
#  The route gate — loopback happy path + cross-origin CSRF rejection.
# --------------------------------------------------------------------------- #
import server      # noqa: E402


def _start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _post(port, payload, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/mail/triage",
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_route_loopback_happy_and_csrf():
    calls = []
    orig = mail_ai.triage_unread
    mail_ai.triage_unread = (lambda acct, folder="INBOX":
                             (calls.append((acct, folder))
                              or {"items": [], "conclusion": "c", "count": 0,
                                  "truncated": False}))
    httpd, port = _start_server()
    try:
        code, out = _post(port, {"acct": "a", "folder": "INBOX"})    # loopback, no Origin
        check("route: loopback POST -> 200", code == 200, str(code))
        check("route: reached triage_unread once with the acct/folder",
              calls == [("a", "INBOX")], str(calls))
        check("route: response carries items+conclusion", "items" in out and "conclusion" in out,
              str(out))

        code2, _ = _post(port, {"acct": "a"}, origin="http://evil.example:1234")
        check("route: cross-origin POST -> 403 (CSRF gate)", code2 == 403, str(code2))
        check("route: triage NOT reached on cross-origin", len(calls) == 1, str(calls))
    finally:
        httpd.shutdown()
        httpd.server_close()
        mail_ai.triage_unread = orig


def test_route_is_in_the_mutation_gate():
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    check("gate: /api/mail/triage is in the mutation tuple _MUT",
          '"/api/mail/triage"' in src)


def main():
    for fn in (test_only_unread_and_uid_mapping, test_truncation_over_40,
               test_scan_limit_requested, test_no_unread_skips_gemini,
               test_hallucinated_index_dropped, test_route_loopback_happy_and_csrf,
               test_route_is_in_the_mutation_gate):
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
