#!/usr/bin/env python3
"""The screen's declaration reaches the model, and reaches it correctly.

What this proves, on a temp repo with three docs/pages files:

- the page named in the ticket's words is listed first, its NE DOZVOLJAVA line
  carries the reason and the ticket that set it (`since`), and the rule text
  says "limitation by design";
- a screen the extension marked as having NO file is named as missing, not
  silently absent;
- no docs/pages -> "" (never an exception -- a ticket must not fail on context);
- the cap holds;
- the trace block renders role, flags, screens, kind and the reporter's edits;
- `filter_page` maps free text to a url_name and leaves unknown text alone;
- both real prompt builders (Gemini triage and the ticket reader) contain the
  blocks when the ticket carries a tag -- so the wiring, not only the renderer.

  python scripts/tickets/test_page_context.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import project_context as pc  # noqa: E402
import ebr_tag  # noqa: E402
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # noqa: E702

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


LIST_MD = """---
url_name: stocktaking:list
kind: list
app: stocktaking
access: prijavljen + flag `stocktaking`
requires: [audit.plan]
feature: stocktaking
uses: [location-picker]
limits:
  - what: brisanje stavki popisa
    why: stavke su knjižene kroz MaterialService.adjust; brisanje bi razbilo saldo
    since: DEMO#08650
  - what: izmena količine posle zaključavanja
    why: zaključana lista je knjigovodstveni dokument
    since: design
---
## Prikaz
Lista popisa po lokaciji, sa filterom po statusu.

