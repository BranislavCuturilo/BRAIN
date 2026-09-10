#!/usr/bin/env python3
"""What a narrow viewport BREAKS, asserted rather than photographed.

**Why assertions and not more screenshots.** `ui-bootstrap` already requires
that anything added to a toolbar "was measured on screen at the supported
widths" -- a rule with no mechanism, which means a rule nobody applies. The
obvious mechanism is to shoot every screen at every width, and it is the wrong
one twice over: 86 screens at five widths is 430 images per change, which takes
half an hour nobody spends, and every one of those images joins a baseline
somebody then has to approve. A noisy baseline gets rubber-stamped, and a
rubber-stamped baseline catches nothing.

A picture also proves less than it looks like it proves. It shows the page did
not explode. It does not tell you the page scrolls sideways, that a button is
smaller than a fingertip, or that a sticky bar is sitting on top of the submit
control -- and those are the mobile defects that actually ship. Each of them is
a measurement, and a measurement passes or fails without anyone looking at it.

**What it cannot do.** Whether a screen is USABLE at 375px is not decidable
here: whether a gap fits a thumb, whether a hover-only affordance has a touch
equivalent, whether the keyboard covers the field being typed into. This takes
the mechanical half so a person's attention is left for the rest.

  responsive.py                      every page in the catalogue
  responsive.py --from-diff          only what changed, at only the widths it
                                     can alter -- read from the @media blocks
                                     the changed CSS lines sit inside
  responsive.py --page <id>          one page
  responsive.py --widths 375,768     override; accepts xs,sm,md,lg,xl,xxl too
  responsive.py --json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))       # the engine is a PACKAGE: `visual.*`

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Bootstrap 5's own breakpoints. The widths are not a matter of taste and not
#: a list of devices -- "iPad" is a dozen different widths and none of them is
#: where the layout changes. The layout changes at these numbers because that
#: is what the framework's media queries say.
BREAKPOINTS = {"xs": 375, "sm": 576, "md": 768, "lg": 992, "xl": 1200, "xxl": 1400}

#: Three widths, one per layout regime: stacked, tablet, desktop. More is
#: available and rarely worth the wall-clock -- between two breakpoints the
#: same CSS is running.
DEFAULT_WIDTHS = (375, 768, 1440)

#: WCAG 2.2 target size (minimum) is 24x24 CSS pixels.
MIN_TARGET = 24
#: Below this, body text is not readable on a phone held at arm's length.
MIN_FONT = 12
#: Touch assertions only make sense where there is a touch device.
TOUCH_BELOW = 992

MAX_PER_KIND = 8

_JS = r"""
(args) => {
  const [minTarget, minFont, touch, maxPerKind] = args;
  const vw = document.documentElement.clientWidth;
  const out = [];
  const seen = {};

  const push = (kind, el, detail) => {
    seen[kind] = (seen[kind] || 0) + 1;
    if (seen[kind] > maxPerKind) return;
    out.push({kind, sel: sel(el), detail});
  };

  function sel(el) {
    if (!el || !el.tagName) return "(page)";
    let s = el.tagName.toLowerCase();
    if (el.id) return s + "#" + el.id;
    const cls = (el.getAttribute("class") || "").trim().split(/\s+/)
      .filter(c => c && !/^(active|show|collapsed?|open|selected)$/.test(c))
      .slice(0, 2);
    if (cls.length) s += "." + cls.join(".");
    const txt = (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 24);
    return txt ? s + ' "' + txt + '"' : s;
  }

  const visible = (el, r) => {
    if (r.width < 1 || r.height < 1) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" &&
           parseFloat(cs.opacity || "1") > 0.05;
  };

  // 1. The page scrolls sideways. Report the OUTERMOST offenders only: every
  //    descendant of an overflowing block also overflows, and listing them all
  //    buries the one element somebody has to change.
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + 1) {
    const bad = [];
    for (const el of document.querySelectorAll("body *")) {
      const r = el.getBoundingClientRect();
      if (!visible(el, r)) continue;
      if (getComputedStyle(el).position === "fixed") continue;
      if (r.right > vw + 1 || r.left < -1) bad.push([el, r]);
    }
    const outermost = bad.filter(([el]) =>
      !bad.some(([other]) => other !== el && other.contains(el)));
    if (outermost.length) {
      for (const [el, r] of outermost) {
        push("overflow-x", el,
             "extends to " + Math.round(r.right) + "px in a " + vw + "px viewport");
      }
    } else {
      push("overflow-x", null,
           "page scrolls to " + de.scrollWidth + "px in a " + vw + "px viewport, "
           + "and no single element accounts for it");
    }
  }

  const interactive = Array.from(document.querySelectorAll(
    'a[href], button, input:not([type="hidden"]), select, textarea, ' +
    '[role="button"], [role="link"], [onclick], summary'));

  for (const el of interactive) {
    const r = el.getBoundingClientRect();
    if (!visible(el, r)) continue;

    // 2. Too small to hit with a finger.
    if (touch && (r.width < minTarget || r.height < minTarget)) {
      push("touch-target", el,
           Math.round(r.width) + "x" + Math.round(r.height) +
           " is under " + minTarget + "x" + minTarget);
    }

    // 3. Something is sitting on top of it. A control whose own centre point
    //    belongs to an unrelated fixed or sticky element cannot be clicked at
    //    all -- the classic sticky-footer-over-the-submit-button.
    if (r.top >= 0 && r.bottom <= window.innerHeight && r.width > 2) {
      const cx = Math.round(r.left + r.width / 2);
      const cy = Math.round(r.top + r.height / 2);
      const hit = document.elementFromPoint(cx, cy);
      if (hit && hit !== el && !el.contains(hit) && !hit.contains(el)) {
        let p = hit, fixed = null;
        while (p && p !== document.body) {
          const pos = getComputedStyle(p).position;
          if (pos === "fixed" || pos === "sticky") { fixed = p; break; }
          p = p.parentElement;
        }
        if (fixed) {
          push("covered", el, "its centre belongs to " + sel(fixed) +
                              ", which is position:" + getComputedStyle(fixed).position);
        }
      }
    }
  }

  // 4. Text nobody can read on a phone.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const small = new Map();
  let node;
  while ((node = walker.nextNode())) {
    const t = (node.textContent || "").trim();
    if (t.length < 4) continue;
    const el = node.parentElement;
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (!visible(el, r)) continue;
    const size = parseFloat(getComputedStyle(el).fontSize || "16");
    if (size && size < minFont && !small.has(el)) small.set(el, size);
  }
  for (const [el, size] of small) {
    push("tiny-text", el, size.toFixed(1) + "px is under " + minFont + "px");
  }

  return {viewport: vw, findings: out, truncated: seen};
}
"""


def widths_for(conditions, defaults=DEFAULT_WIDTHS) -> list[int]:
    """Which widths a CSS change can actually alter.

    Two answers, and the difference is the whole saving:

    * **No `@media` at all** -> the default set. A rule outside a media query
      runs at every width, and its EFFECT still differs by width: a 40px
      padding that is fine at 1440 overflows a 375 viewport.
    * **Inside one** -> the boundaries it names, plus any default width the
      rule is actually active at. A rule under `(max-width: 768px)` cannot
      change a desktop, so rendering it at 1440 is one of the 429 wasted
      images this exists to avoid.

    A condition with no width bound -- orientation, hover, print -- keeps every
    default, because the honest answer for a bound this cannot evaluate is "it
    might apply".
    """
    from visual import affected as vaffected                    # noqa: PLC0415

    conds = [c for c in conditions if c]
    if not conds:
        return list(defaults)
    out = set(vaffected.media_widths(conds))
    out |= {d for d in defaults
            if any(vaffected.media_matches(c, d) for c in conds)}
    return sorted(out) or list(defaults)


def _widths(args: list[str]) -> list[int]:
    if "--widths" in args:
        i = args.index("--widths")
        if i + 1 < len(args):
            out = []
            for tok in args[i + 1].replace(" ", "").split(","):
                if tok in BREAKPOINTS:
                    out.append(BREAKPOINTS[tok])
                elif tok.isdigit():
                    out.append(int(tok))
            if out:
                return sorted(set(out))
    return list(DEFAULT_WIDTHS)


def check(session, url: str, width: int) -> dict:
    """One page at one width. Returns {viewport, findings, truncated}.

    The viewport is set by swapping `cfg["viewport"]` around `new_context()`,
    which is the same path `capture.py` reads -- so the cookie, the read-only
    route guard and reduced-motion all still apply. Building a context directly
    would drop all three.
    """
    from visual import capture                                  # noqa: PLC0415

    saved = session.cfg.get("viewport")
    session.cfg["viewport"] = {"width": width, "height": 900}
    try:
        with session.fresh_page() as page:
            capture._goto(page, url)
            page.wait_for_timeout(150)
            return page.evaluate(_JS, [MIN_TARGET, MIN_FONT,
                                       width <= TOUCH_BELOW, MAX_PER_KIND])
    finally:
        if saved is None:
            session.cfg.pop("viewport", None)
        else:
            session.cfg["viewport"] = saved


def main() -> int:
    args = sys.argv[1:]
    from visual import capture                                  # noqa: PLC0415
    from visual import config as cfg_mod                         # noqa: PLC0415
    from visual import pages as pages_mod                        # noqa: PLC0415

    repo_root = Path.cwd()
    cfg = cfg_mod.load_config(repo_root)
    if cfg_mod.is_error(cfg):
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        return 2

    from_diff = "--from-diff" in args
    media: list = []
    if from_diff:
        from visual import affected as vaffected                # noqa: PLC0415
        aff = vaffected.from_diff(repo_root, config=cfg)
        rows = [p for p in aff.get("pages", []) if p.get("url")]
        media = aff.get("media") or []
        if not rows:
            print("  nothing visual changed — no page to measure.")
            return 0
    else:
        inv = pages_mod.inventory_report(cfg, repo_root)
        rows = [p for p in inv.get("pages", []) if p.get("url")]
    if "--page" in args:
        i = args.index("--page")
        want = args[i + 1] if i + 1 < len(args) else ""
        rows = [p for p in rows if p.get("id") == want or p.get("name") == want]
        if not rows:
            print(f"  no page named {want!r}")
            return 1

    # An explicit --widths always wins. Otherwise --from-diff derives them from
    # the @media blocks the change sits in, and a full run uses the defaults.
    widths = (_widths(args) if "--widths" in args
              else widths_for(media) if from_diff
              else list(DEFAULT_WIDTHS))
    results, errors = [], []

    try:
        session = capture.open_session(cfg, repo_root)
    except Exception as exc:                                    # noqa: BLE001
        print(json.dumps({"error": "session", "detail": str(exc)}, indent=2))
        return 2

    try:
        for p in rows:
            for w in widths:
                try:
                    got = check(session, p["url"], w)
                except Exception as exc:                        # noqa: BLE001
                    errors.append(f"{p.get('id', p['url'])} @{w}: "
                                  f"{type(exc).__name__}: {exc}")
                    continue
                for f in got.get("findings", []):
                    results.append({"page": p.get("id") or p["url"],
                                    "url": p["url"], "width": w, **f})
    finally:
        session.close()

    if "--json" in args:
        print(json.dumps({"widths": widths, "pages": len(rows),
                          "errors": errors, "findings": results},
                         indent=2, ensure_ascii=False))
        return 0

    print("=" * 74)
    print(f"RESPONSIVE — {len(rows)} page(s) at "
          f"{', '.join(str(w) for w in widths)}px"
          + (f"  (from {len(media)} @media block(s))" if media else ""))
    print("=" * 74)

    if not results:
        print("  nothing measurable is broken at these widths.")
    by_page: dict[str, list] = {}
    for r in results:
        by_page.setdefault(r["page"], []).append(r)
    for page, rs in sorted(by_page.items()):
        print(f"\n  {page}")
        for r in sorted(rs, key=lambda x: (x["width"], x["kind"])):
            print(f"    {r['width']:>5}px  [{r['kind']}] {r['sel']}")
            print(f"             {r['detail']}")

    print("\n" + "-" * 74)
    for e in errors:
        print(f"  could not measure: {e}")
    print("  These are MEASUREMENTS, not opinions — each one passes or fails "
          "without\n  anyone looking at it. What they cannot tell you is "
          "whether the screen is\n  USABLE: whether a gap fits a thumb, "
          "whether a hover-only control has a\n  touch equivalent, whether the "
          "keyboard covers the field being typed into.")
    return 1 if results else 0


if __name__ == "__main__":
    raise SystemExit(main())
