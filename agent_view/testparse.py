#!/usr/bin/env python3
"""Pure test-output parsers for the Agent View test-run HUD. No I/O.

Two entry points, both TOTAL (never raise; always a plain dict / None):

  parse_final(text) -> {runner, total, passed, failed, skipped,
                        status: "passed"|"failed"|"unknown", fails: [str, ...]}
      A whole captured run's stdout+stderr, for the `end` event. `fails` is a
      list of "<name> — <reason>" (the reason dropped when none is extractable).

  parse_progress(line) -> {done?, total?, passed?, failed?} | None
      ONE streamed line, for live N/M. `total` is an ABSOLUTE just-discovered
      count; `done`/`passed`/`failed` are INCREMENTS for THIS line (the caller
      accumulates). None when the line carries no test signal.

Runners: Django + unittest (`Ran N tests`, OK / FAILED(...), FAIL:/ERROR:),
pytest (`=== N passed, M failed ===`, `FAILED path::test - reason`), jest
(`Tests: X failed, Y passed, Z total`), go test (`--- FAIL: Name`, ok/FAIL).
Unknown output -> runner "unknown", zeros, status "unknown", empty fails. The
parser NEVER decides pass/fail from an exit code (it has none) — a caller with
the child's real exit code (testrun.py) is the one that breaks a tie.
"""
from __future__ import annotations

import re

_MAX_FAILS = 200
_MAX_REASON = 200
_EM_DASH = "—"      # — , the separator in "<name> — <reason>"
_ELLIPSIS = "…"     # …


def _blank() -> dict:
    return {"runner": "unknown", "total": 0, "passed": 0, "failed": 0,
            "skipped": 0, "status": "unknown", "fails": []}


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _fmt(name: str, reason: str) -> str:
    name = (name or "").strip()
    reason = (reason or "").strip()
    if len(reason) > _MAX_REASON:
        reason = reason[:_MAX_REASON - 1] + _ELLIPSIS
    return f"{name} {_EM_DASH} {reason}" if reason else name


# --------------------------------------------------------------------------- #
#  go test
# --------------------------------------------------------------------------- #
_GO_RESULT_LINE = re.compile(r"(?m)^(?:ok|FAIL)\s+\S+\s+\d+(?:\.\d+)?s")
_GO_VERBOSE = re.compile(r"(?m)^\s*--- (?:PASS|FAIL|SKIP): ")


def _parse_go(text: str):
    if not (_GO_VERBOSE.search(text) or _GO_RESULT_LINE.search(text)):
        return None
    passed = failed = skipped = 0
    fails: list = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*--- (PASS|FAIL|SKIP): (\S+)", line)
        if not m:
            continue
        kind, name = m.group(1), m.group(2)
        if kind == "PASS":
            passed += 1
        elif kind == "SKIP":
            skipped += 1
        else:
            failed += 1
            reason = ""
            for nxt in lines[i + 1:]:          # first indented detail line
                if not nxt.strip() or nxt.startswith("===") \
                        or re.match(r"\s*--- (PASS|FAIL|SKIP):", nxt):
                    break
                reason = nxt.strip()
                break
            fails.append(_fmt(name, reason))
    failing = failed > 0 or bool(re.search(r"(?m)^FAIL\b", text))
    passing = bool(re.search(r"(?m)^ok\s", text)) or bool(re.search(r"(?m)^PASS\b", text))
    if failing:
        status = "failed"
    elif passing or passed > 0:
        status = "passed"
    else:
        status = "unknown"
    return {"runner": "go", "total": passed + failed + skipped, "passed": passed,
            "failed": failed, "skipped": skipped, "status": status,
            "fails": fails[:_MAX_FAILS]}


# --------------------------------------------------------------------------- #
#  pytest
# --------------------------------------------------------------------------- #
_PYTEST_SUMMARY = re.compile(
    r"(?m)^=+.*?\b\d+ (?:passed|failed|error|errors|skipped|deselected"
    r"|xfailed|xpassed|warning|warnings)\b.*?=+\s*$")
_PYTEST_NOTESTS = re.compile(r"(?m)^=+ no tests ran")


