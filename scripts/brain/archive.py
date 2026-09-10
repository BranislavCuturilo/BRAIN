#!/usr/bin/env python3
"""Retire a skill that upstream now covers — without losing it.

**Why archive and not delete.** A skill written because Claude Code could not
do something is still true for an agent that still cannot. Codex and Gemini do
not gain a capability because Anthropic shipped it, so a skill deleted from
here is a skill re-derived there. Archiving keeps it readable and keeps it out
of the way.

**Out of the way, precisely.** `archive/` is NOT under `skills/`, so Claude
Code never discovers it: no description in context, no body in a session, no
clutter. Another agent is pointed at the directory and reads plain markdown.

**Nothing is archived without recording WHY.** An `ARCHIVED.md` beside it names
the version that superseded it and what it was for. Without that, in a year
nobody can tell whether it is still redundant — and the safe assumption then is
to leave it, which is how an archive becomes a graveyard nobody prunes.

  archive.py <skill> --version 2.1.261 --because "…"   retire it
  archive.py <skill> --restore                          bring it back
  archive.py --list                                     what is archived, and why
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = ROOT / "skills"
ARCHIVE = ROOT / "archive"

NOTE = """# {name} — archived {when}

**Superseded by Claude Code {version}.**

{because}

## This is not dead, it is out of the way

`archive/` sits outside `skills/`, so Claude Code never loads it — no
description in context, no body in a session. It is kept because **an agent
without that capability still needs the rule**: Codex and Gemini did not gain
anything when Anthropic shipped it, and a skill deleted here is a skill
re-derived there.

Point another agent at this directory. The `SKILL.md` beside this note is
unchanged from the day it was retired.

## Before assuming it is still redundant

Re-read the release that replaced it. Coverage narrows as often as it widens —
a native feature can lose an option, or apply only to one surface. If what it
covers has shrunk, this comes back with `archive.py {name} --restore` and is
redesigned down to what is uncovered.
"""


def git(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                           text=True, timeout=60, encoding="utf-8", errors="replace")
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def listing() -> int:
    if not ARCHIVE.is_dir() or not any(ARCHIVE.glob("*/SKILL.md")):
        print("  nothing archived")
        return 0
    print("=" * 70)
    print("ARCHIVED SKILLS — invisible to Claude Code, readable by any agent")
    print("=" * 70)
    for d in sorted(ARCHIVE.iterdir()):
        note = d / "ARCHIVED.md"
        if not note.is_file():
            continue
        head = note.read_text(encoding="utf-8", errors="replace").splitlines()
        ver = next((l for l in head if l.startswith("**Superseded")), "")
        print(f"\n  {d.name}")
        print(f"    {ver.strip('*')}")
        for line in head[4:7]:
            if line.strip():
                print(f"    {line.strip()[:66]}")
    print(f"\n  {ARCHIVE}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    def opt(flag):
        i = sys.argv.index(flag) if flag in sys.argv else -1
        return sys.argv[i + 1] if 0 <= i < len(sys.argv) - 1 else None

    if "--list" in sys.argv:
        return listing()
    if not args:
        print(__doc__.strip())
        return 1
    name = args[0]

    # --- restore ----------------------------------------------------------
    if "--restore" in sys.argv:
        src, dst = ARCHIVE / name, SKILLS / name
        if not (src / "SKILL.md").is_file():
            print(f"  {name} is not archived")
            return 1
        if dst.exists():
            print(f"  skills/{name} already exists — resolve by hand")
            return 1
        code, out = git("mv", f"archive/{name}", f"skills/{name}")
        if code:
            shutil.move(str(src), str(dst))
        (dst / "ARCHIVED.md").unlink(missing_ok=True)
        print(f"  restored skills/{name} — its ARCHIVED.md is removed, so redesign it")
        print("  and say in the commit what upstream stopped covering.")
        return 0

    # --- archive ----------------------------------------------------------
    src = SKILLS / name
    if not (src / "SKILL.md").is_file():
        print(f"  no skill named {name!r}")
        return 1

    version = opt("--version")
    because = opt("--because")
    if not version or not because:
        # Refuse rather than record an empty reason. An archive entry with no
        # reason cannot be re-judged later, and the safe reading of "no reason"
        # is "leave it" -- which turns the archive into a graveyard.
        print("  REFUSED: --version and --because are required.")
        print("  An archived skill with no recorded reason cannot be re-judged,")
        print("  and 'unknown' always resolves to 'leave it alone'.")
        return 2

    ARCHIVE.mkdir(exist_ok=True)
    dst = ARCHIVE / name
    if dst.exists():
        print(f"  archive/{name} already exists")
        return 1

    code, out = git("mv", f"skills/{name}", f"archive/{name}")
    if code:
        shutil.move(str(src), str(dst))
    (dst / "ARCHIVED.md").write_text(
        NOTE.format(name=name, when=date.today().isoformat(),
                    version=version, because=because.strip()),
        encoding="utf-8")

    print(f"  archived: skills/{name} -> archive/{name}")
    print(f"  superseded by Claude Code {version}")
    print("  It is out of Claude Code's reach and still readable by another agent.")
    print("\n  Now: run scripts/brain/docs.py, and check nothing preloads it —")
    print("       health.py fails on an agent that names a skill which is gone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
