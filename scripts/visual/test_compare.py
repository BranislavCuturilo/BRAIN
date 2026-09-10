#!/usr/bin/env python3
"""Offline tests for compare.py - the structural verdict must ignore data noise,
catch layout, and say when it is not a verdict at all. Synthetic signatures and
PIL images in a temp dir; no browser, no network.
Run: python test_compare.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # the engine is a PACKAGE: `visual.*`
from visual import compare as vcompare  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def _sig(tree_hash, blocks):
    return {"dom_signature": tree_hash,
            "geometry": [{"path": p, "x": x, "y": y, "w": w, "h": h}
                         for p, x, y, w, h in blocks]}


BLOCKS = [("body:1>main:1", 0, 60, 1440, 800),
          ("body:1>main:1>div:1", 20, 80, 1400, 300)]


def test_the_same_page_with_different_numbers_is_not_structural():
    # The dev database IS production: counters and timestamps move on their own.
    # The tree hash excludes text, so both sides hash the same.
    before = _sig("abc123", BLOCKS)
    after = _sig("abc123", BLOCKS)
    got = vcompare.sweep_changed(before, after)
    ck("sweep: identical tree + layout is not structural", got["structural"] is False)
    ck("sweep: no shift is reported", got["geometry_shift_px"] == 0)
    ck("sweep: why says what was compared", "same tree" in got["why"])
    ck("sweep: and it was a real comparison", got["comparable"] is True)


def test_there_is_no_pixel_verdict_to_misread():
    """Defect: a per-pair pixel ratio turned "the numbers in the database moved"
    into "this screen changed". Structure is the only verdict, so the pixel
    comparison is GONE rather than demoted to a field somebody re-reads as
    evidence."""
    ck("pixels: no pixel comparison is offered at all",
       not hasattr(vcompare, "pixel_changed"))
    ck("pixels: and no threshold invites one back",
       not hasattr(vcompare, "PIXEL_THRESHOLD"))


def test_a_block_that_moved_is_structural():
    before = _sig("abc123", BLOCKS)
    after = _sig("abc123", [("body:1>main:1", 0, 60, 1440, 800),
                            ("body:1>main:1>div:1", 20, 140, 1400, 300)])
    got = vcompare.sweep_changed(before, after)
    ck("sweep: a moved block is structural", got["structural"] is True)
    ck("sweep: the shift is measured", got["geometry_shift_px"] == 60)
    ck("sweep: why names the movement", "moved" in got["why"])


def test_a_sub_pixel_wobble_is_not_a_change():
    before = _sig("abc123", BLOCKS)
    after = _sig("abc123", [("body:1>main:1", 0, 60, 1440, 800),
                            ("body:1>main:1>div:1", 20, 82, 1400, 300)])
    got = vcompare.sweep_changed(before, after)
    ck("sweep: 2px of font metrics is tolerated", got["structural"] is False)
    ck("sweep: but the shift is still reported", got["geometry_shift_px"] == 2)


def test_a_changed_tree_is_structural_even_with_identical_geometry():
    got = vcompare.sweep_changed(_sig("aaa", BLOCKS), _sig("bbb", BLOCKS))
    ck("sweep: a different tree hash is structural", got["structural"] is True)
    ck("sweep: why names the tree", got["why"] == "dom tree changed")


def test_added_and_removed_blocks_are_counted():
    after = _sig("abc123", BLOCKS + [("body:1>main:1>aside:1", 0, 0, 200, 200)])
    got = vcompare.sweep_changed(_sig("abc123", BLOCKS), after)
    ck("sweep: a new block is structural", got["structural"] is True)
    ck("sweep: the counts are in why", "1 added / 0 removed" in got["why"])


def test_a_missing_signature_is_reported_not_assumed_equal():
    ck("sweep: nothing on either side is not a change",
       vcompare.sweep_changed({}, {})["structural"] is False)
    ck("sweep: a signature on one side only is a change",
       vcompare.sweep_changed(_sig("a", []), {})["structural"] is True)
    ck("sweep: a non-dict does not raise",
       vcompare.sweep_changed(None, "x")["structural"] is False)
    # `structural: False` with no signature is an ABSENCE, not a verdict. The
    # caller drops a pair only on a real "same tree, same layout" - a page whose
    # signature failed is the one that most needs looking at.
    ck("sweep: no signature is marked as not comparable",
       vcompare.sweep_changed({}, {})["comparable"] is False)
    ck("sweep: one-sided signature is not comparable either",
       vcompare.sweep_changed(_sig("a", []), {})["comparable"] is False)
    ck("sweep: a real comparison says so",
       vcompare.sweep_changed(_sig("a", BLOCKS), _sig("b", BLOCKS))["comparable"] is True)


def _png(path, size=(200, 120), colour=(255, 255, 255), band=None):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, colour)
    if band:
        ImageDraw.Draw(img).rectangle(band[0], fill=band[1])
    img.save(path)
    return str(path)


def test_phash_is_stable_and_hamming_separates_different_pictures():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = _png(d / "a.png", band=((10, 10, 100, 60), (30, 60, 120)))
        a2 = _png(d / "a2.png", band=((10, 10, 100, 60), (30, 60, 120)))
        b = _png(d / "b.png", band=((100, 60, 190, 110), (200, 30, 30)))
        ha, ha2, hb = vcompare.phash(a), vcompare.phash(a2), vcompare.phash(b)
        ck("phash: 16 hex characters", len(ha) == 16)
        ck("phash: the same picture hashes the same", ha == ha2)
        ck("phash: a different picture is far away", vcompare.hamming(ha, hb) > 4)
        ck("phash: garbage compares as maximally different",
           vcompare.hamming("zzz", ha) == 64)


def test_the_dedup_threshold_matches_what_was_measured():
    """The cut between "another picture of this screen" and "the same picture
    again" is pinned to the run it came from: 84 shots of 30 pages
    (`.shots/vez05513/after`), every state against its page's base shot.

    Everything that only opened a navbar dropdown landed at 0-6 bits; the four
    tab panes of the role form landed at 26-28; NOTHING landed in between. A
    threshold that lets a 6 through, or that swallows a 26, is not the one that
    was measured - which is the whole reason the number is not a taste.
    """
    base = "f0f0f0f0f0f0f0f0"

    def at(distance):
        """A hash exactly `distance` bits away from `base`."""
        return "%016x" % (int(base, 16) ^ ((1 << distance) - 1))

    for d in (0, 2, 4, 6):
        ck("dedup: %d bits is the same picture again" % d,
           vcompare.same_picture(base, at(d)))
    for d in (26, 28):
        ck("dedup: %d bits is a picture of its own (the role form tabs)" % d,
           not vcompare.same_picture(base, at(d)))
    # The tight edge, measured in a browser and not in the real run: an open
    # modal WITH a Bootstrap backdrop is eight bits from its base shot, because
    # a phash is blind to a uniform dimming of the page. One bit of margin.
    ck("dedup: 8 bits is a modal, and a modal is its own picture",
       not vcompare.same_picture(base, at(8)))
    ck("dedup: the threshold is the highest one that collapses the duplicates",
       vcompare.SAME_PICTURE_MAX_HAMMING == 7)
    ck("dedup: an unhashable shot is never collapsed",
       not vcompare.same_picture("", base) and not vcompare.same_picture("zz", base))


def main() -> int:
    for fn in (test_the_same_page_with_different_numbers_is_not_structural,
               test_there_is_no_pixel_verdict_to_misread,
               test_a_block_that_moved_is_structural,
               test_a_sub_pixel_wobble_is_not_a_change,
               test_a_changed_tree_is_structural_even_with_identical_geometry,
               test_added_and_removed_blocks_are_counted,
               test_a_missing_signature_is_reported_not_assumed_equal,
               test_phash_is_stable_and_hamming_separates_different_pictures,
               test_the_dedup_threshold_matches_what_was_measured):
        try:
            fn()
        except Exception as exc:                                # noqa: BLE001
            ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
