#!/usr/bin/env python3
"""When each gate last passed, and against what.

**The incident.** Twice in one session a gate was reported green when it was
not. The first time the suite was piped to `tail -5` and `tail`'s exit code was
read instead of the suite's: 19 passed and 1 failed was reported as 20 and 0.
The second time a patch script silently failed to apply, so `all passed` came
from a run that predated the edit entirely -- the numbers were real, they just
described the previous version of the file.

Both were invisible for the same reason: **nothing recorded when a gate last
ran.** A test result with no timestamp cannot be told apart from a stale one,
so "I ran it" and "I ran it before I made this change" look identical.

This is the record. `tests.py` and `evals.py` write it; `verify_gate.py` reads
it and compares it against the mtime of what was edited. That comparison is the
whole mechanism -- a verification older than the change it claims to cover is
not evidence.

`ts` is stored next to `at` on purpose. `at` is for a human reading the file;
`ts` is what the comparison uses, because parsing a formatted date back into a
number is one more place for the two to disagree.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER = ROOT / "journal" / "verified.json"


def load() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(gate: str, ok: bool, detail: str = "", scope: str = "") -> None:
    """Write the outcome of one gate run.

    Never raises. A gate that fails to record its result must still report that
    result to the caller -- losing the run because the ledger could not be
    written would turn a passing suite into a failed command.
    """
    try:
        data = load()
        data[gate] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "ts": time.time(),
            "ok": bool(ok),
            "detail": detail[:200],
            "scope": scope,
        }
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    data = load()
    if not data:
        print("  no gate has recorded a run yet")
        return 0
    now = time.time()
    for gate in sorted(data):
        e = data[gate]
        age = now - float(e.get("ts") or 0)
        mark = "ok  " if e.get("ok") else "FAIL"
        unit = f"{age/3600:.1f}h" if age > 3600 else f"{age/60:.0f}m"
        print(f"  {mark} {gate:<8} {unit} ago   {e.get('detail','')}"
              + (f"   [{e['scope']}]" if e.get("scope") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
