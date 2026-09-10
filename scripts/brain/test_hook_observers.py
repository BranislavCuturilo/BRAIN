#!/usr/bin/env python3
"""Proof that the two observing hooks record, and that their safety net works.

`InstructionsLoaded` and `StopFailure` are real in the Claude Code runtime and
appear in NEITHER the public docs nor the hook table Claude Code shows the
model -- they were read out of the shipped binary. That makes them usable and
makes them dangerous in one specific way: if Anthropic renames one, the hook
simply stops firing. Nothing errors. A rule that has quietly stopped working
reads exactly like one that has not, which is the failure this brain fears
most.

So two things are pinned here. That each recorder writes what it is given and
never raises on what it is not -- a recorder that breaks the session it
measures is worse than none. And that `health.py` actually catches an event
name the binary does not contain, verified by planting one, because a check
that cannot fail is not a check.

  python scripts/brain/test_hook_observers.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import health                                                    # noqa: E402
import hook_failure                                              # noqa: E402
import instructions_log                                          # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def feed(script: str, payload, journal: Path) -> str:
    """Drive a recorder as a real subprocess, pointed away from the journal.

    The first version omitted the redirect. The in-process monkeypatch does not
    reach a subprocess, so the malformed-payload cases appended four blank rows
    to the real log -- which also exposed the actual defect: an empty payload
    was being recorded at all.
    """
    import os                                                    # noqa: PLC0415
    env = dict(os.environ, BRAIN_JOURNAL_DIR=str(journal))
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    p = subprocess.run([sys.executable, str(HERE / script)], input=raw,
                       capture_output=True, text=True, timeout=60, env=env)
    return (p.stdout or "").strip()


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    real_i, real_f = instructions_log.LOG_DIR, hook_failure.LOG_DIR
    try:
        instructions_log.LOG_DIR = tmp / "instructions"
        hook_failure.LOG_DIR = tmp / "hook-failures"

        # --- InstructionsLoaded ------------------------------------------
        instructions_log.record({
            "file_path": "C:/x/CLAUDE.md", "memory_type": "project",
            "load_reason": "always", "globs": [], "session_id": "s1"})
        instructions_log.record({
            "file_path": "C:/x/rules/db.md", "memory_type": "rule",
            "load_reason": "path_match", "globs": ["**/*.sql"],
            "trigger_file": "C:/x/q.sql", "session_id": "s1"})
        rows = instructions_log.rows()
        ck("records what loaded", len(rows) == 2)
        ck("keeps the REASON, which is the part no file size can give",
           {r["reason"] for r in rows} == {"always", "path_match"})
        ck("keeps what triggered a path-scoped load",
           any(r["trigger"].endswith("q.sql") for r in rows))
        ck("the report runs on real rows", instructions_log.report() == 0)

        # --- StopFailure ---------------------------------------------------
        hook_failure.record({
            "hook_event_name": "StopFailure", "command": "python verify_gate.py",
            "error": "Traceback: KeyError", "exit_code": 1, "session_id": "s1",
            "something_new": "kept anyway"})
        fails = hook_failure.recent()
        ck("records a failed Stop hook", len(fails) == 1)
        ck("keeps the command and the error",
           "verify_gate" in fails[0]["command"] and "KeyError" in fails[0]["error"])
        # A field this does not know about today is the field that explains
        # the failure tomorrow, so unrecognised keys are kept whole.
        ck("keeps fields it does not recognise",
           fails[0]["raw"].get("something_new") == "kept anyway")

        # --- the writer and the reader must resolve the SAME directory ------
        # They did not. The BRAIN_JOURNAL_DIR resolution lived only inside
        # `logfile()`, while `recent()` and `rows()` read the module global, so
        # with the variable set every event was written to the override and
        # read back from the real journal: `--recent` and health.py reported
        # nothing while the file filled up. The cases above cannot see it,
        # because they reassign LOG_DIR and never set the variable -- which is
        # exactly why it survived. Both modules had it; both are pinned here.
        env_dir = tmp / "env-journal"
        os.environ["BRAIN_JOURNAL_DIR"] = str(env_dir)
        try:
            hook_failure.record({
                "hook_event_name": "StopFailure", "command": "python x.py",
                "error": "boom", "exit_code": 2, "session_id": "s2"})
            instructions_log.record({
                "file_path": "C:/x/CLAUDE.md", "memory_type": "project",
                "load_reason": "always", "globs": [], "session_id": "s2"})
            ck("the override is where the failure is actually written",
               any((env_dir / "hook-failures").glob("*.jsonl")))
            ck("and the reader looks there, not at the real journal",
               [r["command"] for r in hook_failure.recent()] == ["python x.py"])
            ck("the instructions twin had the same split",
               len(instructions_log.rows()) == 1)
        finally:
            os.environ.pop("BRAIN_JOURNAL_DIR", None)

        # --- neither may ever raise ----------------------------------------
        def written() -> int:
            n = 0
            for sub in ("instructions", "hook-failures"):
                for f in (tmp / sub).glob("*.jsonl"):
                    n += sum(1 for line in f.read_text(encoding="utf-8").splitlines()
                             if line.strip())
            return n

        before = written()
        for script in ("instructions_log.py", "hook_failure.py"):
            for bad in ("not json", "", "[]", '{"file_path": null}', "12"):
                try:
                    feed(script, bad, tmp)
                    ok = True
                except Exception:                               # noqa: BLE001
                    ok = False
                ck(f"{script} survives {bad[:12]!r}", ok)
        # Surviving is not enough — it must also record NOTHING. The first
        # version left four rows of blanks in the real journal, and a log of
        # blank rows says something happened and cannot say what. Counted
        # rather than checked for emptiness, because the valid rows above are
        # in the same files and belong there.
        ck("ten malformed payloads add no rows at all", written() == before)
    finally:
        instructions_log.LOG_DIR, hook_failure.LOG_DIR = real_i, real_f
        shutil.rmtree(tmp, ignore_errors=True)

    # --- the safety net -------------------------------------------------
    # The whole reason these two events are usable is that a rename is caught
    # rather than silently obeyed. Prove the catch fires.
    events = {"PreToolUse", "Stop", "InstructionsLoaded", "StopFailure"}
    missing, how = health.hook_events_present(events)
    if how == "":
        ck("no Claude Code binary found -- the check stays SILENT, "
           "which is the correct answer when nothing was established", True)
    else:
        ck("the real event names are found in the installed binary", not missing)
        planted, _ = health.hook_events_present(events | {"OnPurpleMoonRise"})
        ck("and a name that does not exist is reported",
           "OnPurpleMoonRise" in planted)
        ck("without dragging the real ones down with it",
           not (planted - {"OnPurpleMoonRise"}))

    # --- the events this brain actually registers -------------------------
    cfg = json.loads((HERE.parent.parent / "hooks" / "hooks.json")
                     .read_text(encoding="utf-8"))
    registered = set(cfg.get("hooks", {}))
    ck("both observers are registered",
       {"InstructionsLoaded", "StopFailure"} <= registered)
    # An undocumented event may ADD visibility; it must never carry a
    # guarantee. PostToolBatch was deliberately not adopted for that reason.
    ck("and PostToolBatch was not adopted for a guard",
       "PostToolBatch" not in registered)

    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
