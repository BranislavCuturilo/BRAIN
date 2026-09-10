#!/usr/bin/env python3
"""Anchor -> the REGION of the page worth looking at.

The anchor comes from the diff (`affected.py`). This turns it into two
rectangles in full-page screenshot pixels:

    element   the thing the change actually named
    block     the nearest ancestor that reads as a VISUAL BLOCK - the card, the
              list item, the form group, the table row it lives in

**The block is what gets cropped, not the element.** A zoom onto a 20px icon
tells a customer nothing; a zoom onto the card that icon sits in tells them where
they are looking and what is around it (operator, 2026-08-21: "ne bukvalno full
zoom na element vec na div u kom se nalazi"). The element rectangle survives only
to draw a thin outline INSIDE that crop.

An earlier version of this module drew a hot-pink box on the full picture and the
caption named the element it had found. That is gone and is not coming back: the
engine shows you where to look, it does not tell you what changed. Nothing here
produces a word.

Three rules keep the region honest:

* **Only anchors the caller offers are measured, and the caller owes it hunk
  anchors** (`affected.anchors_for_page(..., only_from_diff=True)`). This module
  cannot tell a diff anchor from a line nobody touched; handing it everything is
  how an untouched material-icon name once got the rectangle.
* **The walk stops going up when the candidate gets big** (`MAX_BLOCK_AREA_SHARE`
  of the viewport). A crop that is most of the page is the same as no crop.
* **Nothing is guessed.** An anchor that does not resolve resolves to None, and
  the pair is then offered as two plain full pictures.
"""
from __future__ import annotations

#: How many matches of ONE anchor to measure. A class like `.form-label` is on
#: every field of a form and `.row` is on hundreds of things; the first few in
#: document order are the only candidates worth the round trip, and what makes
#: them safe to offer is that a region whose two crops are identical never
#: reaches the customer (`shoot._crop_regions`).
MAX_CANDIDATES = 6

#: Elements measured per page state, over ALL anchors. `blockOf` walks up to
#: twelve ancestors calling `getComputedStyle` on each, so this is a real cost
#: on 68 states; four regions survive the merge on a busy page, so two dozen
#: candidates is already generous.
MAX_ELEMENTS = 24

#: Anchors measured per page state. `affected.py` caps a file at 40 anchors
#: ranked best-first, so this is the same number: everything it offers.
MAX_ANCHORS = 40

#: A block must be at least this big to be worth cropping to - below it we are
#: back to zooming on the element itself.
MIN_BLOCK_W = 80
MIN_BLOCK_H = 32

#: Stop climbing when an ancestor covers more than this share of the VIEWPORT
#: (1440x900 = 1.296 Mpx, so 0.55 is ~713 kpx - about a full-width card 1200px
#: wide and 590px tall). Above that the "region" is the screen again.
MAX_BLOCK_AREA_SHARE = 0.55

#: How far up the tree to look for one. Deeper than this and the ancestor has
#: nothing to do with the change any more.
MAX_BLOCK_HOPS = 12

#: Tags and conventional class names that ARE a visual block by definition, so a
#: card with no border of its own still counts. Everything else is decided
#: STRUCTURALLY - a container holding a label and its control is a form field, and
#: a visible border, a background of its own or a shadow makes anything a block -
#: which is what keeps this working on a project that names its classes
#: differently. Bootstrap writes a field group as `<div class="mb-3">`: no border,
#: no background, no `.form-group`, and it is exactly the segment a customer
#: needs to see. Only the label+control test finds it.
BLOCK_SELECTOR = ("section, article, fieldset, details, li, tr, "
                  ".card, .card-body, .list-group-item, .form-group, .alert, "
                  ".panel, .modal-content, .accordion-item, .tab-pane, "
                  ".hr-card, .form-check")


