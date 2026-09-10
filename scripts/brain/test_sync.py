#!/usr/bin/env python3
"""Proof that the sync commits by group, leaves strangers alone, and never -A.

The bug this file exists for was silent: `git()` stripped its combined output,
which ate the leading space of the FIRST porcelain line only. Exactly one file
per run came out as `ocs/DASHBOARD.md` and landed in "unclassified" while the
other twenty-two parsed correctly — a 1-in-23 corruption that looks like a
quirk rather than a defect.

  python scripts/brain/test_sync.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sync                                                      # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def build(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(root), "config", k, v], capture_output=True)
    for p in ("docs/DASHBOARD.md", "docs/dashboard.html", "skills/x/SKILL.md",
              "tickets_store/DEMO.json", "journal/episodes/d.jsonl",
              "notes/scratch.txt"):
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], capture_output=True)
    for p in ("docs/DASHBOARD.md", "docs/dashboard.html", "skills/x/SKILL.md",
              "tickets_store/DEMO.json", "journal/episodes/d.jsonl",
              "notes/scratch.txt"):
        (root / p).write_text("two\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "brain"
        root.mkdir()
        build(root)

        with mock.patch.object(sync, "ROOT", root):
            rows = sync.changed()
            paths = {p for _s, p in rows}

            # --- THE bug: the first porcelain line keeps its leading space ---
            ck("every changed path is parsed whole, including the FIRST line",
               "docs/DASHBOARD.md" in paths)
            ck("no path lost a leading character",
               not any(p.startswith(("ocs/", "kills/", "ickets_", "ournal/"))
                       for p in paths))
            ck("all six files are seen", len(paths) == 6)

            groups, other = sync.classify(rows)
            ck("generated docs are their own group",
               set(groups.get("generated", [])) ==
               {"docs/DASHBOARD.md", "docs/dashboard.html"})
            ck("the ticket store and the journal are data",
               set(groups.get("data", [])) ==
               {"tickets_store/DEMO.json", "journal/episodes/d.jsonl"})
            ck("a skill is knowledge", groups.get("knowledge") == ["skills/x/SKILL.md"])

            # A path the brain does not own must be LEFT, not swept in. This is
            # the whole reason there is no `git add -A` here.
            ck("an unrecognised path is left alone and reported",
               other == ["notes/scratch.txt"])

            # --- the message must not claim intent -------------------------
            msg = sync.message("knowledge", ["skills/x/SKILL.md"], "rules")
            ck("the message says it was automatic", "automatically" in msg)
            ck("the message disclaims intent", "NO claim about intent" in msg)
            ck("the message names the files", "skills/x/SKILL.md" in msg)
            ck("the message carries the co-author trailer", "Co-Authored-By:" in msg)

            # --- commit, and check WHAT was committed ----------------------
            sys.argv = ["sync.py", "--no-push"]
            sync.main()

            out = subprocess.run(["git", "-C", str(root), "log", "--format=%s"],
                                 capture_output=True, text=True).stdout
            ck("one commit per group, not one for everything",
               out.count("committed automatically") == 3)

            still = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                   capture_output=True, text=True).stdout
            ck("the stranger is STILL uncommitted afterwards",
               "notes/scratch.txt" in still)
            ck("everything the brain owns was committed",
               "SKILL.md" not in still and "DEMO.json" not in still)

            # --- a second run has nothing to do ---------------------------
            sys.argv = ["sync.py", "--no-push", "--quiet"]
            ck("a second run is a no-op, not an empty commit",
               sync.main() == 0 and
               subprocess.run(["git", "-C", str(root), "log", "--oneline"],
                              capture_output=True, text=True
                              ).stdout.count("\n") == 4)

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s)")
        return 1
    print("ok - commits by group, leaves strangers, and claims nothing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
