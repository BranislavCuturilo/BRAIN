#!/usr/bin/env python3
"""Proof that the preload parser reads what the agent files actually contain.

Exists because the same defect shipped twice: a parser that read only the
header line of `skills:` returned an empty list for every agent written in the
block spelling, and an empty list renders as "0 uses" -- a number that looks
like a finding rather than a failure.

  python scripts/brain/test_usage.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage                                                    # noqa: E402

CASES = {
    "block.md": "---\nname: block\nskills:\n  - brain:craft-security\n  - craft-code\ncolor: red\n---\nbody",
    "inline.md": "---\nname: inline\nskills: brain:stack-django, ui-bootstrap\n---\nbody",
    "bracket.md": "---\nname: bracket\nskills: [craft-testing]\n---\nbody",
    "none.md": "---\nname: none\nmodel: opus\n---\nbody",
}
WANT = {
    "block": ["craft-security", "craft-code"],
    "inline": ["stack-django", "ui-bootstrap"],
    "bracket": ["craft-testing"],
    "none": [],
}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        agents = Path(tmp) / "agents"
        agents.mkdir()
        for name, text in CASES.items():
            (agents / name).write_text(text, encoding="utf-8")
        got = usage.preloads(Path(tmp))

    bad = {k: (WANT[k], got.get(k)) for k in WANT if got.get(k) != WANT[k]}
    for k, (want, have) in bad.items():
        print(f"FAIL {k}: expected {want}, got {have}")
    if bad:
        return 1

    # A parser is only proven by input it must reject: break one on purpose.
    with tempfile.TemporaryDirectory() as tmp:
        agents = Path(tmp) / "agents"
        agents.mkdir()
        (agents / "broken.md").write_text(
            "---\nname: broken\nskills:\n  - real-skill\n---\nbody", encoding="utf-8")
        if usage.preloads(Path(tmp)).get("broken") == []:
            print("FAIL: parser returned empty for a file that clearly preloads one skill")
            return 1

    print(f"ok - {len(WANT)} frontmatter spellings parsed correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