def _parse_pytest(text: str):
    m = _PYTEST_SUMMARY.search(text)
    detected = bool(m or _PYTEST_NOTESTS.search(text)
                    or "short test summary info" in text
                    or re.search(r"(?m)^(?:FAILED|ERROR) \S+::", text))
    if not detected:
        return None
    scope = m.group(0) if m else text

    def n(word: str) -> int:
        mm = re.search(r"(\d+) " + word, scope)
        return _int(mm.group(1)) if mm else 0

    passed = n("passed")
    failed = n("failed") + n(r"error(?:s)?")
    skipped = n("skipped")
    xfailed, xpassed = n("xfailed"), n("xpassed")
    total = passed + failed + skipped + xfailed + xpassed
    fails: list = []
    for line in text.splitlines():
        fm = re.match(r"^(?:FAILED|ERROR) (\S+)(?: - (.*))?$", line.rstrip())
        if fm:
            fails.append(_fmt(fm.group(1), (fm.group(2) or "").strip()))
    if failed > 0:
        status = "failed"
    elif passed > 0 or xpassed > 0 or xfailed > 0:
        status = "passed"
    else:
        status = "unknown"
    return {"runner": "pytest", "total": total, "passed": passed, "failed": failed,
            "skipped": skipped, "status": status, "fails": fails[:_MAX_FAILS]}


# --------------------------------------------------------------------------- #
#  jest
# --------------------------------------------------------------------------- #
_JEST_TESTS = re.compile(r"(?m)^Tests:\s+(.+)$")
#: failing-test bullets jest prints in the per-test detail / --verbose output
_JEST_FAIL_BULLET = re.compile(r"^\s*[✕✗×]\s+(.+?)(?:\s+\(\d+\s*ms\))?$")
_JEST_FAIL_HEAD = re.compile(r"^\s*●\s+(.+?)$")   # ● Suite › test


def _parse_jest(text: str):
    m = _JEST_TESTS.search(text)
    if not m and "Test Suites:" not in text:
        return None
    counts = m.group(1) if m else ""

    def n(word: str) -> int:
        mm = re.search(r"(\d+) " + word, counts)
        return _int(mm.group(1)) if mm else 0

    passed, failed = n("passed"), n("failed")
    skipped = n("skipped") + n("todo")
    total = n("total") or (passed + failed + skipped)
    fails: list = []
    for line in text.splitlines():
        fm = _JEST_FAIL_BULLET.match(line)
        if fm:
            fails.append(_fmt(fm.group(1).strip(), ""))
    if not fails:                                # fall back to the ● headers
        for line in text.splitlines():
            fm = _JEST_FAIL_HEAD.match(line)
            if fm:
                fails.append(_fmt(fm.group(1).strip(), ""))
    if failed > 0:
        status = "failed"
    elif passed > 0 or total > 0:
        status = "passed"
    else:
        status = "unknown"
    return {"runner": "jest", "total": total, "passed": passed, "failed": failed,
            "skipped": skipped, "status": status, "fails": fails[:_MAX_FAILS]}


# --------------------------------------------------------------------------- #
#  Django + unittest (Django's runner IS unittest — one parser, two labels)
# --------------------------------------------------------------------------- #
_RAN = re.compile(r"(?m)^Ran (\d+) tests? in ")
_DJANGO_MARK = re.compile(r"Creating test database|Destroying test database"
                          r"|System check identified")


def _kv(s: str, key: str) -> int:
    mm = re.search(r"\b" + key + r"=(\d+)", s or "")
    return _int(mm.group(1)) if mm else 0


def _unittest_fail_blocks(text: str) -> list:
    lines = text.splitlines()
    out: list = []
    for i, line in enumerate(lines):
        m = re.match(r"^(?:FAIL|ERROR): (.+)$", line)
        if not m:
            continue
        name = m.group(1).strip()
        reason = ""
        j = i + 1
        if j < len(lines) and re.match(r"^-{3,}$", lines[j]):
            j += 1                               # divider directly under the header
        while j < len(lines):
            nxt = lines[j]
            if re.match(r"^={3,}$", nxt) or re.match(r"^-{3,}$", nxt) \
                    or re.match(r"^Ran \d+ test", nxt):
                break                            # next block / closing sep / summary
            if nxt.strip():
                reason = nxt.strip()             # keep the LAST non-empty line
            j += 1
        out.append(_fmt(name, reason))
        if len(out) >= _MAX_FAILS:
            break
    return out


