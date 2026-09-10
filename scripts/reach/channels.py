#!/usr/bin/env python3
"""Reading things outside the repo — one channel per kind of source.

**Why this exists rather than a dependency.** `researcher` had `WebFetch` and
`WebSearch` and nothing else: keyword search over pages, and a fetch that
returns whatever a JavaScript-heavy documentation site happens to render. Three
kinds of source that answer this project's actual questions were unreachable --
a library's issue tracker ("is this a known bug"), a security feed ("did Django
ship a fix"), and semantic search ("how does X behave in version Y", which is a
meaning question, not a keyword one).

**Every channel has a PRIMARY and a FALLBACK, and always reports which one
answered.** A result whose provenance is unknown is a result you cannot judge:
Jina's extraction and a crude tag-strip disagree about tables, and `gh` sees
private repositories that the anonymous API cannot. Silent degradation is the
failure this design exists to prevent, so `via` is part of every return value.

**Every channel here is KEYLESS.** No account, no API key, nothing to set up
on a new machine -- which is the property that decides whether a source is
still reachable in six months on a laptop nobody configured. Where a key
would help (`gh auth login` raises GitHub's rate limit), it is an
improvement, never a requirement, and `doctor` says which state you are in.
Any key that does appear later comes from the process environment and
nowhere else (`ops-integrations`).

## The rule that matters more than any of the above

**A query is an OUTBOUND channel.** Fetching a URL sends that URL. Searching
sends the search text. This machine holds real customer names, ticket bodies and
helpdesk credentials, so **never pass ticket text, a customer name or anything
out of `tickets_store/` into a search.** Reduce the question to the technical
part first -- that is also the question that gets a better answer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "brain-reach/1.0 (+https://github.com/BranislavCuturilo)"
TIMEOUT = 30
GZIP_MAGIC = b"\x1f\x8b"


class Unavailable(RuntimeError):
    """This channel cannot run right now, and the message says what to do."""


def _get(url: str, headers: dict | None = None, timeout: int = TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310
        raw = resp.read()
    return raw.decode(resp.headers.get_content_charset() or "utf-8", errors="replace")


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": UA, "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:      # noqa: S310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _strip_html(html: str) -> str:
    """Last-resort text extraction. Deliberately crude, and SAID to be crude:
    it drops tables and code structure, which is exactly the content a
    documentation question usually needs. Reported as `via=raw` so the caller
    knows the extraction was the weak one."""
    html = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


# --------------------------------------------------------------------------
# web
# --------------------------------------------------------------------------

def web_probe() -> tuple[bool, str]:
    try:
        _get("https://r.jina.ai/https://example.com", timeout=15)
        return True, "jina reader reachable"
    except Exception as exc:                                        # noqa: BLE001
        return False, f"jina unreachable ({type(exc).__name__}); falls back to raw fetch"


def web(url: str) -> tuple[str, str]:
    """Readable text of a page. Jina Reader first -- it runs the page and
    returns markdown, which is the difference between reading a docs site and
    reading its loading spinner."""
    if not url.startswith(("http://", "https://")):
        raise Unavailable(f"not an http(s) url: {url!r}")
    try:
        return _get("https://r.jina.ai/" + url), "jina"
    except Exception:                                               # noqa: BLE001
        pass
    try:
        return _strip_html(_get(url)), "raw"
    except Exception as exc:                                        # noqa: BLE001
        raise Unavailable(f"could not fetch {url}: {exc}") from exc


# --------------------------------------------------------------------------
# github
# --------------------------------------------------------------------------

def github_probe() -> tuple[bool, str]:
    if shutil.which("gh"):
        r = subprocess.run(["gh", "auth", "status"], capture_output=True,
                           text=True, timeout=20)
        if r.returncode == 0:
            return True, "gh CLI, authenticated (sees private repos too)"
        return True, "gh CLI present but NOT authenticated -- `gh auth login`; public API used"
    return True, "no gh CLI -- anonymous API only (60 req/h, public repos)"


def github_issues(repo: str, query: str = "", limit: int = 10) -> tuple[list[dict], str]:
    """Search a repository's issues. THE question this answers is 'has someone
    already hit this', which is the cheapest research there is and was
    previously unreachable without a browser."""
    if "/" not in repo:
        raise Unavailable(f"repo must be owner/name, got {repo!r}")

    why = "no gh CLI"
    if shutil.which("gh"):
        args = ["gh", "issue", "list", "--repo", repo, "--state", "all",
                "--limit", str(limit), "--json", "number,title,state,url,updatedAt"]
        if query:
            args += ["--search", query]
        r = subprocess.run(args, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            try:
                return json.loads(r.stdout or "[]"), "gh"
            except ValueError:
                why = "gh returned unparseable JSON"
        else:
            # CARRY THE REASON. "via=api" alone cannot distinguish `gh` being
            # absent from the repository having issues turned off -- and the
            # second one changes the answer, because a project that uses Trac
            # or a mailing list has its real discussion somewhere this channel
            # is not looking. Measured on django/django, which disables issues.
            why = (r.stderr or r.stdout or "gh failed").strip().splitlines()[0][:90]

    q = urllib.parse.quote(f"repo:{repo} {query}".strip())
    url = f"https://api.github.com/search/issues?q={q}&per_page={limit}"
    try:
        data = json.loads(_get(url, {"Accept": "application/vnd.github+json"}))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise Unavailable(
                "GitHub rate-limited this machine (anonymous: 60 req/h). "
                "`gh auth login` raises it to 5000.") from exc
        raise Unavailable(f"github API {exc.code}") from exc
    except Exception as exc:                                        # noqa: BLE001
        raise Unavailable(f"github unreachable: {exc}") from exc

    # The search API returns issues AND pull requests; a caller told only
    # "issues" reads a PR title as a bug report.
    return [{"number": i.get("number"), "title": i.get("title"),
             "state": i.get("state"), "url": i.get("html_url"),
             "kind": "pr" if i.get("pull_request") else "issue",
             "updatedAt": i.get("updated_at")}
            for i in data.get("items", [])], f"api ({why})"


# --------------------------------------------------------------------------
# rss
# --------------------------------------------------------------------------

#: Feeds worth having by name rather than remembered. Security advisories are
#: the case: nobody goes looking for them, and they are the ones that matter.
FEEDS = {
    "django": "https://www.djangoproject.com/rss/weblog/",
    "django-security": "https://groups.google.com/forum/feed/django-announce/msgs/rss.xml",
    "python": "https://blog.python.org/feeds/posts/default",
    "python-security": "https://mail.python.org/archives/list/security-announce@python.org/feed/",
}


def rss_probe() -> tuple[bool, str]:
    try:
        rss(FEEDS["django"], limit=1)
        return True, f"{len(FEEDS)} named feeds ({', '.join(sorted(FEEDS))})"
    except Exception as exc:                                        # noqa: BLE001
        return False, f"feed fetch failed: {type(exc).__name__}"


def rss(url_or_name: str, limit: int = 10) -> tuple[list[dict], str]:
    url = FEEDS.get(url_or_name, url_or_name)
    try:
        root = ET.fromstring(_get(url))
    except ET.ParseError as exc:
        raise Unavailable(f"not a parseable feed: {url} ({exc})") from exc
    except Exception as exc:                                        # noqa: BLE001
        raise Unavailable(f"feed unreachable: {url} ({exc})") from exc

    # A well-formed HTML page IS valid XML, so `fromstring` succeeds on it and
    # the loop below simply finds no items -- returning [] , which reads as
    # "no news" rather than "wrong URL". Check the ROOT before believing an
    # empty result. Found by the test, which fed it `<html>not a feed</html>`.
    root_tag = root.tag.split("}")[-1].lower()
    if root_tag not in ("rss", "feed", "rdf"):
        raise Unavailable(
            f"{url} parsed as XML but its root is <{root_tag}>, not a feed. "
            f"An empty result here would have read as 'nothing new'.")

    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    # RSS 2.0 and Atom in one pass -- a feed list that only understands one of
    # them silently returns nothing for half the sources.
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        def txt(name: str) -> str:
            node = el.find(name) if "}" not in el.tag else el.find(f"a:{name}", ns)
            if node is None:
                node = el.find(name)
            return (node.text or "").strip() if node is not None else ""
        link = txt("link")
        if not link:
            node = el.find("a:link", ns)
            link = node.get("href", "") if node is not None else ""
        items.append({"title": txt("title"), "link": link,
                      "date": txt("pubDate") or txt("updated") or txt("published")})
        if len(items) >= limit:
            break
    return items, "rss"


# --------------------------------------------------------------------------
# search, and the technical forums -- ALL KEYLESS
# --------------------------------------------------------------------------
#
# Exa lived here and was removed: it is paid, and a channel whose key is never
# going to be set is a permanently dead row in `doctor`, which is noise.
#
# What replaced it was chosen by MEASUREMENT, not reputation. Probed
# 2026-08-31 with a real technical query:
#
#   marginalia    20 results, Django docs and the Django forum first   -> kept
#   stackoverflow 5 results                                            -> kept
#   hacker news   5 results                                            -> kept
#   pypi          full release history                                 -> kept
#   duckduckgo    202 + an "anomaly"/"challenge" page, ZERO results    -> refused
#   ddg instant   0-char abstract, 0 related topics on a tech query    -> refused
#   reddit        403 Blocked, plain UA and browser UA alike           -> refused
#   searx.be      returns HTML when asked for JSON                     -> refused
#   lobste.rs     400 Bad Request                                      -> refused
#
# DuckDuckGo is the instructive one. It answers 202 with a plausible-looking
# page, so a scraper that trusts the status code returns an empty list and the
# caller reads "nothing found" instead of "I was blocked" -- exactly the silent
# degradation this module exists to prevent.


def _json(url: str, headers: dict | None = None) -> dict:
    """GET JSON, tolerating the gzip some of these APIs send unasked."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:      # noqa: S310
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == GZIP_MAGIC:
            import gzip
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", errors="replace"))


