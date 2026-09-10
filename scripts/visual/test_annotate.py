#!/usr/bin/env python3
"""Offline tests for annotate.py - the caption a customer reads, the crop they
look at, and the baseline thumbnail. PIL images in a temp dir; no network, no
browser.

The rule under test is the operator's, 2026-08-21: the pair is PRE + POSLE + the
name of the screen, and NOTHING in it may claim what changed. So the caption is
checked for what it must NOT contain (a quoted token, a "novo" prefix, a
percentage, the word for "change") as hard as for what it must.
Run: python test_annotate.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # the engine is a PACKAGE: `visual.*`
from visual import annotate as vannotate  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def _png(path, size=(400, 300), colour=(250, 250, 250), band=None):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, colour)
    if band:
        ImageDraw.Draw(img).rectangle(band, fill=(40, 90, 180))
    img.save(path)
    return str(path)


def _naive_cut(png, rect, out_dir) -> str:
    """The same-COORDINATES crop, which is what this engine must not produce any
    more. Test support: it exists to be compared against and found different."""
    from PIL import Image
    img = Image.open(png).convert("RGB")
    piece = img.crop(rect)
    scale = vannotate.CROP_MIN_WIDTH / float(rect[2] - rect[0])
    scale = min(vannotate.CROP_MAX_SCALE, max(1.0, scale))
    if scale > 1.0:
        piece = piece.resize((int(piece.width * scale), int(piece.height * scale)),
                             Image.LANCZOS)
    path = Path(out_dir) / "naive.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    piece.save(path)
    return str(path)


def _differs(a, b) -> float:
    """Share of pixels that differ between two images - TEST SUPPORT ONLY. The
    engine answers "did this screen change" structurally; this only asks whether
    the drawing code drew."""
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 1.0
    diff = ImageChops.difference(ia, ib).convert("L")
    hist = diff.point(lambda v: 255 if v > 12 else 0).histogram()
    return hist[255] / float(ia.width * ia.height)


#: Characters and words that can only be there because something CLAIMED
#: something: a quoted token off the diff, a one-sided "novo"/"uklonjeno" label,
#: a percentage of moved pixels. All four shipped to a customer at some point.
FORBIDDEN = ('"', "'", "novo", "uklonjeno", "%", "nije lokalizovana",
             "nema promene", "deo #", "deo .", "stil ")


def _clean(caption) -> bool:
    low = str(caption).lower()
    return not any(bad in low for bad in FORBIDDEN)


def test_the_caption_is_the_screen_name():
    got = vannotate.caption_from_facts("Novi korisnik")
    ck("caption: it is the screen name", got == "Novi korisnik")
    ck("caption: nothing in it claims anything", _clean(got))
    ck("caption: the base state adds no label",
       vannotate.caption_from_facts("Novi korisnik", "base") == "Novi korisnik")
    ck("caption: an empty state is the base state",
       vannotate.caption_from_facts("Novi korisnik", "") == "Novi korisnik")


def test_a_surviving_state_names_which_picture_this_is():
    """A state only reaches a caption if its picture survived deduplication, so
    it distinguishes two REAL pictures of one screen. It says which one this is,
    never what changed on it."""
    got = vannotate.caption_from_facts("Nova rola", "lokacije")
    ck("caption: the state is part of the screen name", got == "Nova rola: lokacije")
    ck("caption: with a state it still claims nothing", _clean(got))
    ck("caption: underscores in a state name read as words",
       vannotate.caption_from_facts("Nova rola", "sr_latn") == "Nova rola: sr latn")


def test_it_cannot_quote_a_token_off_the_diff():
    """THE REGRESSION. A real ticket shipped

        novo: Novi korisnik: analytics analitika: "location_city"

    - `location_city` is a material-icon name that genuinely came out of the
    diff and means nothing whatever to the customer. The caption is built from
    the page inventory alone now, so there is no argument through which a token
    can reach it: the facts are the title and the state, and that is the whole
    signature.
    """
    import inspect
    params = list(inspect.signature(vannotate.caption_from_facts).parameters)
    # `others` is a COUNT of screens this picture stands for, not anything read
    # off the change. There is still no argument through which a token could
    # reach the caption.
    ck("caption: the only facts are the title, the state and a screen count",
       params == ["page_title", "state", "others"])
    for title, state in (("Novi korisnik", "analytics_analitika"),
                         ("Korisnici (lista)", "warning_eskalacije"),
                         ('Kreiraj korisnika · Kontrola', "base")):
        ck("caption: %r stays clean" % state,
           _clean(vannotate.caption_from_facts(title, state)))


def test_nothing_that_makes_a_claim_came_back_with_the_crop():
    """The crop returned; the things that spoke did not, and must not.

    `pair()` marked a rectangle on the FULL picture, `standalone()` shipped a
    one-sided zoom, `ONE_SIDED_LABEL` said "novo", `UNLOCATED_NOTE` said the
    change could not be localised and `rephrase()` let a model reword any of it.
    Cropping shows where to look; every one of those told the customer what to
    think.
    """
    for name in ("pair", "standalone", "rephrase", "_mark", "crop_pair",
                 "ONE_SIDED_LABEL", "UNLOCATED_NOTE"):
        ck("dead: annotate.%s stays deleted" % name, not hasattr(vannotate, name))
    ck("crop: the region machinery is back under its own name",
       all(hasattr(vannotate, n) for n in ("region_rect", "crop_regions",
                                           "merge_regions", "shift_rect",
                                           "frame_box")))


def test_the_count_of_screens_a_picture_stands_for_is_declined():
    """A layout-only collapse says how many other screens this one picture is
    the picture of. It is a fact about WHICH SCREENS, not about what changed -
    and it is read by a Serbian-speaking customer, so 1/2/5 decline
    differently."""
    ck("others: one", vannotate.caption_from_facts("Podesavanja", "base", 1)
       == "Podesavanja + 1 drugi ekran")
    ck("others: two", vannotate.caption_from_facts("Podesavanja", "base", 2)
       == "Podesavanja + 2 druga ekrana")
    ck("others: twenty-two", vannotate.caption_from_facts("Podesavanja", "base", 22)
       == "Podesavanja + 22 druga ekrana")
    ck("others: twenty-five", vannotate.caption_from_facts("Podesavanja", "base", 25)
       == "Podesavanja + 25 drugih ekrana")
    ck("others: eleven is not one", vannotate.caption_from_facts("P", "base", 11)
       == "P + 11 drugih ekrana")
    ck("others: none says nothing at all",
       vannotate.caption_from_facts("Podesavanja", "base", 0) == "Podesavanja")
    ck("others: the tail survives a title long enough to be trimmed",
       vannotate.caption_from_facts(" ".join(["rec"] * 20), "base", 3).endswith(
           "+ 3 druga ekrana"))


def test_the_region_is_the_block_not_the_element():
    """THE ASK: "ne bukvalno full zoom na element nego na div u kom se nalazi".
    A 200x24 label cropped with a fixed margin and enlarged to fill a comment is
    a giant word and half a select box - which is what the first run of this
    produced on `locations:location_update`."""
    label = {"x": 400, "y": 600, "w": 206, "h": 24}
    field = {"x": 380, "y": 580, "w": 700, "h": 120}

    bare = vannotate.region_rect({"element": label, "block": None, "kind": ""})
    block = vannotate.region_rect({"element": label, "block": field, "kind": "div"})
    ck("region: the block is what gets cut",
       block[0] < field["x"] and block[2] > field["x"] + field["w"])
    ck("region: and the element alone still gets a usable region",
       (bare[2] - bare[0]) >= vannotate.MIN_REGION_W
       and (bare[3] - bare[1]) >= vannotate.MIN_REGION_H)
    ck("region: which is centred on the element, not hanging off it",
       abs(((bare[0] + bare[2]) // 2) - (label["x"] + label["w"] // 2)) <= 2)
    ck("region: a big block keeps a proportional margin, not a fixed one",
       (block[2] - block[0]) > field["w"] + vannotate.CROP_CONTEXT_PX)
    ck("region: the frame is the block when there is one",
       vannotate.frame_box({"element": label, "block": field}) == field)
    ck("region: and the element when there is not",
       vannotate.frame_box({"element": label, "block": None}) == label)
    ck("region: nothing measured is no region at all",
       vannotate.region_rect(None) is None
       and vannotate.region_rect({"element": None, "block": None}) is None)


def test_each_side_is_cut_where_its_own_content_is():
    """A pair is only comparable if both halves show the same PART OF THE SCREEN.
    Same page COORDINATES is not that: an insertion moves everything below it,
    and a real run produced a crop pair showing the "Nivo" field on one side and
    "Sifra"/"Region" on the other. `rect_before` is the caller answer, and this
    is the geometry it rests on."""
    from PIL import Image
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        before = _png(d / "b.png", size=(1440, 900), band=(400, 200, 900, 300))
        # The block MOVED and CHANGED: identical halves are not a region at all
        # (below), so the fixture has to differ as a real change does.
        after = _png(d / "a.png", size=(1440, 900), band=(400, 600, 1000, 700))
        specs = [{"rect": (380, 560, 1080, 760),
                  "rect_before": (380, 160, 1080, 360),
                  "outline": [{"x": 400, "y": 600, "w": 206, "h": 24}],
                  "side": "after", "kind": "div"}]
        got = vannotate.crop_regions(before, after, specs, d / "crops", "p1")
        ck("crop: one entry per region", len(got) == 1)
        row = got[0]
        ck("crop: both sides are produced", row["before"] and row["after"])
        with Image.open(row["before"]) as b, Image.open(row["after"]) as a:
            ck("crop: and they are the same size, pixel for pixel", b.size == a.size)
            ck("crop: enlarged to something readable in a comment",
               a.width >= min(vannotate.CROP_MIN_WIDTH,
                              int(700 * vannotate.CROP_MAX_SCALE)))
        ck("crop: the before half was cut where the block IS, not where the "
           "after half found it",
           _differs(row["before"], _naive_cut(before, specs[0]["rect"], d)) > 0.05)

        # No honest counterpart: the after half alone, and the missing half says
        # so rather than showing a rectangle nobody can justify.
        alone = vannotate.crop_regions(before, after,
                                       [dict(specs[0], rect_before=None)],
                                       d / "crops", "p2")
        ck("crop: a region with no before still ships", len(alone) == 1)
        ck("crop: with the before half empty", alone[0]["before"] is None)

        # Nothing to see: the two cuts are identical, so there is no region.
        same = vannotate.crop_regions(after, after,
                                      [dict(specs[0], rect_before=specs[0]["rect"])],
                                      d / "crops", "p3")
        ck("crop: a region whose two halves are identical is not written",
           same == [])


def test_two_crops_of_one_card_are_one_crop():
    """Several anchors land in one block routinely - a field, its label and its
    help text - and two crops of the same card is the "4 iste slike" complaint
    wearing a different hat.

    The numbers are the ones measured on the run this came from: two sidebar
    items 36px apart share 86% of the smaller cut, two form field groups 140px
    apart share 46%.
    """
    def spec(y, tag):
        return {"rect": (0, y, 520, y + 260), "rect_before": (0, y, 520, y + 260),
                "outline": [{"x": 10, "y": y + 10, "w": 40, "h": 20}],
                "side": "after", "kind": tag}

    close = vannotate.merge_regions([spec(100, "first"), spec(136, "second")])
    ck("merge: 86% of the smaller is one region", len(close) == 1)
    ck("merge: and it carries the outline of everything merged into it",
       len(close[0]["outline"]) == 2)
    ck("merge: the earlier anchor keeps the identity", close[0]["kind"] == "first")
    ck("merge: the rectangle is the union",
       close[0]["rect"] == (0, 100, 520, 396))

    apart = vannotate.merge_regions([spec(100, "first"), spec(240, "second")])
    ck("merge: 46% is two regions - the operator asked to SEE both field groups",
       len(apart) == 2)

    chain = vannotate.merge_regions([spec(100, "a"), spec(400, "b"), spec(136, "c")])
    ck("merge: merging is transitive and does not depend on the walk order",
       len(chain) == 2 and max(len(r["outline"]) for r in chain) == 2)

    half = vannotate.merge_regions([spec(100, "a"),
                                    dict(spec(136, "b"), rect_before=None)])
    ck("merge: a merged region with half an honest before has none",
       len(half) == 1 and half[0]["rect_before"] is None)


def test_a_region_follows_its_block_to_the_other_side():
    ck("shift: the rectangle moves by the block delta",
       vannotate.shift_rect((10, 100, 110, 200),
                            {"x": 20, "y": 120}, {"x": 20, "y": 520})
       == (10, 500, 110, 600))
    ck("shift: sideways too",
       vannotate.shift_rect((10, 100, 110, 200),
                            {"x": 20, "y": 120}, {"x": 60, "y": 120})
       == (50, 100, 150, 200))
    ck("shift: nothing to follow is no rectangle",
       vannotate.shift_rect((10, 100, 110, 200), {"x": 1, "y": 1}, None) is None)


def test_a_long_title_is_cut_not_wrapped():
    long_title = "Pregled svih zahteva za nabavku po lokacijama i statusima za tekucu godinu"
    got = vannotate.caption_from_facts(long_title, "base")
    ck("caption: at most ten words",
       len(got.split()) <= vannotate.MAX_CAPTION_WORDS)
    ck("caption: it is a prefix of the real title", long_title.startswith(got))
    ck("caption: no dangling separator", not got.endswith((":", ",", "-")))


def test_with_no_title_at_all_it_still_claims_nothing():
    got = vannotate.caption_from_facts("", "base")
    ck("caption: an unnamed screen is still a screen", got == "Ekran")
    ck("caption: and not a claimed change", "izmen" not in got.lower())
    ck("caption: a None title behaves the same",
       vannotate.caption_from_facts(None) == "Ekran")


def test_trim_words_is_the_one_cap():
    ck("trim: cuts at the limit",
       vannotate.trim_words("a b c d e f g h i j k l") == "a b c d e f g h i j")
    ck("trim: a short text is untouched", vannotate.trim_words("a b") == "a b")
    ck("trim: empty in, empty out", vannotate.trim_words(None) == "")


def test_the_baseline_thumbnail_is_small():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # A real full-page shot: narrow and very tall.
        src = _png(d / "full.png", size=(1440, 6784), band=(0, 0, 1440, 200))
        out = vannotate.thumbnail(src, d / "thumbs" / "p.webp")
        from PIL import Image
        with Image.open(out) as img:
            ck("thumb: scaled to the tracked width", img.width == vannotate.THUMB_WIDTH)
            ck("thumb: and capped in height", img.height <= vannotate.THUMB_MAX_HEIGHT)
        ck("thumb: a few kilobytes, not a screenshot",
           Path(out).stat().st_size < 30 * 1024)


def main() -> int:
    for fn in (test_the_caption_is_the_screen_name,
               test_a_surviving_state_names_which_picture_this_is,
               test_it_cannot_quote_a_token_off_the_diff,
               test_nothing_that_makes_a_claim_came_back_with_the_crop,
               test_the_count_of_screens_a_picture_stands_for_is_declined,
               test_the_region_is_the_block_not_the_element,
               test_each_side_is_cut_where_its_own_content_is,
               test_two_crops_of_one_card_are_one_crop,
               test_a_region_follows_its_block_to_the_other_side,
               test_a_long_title_is_cut_not_wrapped,
               test_with_no_title_at_all_it_still_claims_nothing,
               test_trim_words_is_the_one_cap,
               test_the_baseline_thumbnail_is_small):
        try:
            fn()
        except Exception as exc:                                # noqa: BLE001
            ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    print("")
    if FAILS:
        print("FAILED %d check(s):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