def _selector(anchor) -> str:
    kind = str((anchor or {}).get("kind") or "")
    value = str((anchor or {}).get("value") or "").strip()
    if not value:
        return ""
    if kind == "id":
        return '[id="%s"]' % value.replace('"', '\\"')
    if kind == "class":
        # Attribute-token match, not `.value`: a class can carry characters
        # (`:`, `/`, `.`) that a CSS class selector would have to escape.
        return '[class~="%s"]' % value.replace('"', '\\"')
    if kind == "css_selector":
        return value
    if kind == "field":
        # A Django form field: the FORM renders it, so the hunk that adds one
        # carries no id and no label - only `{{ form.<name> }}`. Django writes
        # that as `name="<name>" id="id_<name>"`, and both are worth trying
        # because a custom widget may keep one and not the other.
        safe = value.replace('"', '\\"')
        return '[name="%s"], [id="id_%s"]' % (safe, safe)
    return ""


def anchor_key(anchor) -> str:
    """The identity of an anchor across two captures taken minutes apart. It
    ends up in the manifest, so it is a plain string, not a tuple."""
    return "%s:%s" % (str((anchor or {}).get("kind") or ""),
                      str((anchor or {}).get("value") or "").strip())


_JS_REGIONS = """
(args) => {
  const [anchors, maxCandidates, blockSel, minW, minH, maxShare, maxHops,
         maxElements] = args;
  const sx = window.scrollX, sy = window.scrollY;
  const maxArea = window.innerWidth * window.innerHeight * maxShare;
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const visible = (el) => {
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    if (parseFloat(st.opacity || '1') === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width >= 1 && r.height >= 1;
  };
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.left + sx), y: Math.round(r.top + sy),
            w: Math.round(r.width), h: Math.round(r.height)};
  };
  // The INNERMOST element carrying the string: every ancestor up to <body>
  // contains it too, and boxing <body> tells the customer nothing.
  const byText = (value) => {
    const want = norm(value).toLowerCase();
    const out = [];
    if (!want) return out;
    for (const el of document.querySelectorAll('body *')) {
      if (!norm(el.textContent).toLowerCase().includes(want)) continue;
      let deeper = false;
      for (const c of el.children) {
        if (norm(c.textContent).toLowerCase().includes(want)) { deeper = true; break; }
      }
      if (!deeper) out.push(el);
      if (out.length >= maxCandidates) break;
    }
    return out;
  };
  const isField = (el) => !!(el.querySelector('label') &&
                            el.querySelector('input, select, textarea'));
  const hasBorder = (st) => ['Top', 'Right', 'Bottom', 'Left'].some((s) =>
      st['border' + s + 'Style'] !== 'none' &&
      parseFloat(st['border' + s + 'Width'] || '0') >= 1);
  const painted = (st) => {
    const bg = st.backgroundColor || '';
    if (bg && bg !== 'transparent' && !/rgba\\(0, 0, 0, 0\\)/.test(bg)) return true;
    return !!st.boxShadow && st.boxShadow !== 'none';
  };
  // The nearest ancestor that reads as a block. `looksLike` is deliberately two
  // tests: what the markup SAYS it is, and what it LOOKS like - a project that
  // names its classes differently still gets a card, because the card has a
  // border or a background of its own.
  const blockOf = (el) => {
    let n = el.parentElement, hops = 0;
    while (n && n.tagName && n.tagName.toLowerCase() !== 'body' && hops < maxHops) {
      const r = n.getBoundingClientRect();
      if (r.width * r.height > maxArea) return null;       // the region is the screen again
      if (r.width >= minW && r.height >= minH) {
        let is = false;
        try { is = n.matches(blockSel); } catch (e) { is = false; }
        if (!is) {
          const st = window.getComputedStyle(n);
          is = isField(n) || hasBorder(st) || painted(st);
        }
        if (is) {
          const cls = (n.getAttribute('class') || '').trim().split(/\\s+/)[0] || '';
          return {box: boxOf(n),
                  kind: n.tagName.toLowerCase() + (cls ? '.' + cls : '')};
        }
      }
      n = n.parentElement;
      hops++;
    }
    return null;
  };
  const out = {};
  let budget = maxElements;
  for (const a of anchors) {
    let els = [];
    try {
      if (a.kind === 'text') {
        for (const v of (a.values || [a.value])) {
          els = byText(v);
          if (els.length) break;
        }
      } else if (a.selector) {
        els = Array.from(document.querySelectorAll(a.selector)).slice(0, maxCandidates);
      }
    } catch (e) { els = []; }
    // EVERY visible match, not the first: one change routinely touches several
    // places on a screen, and the anchor a form hunk yields (`form-label`) is on
    // all of them.
    const found = [];
    for (const el of els) {
      if (budget <= 0) break;
      if (!visible(el)) continue;
      budget--;
      const block = blockOf(el);
      found.push({element: boxOf(el), block: block ? block.box : null,
                  kind: block ? block.kind : ''});
    }
    out[a.key] = found;
  }
  return out;
}
"""


