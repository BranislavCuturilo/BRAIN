#!/usr/bin/env python3
"""Record a Stop hook that failed, so its silence is not mistaken for consent.

**The incident, from the day `verify_gate.py` was written.** It fails OPEN by
design: any unexpected input returns 0 rather than blocking a turn over its own
breakage. That is the right default and it has one bad consequence -- a hook
that stayed quiet because the work was verified and a hook that stayed quiet
because it crashed produce exactly the same nothing. On its first live run it
was quiet for a malformed payload, and that read as "all clear". `--explain`
was added to tell the branches apart, but `--explain` only helps someone who
thinks to run it.

`StopFailure` is the runtime telling us directly. It cannot reach the model --
the turn is already ending -- so it writes a line, and `health.py` reports it
at the next session start, where the session-start check already looks.

**It is UNDOCUMENTED**, read out of the shipped binary and absent from the hook
table Claude Code shows the model. That is survivable here precisely because it
only ADDS visibility: if it silently stops firing, `verify_gate.py` still
guards exactly as it did before. An undocumented event may add visibility; it
must never carry a guarantee.

  hook_failure.py            (as a hook: reads one event from stdin)
  hook_failure.py --recent   what has failed lately
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
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

LOG_DIR = ROOT / "journal" / "hook-failures"
#: Old failures are history, not news. health.py only reads this far back, so
#: a hook fixed last month does not keep reporting itself forever.
RECENT_DAYS = 7
MAX_BYTES = 2_000_000


def log_dir() -> Path:
    """The one directory the writer and the reader both use.

    They used to disagree: this resolution lived inside `logfile()`, while
    `recent()` read the module global directly. With BRAIN_JOURNAL_DIR set,
    failures were written to the override and read back from the real journal,
    so `--recent` and `health.py` reported none. `instructions_log.py` is this
    file's twin and had the same split.

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
    """Never raises. A recorder that breaks on the failure it records is
    worse than no recorder."""
    # Nothing identifying means nothing worth recording. A blank row in a
    # failure log is worse than no row: it says something failed and cannot
    # say what.
    if not any(str(payload.get(k) or "").strip() for k in
               ("command", "hook_command", "error", "message", "stderr",
                "exit_code", "session_id")):
        return
    try:
        f = logfile()
        if f.exists() and f.stat().st_size > MAX_BYTES:
            return
        row = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "event": str(payload.get("hook_event_name") or "StopFailure")[:40],
            # Field names are not documented, so take whichever of these the
            # payload actually carries rather than assuming one.
            "command": str(payload.get("command")
                           or payload.get("hook_command") or "")[:300],
            "error": str(payload.get("error") or payload.get("message")
                         or payload.get("stderr") or "")[:600],
            "code": payload.get("exit_code"),
            "session": str(payload.get("session_id") or "")[:64],
            # Anything unrecognised is kept whole. A field this does not know
            # about today is the field that explains the failure tomorrow.
            "raw": {k: str(v)[:200] for k, v in payload.items()
                    if k not in ("command", "error", "message", "stderr",
                                 "exit_code", "session_id", "hook_event_name",
                                 "transcript_path")},
        }
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (OSError, ValueError, TypeError):
        pass


def recent(days: int = RECENT_DAYS) -> list[dict]:
    cut = datetime.now() - timedelta(days=days)
    out = []
    d = log_dir()
    for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        if datetime.fromisoformat(str(r.get("at"))[:19]) >= cut:
                            out.append(r)
                    except ValueError:
                        continue
        except OSError:
            continue
    return out


def main() -> int:
    if "--recent" in sys.argv:
        rows = recent()
        if not rows:
            print(f"  no hook failed in the last {RECENT_DAYS} days")
            return 0
        print(f"  {len(rows)} hook failure(s) in the last {RECENT_DAYS} days:\n")
        for r in rows[-20:]:
            print(f"  {r['at']}  [{r['event']}] exit={r.get('code')}")
            if r.get("command"):
                print(f"      {r['command'][:100]}")
            if r.get("error"):
                print(f"      {r['error'][:160]}")
        return 1

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
