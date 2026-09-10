#!/usr/bin/env python3
"""The extension's trace tag, read back out of a ticket description.

BugReporter appends one bracketed line to every description it sends:

    [ebr 0.4 | screens: audits:audit_detail✓ locations:create✗ | role: revizor
     | flags: escalations,scoring | skills: screen-list,vez-tenant
     | kind: limitation | split: 2/2 | skip: pitanja | edited: title,priority]

The helpdesk API accepts nothing but the description, so this line is the
ONLY channel through which "the extension was used, on these screens, with
this role and these flags, and the reporter corrected these fields before
sending" reaches the brain. `sync.py` pulls the description into the store;
this module parses the line back. Same grammar as `src/lib/tag.js` on the
extension side -- a format change is one edit there and one here, and
`test_ebr_tag.py` holds the round trip.

`skip` (tag 0.4) names the wizard steps the reporter declined. Fields are read
BY NAME, so every 0.3 ticket already on the helpdesk keeps parsing and simply
reports nothing declined -- which is not the same statement as "the interview
was completed", and `ebr_review.py` must not conflate the two.

  ebr_tag.py <description-file>     print the parsed tag as JSON
"""
from __future__ import annotations

import json
import re
import sys

TAG_RE = re.compile(r"\[ebr ([^|\]]+)\|([^\]]*)\]\s*$")
STRIP_RE = re.compile(r"\n*\[ebr [^\]]*\]\s*$")

FIELDS = ("version", "with_context", "without_context", "role", "flags",
          "skills", "kind", "split_index", "split_total", "skipped", "edited")


def parse(description) -> dict | None:
    """The tag as a dict, or None when the description carries none. A dash
    means empty, exactly as the extension writes it."""
    m = TAG_RE.search(str(description or ""))
    if not m:
        return None
    out = {"version": m.group(1).strip(), "with_context": [], "without_context": [],
           "role": "", "flags": [], "skills": [], "kind": "",
           "split_index": 0, "split_total": 0, "skipped": [], "edited": []}
    for raw in m.group(2).split("|"):
        k, _, v = raw.partition(":")
        k, v = k.strip(), v.strip()
        if k == "screens":
            for s in v.split():
                if s == "-":
                    continue
                if s.endswith("✓"):
                    out["with_context"].append(s[:-1])
                elif s.endswith("✗"):
                    out["without_context"].append(s[:-1])
        elif k in ("role", "kind"):
            out[k] = "" if v == "-" else v
        elif k in ("flags", "skills", "edited", "skip"):
            # `skip` is the wire name; `skipped` is the field, matching tag.js.
            out["skipped" if k == "skip" else k] = (
                [] if v == "-" else [x for x in v.split(",") if x])
        elif k == "split":
            i, _, n = v.partition("/")
            out["split_index"] = int(i) if i.isdigit() else 0
            out["split_total"] = int(n) if n.isdigit() else 0
    return out


def strip(description) -> str:
    """The description without its tag -- for anything that reads the text as
    the customer's words (similarity, profiles), where `screens` and `role`
    would be noise."""
    return STRIP_RE.sub("", str(description or ""))


def screens(tag: dict | None) -> list[str]:
    """Every screen the session touched, with or without a context file."""
    if not tag:
        return []
    return list(tag.get("with_context", [])) + list(tag.get("without_context", []))


def of_ticket(t: dict) -> dict | None:
    """The tag on a store ticket (original.description), or None."""
    o = t.get("original") if isinstance(t, dict) and isinstance(t.get("original"), dict) else {}
    return parse(o.get("description") or "")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    text = open(sys.argv[1], encoding="utf-8").read()
    print(json.dumps(parse(text), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