def _parse_unittest(text: str):
    m = _RAN.search(text)
    if not m:
        return None
    total = _int(m.group(1))
    fm = re.search(r"(?m)^FAILED \((.*)\)", text)
    ok = re.search(r"(?m)^OK\b(?: \((.*)\))?", text)
    failures = errors = skipped = 0
    if fm:
        inside = fm.group(1)
        failures, errors, skipped = _kv(inside, "failures"), _kv(inside, "errors"), _kv(inside, "skipped")
        status = "failed"
    elif ok:
        skipped = _kv(ok.group(1) or "", "skipped")
        status = "passed"
    else:
        status = "unknown"
    failed = failures + errors
    passed = max(0, total - failed - skipped)
    runner = "django" if _DJANGO_MARK.search(text) else "unittest"
    return {"runner": runner, "total": total, "passed": passed, "failed": failed,
            "skipped": skipped, "status": status,
            "fails": _unittest_fail_blocks(text)}


# --------------------------------------------------------------------------- #
#  Dispatch — most distinctive markers first, so one runner never steals another.
# --------------------------------------------------------------------------- #
_PARSERS = (_parse_go, _parse_pytest, _parse_jest, _parse_unittest)


def parse_final(text) -> dict:
    if not isinstance(text, str) or not text.strip():
        return _blank()
    try:
        for parser in _PARSERS:
            res = parser(text)
            if res is not None:
                return res
    except Exception:
        return _blank()
    return _blank()


# --------------------------------------------------------------------------- #
#  Incremental progress — one line at a time. total ABSOLUTE, rest INCREMENTS.
# --------------------------------------------------------------------------- #
def parse_progress(line):
    if not isinstance(line, str) or not line.strip():
        return None
    try:
        return _progress(line)
    except Exception:
        return None


def _progress(line: str):
    s = line.strip()
    # pytest: item count announced up front
    m = re.match(r"collected (\d+) items?", s)
    if m:
        return {"total": _int(m.group(1))}
    # pytest verbose per-test:  path::test PASSED/FAILED/ERROR/SKIPPED [ 45%]
    m = re.search(r"::.+\b(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", line)
    if m:
        k = m.group(1)
        if k == "PASSED":
            return {"done": 1, "passed": 1}
        if k in ("FAILED", "ERROR"):
            return {"done": 1, "failed": 1}
        return {"done": 1}
    # go verbose per-test
    m = re.match(r"\s*--- (PASS|FAIL|SKIP): ", line)
    if m:
        k = m.group(1)
        if k == "PASS":
            return {"done": 1, "passed": 1}
        if k == "FAIL":
            return {"done": 1, "failed": 1}
        return {"done": 1}
    # jest final summary line -> the absolute total (its passed/failed are the
    # WHOLE run, not an increment, so we surface only `total` to avoid double count)
    m = re.match(r"Tests:\s+(.+)$", s)
    if m:
        mm = re.search(r"(\d+) total", m.group(1))
        return {"total": _int(mm.group(1))} if mm else None
    # jest verbose per-test bullets
    if re.match(r"\s*✓\s+", line):                       # ✓
        return {"done": 1, "passed": 1}
    if re.match(r"\s*[✕✗×]\s+", line):         # ✕ ✗ ×
        return {"done": 1, "failed": 1}
    # unittest verbose:  test_x (mod.Cls) ... ok / FAIL / ERROR / skipped
    m = re.search(r"\.\.\. (ok|FAIL|ERROR|skipped|expected failure"
                  r"|unexpected success)\b", line)
    if m:
        k = m.group(1)
        if k == "ok":
            return {"done": 1, "passed": 1}
        if k in ("FAIL", "ERROR"):
            return {"done": 1, "failed": 1}
        return {"done": 1}
    # unittest non-verbose progress chars: a line of ONLY . F E s x
    if re.fullmatch(r"[.FEsx]+", s):
        done = len(s)
        passed = s.count(".")
        failed = s.count("F") + s.count("E")
        out: dict = {"done": done}
        if passed:
            out["passed"] = passed
        if failed:
            out["failed"] = failed
        return out
    return None
