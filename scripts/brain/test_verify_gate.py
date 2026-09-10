#!/usr/bin/env python3
"""Proof that the stop gate catches the two ways a green report lied.

Both happened in one session, and neither was a knowledge failure.

**A suite that ran BEFORE the edit.** A patch script failed silently, so
`all passed` was a real result that described the previous version of the file.
The numbers were true. They were about something else.

**A suite that never ran at all**, reported from recall.

The check is one comparison -- is the verification older than the thing it
claims to cover -- so the tests here are mostly about the cases where it must
stay QUIET. A stop hook that blocks when it should not is worse than none: it
holds a turn hostage over a docs edit, and then it gets disabled.

  python scripts/brain/test_verify_gate.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verified                                                  # noqa: E402
import verify_gate                                               # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def transcript(tmp: Path, paths: list[str], tool: str = "Edit") -> str:
    """A session transcript containing one Edit per path."""
    f = tmp / "t.jsonl"
    with f.open("w", encoding="utf-8") as fh:
        for p in paths:
            fh.write(json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "name": tool, "input": {"file_path": p}}
                ]},
            }) + "\n")
    return str(f)


def touch(root: Path, rel: str) -> Path:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")
    return f


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    real_root, real_ledger = verify_gate.ROOT, verified.LEDGER
    try:
        verify_gate.ROOT = tmp
        verified.LEDGER = tmp / "journal" / "verified.json"

        script = touch(tmp, "scripts/brain/thing.py")
        skill = touch(tmp, "skills/craft-code/SKILL.md")
        doc = touch(tmp, "docs/NOTES.md")
        tick = touch(tmp, "tickets_store/DEMO.json")

        # --- reading the transcript ---------------------------------------
        t = transcript(tmp, [str(script), str(doc)])
        ck("finds the files an Edit wrote", len(verify_gate.edited(t)) == 2)
        ck("an unreadable transcript yields nothing, not a crash",
           verify_gate.edited(str(tmp / "nope.jsonl")) == [])

        # --- nothing has ever verified this tree --------------------------
        need = verify_gate.stale([str(script)])
        ck("blocks on a script edit with no gate run", "tests" in need)
        ck("and names the command", "tests.py" in need["tests"][0])

        need = verify_gate.stale([str(skill)])
        ck("a skill edit needs evals, not tests", set(need) == {"evals"})

        # --- what must NOT block ------------------------------------------
        ck("a docs edit blocks nothing", verify_gate.stale([str(doc)]) == {})
        ck("a ticket sync blocks nothing", verify_gate.stale([str(tick)]) == {})
        ck("a file outside the brain blocks nothing",
           verify_gate.stale([str(tmp.parent / "elsewhere.py")]) == {})
        ck("a deleted file blocks nothing",
           verify_gate.stale([str(tmp / "scripts" / "gone.py")]) == {})

        # --- THE INCIDENT: green, but older than the edit ------------------
        verified.record("tests", True, "30 passed, 0 failed")
        time.sleep(0.02)
        script.write_text("changed after the suite ran", encoding="utf-8")
        need = verify_gate.stale([str(script)])
        ck("a PASSING run that predates the edit is not evidence", "tests" in need)

        # --- and when it genuinely covers the edit -------------------------
        time.sleep(0.02)
        verified.record("tests", True, "30 passed, 0 failed")
        ck("a passing run made after the edit is silent",
           verify_gate.stale([str(script)]) == {})

        # --- a red suite still blocks --------------------------------------
        verified.record("tests", False, "29 passed, 1 failed")
        ck("a FAILING run does not satisfy the gate",
           "tests" in verify_gate.stale([str(script)]))

        # --- an edit from last week is not this session's problem ----------
        verified.record("tests", True, "30 passed, 0 failed")
        old = touch(tmp, "scripts/brain/ancient.py")
        import os                                                # noqa: PLC0415
        stamp = time.time() - verify_gate.WINDOW - 60
        os.utime(old, (stamp, stamp))
        ck("an edit older than the window is ignored",
           verify_gate.stale([str(old)]) == {})
    finally:
        verify_gate.ROOT, verified.LEDGER = real_root, real_ledger
        shutil.rmtree(tmp, ignore_errors=True)

    # --- the hook end to end, against the real tree ------------------------
    def run(payload: dict) -> str:
        p = subprocess.run([sys.executable, str(HERE / "verify_gate.py")],
                           input=json.dumps(payload), capture_output=True,
                           text=True, timeout=60)
        return (p.stdout or "").strip()

    ck("no transcript, no opinion", run({}) == "")

    # Every silent path returns the same nothing, and that made a broken test
    # payload look exactly like verified work: a hand-written payload carried a
    # bad backslash escape, the hook stayed quiet, and it read as "all clear".
    # --explain names which branch the silence came from.
    def explain(stdin: str) -> str:
        return subprocess.run(
            [sys.executable, str(HERE / "verify_gate.py"), "--explain"],
            input=stdin, capture_output=True, text=True, timeout=60).stdout

    # A Windows path with a single backslash -- the exact shape that broke, and
    # written with an explicit escape so this file holds no raw control byte.
    bad_payload = '{"transcript_path":"C:\\Users\\x"}'
    ck("--explain names a bad payload rather than passing for silence",
       "payload unreadable" in explain(bad_payload))
    ck("--explain names a missing transcript_path",
       "no transcript_path" in explain("{}"))
    ck("--explain names the loop guard",
       "stop_hook_active" in explain('{"transcript_path":"x","stop_hook_active":true}'))
    ck("without --explain it stays quiet",
       run({"transcript_path": "x", "stop_hook_active": True}) == "")
    ck("garbage in, silence out -- it fails OPEN",
       subprocess.run([sys.executable, str(HERE / "verify_gate.py")],
                      input="not json", capture_output=True, text=True,
                      timeout=60).stdout.strip() == "")

    # The loop guard. Claude Code sets this on a stop that a stop hook already
    # caused; blocking again there is the one way this could wedge a session.
    tmp2 = Path(tempfile.mkdtemp())
    try:
        t = transcript(tmp2, [str(HERE / "verify_gate.py")])
        out = run({"transcript_path": t, "stop_hook_active": True})
        ck("stop_hook_active is never blocked twice", out == "")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # --- files written any way BUT the Edit tool ---------------------------
    # The transcript only sees Edit/Write/MultiEdit. On the day this was
    # added, 9 of 29 files committed to the brain had been written through
    # Bash -- a heredoc, a sed, a python one-liner -- and the gate was blind
    # to 31% of the work it exists to gate.
    tmp3 = Path(tempfile.mkdtemp())
    try:
        subprocess.run(["git", "init", "-q", str(tmp3)], capture_output=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(tmp3), "config", k, v],
                           capture_output=True)
        (tmp3 / "keep.py").write_text("x = 1\n", encoding="utf-8")
        (tmp3 / "gone.py").write_text("y = 2\n", encoding="utf-8")
        (tmp3 / "old.py").write_text("z = 3\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp3), "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", str(tmp3), "commit", "-qm", "base"],
                       capture_output=True)

        (tmp3 / "keep.py").write_text("x = 99\n", encoding="utf-8")     # modified
        (tmp3 / "fresh.py").write_text("new = 1\n", encoding="utf-8")   # untracked
        (tmp3 / "gone.py").unlink()                                     # deleted
        subprocess.run(["git", "-C", str(tmp3), "mv", "old.py", "renamed.py"],
                       capture_output=True)

        saved_root = verify_gate.ROOT
        verify_gate.ROOT = tmp3
        try:
            got = {Path(p).name for p in verify_gate.git_changed()}
        finally:
            verify_gate.ROOT = saved_root

        ck("a modified file is seen however it was written", "keep.py" in got)
        ck("an untracked one too", "fresh.py" in got)
        ck("a DELETED file is not -- there is nothing left to verify",
           "gone.py" not in got)
        ck("a rename reports the destination", "renamed.py" in got)
        # Porcelain carries the rename SOURCE in the next NUL field. Reading it
        # as a path would gate a file that no longer exists.
        ck("and never the rename source", "old.py" not in got)
        # `.strip()` on porcelain output ate the leading space of the FIRST
        # line only -- one file in twenty-three came out as `ocs/DASHBOARD.md`.
        ck("no path lost its first character",
           all(not n.startswith(("eep", "resh", "enamed")) for n in got))
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    # Outside a repository it must answer nothing, not raise.
    tmp4 = Path(tempfile.mkdtemp())
    try:
        saved_root = verify_gate.ROOT
        verify_gate.ROOT = tmp4
        try:
            ck("outside a git repo it returns nothing rather than raising",
               verify_gate.git_changed() == [])
        finally:
            verify_gate.ROOT = saved_root
    finally:
        shutil.rmtree(tmp4, ignore_errors=True)


    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
