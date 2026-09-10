#!/usr/bin/env python3
"""Proof that a detached job really is detached, and really holds the machine.

The claim is the whole point of the module, and it is the kind of claim that
is comfortable to assume: "it runs in the background". Two things have to be
true and neither is visible by reading the code:

  * `run` RETURNS while the job is still going -- otherwise the caller waited,
    which is exactly the failure this replaces;
  * the job KEEPS GOING after that, and records what it did.

A plan was once rejected for asserting a non-blocking property instead of
demonstrating one. This file demonstrates it.

  python scripts/brain/test_detach.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import detach                                                    # noqa: E402

FAILS: list[str] = []
NAME = "_test_detach_probe"


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def state() -> dict:
    p = detach.JOBS / f"{NAME}.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def main() -> int:
    for suffix in (".json", ".log"):
        f = detach.JOBS / f"{NAME}{suffix}"
        if f.exists():
            f.unlink()

    # A job long enough that "did the caller wait" is unambiguous.
    cmd = [sys.executable, "-c",
           "import time,sys\n"
           "for i in range(8):\n"
           "    print('tick', i, flush=True)\n"
           "    time.sleep(1)\n"
           "print('done')\n"]

    t0 = time.time()
    detach.launch(NAME, str(HERE), cmd)
    elapsed = time.time() - t0

    # THE claim. The job takes ~8s; returning in under two means the caller was
    # never waiting on it.
    ck(f"launch returns immediately ({elapsed:.2f}s for an 8s job)", elapsed < 2.0)

    # ...and it is genuinely still running, not silently dead.
    deadline = time.time() + 20
    saw_running = False
    while time.time() < deadline:
        if state().get("state") == "running":
            saw_running = True
            break
        time.sleep(0.5)
    ck("the job is recorded as running while the caller is free", saw_running)

    # A detached process must survive on its own, so wait for it rather than
    # for a signal from it.
    deadline = time.time() + 60
    while time.time() < deadline and state().get("state") != "finished":
        time.sleep(1)
    st = state()
    ck("the job finished on its own", st.get("state") == "finished")
    ck("its exit code was recorded", st.get("exit") == 0)
    ck("it took roughly as long as the work, not zero",
       isinstance(st.get("seconds"), (int, float)) and st["seconds"] >= 6)

    log = (detach.JOBS / f"{NAME}.log").read_text(encoding="utf-8", errors="replace")
    ck("the output was captured", "tick 0" in log and "done" in log)
    ck("the log names the command it ran", "[detach]" in log)

    # The wake lock is the half that makes a long job survive this machine at
    # all -- modern standby fired 27s into a run when it was measured. On
    # Windows it must engage; elsewhere it is honestly reported as absent.
    if os.name == "nt":
        ck("the wake lock engaged (Windows)", st.get("wake_lock") is True)
    else:
        ck("the wake lock is reported absent rather than assumed",
           st.get("wake_lock") is False)

    # A worktree request that cannot be satisfied must FAIL, not silently run
    # the job against the current directory -- that would read the tree the
    # operator is editing, which is the thing being prevented.
    ok, detail = detach.snapshot("/nonexistent-repo", "/nonexistent-worktree")
    ck("an impossible worktree is refused, not ignored", ok is False and bool(detail))

    for suffix in (".json", ".log"):
        f = detach.JOBS / f"{NAME}{suffix}"
        if f.exists():
            f.unlink()

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s)")
        return 1
    print("ok - the caller is free, the job survives, and the machine stays awake")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
