#!/usr/bin/env python3
"""Run every `test_*.py` in the repository and report one line per file.

**Why this exists.** The brain carried 24 test files and nothing ran them. A
test nobody runs is worse than no test: it reads as coverage in a review, it
ages against the code it covers, and the day it finally runs it fails for four
reasons at once and gets deleted rather than fixed.

There is no pytest here on purpose. Every test file in this repo is a plain
script with its own `main()` returning an exit code, runnable as
`python scripts/brain/test_usage.py`. That convention costs nothing to install
-- which is the only reason it survives on a machine that is not a CI runner.
This runner just walks them.

  tests.py            run everything, one line per file
  tests.py --quiet    print only failures; exit 1 if any (for hooks and CI)
  tests.py --fast     skip the slow integration tests (browser, local server)
  tests.py -k sync    run only files whose path contains "sync"
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent

#: A test that needs a network, a real helpdesk or an API key is not a unit
#: test and must never gate a commit. It is skipped here and named, so the
#: skip is visible rather than silent.
NEEDS_NETWORK = {
    "test_gemini_client.py",
    "test_gemini_image.py",
    "test_acme_helpdesk.py",
}

#: Integration tests that drive a real browser or a real local server. They
#: are not broken and they are not optional -- they are just slow, and a
#: too-short timeout reports them as failures, which is how a working test
#: gets deleted.
#:
#: Measured, not guessed, 2026-08-31:
#:   test_shoot.py    ~3m35s  -- the default 120s called it a timeout outright
#:   test_capture.py  ~103s   -- 17s under the default, so it passed alone and
#:                               failed whenever anything else was running.
#:                               Two suite runs minutes apart: 20/0 then 19/1,
#:                               same test, same machine.
#:
#: The second one is the instructive case. A timeout close to the measured
#: runtime does not fail honestly -- it fails as a function of machine load,
#: and reports "timed out", which reads as a broken test rather than a budget
#: that was never big enough. Give an integration test several times its
#: measured runtime; the number only matters when something is genuinely hung.
SLOW = {
    "test_shoot.py": 600,
    "test_capture.py": 600,
    "test_responsive.py": 600,          # launches chromium, twice per width
}

TIMEOUT = 120


def discover(pattern: str | None) -> list[Path]:
    # agent_view/ too. It held 41 test files that NOTHING ran -- not this
    # suite, not CI -- which is 45% of the repo's tests outside the gate. Three
    # of them were failing and nobody could have known.
    files = sorted(ROOT.glob("scripts/**/test_*.py")) + sorted(ROOT.glob("agent_view/test_*.py"))
    if pattern:
        files = [f for f in files if pattern in str(f.relative_to(ROOT)).replace("\\", "/")]
    return files


def run_one(path: Path, fast: bool = False) -> tuple[str, str]:
    """Return (state, detail). state is ok | fail | skip | error."""
    if path.name in NEEDS_NETWORK:
        return "skip", "needs network or an API key"
    if fast and path.name in SLOW:
        return "skip", "slow integration test (drop --fast to run it)"
    timeout = SLOW.get(path.name, TIMEOUT)
    try:
        # PYTHONIOENCODING, because `encoding="utf-8"` above only says how WE
        # read the child -- the child still writes in the OS default, which is
        # cp1252 here. A test that printed a `→` or a `đ` died with
        # UnicodeEncodeError and was counted as a failing test. Measured: 40 of
        # 41 files under agent_view/ never set it themselves, so the fix belongs
        # in the runner, not in forty copies of one line.
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), encoding="utf-8", errors="replace", env=env,
        )
    except subprocess.TimeoutExpired:
        return "error", f"timed out after {timeout}s"
    except Exception as exc:                                    # noqa: BLE001
        return "error", str(exc)

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode == 0:
        # Report the test's own last line -- it usually says what was proven,
        # which is more useful than the word "ok" repeated 24 times.
        last = out.splitlines()[-1] if out else "passed"
        return "ok", last
    # Show the lines that FAILED, not the first lines of output. These tests
    # print one line per assertion, so a file that passes forty checks and
    # fails the forty-first showed forty PASSes and no failure at all -- which
    # is exactly what the first CI run reported, on five files at once.
    text = out or err or f"exit {proc.returncode}"
    bad = [l for l in text.splitlines()
           if re.search(r"\b(FAIL|ERROR|Traceback|AssertionError|Exception)\b", l)]
    if not bad:
        bad = text.splitlines()[-12:]          # no marker: the tail is the clue
    return "fail", "\n".join(bad)


def main() -> int:
    args = sys.argv[1:]
    quiet = "--quiet" in args
    fast = "--fast" in args
    pattern = None
    if "-k" in args:
        i = args.index("-k")
        if i + 1 < len(args):
            pattern = args[i + 1]

    files = discover(pattern)
    if not files:
        print("no test files found" if not pattern else f"no test files matching {pattern!r}")
        return 1

    counts = {"ok": 0, "fail": 0, "skip": 0, "error": 0}
    failures: list[tuple[Path, str]] = []

    for path in files:
        state, detail = run_one(path, fast=fast)
        counts[state] += 1
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if state in ("fail", "error"):
            failures.append((path, detail))
            print(f"FAIL {rel}")
            for line in detail.splitlines()[:14]:
                print(f"     {line}")
        elif not quiet:
            mark = "ok  " if state == "ok" else "skip"
            print(f"{mark} {rel:<52} {detail[:60]}")

    bad = counts["fail"] + counts["error"]
    if not quiet or bad:
        print()
        print(f"{counts['ok']} passed, {bad} failed, {counts['skip']} skipped")

    # Record WHEN this ran, not just what it said. A result with no timestamp
    # cannot be told apart from a stale one, which is how "all passed" once
    # described the version of the file before the edit.
    try:
        import verified                                         # noqa: PLC0415
        verified.record("tests", bad == 0,
                        f"{counts['ok']} passed, {bad} failed, "
                        f"{counts['skip']} skipped",
                        scope="fast" if fast else "full")
    except Exception:                                           # noqa: BLE001
        pass                    # a ledger write must never fail the suite

    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
