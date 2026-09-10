#!/usr/bin/env python3
"""Proof that generating DESIGN.md does not quietly lose what the CSS says.

Four defects, all found by reading the first real output rather than the code.
Every one of them produced a document that looked finished.

**A trailing comment leaked downwards.** `--tree-tint-1` had `/* npr. Grad */`
on its own line; the next two tints inherited it by looking a few lines up, so
three different depths were labelled as the same thing.

**A status triple was split in half.** `--hr-status-active-bg` contains `bg`,
which the colour section claimed first, so the `-bg` tokens landed in one table
and their `-fg` and `-dot` partners in another.

**Long notes were deleted.** Anything over 300 characters returned empty --
which is exactly the note explaining that the tint scale is deliberately finite
because eight shades of that range are indistinguishable. The rule with a
reason was the one that got dropped.

**Prose belonging to no token vanished.** The note saying `body`, `::selection`
and the scrollbar are deliberately left alone, because overriding them broke
the older templates, is attached to no declaration. Losing it leaves an agent
free to "fix" the decision and to believe it was an oversight.

  python scripts/test_design.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import design                                                    # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


CSS = """/* ==========================================================================
   Demo Tokens (Single Source of Truth)
   --------------------------------------------------------------------------
   Change a value HERE and it changes everywhere in the app.
   ========================================================================== */

:root {
  /* BRAND */
  --x-primary: #165076;
  --x-primary-rgb: 22, 80, 118;

  /* Hierarchy tint scale, deliberately finite: anything deeper than tint-2
     keeps tint-2, because eight shades of this range would be
     indistinguishable on a real screen and the client asked for depth to be
     readable at a glance rather than merely encoded. */
  --tree-tint-0: #DCE6EF;   /* root, darkest */
  --tree-tint-1: #E7EDF4;   /* one level down */
  --tree-tint-2: #F0F4F8;

  /* STATUS (bg + fg + dot triple) */
  --x-status-active-bg:  #E8F5E9;
  --x-status-active-fg:  #1B5E20;
  --x-status-active-dot: #2E7D32;

  --x-space-1: 4px;
  --x-radius: 10px;
  --x-shadow: 0 4px 12px rgba(15, 23, 42, .07);
  --x-z-modal: 1050;
  --x-card-padding: 20px;
  --x-selection-bg: rgba(0,0,0,.2);
}

.tenant-mns {
  --x-primary: #165076;
}

.tenant-demo {
  --x-primary: #7C3AED;
  --x-radius: 14px;
}

/* NOTE: this file deliberately does not touch <body>, ::selection or the
   scrollbar globally. Those belong to the older Bootstrap-only templates,
   which were not designed for modern overrides, and overriding them broke
   the layout. */
"""


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        css = tmp / "tokens.css"
        css.write_text(CSS, encoding="utf-8")
        out = design.render(css)

        def row(token: str) -> str:
            for line in out.splitlines():
                if line.startswith(f"| `{token}` |"):
                    return line
            return ""

        # --- the leak ------------------------------------------------------
        ck("a trailing comment stays on its own token",
           "one level down" in row("--tree-tint-1"))
        ck("and does NOT leak to the next declaration",
           "one level down" not in row("--tree-tint-2"))

        # --- the split triple ----------------------------------------------
        status = out[out.index("## Status colours"):]
        status = status[:status.index("\n## ")]
        ck("bg, fg and dot stay in ONE table",
           all(f"--x-status-active-{p}" in status for p in ("bg", "fg", "dot")))

        # --- the deleted long note -----------------------------------------
        ck("a long note is kept, not truncated away",
           "indistinguishable" in out)
        ck("and it is not crammed into a table cell",
           "indistinguishable" not in row("--tree-tint-0"))

        # --- prose belonging to no token ------------------------------------
        ck("prose no token claimed survives",
           "deliberately does not touch" in out)
        ck("under a heading that says what it is",
           "Decisions recorded in the stylesheet" in out)

        # --- the ordinary content -------------------------------------------
        ck("every token appears", all(t in out for t in
           ("--x-space-1", "--x-radius", "--x-shadow", "--x-z-modal")))
        ck("tenants are listed", ".tenant-mns" in out and ".tenant-demo" in out)
        ck("a tenant's override is shown", "#7C3AED" in out)
        ck("the file header is quoted", "changes everywhere in the app" in out.lower())
        ck("it cites ui-bootstrap rather than restating it",
           "ui-bootstrap" in out)
        ck("and says not to hand-edit it", "Do not edit this file" in out)

        # --- --check ignores the date ---------------------------------------
        stamped = out.replace(design.date.today().isoformat(), "1999-01-01")
        import re                                                # noqa: PLC0415
        norm = lambda s: re.sub(r"on \d{4}-\d{2}-\d{2}", "on DATE", s)   # noqa: E731
        ck("--check does not call a file stale just for a different date",
           norm(stamped) == norm(out))

        # --- a stylesheet with no :root -------------------------------------
        empty = tmp / "empty.css"
        empty.write_text("/* nothing here */\n.a { color: red; }\n", encoding="utf-8")
        try:
            design.render(empty)
            ck("a stylesheet with no tokens does not crash", True)
        except Exception as exc:                                 # noqa: BLE001
            ck(f"a stylesheet with no tokens does not crash ({exc})", False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
