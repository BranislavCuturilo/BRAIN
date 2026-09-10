#!/usr/bin/env python3
"""Guard the idle-game SETTINGS entry point in the focus/timer popover.

Bug (F1/F2 review): the game enable/disable toggle IS in #focus-pop .fpop-foot (since F1),
but there was NO reachable game-SETTINGS entry (quiz topics) from the timer popup — the tags
modal was only reachable from inside the quiz card. This pins the added "Kviz teme" entry so it
cannot silently regress: it must live in the popover foot, be CSS-shown only while the overlay is
active (body.game-on), and be wired to open the existing tags modal. Pure static-file assertions;
no server. Run:  python test_game_fpop_entry.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


def _read(rel):
    return (HERE / "web" / rel).read_text(encoding="utf-8")


def test_index_html_has_tags_entry_in_fpop_foot():
    html = _read("index.html")
    # Isolate the popover foot so the assertion is about THIS location, not just the file.
    m = re.search(r'<div class="fpop-foot">(.*?)</div>', html, re.S)
    foot = m.group(1) if m else ""
    # Presence pair: the pre-existing toggle AND the new settings entry both live in the foot
    # (a presence check next to the target so neither can pass vacuously if the block moves).
    check("index: fpop-foot found", bool(foot))
    check("index: game toggle still in foot", 'id="ig-toggle"' in foot)
    check("index: quiz-tags entry in foot", 'id="ig-tagsbtn"' in foot)
    check("index: entry uses the .fpop-gametags class",
          bool(re.search(r'class="fpop-gametags"[^>]*id="ig-tagsbtn"'
                         r'|id="ig-tagsbtn"[^>]*class="fpop-gametags"', foot)))


def test_game_css_gates_entry_on_game_on():
    css = _read("game.css")
    # Hidden by default (a dead button when the game is off would be worse than absent) ...
    check("css: .fpop-gametags hidden by default",
          bool(re.search(r'\.fpop-gametags\{[^}]*display:\s*none', css)))
    # ... and revealed only while the overlay is active.
    check("css: shown under body.game-on",
          bool(re.search(r'body\.game-on\s+\.fpop-gametags\{[^}]*display:\s*block', css)))


def test_game_js_wires_entry_to_tags_modal():
    js = _read("game.js")
    # The click handler must reference the button id and open the tags modal.
    m = re.search(r'\$\("ig-tagsbtn"\).*?\}\);', js, re.S)
    handler = m.group(0) if m else ""
    check("js: ig-tagsbtn handler present", bool(handler))
    check("js: handler opens the tags modal", "gameTagsOpen(" in handler)
    # gameTagsOpen must still exist as the shared opener it delegates to.
    check("js: gameTagsOpen defined", "function gameTagsOpen(" in js)


def main():
    for fn in (test_index_html_has_tags_entry_in_fpop_foot,
               test_game_css_gates_entry_on_game_on,
               test_game_js_wires_entry_to_tags_modal):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a raised test is a failed test
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