def search_probe() -> tuple[bool, str]:
    try:
        items, _ = search("django queryset")
        return bool(items), f"marginalia, keyless ({len(items)} for a probe query)"
    except Exception as exc:                                        # noqa: BLE001
        # Carry the MESSAGE, not the class name. `Unavailable` is this
        # module's own wrapper, so reporting the type says "unavailable:
        # Unavailable" and hides the cause -- the same defect already fixed
        # once in the github fallback.
        return False, f"marginalia unreachable: {exc}"


def search(query: str, limit: int = 10) -> tuple[list[dict], str]:
    """General web search. Marginalia is an independent index that favours
    documentation and long-form pages over SEO chaff, which is the right bias
    for "how does this actually behave". No key, no account."""
    url = "https://api.marginalia.nu/public/search/" + urllib.parse.quote(query)
    try:
        data = _json(url)
    except Exception as exc:                                        # noqa: BLE001
        raise Unavailable(f"marginalia unreachable: {exc}") from exc
    return [{"title": r.get("title"), "url": r.get("url"),
             "text": (r.get("description") or "")[:400]}
            for r in data.get("results", [])[:limit]], "marginalia"


def so_probe() -> tuple[bool, str]:
    try:
        stackoverflow("django", limit=1)
        return True, "stackexchange API, keyless (300 req/day per IP)"
    except Unavailable as exc:
        return False, str(exc)[:90]


