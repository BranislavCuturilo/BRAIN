#!/usr/bin/env python3
"""ebr_review finds exactly the three marked tickets, and nothing else.

A temp store with six tickets: one the extension got right (limitation, closed
by design), one where the context LIED (limitation, closed as fixed), one where
context was MISSING (bug, closed by design), an untagged ticket (not the
extension's; must be invisible), and a split pair resolved on different days.
The report must name the lie and the gap by ticket, rank the screen with no
file as the one to write next, and propose edits in that order.

  python scripts/tickets/test_ebr_review.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebr_review  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def tag(**kw) -> str:
    d = {"screens": "-", "role": "revizor", "flags": "-", "skills": "-", "kind": "bug",
         "split": "", "edited": "-"}
    d.update(kw)
    parts = [f"screens: {d['screens']}", f"role: {d['role']}", f"flags: {d['flags']}",
             f"skills: {d['skills']}", f"kind: {d['kind']}"]
    if d["split"]:
        parts.append(f"split: {d['split']}")
    parts.append(f"edited: {d['edited']}")
    return "[ebr 0.3 | " + " | ".join(parts) + "]"


def ticket(desc: str, resolution: str = "", closed_at: str = "") -> dict:
    t = {"title": "t", "original": {"title": "t", "description": desc}}
    if resolution:
        t["closed"] = {"at": closed_at or "2026-09-01T10:00:00", "resolution": resolution}
    return t


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vez = {"tickets": {
            "1": ticket("Ne mogu da obrišem.\n\n" + tag(screens="stocktaking:list✓", kind="limitation", skills="screen-list"),
                        "Nije greška — brisanje je namerno onemogućeno, objašnjeno korisniku."),
            "2": ticket("Ne mogu da obrišem stavku.\n\n" + tag(screens="audits:audit_detail✓", kind="limitation", skills="screen-detail", edited="title"),
                        "Ispravljeno: brisanje je bilo blokirano greškom u proveri prava."),
            "3": ticket("Dugme fali.\n\n" + tag(screens="faults:list✗", kind="bug", skills="screen-list", edited="title,priority"),
                        "Korisnik nema pravo faults.manage — nije greška."),
            "4": ticket("Obican tiket bez ekstenzije.", "Ispravljeno."),
            "5": ticket("Deo 1.\n\n" + tag(screens="faults:list✗ locations:create✗", kind="bug", split="1/2"),
                        "Ispravljeno.", "2026-09-02T10:00:00"),
            "6": ticket("Deo 2.\n\n" + tag(screens="faults:list✗ locations:create✗", kind="change_request", split="2/2"),
                        "Nije greška, po dizajnu.", "2026-09-04T10:00:00"),
            "7": ticket("Popis ne radi.\n\n" + tag(screens="?/popis/lista✗", kind="bug"), "Ispravljeno."),
            "8": ticket("Nema dugmeta.\n\n" + tag(screens="?/popis/unos✗", kind="bug"), "Korisnik nema pravo."),
            "9": ticket("Bez ekrana.\n\n" + tag(screens="-", kind="limitation"), "Ispravljeno."),
        }}
        (root / "DEMO.json").write_text(json.dumps(vez, ensure_ascii=False), encoding="utf-8")
        (root / "modules.json").write_text("{}", encoding="utf-8")   # a catalog, not a module file

        print("classify()")
        ok(ebr_review.classify("Ispravljeno: bug u proveri") == "fixed", "fixed (sr)")
        ok(ebr_review.classify("Nije greška — po dizajnu") == "by_design", "by design (sr)")
        ok(ebr_review.classify("Korisnik nema pravo") == "by_design", "no rights -> by design")
        ok(ebr_review.classify("Ispravljeno, ali je bilo namerno") == "by_design", "by-design words outrank fixed words (the ticket names it; read the note)")
        ok(ebr_review.classify("") == "" and ebr_review.classify("Zatvoreno.") == "other", "empty / other")

        print("review()")
        r = ebr_review.review(root)
        ok(r["tickets_tagged"] == 8, f"eight tagged tickets, the untagged one invisible: {r['tickets_tagged']}")
        ok(r["screens"] == 6 and r["coverage_pct"] == 33, f"6 screens, 2 always with a file -> 33%: {r['coverage_pct']}")
        ok([w["screen"] for w in r["write_next"]] == ["faults:list", "locations:create", "/popis/lista", "/popis/unos"],
           f"write next: the screen with the most uncovered tickets first: {r['write_next']}")
        ok(r["write_next"][2]["unmarked"] is True and r["write_next"][0]["unmarked"] is False, "unmarked flagged")
        ok(any(p["action"] == "adopt page context" and "/popis/lista" in p["target"] for p in r["proposals"]),
           "an unmarked screen proposes adopting page context in that app, not writing a file")
        ok(not any("docs/pages/?" in p["target"] for p in r["proposals"]), "never a file named after a path")
        ok(r["write_next"][0]["tickets"] == 3, "faults:list counted across 3 tickets")
        ok([x["id"] for x in r["lies"]] == ["2", "9"], f"tickets 2 and 9 are the lies: {[x['id'] for x in r['lies']]}")
        ok([x["id"] for x in r["missing"]] == ["3", "8"], f"tickets 3 and 8 are the gaps: {[x['id'] for x in r['missing']]}")
        ok(any(p["action"] == "adopt page context" and "/popis/unos" in p["target"] for p in r["proposals"]),
           "a gap on an unmarked screen proposes adoption, not a file")
        ok(any(p["action"] == "find the screen" for p in r["proposals"]), "a lie with no screen recorded asks for the screen")
        ok(not any("?" in p["target"] for p in r["proposals"]), "no proposal names a ? path")
        ok(r["edited_fields"] == {"title": 2, "priority": 1}, f"edited fields counted: {r['edited_fields']}")
        ok(r["edits_by_skill"].get("screen-list") == {"title": 1, "priority": 1}, f"edits per skill: {r['edits_by_skill']}")
        ok(len(r["splits"]) == 1 and r["splits"][0]["verdict"] == "separate" and r["splits"][0]["ids"] == ["5", "6"],
           f"the split pair, resolved separately: {r['splits']}")

        print("proposals")
        acts = [(p["action"], p["target"]) for p in r["proposals"]]
        ok(acts[0] == ("fix limits", "docs/pages/audits-audit_detail.md"), f"first: fix the lie: {acts[0]}")
        ok(acts[1] == ("find the screen", "(the tag recorded no screen; read the ticket)"), f"second: the other lie, with no screen: {acts[1]}")
        gap = acts.index(("add limits/requires", "docs/pages/faults-list.md"))
        ok(gap > 1 and gap < acts.index(("write", "docs/pages/faults-list.md")), f"lies, then gaps, then writes: {acts}")
        ok(("write", "docs/pages/faults-list.md") in acts and ("write", "docs/pages/locations-create.md") in acts,
           "then write the uncovered screens")
        ok([p["n"] for p in r["proposals"]] == list(range(1, len(acts) + 1)), "numbered for a 'da'")
        ok(len({(p['action'], p['target']) for p in r['proposals']}) == len(r['proposals']), "no duplicate proposal")

        print("render()")
        text = ebr_review.render(r)
        ok("CONTEXT LIES" in text and "DEMO#2" in text, "the alarm names the ticket")
        ok("CONTEXT MISSING" in text and "DEMO#3" in text, "the gap names the ticket")
        ok("WRITE NEXT" in text and text.index("faults:list") < text.index("locations:create"), "write-next order")
        ok("cadence.py did ebr-review" in text, "tells how to record the run")
        empty = ebr_review.review(root / "nothing")
        ok(empty["tickets_tagged"] == 0 and "Nothing to measure" in ebr_review.render(empty), "an empty store is a sentence, not a crash")

        print("does the interview pay? -- 0.3 tickets are NOT evidence either way")
        # Everything in the store above carries a 0.3 tag, which has no `skip`
        # field at all. Counting those as "not skipped" would manufacture a
        # finished-arm out of tickets that predate the question.
        ok(r["interview"]["comparable"] == 0,
           f"0.3 tags are excluded from the comparison: {r['interview']['comparable']}")
        ok(r["interview"]["finished"]["tickets"] == 0,
           "and specifically do NOT land in the finished arm")
        ok("Nothing to compare yet" in text, "the report says so rather than printing 0.0 vs 0.0")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Two arms, deliberately lopsided: the skipped tickets needed rounds of
        # comments, the finished ones closed clean.
        def t04(skip: str, rounds: int, edited: str = "-") -> dict:
            tg = ("[ebr 0.4 | screens: a:b✓ | role: r | flags: - | skills: - "
                  f"| kind: bug | skip: {skip} | edited: {edited}]")
            d = ticket("Opis.\n\n" + tg, "Ispravljeno.")
            d["comments"] = [{"text": "?"} for _ in range(rounds)]
            return d

        (root / "DEMO.json").write_text(json.dumps({"tickets": {
            "10": t04("pitanja", 3, "title,priority"),
            "11": t04("pitanja", 1, "title"),
            "12": t04("-", 0),
            "13": t04("-", 0),
        }}, ensure_ascii=False), encoding="utf-8")

        r2 = ebr_review.review(root)
        iv = r2["interview"]
        ok(iv["comparable"] == 4, f"all four 0.4 tickets are comparable: {iv['comparable']}")
        ok(iv["skipped"]["tickets"] == 2 and iv["finished"]["tickets"] == 2, "two per arm")
        ok(iv["skipped"]["rounds_avg"] == 2.0, f"skipped rounds: {iv['skipped']['rounds_avg']}")
        ok(iv["finished"]["rounds_avg"] == 0.0, f"finished rounds: {iv['finished']['rounds_avg']}")
        ok(iv["finished"]["clean_pct"] == 100 and iv["skipped"]["clean_pct"] == 0, "closed-clean split")
        ok(iv["skipped"]["edits_avg"] == 1.5, f"corrections follow the skip: {iv['skipped']['edits_avg']}")

        text2 = ebr_review.render(r2)
        ok("DOES THE INTERVIEW PAY?" in text2, "the section is rendered")
        # Four tickets is an anecdote. The report must refuse to sound conclusive.
        ok("Too few in one arm" in text2, "it refuses to conclude from two per arm")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
