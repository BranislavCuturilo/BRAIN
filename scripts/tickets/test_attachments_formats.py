#!/usr/bin/env python3
"""The three formats a customer sends that we used to list by name and never read.

.odt/.ods/.odp, .pptx and .rtf were classified "other": the attachment appeared
on the ticket as a file name and its contents never reached the model. The
capability is anydoc's; the implementation is stdlib, so there is no wheel to
install and nothing to fail on a fresh machine.

Every case here is built as a REAL file in memory -- a genuine zip, a genuine
RTF control-word soup -- because a renderer tested against a string that was
never zipped proves nothing about the format.

  python scripts/tickets/test_attachments_formats.py
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attachments as A  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def zipped(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members.items():
            z.writestr(name, body)
    return buf.getvalue()


def main() -> int:
    print("odf -- a real OpenDocument zip")
    odt = zipped({"mimetype": "application/vnd.oasis.opendocument.text",
                  "content.xml": '<?xml version="1.0"?><office:document-content>'
                                 "<text:p>Nalog nije proknji&amp;#382;en</text:p>"
                                 "<text:p>Drugi pasus</text:p>"
                                 "<table:table-row><table:table-cell>A1</table:table-cell>"
                                 "<table:table-cell>B1</table:table-cell></table:table-row>"
                                 "</office:document-content>"})
    ok(A.classify(odt, "x.odt") == "odf", f"classified: {A.classify(odt, 'x.odt')}")
    out = A.render_odf(odt)
    ok("Drugi pasus" in out, "the second paragraph is there")
    ok(out.count("\n") >= 2, f"paragraphs became lines, not one run-on blob: {out!r}")
    ok("<text:p>" not in out and "office:" not in out, "no tags leaked into the text")

    print("odf -- a spreadsheet reads row by row")
    ods = zipped({"content.xml": "<x><table:table-row>a</table:table-row>"
                                 "<table:table-row>b</table:table-row></x>"})
    ok(A.classify(ods, "x.ods") == "odf", "an .ods is odf too")
    ok(A.render_odf(ods).splitlines() == ["a", "b"], "one line per row")

    print("pptx -- slide ORDER is numeric, not alphabetical")
    slides = {f"ppt/slides/slide{i}.xml": f"<p:sld><a:t>Slajd {i}</a:t></a:p></p:sld>"
              for i in (1, 2, 10)}
    slides["ppt/notesSlides/notesSlide1.xml"] = "<a:t>beleska</a:t>"
    pptx = zipped(slides)
    ok(A.classify(pptx, "d.pptx") == "pptx", "classified as pptx")
    text = A.render_pptx(pptx)
    ok([ln for ln in text.splitlines() if ln] == ["Slajd 1", "Slajd 2", "Slajd 10"],
       f"1, 2, 10 -- not 1, 10, 2: {text.splitlines()}")
    ok("beleska" not in text, "speaker notes are not slide content")

    print("rtf -- the font table must not become the first paragraph")
    rtf = (rb"{\rtf1\ansi\deff0{\fonttbl{\f0\froman Times New Roman;}"
           rb"{\f1\fswiss Arial;}}{\colortbl;\red0\green0\blue0;}"
           rb"\f0\fs24 Ne mogu da obri\'9aem stavku\par Drugi red\par}")
    ok(A.classify(rtf, "b.rtf") == "rtf", "classified as rtf")
    out = A.render_rtf(rtf)
    ok("Times New Roman" not in out and "Arial" not in out,
       f"the font table is gone -- this is the whole trap: {out!r}")
    ok("Ne mogu da obri" in out, "the body survived")
    ok("Drugi red" in out, r"\par became a line break")
    ok("\\" not in out and "{" not in out, f"no control words or braces left: {out!r}")

    print("rtf is recognised by its magic too, not only by the extension")
    ok(A.classify(rtf, "attachment") == "rtf", "no extension, still rtf")

    print("nothing raises on rubbish")
    for fn in (A.render_odf, A.render_pptx, A.render_rtf):
        got = fn(b"\x00\x01 not a document at all")
        ok(isinstance(got, str), f"{fn.__name__} returned a string, not an exception")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