def stackoverflow(query: str, limit: int = 8, site: str = "stackoverflow"
                  ) -> tuple[list[dict], str]:
    """The single most likely place a Django question is already answered.

    `score` and `answered` come back because an unanswered question with two
    votes is a different artefact from an accepted answer with four hundred,
    and the title alone hides the difference."""
    url = ("https://api.stackexchange.com/2.3/search/advanced"
           f"?order=desc&sort=relevance&q={urllib.parse.quote(query)}"
           f"&site={urllib.parse.quote(site)}&pagesize={limit}&filter=default")
    try:
        data = _json(url)
    except Exception as exc:                                        # noqa: BLE001
        raise Unavailable(f"stackexchange unreachable: {exc}") from exc
    if data.get("error_id"):
        raise Unavailable(f"stackexchange: {data.get('error_message')}")
    return [{"title": i.get("title"), "url": i.get("link"),
             "score": i.get("score"), "answered": i.get("is_answered"),
             "answers": i.get("answer_count"), "tags": i.get("tags", [])[:5]}
            for i in data.get("items", [])], f"stackexchange/{site}"


def hn_probe() -> tuple[bool, str]:
    try:
        hn("python", limit=1)
        return True, "hn via algolia, keyless -- the forum that IS reachable"
    except Unavailable as exc:
        return False, str(exc)[:90]


