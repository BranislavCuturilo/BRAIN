#!/usr/bin/env python3
"""What the customer actually receives: the BEFORE picture, the AFTER picture,
and the NAME OF THE SCREEN. Nothing else.

Plus, when the change can be located on the screen, **the same region of both
pictures, enlarged** - the card / list item / form group the change sits in, cut
to ONE rectangle in page coordinates so the eye compares like with like.

No "novo", no percentage, no token in the caption, no rectangle on the full
pictures: every one of those is a CLAIM about what changed, and a claim the
customer cannot check costs more than the picture is worth. The operator,
2026-08-21: "odbaci poredjenja koja su napravljena jer ne valjaju - ne prikazuju
poredjenje sta je stvarno uradjeno vec ikonice i nebuloze."

The crop is the other half of that sentence, from the same operator a day later:
"nije napravio poredjenje sta je izmenjeno uvecano ... ne zoom na ikonicu vec taj
segment ukviren i uvecan". Showing WHERE TO LOOK is not a claim about what
changed - the customer still judges. So the crop exists, the outline inside it is
thin, and neither of them says a word.

What that replaced: the engine boxed the best anchor it could read out of the
diff and captioned it, so a real ticket shipped
`novo: Novi korisnik: analytics analitika: "location_city"` - `location_city`
being a material-icon name that genuinely came from the diff and means nothing
whatever to the person reading the ticket. Ranking the anchors better does not
fix that; the customer is not owed the engine's evidence, they are owed the two
pictures.

**Whether a screen changed at all is still decided** - by `compare.sweep_changed`,
structurally, in `shoot.py`. That is what keeps an unchanged page out of the
gallery. It just never speaks in the caption.

So this module now holds the two things that are still MADE rather than
captured: the caption (derived from the page inventory, never written) and the
baseline thumbnail.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_CAPTION_WORDS = 10

#: Cropping. `CROP_CONTEXT_PX` is the margin around the block (or around the bare
#: element, when no block could be found) - enough to see what the region sits
#: next to. A crop narrower than `CROP_MIN_WIDTH` is enlarged up to
#: `CROP_MAX_SCALE`, because a 200px-wide cut of a 1440px page is unreadable in a
#: ticket comment. A crop with any side under `CROP_MIN_SIDE` is not sent at all.
CROP_CONTEXT_PX = 48
CROP_MIN_WIDTH = 900
CROP_MAX_SCALE = 3.0
CROP_MIN_SIDE = 48

#: The smallest region worth sending, grown around the block. A `<label>` is 200
#: by 24 pixels: cut with a fixed margin and enlarged to fill a comment, it is a
#: giant word and half a select box - technically the changed element, and
#: useless (measured on the first run of this: `locations:location_update`
#: produced a 302x120 cut upscaled 3x). Roughly a third of a 1440x900 screen is
#: enough to recognise the form, the card or the menu the region sits in.
MIN_REGION_W = 520
MIN_REGION_H = 260

#: Two regions this close are ONE region. The measure is the share of the
#: SMALLER cut rectangle the two have in common, because that is what "the two
#: crops would show mostly the same pixels" means.
#:
#: Measured on the run this came from (1440x900 viewport, region floor 520x260):
#:   two sidebar items 36px apart      -> 86% of the smaller  -> one crop
#:   two form field groups 140px apart -> 46%                 -> two crops
#:   an unrelated block 180px away     -> 32%                 -> two crops
#: The operator asked to SEE added field groups separately, so the cut sits above
#: 46 and below 86, with room on both sides. Merging is transitive: a union that
#: then covers a third region swallows it too.
MERGE_MIN_OVERLAP = 0.6

#: The outline drawn INSIDE the crop, around the element the diff named. Two
#: tones so it reads on a white card and on a dark navbar alike - and blue
#: rather than red: it says "look here", not "this is wrong". It is never drawn
#: on the full picture, and never on a side where the element was not measured.
OUTLINE_RGB = (37, 99, 235)
OUTLINE_HALO = (255, 255, 255)
OUTLINE_WIDTH = 2

#: The git-tracked baseline's budget: 320px wide, the top of the page only, and
#: a quality that keeps a real screen around 15 KB.
THUMB_WIDTH = 320
THUMB_MAX_HEIGHT = 640
THUMB_QUALITY = 60

_WORD = re.compile(r"\S+")


def frame_box(region):
    """What gets the outline: THE BLOCK, and the element only when there is no
    block. The operator asked for "taj segment ukviren" - the segment framed -
    and a rectangle around a 20px icon is the picture they were complaining
    about, not the fix for it."""
    if not region:
        return None
    return region.get("block") or region.get("element")


def region_rect(region) -> tuple:
    """The rectangle to cut, as `(x0, y0, x1, y1)` in page pixels, or None.

    THE BLOCK, not the element: a zoom onto a 20px icon is a picture of an icon.
    Then a margin, and then a floor - `MIN_REGION_W/H` - because a small block
    with a proportional margin is still a small crop, and the customer has to be
    able to tell WHERE on the screen they are looking. The full pictures beside
    it carry the rest of that job.
    """
    box = frame_box(region)
    if not box:
        return None
    try:
        x, y = int(box["x"]), int(box["y"])
        w, h = int(box["w"]), int(box["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if w < 1 or h < 1:
        return None
    # Proportional margin, never below the fixed one: a big card needs air
    # around it too, or the crop reads as a broken screenshot.
    pad_x = max(CROP_CONTEXT_PX, int(w * 0.15))
    pad_y = max(CROP_CONTEXT_PX, int(h * 0.25))
    x0, y0, x1, y1 = x - pad_x, y - pad_y, x + w + pad_x, y + h + pad_y
    grow_x = max(0, MIN_REGION_W - (x1 - x0)) // 2
    grow_y = max(0, MIN_REGION_H - (y1 - y0)) // 2
    return (x0 - grow_x, y0 - grow_y, x1 + grow_x, y1 + grow_y)


def _area(rect) -> int:
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def _union(a, b) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _overlaps(a, b) -> bool:
    """Would two crops of these rectangles show mostly the same pixels?"""
    inter = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    smaller = min(_area(a), _area(b))
    return bool(smaller) and _area(inter) >= MERGE_MIN_OVERLAP * smaller


def merge_regions(specs) -> list:
    """Fold `[{rect, outline, side, kind}, ...]` into one entry per REGION.

    Several anchors routinely land in the same block - a field, its label and its
    help text are three anchors and one thing to look at - and two crops of one
    card is the "4 iste slike" complaint wearing a different hat. Rectangles that
    overlap, contain each other, or sit close enough that the crops would mostly
    agree become one; the outlines of everything merged travel with it, so a
    merged crop still marks each element inside it.

    Input order is anchor rank, and the earlier entry keeps the identity (its
    side, its kind). The caller sorts the survivors into reading order
    afterwards. Merging is transitive and runs to a fixed point.

    The OTHER side merges with it: two regions that are one region here are one
    region there too. If either of them has no honest counterpart, the merged
    region has none - a before rectangle that is right for half of what the crop
    shows is not right.
    """
    out = []
    for spec in specs:
        rect = spec.get("rect")
        if not rect:
            continue
        cur = {"rect": tuple(rect),
               "rect_before": tuple(spec["rect_before"]) if spec.get("rect_before") else None,
               "outline": list(spec.get("outline") or []),
               "side": spec.get("side") or "after", "kind": spec.get("kind") or ""}
        merged = True
        while merged:
            merged = False
            for other in list(out):
                if not _overlaps(cur["rect"], other["rect"]):
                    continue
                out.remove(other)
                both = other["rect_before"] and cur["rect_before"]
                # The EARLIER entry keeps the identity: it came from the
                # higher-ranked anchor, and its side is the one its outlines can
                # be drawn on.
                cur = {"rect": _union(other["rect"], cur["rect"]),
                       "rect_before": (_union(other["rect_before"],
                                              cur["rect_before"]) if both else None),
                       "outline": other["outline"] + cur["outline"],
                       "side": other["side"],
                       "kind": other["kind"] or cur["kind"]}
                merged = True
        out.append(cur)
    return out


def shift_rect(rect, box_from, box_to) -> tuple:
    """`rect` moved so that it sits on `box_to` the way it sat on `box_from`.

    This is what content alignment is: the region keeps its size and its framing,
    and follows the BLOCK it was measured around to wherever that block ended up
    on the other page. An insertion above it moves it down; nothing about the
    picture is guessed.
    """
    if not rect or not box_from or not box_to:
        return None
    try:
        dx = int(box_to["x"]) - int(box_from["x"])
        dy = int(box_to["y"]) - int(box_from["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return (rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy)


def _fit(rect, size):
    """`rect` intersected with an image, or None when what is left is too small
    to be worth sending."""
    x0, y0, x1, y1 = rect
    w, h = size
    x0, y0 = max(0, min(int(x0), w)), max(0, min(int(y0), h))
    x1, y1 = max(0, min(int(x1), w)), max(0, min(int(y1), h))
    if x1 - x0 < CROP_MIN_SIDE or y1 - y0 < CROP_MIN_SIDE:
        return None
    return (x0, y0, x1, y1)


def _same_size(a, b) -> tuple:
    """Two fitted rectangles cut to identical dimensions, each anchored at its
    own top-left. One of them may have been clamped by the edge of a page that
    is shorter or narrower, and two crops of different shapes read as two
    different things."""
    w = min(a[2] - a[0], b[2] - b[0])
    h = min(a[3] - a[1], b[3] - b[1])
    if w < CROP_MIN_SIDE or h < CROP_MIN_SIDE:
        return a, None
    return ((a[0], a[1], a[0] + w, a[1] + h),
            (b[0], b[1], b[0] + w, b[1] + h))


def crop_regions(before_png, after_png, specs, out_dir, prefix) -> list:
    """The same PART OF THE SCREEN on both pictures, enlarged - one entry per
    region.

    `specs` is `[{rect, rect_before, outline, side, kind}]`, already merged and
    ordered; the result is that list with `before` and `after` crop paths added.
    Each picture is opened once for the whole page: a full-page shot is
    1440x6784, and re-opening it per region is the difference between a fast pass
    and a slow one.

    **`rect_before` is not `rect`, and that is the whole point.** Cutting both
    sides at the same page COORDINATES is only right while the layout holds
    still: this engine mostly photographs insertions, and everything below an
    insertion moves down by hundreds of pixels. A crop pair built that way showed
    the "Nivo" field on one side and "Sifra"/"Region" on the other - two
    different parts of one form, side by side, inviting a comparison that was
    never there. The caller aligns on CONTENT and passes the rectangle each side
    actually needs; `rect_before` None means there is no honest before, and the
    region ships with the after half alone.

    One rectangle for both sides, in page coordinates, intersected with both
    images: that is what makes the two crops comparable at a glance. Cutting each
    side to its own measurement produces two pictures of two different places,
    which is the defect the whole pairing rule exists to avoid.

    Both or neither, on purpose. Half a zoom invites the reader to compare a
    detail against a full page, and the missing half looks like a failure of the
    change rather than of the crop.

    The `outline` boxes are drawn thinly on the region own `side` only - the one
    they were measured on. Nothing is drawn on the other side, because we do not
    know where the element is there, and guessing is the one thing this module
    may not do.

    Both sides or neither, per region: half a zoom invites a comparison against
    nothing.

    **A region whose two cuts are IDENTICAL is not written at all.** That is what
    makes it safe to resolve every match of a generic anchor: the hunk that adds
    a form field yields `form-label`, which is on every field of that form, and
    the fields it did not touch cut to the same pixels on both sides. Identical,
    not "similar" - a phash is far too coarse for a crop, where a whole word
    added to a header moves it by two bits, and dropping that would hide the
    change the customer was called in to see.
    """
    from PIL import Image, ImageChops, ImageDraw

    if not specs or not after_png:
        return []
    out = Path(out_dir)
    images, kept = {}, []
    try:
        images["after"] = Image.open(after_png).convert("RGB")
        if before_png:
            images["before"] = Image.open(before_png).convert("RGB")
        for i, spec in enumerate(specs, start=1):
            fit = _fit(spec.get("rect"), images["after"].size)
            if not fit:
                continue
            fit_before = None
            if "before" in images and spec.get("rect_before"):
                fit_before = _fit(spec["rect_before"], images["before"].size)
                # Equal size or the eye has nothing to compare: clamping at the
                # edge of one page must not leave two crops of different shapes.
                fit, fit_before = _same_size(fit, fit_before)
            pieces = {"after": images["after"].crop(fit)}
            if fit_before:
                pieces["before"] = images["before"].crop(fit_before)
                if ImageChops.difference(pieces["before"],
                                         pieces["after"]).getbbox() is None:
                    continue                # this region shows nothing
            scale = min(CROP_MAX_SCALE,
                        max(1.0, CROP_MIN_WIDTH / float(fit[2] - fit[0])))
            out.mkdir(parents=True, exist_ok=True)
            row = dict(spec)
            for side, piece in pieces.items():
                origin = (fit if side == "after" else fit_before)[:2]
                if scale > 1.0:
                    piece = piece.resize((max(1, int(piece.width * scale)),
                                          max(1, int(piece.height * scale))),
                                         Image.LANCZOS)
                if side == spec.get("side"):
                    draw = ImageDraw.Draw(piece)
                    for box in spec.get("outline") or []:
                        _outline(draw, box, origin, scale, piece.size)
                path = out / ("%s_r%d_%s_crop.png" % (prefix, i, side))
                piece.save(path)
                row[side] = str(path)
            row.setdefault("before", None)
            kept.append(row)
    finally:
        for img in images.values():
            img.close()
    return kept


def _outline(draw, box, origin, scale, size) -> None:
    """The element, marked inside the crop. Clipped to the crop rather than
    skipped when it hangs over the edge: the part that IS in frame is still the
    part worth pointing at."""
    try:
        x = (int(box["x"]) - origin[0]) * scale
        y = (int(box["y"]) - origin[1]) * scale
        w, h = int(box["w"]) * scale, int(box["h"]) * scale
    except (KeyError, TypeError, ValueError):
        return
    x0, y0 = max(0, x - OUTLINE_WIDTH), max(0, y - OUTLINE_WIDTH)
    x1 = min(size[0] - 1, x + w + OUTLINE_WIDTH)
    y1 = min(size[1] - 1, y + h + OUTLINE_WIDTH)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    draw.rectangle((x0 - 1, y0 - 1, x1 + 1, y1 + 1), outline=OUTLINE_HALO, width=1)
    draw.rectangle((x0, y0, x1, y1), outline=OUTLINE_RGB, width=OUTLINE_WIDTH)


def thumbnail(png_in, dest, width=THUMB_WIDTH, max_height=THUMB_MAX_HEIGHT) -> str:
    """A ~15 KB webp for the rolling baseline's index.

    A full-page shot of a real screen is 1440x6784; scaled to 320px wide it is
    still 1500px tall and weighs 37 KB - two and a half times what the plan
    sized 232 tracked pages against. The thumb is a RECOGNITION aid, not the
    comparison (that is `compare.phash`, computed on the full image), so it
    keeps the top of the page and caps the height.
    """
    from PIL import Image
    img = Image.open(png_in).convert("RGB")
    if img.width > width:
        img = img.resize((width, max(1, int(img.height * width / img.width))), Image.LANCZOS)
    if img.height > max_height:
        img = img.crop((0, 0, img.width, max_height))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "WEBP", quality=THUMB_QUALITY, method=4)
    return str(dest)


# --------------------------------------------------------------------------- #
#  Caption
# --------------------------------------------------------------------------- #
def trim_words(text, limit=MAX_CAPTION_WORDS) -> str:
    words = _WORD.findall(str(text or ""))
    out = " ".join(words[:limit])
    return out.rstrip(" ,;:-")


def caption_from_facts(page_title, state="base", others=0) -> str:
    """The caption: THE NAME OF THE SCREEN, plus which state of it this is, plus
    how many OTHER screens this one picture stands for.

    It says WHICH PICTURE this is and nothing more. Both parts are facts the
    engine already had before it took the shot - the title comes from the page
    inventory or the browser, the state from the trigger that was clicked - so
    there is nothing here that can turn into a claim about the change.

    A state name only appears for a state that SURVIVED deduplication, i.e. one
    whose picture genuinely differs from the base shot (`capture.capture_page`),
    so it distinguishes two real pictures of one screen rather than labelling
    four copies of the same one.

    `others` is the count of screens whose only reason for being in this change
    is the same shared layout (`shoot._collapse_layout_only`). It is a fact about
    WHICH SCREENS this picture stands for, not a claim about what changed on any
    of them, and the screens themselves are named in the record.

    The word cap applies to the SCREEN NAME, never to the tail: a title long
    enough to eat the count would leave the customer reading "+" and nothing.
    """
    title = " ".join(_WORD.findall(str(page_title or ""))[:8])
    parts = [p for p in (title, _state_label(state)) if p]
    out = trim_words(": ".join(parts))
    # "Ekran" and not "Izmena": with no title at all we still know this is a
    # picture of a screen, and we still do not know that anything changed on it.
    out = (out[:1].upper() + out[1:]) if out else "Ekran"
    return out + _others_phrase(others)


def _others_phrase(n) -> str:
    """" + 29 drugih ekrana", declined the way Serbian declines it: 1 ekran,
    2-4 ekrana, 5+ ekrana. A count in the wrong case reads as machine output on
    a comment a customer is meant to trust."""
    n = int(n or 0)
    if n < 1:
        return ""
    tens, unit = n % 100, n % 10
    if unit == 1 and tens != 11:
        word = "drugi ekran"
    elif unit in (2, 3, 4) and tens not in (12, 13, 14):
        word = "druga ekrana"
    else:
        word = "drugih ekrana"
    return " + %d %s" % (n, word)


def _state_label(state) -> str:
    s = str(state or "base")
    return "" if s in ("", "base") else s.replace("_", " ")
