#!/usr/bin/env python3
"""Print the operator's STANDING RULES for a project (module) — the `project.ai_note`
of that module plus the notes of every module sharing its repo. Called by the
`brain:project-rules` skill from a generated ticket prompt, so the rules are read
ON DEMAND (when the ticket touches design/model/flow/permissions) instead of being
pasted into every prompt.

  python project_rules.py VEZ        # one module
  python project_rules.py --all      # every module that has a note
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import store              # noqa: E402
import project_context    # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--root", default=os.environ.get("TICKETS_STORE") or str(store.default_store()))
    a = ap.parse_args()
    root = a.root
    mods = []
    if a.all:
        mods = [m for m, _u, _r in project_context.modules_catalog(root)]
    elif a.module:
        mods = [a.module]
    else:
        print("pass <MODULE> or --all", file=sys.stderr)
        return 2
    shown = 0
    for m in mods:
        note = project_context.project_note(root, m)
        if not note:
            if not a.all:
                print(f"(modul {m}: nema stalnih pravila — radi po CLAUDE.md i skillovima repoa)")
            continue
        shown += 1
        print(f"=== STALNA PRAVILA PROJEKTA — modul {m} (merodavna za dizajn; poštuj ih u svakoj odluci) ===")
        print(note)
        print()
    if a.all and not shown:
        print("(nijedan modul nema stalna pravila)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
