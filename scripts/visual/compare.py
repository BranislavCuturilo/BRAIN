#!/usr/bin/env python3
"""Did this screen CHANGE? One answer, and it is structural.

`sweep_changed` compares the DOM tree signature and the positions of the main
blocks. Same tree with different numbers is NOT a change; a block that moved or
appeared is. It is the verdict for the sweep over every page AND for the pair
the customer is about to receive - one question, one implementation.

**Pixels are deliberately not here.** The dev database IS the production one, so
counters, times, chart values and row counts move on their own; a per-pair pixel
ratio flagged those pages as changed and the operator was shown data noise as if
it were a UI change ("vide se i svi grafikoni i inputi u bazi ... a zapravo su
samo promenjeni podaci"). A ratio nobody is allowed to decide on is a number
that will be read as a decision anyway, so it is gone rather than demoted.

`phash`/`hamming` stay, and they have exactly two jobs, neither of which is a
verdict: the BASELINE's cheap identity check (has this page's picture moved at
all), and DEDUPLICATION of one page's states (`same_picture`) - "have I already
kept this exact picture", where being wrong costs one duplicate rather than a
claim to a customer.
"""
from __future__ import annotations

import math

#: Blocks move a pixel or two from font metrics and scrollbars. Below this, a
#: shift is not a layout change.
GEOMETRY_TOLERANCE_PX = 2


def _sig(x):
    """Accept either a shot dict (`{dom_signature, geometry, ...}`) or the bare
    pair, so a caller can pass what capture returned without unpacking it."""
    if not isinstance(x, dict):
        return "", []
    return str(x.get("dom_signature") or ""), list(x.get("geometry") or [])


def sweep_changed(before_sig, after_sig) -> dict:
    """`{"structural": bool, "comparable": bool, "geometry_shift_px": int,
    "why": str}` - THE verdict on whether a screen changed.

    `comparable` is False when a side carries no signature at all. Then
    `structural` is an absence, not an answer, and a caller must not read False
    as "nothing changed" and hide the pair: a page whose signature failed to
    record is exactly the page somebody needs to look at.
    """
    b_hash, b_geo = _sig(before_sig)
    a_hash, a_geo = _sig(after_sig)

    if not b_hash and not a_hash:
        return {"structural": False, "comparable": False, "geometry_shift_px": 0,
                "why": "no signature on either side"}
    if bool(b_hash) != bool(a_hash):
        return {"structural": True, "comparable": False, "geometry_shift_px": 0,
                "why": "signature missing on one side"}

    b_map = {g.get("path"): g for g in b_geo if isinstance(g, dict)}
    a_map = {g.get("path"): g for g in a_geo if isinstance(g, dict)}
    added = [p for p in a_map if p not in b_map]
    removed = [p for p in b_map if p not in a_map]

    shift = 0
    for path, gb in b_map.items():
        ga = a_map.get(path)
        if not ga:
            continue
        for key in ("x", "y", "w", "h"):
            try:
                shift = max(shift, abs(int(ga.get(key, 0)) - int(gb.get(key, 0))))
            except (TypeError, ValueError):
                continue

    if b_hash != a_hash:
        return {"structural": True, "comparable": True, "geometry_shift_px": shift,
                "why": "dom tree changed"}
    if added or removed:
        return {"structural": True, "comparable": True, "geometry_shift_px": shift,
                "why": "blocks %d added / %d removed" % (len(added), len(removed))}
    if shift > GEOMETRY_TOLERANCE_PX:
        return {"structural": True, "comparable": True, "geometry_shift_px": shift,
                "why": "block moved or resized by %dpx" % shift}
    return {"structural": False, "comparable": True, "geometry_shift_px": shift,
            "why": "same tree, same layout (text/numbers may differ)"}


# --------------------------------------------------------------------------- #
#  Perceptual hash - the baseline's 16 characters per page
# --------------------------------------------------------------------------- #
_PHASH_SIZE = 32
_PHASH_LOW = 8
_COS = [[math.cos((2 * x + 1) * u * math.pi / (2 * _PHASH_SIZE))
         for x in range(_PHASH_SIZE)] for u in range(_PHASH_SIZE)]


