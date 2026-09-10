#!/usr/bin/env python3
"""Responsive assertions against a LOCAL fixture page, at two widths.

The point of the whole tool is that these are MEASUREMENTS: they pass or fail
without anybody looking. So the test drives a real browser at a real width and
checks the numbers, rather than checking that some code ran.

Half of what is pinned here is the SILENCE. A page that is fine at 1440 must
produce nothing at 1440 -- an assertion that fires on a correct desktop layout
is an assertion that gets deleted, and then the mobile case it was written for
goes unchecked too.

Slow: it launches chromium. `tests.py --fast` skips it.

  python scripts/visual/test_responsive.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # the engine is a PACKAGE: `visual.*`
from visual import capture as vcapture  # noqa: E402
from visual import fixture_site  # noqa: E402
from visual import responsive  # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


#: Every defect this asserts, in one page, each one obvious on a phone and
#: invisible on a desktop -- which is exactly why they ship.
PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>fixture</title>
<style>
  body { margin: 0; font-family: sans-serif; }
  .wide { width: 900px; background: #eee; padding: 8px; }
  .tiny-btn { width: 16px; height: 16px; padding: 0; }
  .fineprint { font-size: 9px; }
  .stickybar { position: fixed; bottom: 0; left: 0; right: 0;
               height: 90px; background: #333; }
  .buried { margin-top: 20px; }
  .ok-btn { width: 120px; height: 40px; }
</style></head>
<body>
  <div class="wide">A table that does not fit a phone at all</div>
  <button class="ok-btn">Fine</button>
  <button class="tiny-btn">x</button>
  <p class="fineprint">Legal text nobody can read on a phone</p>
  <div style="height: 400px"></div>
  <!-- Placed absolutely so it lands UNDER the fixed bar in a 900px-tall
       viewport (bar 810-900, button 840-880). A spacer put it above the bar
       and the assertion correctly said nothing -- the fixture was wrong, not
       the check. -->
  <button class="buried"
          style="position:absolute;top:840px;left:20px;width:120px;height:40px">Submit</button>
  <div class="stickybar"></div>
</body></html>
"""

CLEAN = """<!doctype html>
<html><head><meta charset="utf-8"><title>clean</title>
<style>
  body { margin: 0; font-family: sans-serif; font-size: 16px; }
  .box { max-width: 100%; padding: 8px; }
  button { width: 120px; height: 44px; }
</style></head>
<body>
  <div class="box">Everything here fits.</div>
  <button>Fine</button>
</body></html>
"""


def cfg_for(base_url: str) -> dict:
    return {"base_url": base_url, "tenant": "", "role": "",
            "auth": {"kind": "none", "module": "", "func": "", "args": {},
                     "cookie": {"name": "sessionid", "domain": "", "path": "/"}},
            "server": {"health": base_url + "/", "start": []},
            "pages": {"kind": "list", "pages": []},
            "ignore_selectors": [],
            "states": {"auto": False, "max_per_page": 1, "manual": {}},
            "viewport": {"width": 1440, "height": 900}}


def main() -> int:
    root = Path(tempfile.mkdtemp())
    (root / "broken.html").write_text(PAGE, encoding="utf-8")
    (root / "clean.html").write_text(CLEAN, encoding="utf-8")

    base, stop = fixture_site.serve(root)
    session = None
    try:
        session = vcapture.open_session(cfg_for(base), root)

        narrow = responsive.check(session, base + "/broken.html", 375)
        kinds = {f["kind"] for f in narrow["findings"]}

        ck("a 900px block in a 375px viewport is reported",
           "overflow-x" in kinds)
        ck("and the finding names the element, not just the page",
           any("wide" in f["sel"] for f in narrow["findings"]
               if f["kind"] == "overflow-x"))
        ck("a 16x16 button is under the touch target", "touch-target" in kinds)
        ck("a 120x40 button is not",
           not any("Fine" in f["sel"] for f in narrow["findings"]
                   if f["kind"] == "touch-target"))
        ck("9px text is reported", "tiny-text" in kinds)
        ck("a control under a fixed bar is reported", "covered" in kinds)
        ck("and it names what covers it",
           any("stickybar" in f["detail"] for f in narrow["findings"]
               if f["kind"] == "covered"))

        # --- the silence ---------------------------------------------------
        wide = responsive.check(session, base + "/broken.html", 1440)
        wk = {f["kind"] for f in wide["findings"]}
        ck("a 900px block in a 1440px viewport does not overflow",
           "overflow-x" not in wk)
        ck("touch targets are not asserted on a desktop width",
           "touch-target" not in wk)
        ck("but unreadable text is reported at every width", "tiny-text" in wk)

        clean = responsive.check(session, base + "/clean.html", 375)
        ck("a page that fits produces nothing at 375",
           clean["findings"] == [])
        clean_wide = responsive.check(session, base + "/clean.html", 1440)
        ck("and nothing at 1440", clean_wide["findings"] == [])

        ck("the viewport it measured is reported back",
           clean["viewport"] <= 375 and clean_wide["viewport"] <= 1440)

        # --- the width vocabulary ------------------------------------------
        ck("named breakpoints resolve", responsive._widths(["--widths", "md,xl"])
           == [768, 1200])
        ck("raw numbers resolve", responsive._widths(["--widths", "320,375"])
           == [320, 375])
        ck("a bad list falls back to the default",
           responsive._widths(["--widths", "banana"]) == list(responsive.DEFAULT_WIDTHS))
        ck("no flag means the default three",
           responsive._widths([]) == list(responsive.DEFAULT_WIDTHS))
    finally:
        if session is not None:
            session.close()
        stop()

    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
