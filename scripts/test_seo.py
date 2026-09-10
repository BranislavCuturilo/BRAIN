#!/usr/bin/env python3
"""Proof that the SEO reader finds what is absent and never invents a problem.

The failure that matters here is a false MISSING: a page that is correct being
reported as broken trains the operator to skim the list, and then the one real
finding goes past with the noise. So every check is pinned in BOTH directions.

  python scripts/test_seo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seo                                                       # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def states(html: str) -> dict[str, str]:
    return {c["check"]: c["state"] for c in seo.check(html, "t")}


GOOD = """<!doctype html><html lang="sr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PALAS - Sveobuhvatna Poslovna Resenja za digitalno doba</title>
<meta name="description" content="Razvoj softvera, marketing i poslovna optimizacija za kompanije koje zele da rastu bez menjanja nacina na koji rade svaki dan.">
<link rel="canonical" href="https://example.rs/">
<meta property="og:type" content="website">
<meta property="og:url" content="https://example.rs/">
<meta property="og:title" content="PALAS">
<meta property="og:description" content="Poslovna resenja">
<meta property="og:image" content="https://example.rs/og.png">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"PALAS"}</script>
</head><body><h1>Naslov</h1><h2>Pod</h2><img src="a.png" alt="a"></body></html>"""


def main() -> int:
    s = states(GOOD)
    for k in ("title", "meta description", "html lang", "viewport", "canonical",
              "Open Graph", "JSON-LD", "one h1", "heading order", "img alt",
              "twitter:card"):
        ck(f"a correct page: {k} is not flagged", s.get(k) == seo.OK)

    # --- each absence is found ---------------------------------------------
    ck("no og: tags is MISSING",
       states(GOOD.replace('property="og:', 'data-x="og:'))["Open Graph"] == seo.MISS)
    ck("no canonical is MISSING",
       states(GOOD.replace('rel="canonical"', 'rel="nope"'))["canonical"] == seo.MISS)
    ck("no JSON-LD is MISSING",
       states(GOOD.replace("application/ld+json", "text/plain"))["JSON-LD"] == seo.MISS)
    ck("no lang is MISSING", states(GOOD.replace('<html lang="sr">', "<html>"))["html lang"] == seo.MISS)

    # --- the traps that make a tag present and useless ---------------------
    # A relative og:image is the commonest way the tags are all there and the
    # shared card is still blank.
    rel = states(GOOD.replace('content="https://example.rs/og.png"', 'content="assets/og.png"'))
    ck("a RELATIVE og:image is caught even though og: is complete",
       rel.get("og:image absolute") == seo.MISS and rel["Open Graph"] == seo.OK)

    # An invalid JSON-LD block is ignored by every consumer -- same as absent.
    bad = states(GOOD.replace('{"@context":"https://schema.org","@type":"Organization","name":"PALAS"}',
                              '{"@context": broken,}'))
    ck("an unparseable JSON-LD block is MISSING, not OK", bad["JSON-LD"] == seo.MISS)

    # --- structure ----------------------------------------------------------
    ck("two h1 is flagged", states(GOOD.replace("<h1>Naslov</h1>",
       "<h1>A</h1><h1>B</h1>"))["one h1"] != seo.OK)
    ck("a skipped heading level is flagged",
       states(GOOD.replace("<h2>Pod</h2>", "<h4>Pod</h4>"))["heading order"] != seo.OK)
    ck("an image without alt is flagged",
       states(GOOD.replace('<img src="a.png" alt="a">', '<img src="a.png">'))["img alt"] != seo.OK)

    # --- hreflang fires only when the page really is multilingual ----------
    ck("hreflang is not demanded of a single-language page",
       "hreflang" not in states(GOOD))
    multi = states(GOOD.replace("</body>", '<script src="translations.js"></script></body>'))
    ck("a multilingual page with no alternates is flagged",
       multi.get("hreflang") == seo.MISS)
    both = states(GOOD.replace("</head>",
                  '<link rel="alternate" hreflang="en" href="https://example.rs/en/"></head>')
                  .replace("</body>", '<script src="translations.js"></script></body>'))
    ck("declaring alternates clears it", both.get("hreflang") == seo.OK)

    # --- lengths are NOTES, never failures ---------------------------------
    # These are conventions about pixel width, not specification. Reporting a
    # convention as a failure is how a checklist stops being read.
    short = states(GOOD.replace("<title>PALAS - Sveobuhvatna Poslovna Resenja za digitalno doba</title>",
                                "<title>X</title>"))
    ck("a too-short title is a note, not MISSING", short["title"] == seo.WARN)

    # --- the client report: two tenses, never one -------------------------
    # One string cannot serve both readings. Written only in the completed
    # tense, the "what is missing" list read "NEDOSTAJE: Link ka sajtu SADA
    # prikazuje naslov" -- missing: the link now shows the title. Nonsense, in
    # a document a customer is paying for.
    for name, (problem, fixed) in seo.CLIENT_WORDS.items():
        ck(f"client wording differs by tense: {name}", problem != fixed)
    ck("a problem sentence never claims the fix is done",
       not any("sada" in prob.lower() for prob, _f in seo.CLIENT_WORDS.values()))
    ck("a fixed sentence never reads as a complaint",
       not any(f.lower().startswith("nedostaje") for _p, f in seo.CLIENT_WORDS.values()))
    ck("every MISSING-able check has client wording",
       all(k in seo.CLIENT_WORDS for k in
           ("Open Graph", "JSON-LD", "canonical", "hreflang", "og:image absolute")))

    # --- baseline and delta ------------------------------------------------
    import json as _json, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "index.html"
        # Start from a page missing Open Graph and canonical.
        broken = GOOD.replace('property="og:', 'data-x="og:').replace('rel="canonical"', 'rel="x"')
        page.write_text(broken, encoding="utf-8")

        first = [(str(page), seo.check(broken, "t"))]
        seo.save_baseline(str(page), first)
        ck("a baseline file is written beside the page",
           (Path(tmp) / seo.BASELINE).is_file())

        # No movement against itself.
        ck("no delta when nothing changed", seo.delta(seo.load_baseline(str(page)), first) == [])

        # Now fix it and compare.
        page.write_text(GOOD, encoding="utf-8")
        second = [(str(page), seo.check(GOOD, "t"))]
        moved = seo.delta(seo.load_baseline(str(page)), second)
        names = {m["check"]: (m["from"], m["to"]) for m in moved}
        ck("the delta reports Open Graph as fixed",
           names.get("Open Graph") == (seo.MISS, seo.OK))
        ck("the delta reports canonical as fixed",
           names.get("canonical") == (seo.MISS, seo.OK))
        ck("the delta reports ONLY what moved, not the whole list",
           len(moved) <= 4 and "html lang" not in names)

        # A REGRESSION must be visible, not silently absent from "urađeno".
        page.write_text(broken, encoding="utf-8")
        back = seo.delta(seo.load_baseline(str(page)), [(str(page), seo.check(broken, "t"))])
        ck("nothing is reported when the page returns to its baseline", back == [])

        # And a genuine regression against a GOOD baseline is caught.
        page.write_text(GOOD, encoding="utf-8")
        seo.save_baseline(str(page), [(str(page), seo.check(GOOD, "t"))])
        regressed = seo.delta(seo.load_baseline(str(page)),
                              [(str(page), seo.check(broken, "t"))])
        broke = [m for m in regressed if m["from"] == seo.OK]
        ck("a regression from the baseline is reported as BROKE", len(broke) >= 2)

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s)")
        return 1
    print("ok - finds what is absent, and leaves a correct page alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