def _dct_1d(vec):
    return [sum(vec[x] * _COS[u][x] for x in range(_PHASH_SIZE))
            for u in range(_PHASH_SIZE)]


def phash(png_path) -> str:
    """64-bit DCT perceptual hash as 16 hex chars. Compare with `hamming`."""
    from PIL import Image
    img = Image.open(png_path).convert("L").resize((_PHASH_SIZE, _PHASH_SIZE))
    px = list(img.getdata())
    rows = [_dct_1d(px[r * _PHASH_SIZE:(r + 1) * _PHASH_SIZE]) for r in range(_PHASH_SIZE)]
    cols = [_dct_1d([rows[r][c] for r in range(_PHASH_SIZE)]) for c in range(_PHASH_SIZE)]
    low = [cols[u][v] for v in range(_PHASH_LOW) for u in range(_PHASH_LOW)]
    rest = sorted(low[1:])                                     # drop DC before the median
    med = rest[len(rest) // 2]
    bits = 0
    for i, val in enumerate(low):
        if val > med:
            bits |= (1 << i)
    return "%016x" % bits


def phash_or_blank(png_path) -> str:
    """`phash`, or "" when there is nothing to hash - no file, no Pillow, a
    truncated write. "" never compares equal to anything (`hamming` answers 64),
    so a picture that cannot be hashed is always KEPT, whether the caller is
    deduplicating a page state or a crop region.
    """
    import sys
    from pathlib import Path as _Path
    if not png_path or not _Path(png_path).is_file():
        return ""
    try:
        return phash(png_path)
    except Exception as exc:                                    # noqa: BLE001
        print("visual/compare: phash failed for %s (%s: %s)"
              % (png_path, type(exc).__name__, str(exc)[:80]), file=sys.stderr)
        return ""


def hamming(a: str, b: str) -> int:
    """Differing bits between two `phash` values; 64 when either is unusable."""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


#: Two shots of the SAME PAGE this close are the same picture, and only one of
#: them is worth keeping.
#:
#: Measured over a real 84-shot run (DEMO#05513, 30 pages, `.shots/vez05513/after`),
#: every state against its page's base shot:
#:
#:     hamming  0 -> 23 states   6 -> 2 states
#:              2 -> 14 states  26 -> 2 states  (role form, "revizije"/"sistem" tab)
#:              4 -> 11 states  28 -> 2 states  (role form, "lokacije"/"upitnici" tab)
#:
#: 50 of 54 states were a navbar dropdown over an unchanged page; the 4 that
#: genuinely showed something else were an order of magnitude away.
#:
#: **The gap is not as wide as those two clusters suggest, so the cut is the
#: HIGHEST value that still collapses every measured duplicate, not the middle
#: of the gap.** An open Bootstrap modal WITH its half-black backdrop measures
#: EIGHT bits from the same page's base shot (`fixture_site.PAGE`, measured in
#: chromium) - a dialog no human would call the same picture, one bit above the
#: cut. A phash compares each DCT coefficient against the median, so it is
#: deliberately blind to a uniform dimming of the whole page; what moves it is a
#: change in the LAYOUT of light and dark, which is why the role form's tab
#: panes are at 26-28 and a modal over the same content is at 8.
#:
#: So the two mistakes are not symmetric and the margin is one bit. Keeping a
#: near-duplicate costs the operator one click in the gallery. Collapsing a
#: state that mattered costs the picture the ticket was about, which is the
#: exact case this engine exists for ("stranica moze da izgleda isto a da joj se
#: modal promenio"). Raising this number to the conventional pHash "different
#: image" line (10) would swallow every modal state in the suite.
#:
#: Both edges are pinned: `test_compare` on the numbers, `test_capture` in a
#: browser (the modal at 8 survives, the `<details>` at 2 collapses).
SAME_PICTURE_MAX_HAMMING = 7


def same_picture(a: str, b: str) -> bool:
    """Is this the picture I already have? DEDUPLICATION ONLY.

    Never ask it whether a screen CHANGED - that is `sweep_changed`, it is
    structural, and it is the only thing allowed to decide keep-vs-drop. An
    unusable hash answers 64 -> False, so a shot that cannot be hashed is KEPT.
    """
    return hamming(a, b) <= SAME_PICTURE_MAX_HAMMING