def hn(query: str, limit: int = 8) -> tuple[list[dict], str]:
    """Hacker News, and it is worth naming why this and not the others:
    Reddit answers 403 to an unauthenticated client (measured, plain and
    browser user-agents alike) and X needs a paid key. This is the technical
    forum that is still readable without an account."""
    url = ("https://hn.algolia.com/api/v1/search?"
           f"query={urllib.parse.quote(query)}&hitsPerPage={limit}")
    try:
        data = _json(url)
    except Exception as exc:                                        # noqa: BLE001
        raise Unavailable(f"hn unreachable: {exc}") from exc
    out = []
    for h in data.get("hits", []):
        oid = h.get("objectID")
        out.append({"title": h.get("title") or h.get("story_title"),
                    "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                    "discussion": f"https://news.ycombinator.com/item?id={oid}",
                    "points": h.get("points"), "comments": h.get("num_comments"),
                    "date": (h.get("created_at") or "")[:10]})
    return out, "hn"


def pypi_probe() -> tuple[bool, str]:
    try:
        pypi("django")
        return True, "pypi json, keyless -- versions and release dates"
    except Unavailable as exc:
        return False, str(exc)[:90]


def pypi(package: str) -> tuple[dict, str]:
    """Version facts -- the ones most often answered from memory and most often
    wrong. `requires_python` plus the release dates settle "can we upgrade"
    without reading a changelog."""
    try:
        data = _json(f"https://pypi.org/pypi/{urllib.parse.quote(package)}/json")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise Unavailable(f"no such package on PyPI: {package!r}") from exc
        raise Unavailable(f"pypi {exc.code}") from exc
    except Exception as exc:                                        # noqa: BLE001
        raise Unavailable(f"pypi unreachable: {exc}") from exc

    info = data.get("info") or {}
    recent = []
    for ver, files in (data.get("releases") or {}).items():
        when = (files[0].get("upload_time") or "")[:10] if files else ""
        if when:
            recent.append((when, ver))
    recent.sort(reverse=True)
    return {"name": info.get("name"), "version": info.get("version"),
            "summary": info.get("summary"),
            "requires_python": info.get("requires_python"),
            "home": info.get("home_page") or info.get("project_url"),
            "recent": [{"version": v, "date": d} for d, v in recent[:10]]}, "pypi"


CHANNELS = {
    "web": (web_probe, "read a page as text"),
    "search": (search_probe, "general web search"),
    "so": (so_probe, "stackoverflow questions"),
    "hn": (hn_probe, "hacker news discussion"),
    "github": (github_probe, "search a repo's issues"),
    "pypi": (pypi_probe, "package versions and dates"),
    "rss": (rss_probe, "release and security feeds"),
}