## Obrada
Ništa.
"""
DETAIL_MD = """---
url_name: audits:audit_detail
kind: detail
app: audits
requires: [audit.view_all]
limits: []
---
## Prikaz
Jedna revizija: zaglavlje, status, tim.
"""
CREATE_MD = """---
url_name: locations:create
kind: create
app: locations
---
## Prikaz
Nova lokacija.
"""
TAG = ("[ebr 0.3 | screens: stocktaking:list✓ faults:list✗ | role: revizor"
       " | flags: stocktaking,scoring | skills: screen-list | kind: limitation | edited: title]")


def repo_with_pages(tmp: Path) -> str:
    d = tmp / "docs" / "pages"
    d.mkdir(parents=True)
    (d / "stocktaking-list.md").write_text(LIST_MD, encoding="utf-8")
    (d / "audits-audit_detail.md").write_text(DETAIL_MD, encoding="utf-8")
    (d / "locations-create.md").write_text(CREATE_MD, encoding="utf-8")
    (d / "README.md").write_text("# not a page\n", encoding="utf-8")
    return str(tmp)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        repo = repo_with_pages(Path(td))

        print("pages_catalog()")
        cat = pc.pages_catalog(repo)
        ok([p["url_name"] for p in cat] == ["audits:audit_detail", "locations:create", "stocktaking:list"],
           f"three pages, README skipped: {[p['url_name'] for p in cat]}")
        st = next(p for p in cat if p["url_name"] == "stocktaking:list")
        ok(st["kind"] == "list" and st["requires"] == ["audit.plan"] and st["feature"] == "stocktaking",
           "scalars and the requires list")
        ok(len(st["limits"]) == 2 and st["limits"][0]["since"] == "DEMO#08650"
           and "MaterialService" in st["limits"][0]["why"], f"limits with why + since: {st['limits']}")
        ok(st["summary"].startswith("Lista popisa"), f"summary from ## Prikaz: {st['summary']!r}")

        print("pages_block()")
        blk = pc.pages_block(repo, "Ne mogu da obrišem stavku popisa na listi popisa")
        ok(blk.startswith(pc.PAGES_HEAD), "head")
        ok(blk.count(chr(10) + "- ") == 1 and "- stocktaking:list" in blk,
           "only the screen the words name is listed; a page with no overlap and no limits costs nothing")
        ok("NE DOZVOLJAVA: brisanje stavki popisa -- stavke su knjižene" in blk and "(DEMO#08650)" in blk,
           "the limit carries reason and provenance")
        ok("locations:create" not in blk, "a page with no overlap and no limits is not listed")
        ok("LIMITATION BY DESIGN" in blk and "rights/configuration question" in blk, "the rule")

        tag = ebr_tag.parse("x\n\n" + TAG)
        blk2 = pc.pages_block(repo, "nesto sasvim drugo", tag)
        ok("faults:list: NO CONTEXT FILE (docs/pages/faults-list.md" in blk2,
           "a screen the trace marks without a file is named as missing")
        ok(blk2.startswith(pc.PAGES_HEAD + chr(10) + "- stocktaking:list"),
           "a screen the trace names is listed first even with no word overlap")
        un = ebr_tag.parse("x" + chr(10) + chr(10) + "[ebr 0.3 | screens: ?/popis/lista✗ | role: - | flags: - | skills: - | kind: bug | edited: -]")
        blk3 = pc.pages_block(repo, "nesto", un)
        ok("/popis/lista: UNMARKED SCREEN" in blk3 and "docs/pages/?" not in blk3,
           "an unmarked screen is named as such, never as a missing file")
        ok("/popis/lista (app publishes no page context)" in pc.tag_block(un), "the trace says the app is silent")

        ok(pc.pages_block(str(Path(td) / "nope"), "bilo sta") == "", "no docs/pages -> empty string")
        ok(pc.pages_block("", "x") == "", "no repo -> empty string")
        small = pc.pages_block(repo, "popis revizija lokacija", cap=len(pc.PAGES_HEAD) + 120)
        ok("cap reached" in small and small.count("\n- ") <= 2, f"cap holds: {small.count(chr(10) + '- ')} entries")

        print("tag_block()")
        tb = pc.tag_block(tag)
        ok(tb.startswith(pc.TRACE_HEAD), "head")
        ok("role: revizor" in tb and "flags ON: stocktaking, scoring" in tb, "role + flags")
        ok("stocktaking:list (has context file)" in tb and "faults:list (NO context file)" in tb, "screens")
        ok("classified this as: limitation" in tb and "weigh it" in tb, "kind, with the caveat")
        ok("corrected the extension's draft in: title" in tb, "edited fields")
        ok(pc.tag_block({}) == "" and pc.tag_block(None) == "", "no tag -> empty")

        print("filter_page()")
        ok(pc.filter_page("stocktaking:list", repo) == "stocktaking:list", "exact")
        ok(pc.filter_page("Stocktaking:List page", repo) == "stocktaking:list", "case + surrounding text")
        ok(pc.filter_page("/faults/ (servis)", repo) == "/faults/ (servis)", "unknown text kept as is")
        ok(pc.filter_page("", repo) == "" and pc.filter_page(None, repo) == "", "empty")

        print("the two prompt builders")
        ticket = {"original": {"title": "Brisanje na popisu", "customer": "Pera",
                               "description": "Ne mogu da obrišem stavku popisa.\n\n" + TAG,
                               "category": "Greška"}, "comments": []}
        import analyze_gemini  # noqa: PLC0415
        prompt = analyze_gemini.triage_prompt(ticket, repo=repo)
        ok(pc.PAGES_HEAD in prompt and "(DEMO#08650)" in prompt, "Gemini triage prompt carries PAGE CONTEXT")
        ok(pc.TRACE_HEAD in prompt and "role: revizor" in prompt, "Gemini triage prompt carries EXTENSION TRACE")
        untagged = analyze_gemini.triage_prompt({"original": {"title": "x", "description": "y"}}, repo=repo)
        ok(pc.TRACE_HEAD not in untagged, "no tag -> no trace block")
        try:
            import ticket_reader  # noqa: PLC0415
            rp = ticket_reader._build_prompt(ticket, "DEMO", {}, repo=repo)
            ok(pc.PAGES_HEAD in rp and pc.TRACE_HEAD in rp, "ticket reader prompt carries both blocks")
        except Exception as e:  # noqa: BLE001
            ok(False, f"ticket_reader._build_prompt: {e}")

        print("similar: the tag is not the customer's words; same page boosts")
        import similar  # noqa: PLC0415
        ok("ebr" not in similar.tokens(similar.ticket_text(ticket)) and "revizor" not in similar.ticket_text(ticket),
           "ticket_text strips the tag")
        ok(similar.page_of(ticket) == "stocktaking:list", "page_of falls back to the trace's first screen")
        ok(similar.page_of({"reading": {"page": "audits:audit_detail"}, "original": {}}) == "audits:audit_detail",
           "page_of prefers the reader's url_name")
        row = {"toks": ["obris", "stavk"], "page": "stocktaking:list"}
        w = {"obris": 1.0, "stavk": 1.0}
        base, _ = similar.score(["obris"], row, w, False, False, False)
        boosted, _ = similar.score(["obris"], row, w, False, False, False, same_page=True)
        ok(abs(boosted / base - 1.30) < 1e-9, f"same_page boost is 1.30x ({boosted / base:.2f})")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
