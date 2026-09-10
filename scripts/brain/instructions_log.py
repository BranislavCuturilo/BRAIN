#!/usr/bin/env python3
"""What actually loads into context, and WHY -- from the runtime, not a guess.

**The gap this closes.** Every context measurement in this brain reads the
files: `health.py` sums description lengths, `budget.py` counts what a preload
costs. All of it answers "how big is this file", and none of it answers "did it
load, and what made it load". A skill that never loads costs nothing and helps
nothing, and until now the two were indistinguishable from here.

`InstructionsLoaded` is the runtime's own answer. It carries `file_path`,
`memory_type`, `load_reason`, `globs` and `trigger_file` -- the reason is the
part no static count can produce.

**It is UNDOCUMENTED.** It does not appear in the hook table Claude Code shows
the model, and it is not in the public docs; it was read out of the shipped
binary. That is why this only OBSERVES. If Anthropic renames the event this
stops firing silently, and what is lost is a measurement, not a guard --
`health.py` checks that the event still exists so the silence is not silent.

  instructions_log.py            (as a hook: reads one event from stdin)
  instructions_log.py --report   what loaded, how often, and why
  instructions_log.py --json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "tickets"))
try:
    from store import device_id                                 # noqa: E402
except ImportError:                                             # pragma: no cover
    def device_id() -> str:                                     # type: ignore
        import socket
        return (socket.gethostname() or "device").lower()[:64]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOG_DIR = ROOT / "journal" / "instructions"
#: One file per device, for the reason worklog.py already gives: two machines
#: appending to one tracked file conflict on every pull, over data that is pure
#: history and never needs merging.
MAX_BYTES = 4_000_000


def log_dir() -> Path:
    """The one directory the writer and the reader both use.

    They used to disagree: this resolution lived inside `logfile()`, while the
    reader below read the module global directly. With BRAIN_JOURNAL_DIR set,
    events were written to the override and read back from the real journal.
    `hook_failure.py` is this file's twin and had the same split.

    Reads LOG_DIR at call time on purpose: `test_hook_observers.py` reassigns
    it on the module, and that has to keep working.
    """
    # Overridable so a test can never append to the real journal --
    # the first version of the test drove these as subprocesses and
    # left four blank rows in it.
    import os                                                    # noqa: PLC0415
    base = os.environ.get("BRAIN_JOURNAL_DIR")
    return Path(base) / LOG_DIR.name if base else LOG_DIR


def logfile() -> Path:
    return log_dir() / f"{device_id()}.jsonl"


def record(payload: dict) -> None:
    """Append one load event. Never raises: a measurement that breaks the
    session it measures is worse than no measurement."""
    # A payload with no file in it records nothing. The event fires with
    # whatever the runtime sends, and a malformed or empty one would otherwise
    # append a row of blanks -- forever, silently, to a log whose whole purpose
    # is to be read later.
    if not str(payload.get("file_path") or "").strip():
        return
    try:
        f = logfile()
        if f.exists() and f.stat().st_size > MAX_BYTES:
            return
        row = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "file": str(payload.get("file_path") or "")[:400],
            "type": str(payload.get("memory_type") or "")[:40],
            "reason": str(payload.get("load_reason") or "")[:80],
            "globs": payload.get("globs") or [],
            "trigger": str(payload.get("trigger_file") or "")[:400],
            "session": str(payload.get("session_id") or "")[:64],
        }
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (OSError, ValueError, TypeError):
        pass


def rows(limit: int = 20_000) -> list[dict]:
    out = []
    d = log_dir()
    for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return out[-limit:]


def report() -> int:
    data = rows()
    if not data:
        print("  nothing recorded yet — the hook fires when an instruction "
              "file loads,\n  so this fills up from the next session onward.")
        return 0

    sessions = len({r.get("session") for r in data if r.get("session")})
    print("=" * 74)
    print(f"INSTRUCTIONS LOADED — {len(data):,} load(s)"
          + (f" across {sessions} session(s)" if sessions else ""))
    print("=" * 74)

    by_file = Counter(r["file"] for r in data if r.get("file"))
    print("\n  what loads, most often first:")
    for path, n in by_file.most_common(15):
        short = path.replace("\\", "/").split("/")
        short = "/".join(short[-3:]) if len(short) > 3 else path
        per = f"{n / sessions:.1f}/session" if sessions else f"{n}x"
        print(f"    {n:>5}  {per:>14}  {short}")

    print("\n  why it loaded:")
    for reason, n in Counter(r["reason"] for r in data if r.get("reason")).most_common():
        print(f"    {n:>5}  {reason}")

    kinds = Counter(r["type"] for r in data if r.get("type"))
    if kinds:
        print("\n  kind:")
        for k, n in kinds.most_common():
            print(f"    {n:>5}  {k}")

    triggered = [r for r in data if r.get("trigger")]
    if triggered:
        print(f"\n  {len(triggered)} load(s) were triggered by reading another "
              f"file — those are\n  the path-scoped ones, and they cost nothing "
              f"in sessions that never\n  touch a matching file.")
    print("\n  A file that never appears here is costing nothing and helping "
          "nothing.\n  A file loading in every session is paying rent in every "
          "session.")
    return 0


def main() -> int:
    if "--report" in sys.argv or "--json" in sys.argv:
        if "--json" in sys.argv:
            print(json.dumps(rows(), indent=2, ensure_ascii=False))
            return 0
        return report()

    # Hook mode. Fail silent on anything unexpected.
    try:
        raw = sys.stdin.read(200_000)
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:                                           # noqa: BLE001
        return 0
    if isinstance(payload, dict):
        record(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
