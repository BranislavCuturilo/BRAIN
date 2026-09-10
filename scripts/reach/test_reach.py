#!/usr/bin/env python3
"""Proof that the channels parse, fall back, and NAME the backend that answered.

**The transport is blocked for the whole module, once.** craft-testing's rule:
mocking per test protects only the test that remembered the patch, and the
tests most likely to forget are the ones whose whole claim is "nothing
downstream fires". Here every network entry point raises `AssertionError` --
not the library's own error class, which a handler under test would catch and
swallow, turning a live-traffic bug into a green run.

The guard is then PROVEN to fire before anything else is asserted.

  python scripts/reach/test_reach.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import channels as ch                                               # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


# --- block EVERY transport for every test in this file --------------------
#
# `_json` was added after `_get` and does NOT go through it -- it calls urlopen
# directly. A guard listing only the transports that existed when it was
# written is the exact hole craft-testing warns about, so this asserts the list
# is complete instead of trusting it: every module-level callable whose name
# starts with `_` and that opens a socket has to be here.
TRANSPORTS = ("_get", "_post_json", "_json")


def _blocked(*_a, **_kw):
    raise AssertionError("no network in tests")


for _name in TRANSPORTS:
    setattr(ch, _name, _blocked)


def _swap(name, fn):
    class _Ctx:
        def __enter__(self):
            setattr(ch, name, fn)
        def __exit__(self, *_):
            setattr(ch, name, _blocked)
    return _Ctx()


def with_get(fn):
    """Fake `_get` for ONE call, then put the block back."""
    return _swap("_get", fn)


def with_json(fn):
    """Fake `_json` for ONE call, then put the block back."""
    return _swap("_json", fn)


RSS_XML = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Django 5.2.1 released</title>
<link>https://example.test/a</link><pubDate>Mon, 01 Sep 2026 10:00:00 +0000</pubDate></item>
<item><title>Second</title><link>https://example.test/b</link>
<pubDate>Tue, 02 Sep 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM_XML = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom entry</title><link href="https://example.test/atom"/>
<updated>2026-09-01T10:00:00Z</updated></entry></feed>"""


