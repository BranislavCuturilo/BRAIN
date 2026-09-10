#!/usr/bin/env python3
"""The tag round-trips between the extension's JS and this parser.

The two sides share a grammar by agreement, not by code, so the line the
extension's own test builds (tests/tag.test.js: `full`) is parsed here
verbatim. If either side changes the format, one of the two suites goes red.

  python scripts/tickets/test_ebr_tag.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebr_tag  # noqa: E402
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # noqa: E702

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


# byte-for-byte what buildTag(full) produces on the extension side
FULL = ("[ebr 0.4 | screens: audits:audit_detail✓ locations:create✗ | role: revizor"
        " | flags: escalations,scoring | skills: screen-list,vez-tenant"
        " | kind: limitation | split: 2/2 | skip: pitanja | edited: title,priority]")
EMPTY = ("[ebr 0.4 | screens: - | role: - | flags: - | skills: - | kind: - "
         "| skip: - | edited: -]")
# Every ticket sent before the wizard existed. It has no `skip:` field at all.
V03 = ("[ebr 0.3 | screens: audits:audit_detail✓ | role: revizor | flags: - "
       "| skills: screen-list | kind: bug | edited: title]")


def main() -> int:
    print("parse()")
    t = ebr_tag.parse("Opis.\n\nKoraci...\n\n" + FULL)
    ok(t is not None and t["version"] == "0.4", "version")
    ok(t["skipped"] == ["pitanja"], f"skipped: {t['skipped']}")
    ok(t["with_context"] == ["audits:audit_detail"], f"screens with context: {t['with_context']}")
    ok(t["without_context"] == ["locations:create"], f"screens without: {t['without_context']}")
    ok(t["role"] == "revizor" and t["flags"] == ["escalations", "scoring"], "role + flags")
    ok(t["skills"] == ["screen-list", "vez-tenant"], "skills")
    ok(t["kind"] == "limitation", "kind")
    ok((t["split_index"], t["split_total"]) == (2, 2), "split")
    ok(t["edited"] == ["title", "priority"], "edited")

    e = ebr_tag.parse(EMPTY)
    ok(e["with_context"] == [] and e["flags"] == [] and e["kind"] == "" and e["split_total"] == 0,
       "dashes parse to empty")
    ok(e["skipped"] == [], "a dash in skip parses to empty")

    print("a 0.3 tag still parses -- tickets already on the helpdesk stay readable")
    old = ebr_tag.parse("Opis.\n\n" + V03)
    ok(old is not None and old["version"] == "0.3", "an older version is read, not rejected")
    ok(old["skipped"] == [], "an ABSENT skip field means nothing declared")
    ok(old["edited"] == ["title"] and old["with_context"] == ["audits:audit_detail"],
       "and every other field is unaffected by the new one")
    ok(ebr_tag.parse("Opis [ebr nije tag] jos teksta") is None, "a bracket mid-text is not a tag")
    ok(ebr_tag.parse("") is None and ebr_tag.parse(None) is None, "no text -> None")
    ok(ebr_tag.parse("x\n\n" + FULL + "\n  ") is not None, "trailing whitespace tolerated")

    print("strip() / screens() / of_ticket()")
    ok(ebr_tag.strip("Opis.\n\n" + FULL) == "Opis.", "strip removes the tag and the gap")
    ok(ebr_tag.strip("Opis bez taga") == "Opis bez taga", "strip leaves untagged text alone")
    ok(ebr_tag.screens(t) == ["audits:audit_detail", "locations:create"], "screens() = both lists")
    ok(ebr_tag.screens(None) == [], "screens(None)")
    ok(ebr_tag.of_ticket({"original": {"description": "x\n\n" + FULL}})["kind"] == "limitation",
       "of_ticket reads original.description")
    ok(ebr_tag.of_ticket({"original": {}}) is None and ebr_tag.of_ticket({}) is None,
       "of_ticket without a description")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
