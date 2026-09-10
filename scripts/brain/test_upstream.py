#!/usr/bin/env python3
"""Proof that the upstream check reads identifiers, and that archiving keeps
what it retires.

Two defects this file exists for.

**The first version of `signals()` matched prose.** It pulled bare words out of
a release note, so "through", "status", "raise" and "saved" each hit something
somewhere in the repository. Fourteen of about thirty entries came back marked
as touching the brain — a list where almost everything matches is a list nobody
reads, and it would have been mistaken for thoroughness rather than noise.

**`archive.py` must refuse an unexplained retirement.** An entry with no
recorded version and no reason cannot be re-judged a year later, and the safe
reading of "no reason" is always "leave it alone" — which turns the archive
into a graveyard that only grows.

  python scripts/brain/test_upstream.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import upstream                                                  # noqa: E402

ROOT = HERE.parent.parent
FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


SAMPLE = """
# Changelog

## 2.1.261

- Added /skill-doctor to show which loaded skills go unused and what they cost
- Fixed a crash when running through a proxy with saved status
- Changed `maxOutputTokens` so you can raise it per session

## 2.1.260

- Improved performance of the file watcher
"""


def test_parse() -> None:
    rel = upstream.releases(SAMPLE)
    ck("parses both releases", [v for v, _ in rel] == ["2.1.261", "2.1.260"])
    ck("keeps every bullet", len(rel[0][1]) == 3)
    ck("orders by version, not by text",
       upstream.vtuple("2.1.261") > upstream.vtuple("2.1.9"))


def test_signals_are_identifiers() -> None:
    """The regression: prose must not become a signal."""
    entry = "Fixed a crash when running through a proxy with saved status"
    sig = upstream.signals(entry)
    leaked = {s for s in sig if s in {"through", "status", "saved", "running", "proxy"}}
    ck(f"prose words are not signals (got {sorted(sig)})", not leaked)

    sig = upstream.signals("Added /skill-doctor to show which loaded skills go unused")
    ck("a slash command is a signal", "/skill-doctor" in sig)

    sig = upstream.signals("Changed `maxOutputTokens` so you can raise it per session")
    ck("a backticked name is a signal", "maxOutputTokens" in sig)
    ck("'raise' and 'session' are not", not ({"raise", "session"} & sig))

    ck("a --flag is a signal", "--verbose" in upstream.signals("Added --verbose output"))

    # Found on the first real run: the note about the /status COMMAND matched
    # ui-bootstrap, whose rule says "every progress/status indicator". The slash
    # was mid-word and the extractor did not care.
    ck("a mid-word slash is not a command",
       "/status" not in upstream.signals("Every progress/status indicator names it"))
    ck("but a real one still is",
       "/status" in upstream.signals("Added a line to /status saying why"))


def test_touch_needs_a_real_hit() -> None:
    idx = {"skills/a/SKILL.md": "we work around this with budget.py and usage.py",
           "skills/b/SKILL.md": "run /skill-doctor to see the cost"}
    hits = dict(upstream.touches({"/skill-doctor"}, idx))
    ck("names the file that mentions the identifier", "skills/b/SKILL.md" in hits)
    ck("and only that one", "skills/a/SKILL.md" not in hits)


def test_archive_is_outside_skills() -> None:
    """The structural guarantee: Claude Code scans skills/, so archive/ must not
    live under it, or the whole point is lost."""
    import archive                                               # noqa: PLC0415
    ck("archive/ is not under skills/",
       "skills" not in archive.ARCHIVE.relative_to(ROOT).parts)
    ck("archive/ is a sibling of skills/",
       archive.ARCHIVE.parent == archive.SKILLS.parent)


def test_archive_refuses_and_round_trips() -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "skills" / "demo").mkdir(parents=True)
        (tmp / "skills" / "demo" / "SKILL.md").write_text("body", encoding="utf-8")
        script = tmp / "archive.py"
        src = (HERE / "archive.py").read_text(encoding="utf-8")
        # Point the copy at the fixture instead of the real brain.
        src = src.replace('ROOT = Path(__file__).resolve().parent.parent.parent',
                          f'ROOT = Path(r"{tmp}")')
        script.write_text(src, encoding="utf-8")

        def run(*a):
            return subprocess.run([sys.executable, str(script), *a],
                                  capture_output=True, text=True, cwd=str(tmp))

        p = run("demo")
        ck("refuses to archive with no reason", p.returncode == 2)
        ck("says why it refused", "REFUSED" in p.stdout)
        ck("and leaves the skill alone", (tmp / "skills" / "demo" / "SKILL.md").is_file())

        p = run("demo", "--version", "2.1.261", "--because", "native now")
        ck("archives when told why", p.returncode == 0)
        ck("skill left skills/", not (tmp / "skills" / "demo").exists())
        ck("SKILL.md survives verbatim",
           (tmp / "archive" / "demo" / "SKILL.md").read_text(encoding="utf-8") == "body")
        note = (tmp / "archive" / "demo" / "ARCHIVED.md").read_text(encoding="utf-8")
        ck("the note records the version", "2.1.261" in note)
        ck("the note records the reason", "native now" in note)
        ck("the note says other agents still need it",
           "Codex" in note and "re-derived" in note)

        ck("--list shows it", "demo" in run("--list").stdout)

        p = run("demo", "--restore")
        ck("restores", p.returncode == 0 and (tmp / "skills" / "demo" / "SKILL.md").is_file())
        ck("and drops the note, so it must be redesigned",
           not (tmp / "skills" / "demo" / "ARCHIVED.md").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_network_at_import() -> None:
    """Everything above ran without fetching anything. If a future edit moves the
    fetch to module level this test file starts hanging in CI, which is the
    signal."""
    ck("the changelog URL is still anthropics/claude-code",
       "anthropics/claude-code" in upstream.CHANGELOG)


def main() -> int:
    test_parse()
    test_signals_are_identifiers()
    test_touch_needs_a_real_hit()
    test_archive_is_outside_skills()
    test_archive_refuses_and_round_trips()
    test_no_network_at_import()
    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