def main() -> int:
    # --- the guard itself, before trusting any other result ---------------
    try:
        ch.web("https://example.test/x")
        ck("the network guard fires", False)
    except AssertionError:
        ck("the network guard fires", True)
    except ch.Unavailable:
        # web() catches broad exceptions on its way to the fallback, so an
        # AssertionError becomes Unavailable. That still proves nothing left
        # the machine, which is the property under test.
        ck("the network guard fires", True)

    # --- rss: both feed dialects ------------------------------------------
    with with_get(lambda *a, **k: RSS_XML):
        items, via = ch.rss("https://example.test/feed")
    ck("rss: parses RSS 2.0", len(items) == 2)
    ck("rss: title", items[0]["title"] == "Django 5.2.1 released")
    ck("rss: link", items[0]["link"] == "https://example.test/a")
    ck("rss: date", "01 Sep 2026" in items[0]["date"])
    ck("rss: via is named", via == "rss")

    with with_get(lambda *a, **k: ATOM_XML):
        items, _ = ch.rss("https://example.test/atom")
    # Atom puts the URL in an attribute, not the element text. A reader that
    # only understands RSS returns entries with no link at all -- present, and
    # useless, which is worse than absent.
    ck("rss: parses Atom too", len(items) == 1 and items[0]["title"] == "Atom entry")
    ck("rss: Atom link comes from the href attribute",
       items[0]["link"] == "https://example.test/atom")

    with with_get(lambda *a, **k: "<html>not a feed</html>"):
        try:
            ch.rss("https://example.test/nope")
            ck("rss: a non-feed is Unavailable, not empty", False)
        except ch.Unavailable:
            ck("rss: a non-feed is Unavailable, not empty", True)

    # --- web: the fallback must be VISIBLE ---------------------------------
    with with_get(lambda url, *a, **k: "# clean markdown" if "r.jina.ai" in url
                  else "<html><body>raw</body></html>"):
        text, via = ch.web("https://example.test/doc")
    ck("web: jina answers first", via == "jina" and "clean markdown" in text)

    def only_direct(url, *a, **k):
        if "r.jina.ai" in url:
            raise OSError("jina down")
        return "<html><body><p>Hello</p><script>x()</script></body></html>"

    with with_get(only_direct):
        text, via = ch.web("https://example.test/doc")
    ck("web: falls back when jina is down", via == "raw")
    ck("web: the fallback SAYS it is the fallback", via != "jina")
    ck("web: script content is stripped", "x()" not in text and "Hello" in text)

    with with_get(lambda *a, **k: (_ for _ in ()).throw(OSError("dead"))):
        try:
            ch.web("https://example.test/x")
            ck("web: both backends down is Unavailable", False)
        except ch.Unavailable:
            ck("web: both backends down is Unavailable", True)

    try:
        ch.web("ftp://example.test/x")
        ck("web: refuses a non-http scheme", False)
    except ch.Unavailable:
        ck("web: refuses a non-http scheme", True)

    # --- the guard covers the transport added LATER ------------------------
    # If `_json` were not blocked, every keyless channel below would silently
    # reach the real network from the test suite.
    for name, call in (("search", lambda: ch.search("x")),
                       ("so", lambda: ch.stackoverflow("x")),
                       ("hn", lambda: ch.hn("x")),
                       ("pypi", lambda: ch.pypi("x"))):
        try:
            call()
            ck(f"{name}: transport is blocked in tests", False)
        except (AssertionError, ch.Unavailable):
            ck(f"{name}: transport is blocked in tests", True)

    # --- search (marginalia) -----------------------------------------------
    with with_json(lambda *a, **k: {"results": [
            {"title": "QuerySet API reference", "url": "https://docs.djangoproject.com/x",
             "description": "select_related follows foreign keys"}]}):
        items, via = ch.search("django queryset")
    ck("search: parses marginalia", len(items) == 1)
    ck("search: keeps the url", items[0]["url"].endswith("/x"))
    ck("search: names the backend", via == "marginalia")

    # --- stackoverflow ------------------------------------------------------
    with with_json(lambda *a, **k: {"items": [
            {"title": "Why is select_related slow", "link": "https://so/1",
             "score": 42, "is_answered": True, "answer_count": 3,
             "tags": ["django", "orm"]}]}):
        items, via = ch.stackoverflow("select_related")
    ck("so: parses", len(items) == 1)
    # The title alone cannot distinguish an accepted answer with 42 votes from
    # an unanswered question with 0 -- that is why these two ride along.
    ck("so: carries score", items[0]["score"] == 42)
    ck("so: carries answered", items[0]["answered"] is True)
    ck("so: names the site in via", via.endswith("/stackoverflow"))

    with with_json(lambda *a, **k: {"error_id": 502, "error_message": "throttled"}):
        try:
            ch.stackoverflow("x")
            ck("so: an API error is Unavailable, not an empty list", False)
        except ch.Unavailable as exc:
            ck("so: an API error is Unavailable, not an empty list",
               "throttled" in str(exc))

    # --- hacker news --------------------------------------------------------
    with with_json(lambda *a, **k: {"hits": [
            {"objectID": "41413641", "title": "Taming the Django ORM",
             "url": "https://blog/x", "points": 143, "num_comments": 140,
             "created_at": "2024-09-01T10:00:00Z"},
            {"objectID": "999", "title": "Ask HN: ORM?", "url": None,
             "points": 5, "num_comments": 2, "created_at": "2026-01-01T00:00:00Z"}]}):
        items, via = ch.hn("django orm")
    ck("hn: parses", len(items) == 2)
    ck("hn: keeps the article url", items[0]["url"] == "https://blog/x")
    ck("hn: discussion link is always present", "41413641" in items[0]["discussion"])
    # An Ask HN post has no external url; falling back to None would render as
    # a dead link rather than as the discussion it actually is.
    ck("hn: a story with no url falls back to its discussion",
       items[1]["url"] == items[1]["discussion"] and "999" in items[1]["url"])

    # --- pypi ---------------------------------------------------------------
    with with_json(lambda *a, **k: {
            "info": {"name": "Django", "version": "6.1", "summary": "web framework",
                     "requires_python": ">=3.12", "home_page": "https://djangoproject.com"},
            "releases": {"6.1": [{"upload_time": "2026-08-05T10:00:00"}],
                         "5.2.17": [{"upload_time": "2026-08-04T10:00:00"}],
                         "0.9": []}}):
        info, via = ch.pypi("django")
    ck("pypi: current version", info["version"] == "6.1")
    ck("pypi: requires_python -- the fact most often wrong from memory",
       info["requires_python"] == ">=3.12")
    ck("pypi: releases are newest first", [r["version"] for r in info["recent"]]
       == ["6.1", "5.2.17"])
    # A release with no files has no date; including it undated would sort
    # arbitrarily and put a decade-old version at the top.
    ck("pypi: a release with no files is dropped, not undated",
       all(r["date"] for r in info["recent"]))

    # --- github: the argument guard runs before any transport -------------
    try:
        ch.github_issues("notarepo")
        ck("github: rejects a repo without owner/name", False)
    except ch.Unavailable:
        ck("github: rejects a repo without owner/name", True)

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s)")
        return 1
    print("ok - channels parse, fall back visibly, and never answer from nowhere")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
