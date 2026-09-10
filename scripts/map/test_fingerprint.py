#!/usr/bin/env python3
"""Proof that the map knows when it no longer describes the code.

A full scan is seconds to tens of seconds depending on how cold the disk is --
too long to sit in front of a commit, and entirely wasted when nothing it reads
has moved. Fingerprinting the inputs costs a fraction of that, so the scan can
decide before doing the work.

The error is deliberately one-sided. A false "changed" costs one scan nobody
needed. A false "unchanged" leaves a stale map claiming to be current, and a
diagram that quietly stopped matching the code is worse than no diagram --
someone acts on it.

  python scripts/map/test_fingerprint.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scan                                                      # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def build(root: Path) -> None:
    app = root / "billing"
    app.mkdir(parents=True)
    (app / "models.py").write_text("class Invoice: pass\n", encoding="utf-8")
    (app / "views.py").write_text("def index(request): pass\n", encoding="utf-8")
    tpl = root / "templates"
    tpl.mkdir()
    (tpl / "list.html").write_text("{% extends 'base.html' %}\n", encoding="utf-8")
    venv = root / "venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "junk.py").write_text("x = 1\n", encoding="utf-8")


def touch(f: Path) -> None:
    # A whole second, because mtime resolution is not guaranteed finer and a
    # test that passes on a fast disk and fails on a slow one is worse than no
    # test.
    t = time.time() + 1
    os.utime(f, (t, t))


def main() -> int:
    root = Path(tempfile.mkdtemp())
    try:
        build(root)
        name = "scan-all.json"

        # --- what counts as an input ------------------------------------
        seen = {f.name for f in scan.sources(root)}
        ck("python is fingerprinted", "models.py" in seen)
        ck("templates are too — a screen graph is built from them, so a "
           "template edit changes the map with no .py touched",
           "list.html" in seen)
        ck("and a vendored virtualenv is not", "junk.py" not in seen)

        # --- with no map at all -------------------------------------------
        ck("no output means not current", not scan.unchanged(root, name))

        # --- after a scan --------------------------------------------------
        out = root / ".map"
        out.mkdir(exist_ok=True)
        (out / name).write_text("{}", encoding="utf-8")
        scan.write_stamp(root, name)
        ck("a written map with a stamp is current", scan.unchanged(root, name))

        # --- the whole point ----------------------------------------------
        before = scan.fingerprint(root)
        touch(root / "billing" / "views.py")
        ck("touching a source file changes the fingerprint",
           scan.fingerprint(root) != before)
        ck("and the map is reported stale", not scan.unchanged(root, name))

        scan.write_stamp(root, name)
        ck("re-stamping makes it current again", scan.unchanged(root, name))

        touch(root / "templates" / "list.html")
        ck("a TEMPLATE edit alone is enough to go stale",
           not scan.unchanged(root, name))

        scan.write_stamp(root, name)
        touch(root / "venv" / "lib" / "junk.py")
        ck("but a vendored file is not", scan.unchanged(root, name))

        # --- a new file, not just a changed one ----------------------------
        (root / "billing" / "services.py").write_text("def post(): pass\n",
                                                      encoding="utf-8")
        ck("adding a file goes stale", not scan.unchanged(root, name))
        scan.write_stamp(root, name)

        # --- deleting one -------------------------------------------------
        (root / "billing" / "services.py").unlink()
        ck("deleting one goes stale too", not scan.unchanged(root, name))
        scan.write_stamp(root, name)

        # --- the stamp alone proves nothing --------------------------------
        # A stamp whose json was deleted must not report "current"; that is
        # how a missing map becomes an invisible one.
        (out / name).unlink()
        ck("a stamp without its output is not current",
           not scan.unchanged(root, name))

        # --- and a missing stamp is not current either ---------------------
        (out / name).write_text("{}", encoding="utf-8")
        scan.stamp_path(root, name).unlink(missing_ok=True)
        ck("output without a stamp is not current",
           not scan.unchanged(root, name))

        # --- the flags, end to end ----------------------------------------
        import subprocess                                        # noqa: PLC0415
        p = subprocess.run([sys.executable, str(HERE / "scan.py"), str(root),
                            "--all", "--stale"], capture_output=True, text=True,
                           timeout=300)
        ck("--stale exits non-zero when it is not current", p.returncode == 1)
        ck("and says which file", name in p.stdout)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