def _clean_box(raw):
    if not isinstance(raw, dict):
        return None
    try:
        box = {k: int(round(float(raw.get(k, 0)))) for k in ("x", "y", "w", "h")}
    except (TypeError, ValueError):
        return None
    return box if box["w"] >= 1 and box["h"] >= 1 else None


def _clean_region(raw):
    if not isinstance(raw, dict):
        return None
    element = _clean_box(raw.get("element"))
    if not element:
        return None
    return {"element": element, "block": _clean_box(raw.get("block")),
            "kind": str(raw.get("kind") or "")}


def _specs(anchors, limit):
    out, seen = [], set()
    for a in anchors or []:
        kind = str((a or {}).get("kind") or "")
        value = str((a or {}).get("value") or "").strip()
        if not value:
            continue
        key = anchor_key(a)
        if key in seen:
            continue
        seen.add(key)
        # A translated string renders as its TRANSLATION; the msgid is what the
        # template said. Both are offered, msgid first, and the first one that
        # matches anything decides - they cannot both be on the page.
        values = [value[:200]] + [str(v)[:200] for v in (a.get("alternates") or [])]
        out.append({"key": key, "kind": kind, "value": values[0],
                    "values": values, "selector": _selector(a)})
        if len(out) >= limit:
            break
    return out


def resolve_regions(page, anchors, limit=MAX_ANCHORS) -> dict:
    """`{anchor_key: [{"element": box, "block": box|None, "kind": str}, ...]}`
    in FULL-PAGE screenshot pixels, in document order.

    Playwright's bounding box is viewport-relative and a full-page screenshot is
    document-relative, so the scroll offset is added; headless chromium runs at
    device pixel ratio 1, so CSS pixels are PNG pixels.

    A key is present when the anchor was MEASURED and empty when it was measured
    and not found - an absent key means "not measured", which is why the caller
    ranks the anchors before handing them over.
    """
    specs = _specs(anchors, limit)
    if not specs:
        return {}
    try:
        raw = page.evaluate(_JS_REGIONS, [specs, MAX_CANDIDATES, BLOCK_SELECTOR,
                                          MIN_BLOCK_W, MIN_BLOCK_H,
                                          MAX_BLOCK_AREA_SHARE, MAX_BLOCK_HOPS,
                                          MAX_ELEMENTS])
    except Exception:                                          # noqa: BLE001
        return {}
    out = {}
    for spec in specs:
        rows = (raw or {}).get(spec["key"]) or []
        out[spec["key"]] = [r for r in (_clean_region(x) for x in rows) if r]
    return out


def regions_of(shot) -> dict:
    """The measured map of one capture, or `{}` for a shot taken before regions
    existed (an adopted baseline, a run from an older engine). Empty is a
    legitimate answer: the other side's rectangle still cuts this picture."""
    regions = (shot or {}).get("regions")
    return regions if isinstance(regions, dict) else {}


def all_regions(shot, anchors) -> list:
    """Every region the diff's anchors resolved to on this shot, best anchor
    first and in document order within an anchor.

    ONE CHANGE TOUCHES SEVERAL PLACES: a commit that adds four fields to a form
    is four regions, and returning only the best one is how three of them became
    invisible. Ordering for the customer is done by the caller, in page
    coordinates - reading order, not anchor rank.
    """
    measured = regions_of(shot)
    out = []
    for a in anchors or []:
        out.extend(measured.get(anchor_key(a)) or [])
    return out
