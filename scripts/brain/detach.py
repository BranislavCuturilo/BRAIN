#!/usr/bin/env python3
"""Run a long job so that it CANNOT block you — at any hour, awake or not.

**The reasoning this replaces was wrong.** A plan once said a nightly rebuild
"does not block you, because you are asleep". Being asleep is not a mechanism.
If a job can occupy your session or lock your working tree, it blocks you, and
when it happens is irrelevant. Either it is structurally incapable of blocking
you or it is not.

And on this machine the nightly half does not even run. `craft-testing`,
`references/infra-failures.md`, measured on Windows: `Kernel-Power` event 507
(modern standby) fired **27 seconds** after a run started, and eight times in
six hours. A job scheduled for 03:00 is suspended almost immediately, produces
no error, and simply stands there.

So three things, and all three are required:

  1. ITS OWN PROCESS   detached, no console, outlives the session that started
                       it. Your Claude session is free the moment it starts.
  2. ITS OWN TREE      a git worktree pinned to a commit. Your working tree is
                       untouched, and edits you make while it runs cannot
                       corrupt what it is reading.
  3. A WAKE LOCK       `ES_CONTINUOUS | ES_SYSTEM_REQUIRED`, deliberately
                       WITHOUT `ES_DISPLAY_REQUIRED` -- the machine stays awake
                       for the job's duration, the screen still goes dark. It
                       is per-thread state that lapses when the process exits,
                       so a killed job leaves nothing altered.

  detach.py run --name graphify --worktree ../kv-graph --repo C:/projects/acme-audit -- <cmd...>
  detach.py status                    what is running, what finished, how long
  detach.py log <name>                the tail of one job's output

There is no `--schedule`. The trigger is intent, not a calendar: you start it
when you know you will want the result, and then you keep working.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
JOBS = ROOT / "journal" / "jobs"

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake() -> bool:
    """Hold the machine awake for as long as this process lives. Screen may
    still sleep -- ES_DISPLAY_REQUIRED is deliberately omitted."""
    if os.name != "nt":
        return False            # POSIX: caddy/systemd-inhibit differ per distro
    try:
        import ctypes
        ok = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        return bool(ok)
    except Exception:                                           # noqa: BLE001
        return False


def snapshot(repo: str, worktree: str, ref: str = "HEAD") -> tuple[bool, str]:
    """A worktree pinned to a commit. Created once, refreshed on every run.

    This is the half that makes the job independent of you: it reads a fixed
    tree, so the files you edit while it runs are not the files it is reading,
    and it cannot hold a lock on anything you are touching.
    """
    wt = Path(worktree)
    try:
        if not wt.is_dir():
            r = subprocess.run(["git", "-C", repo, "worktree", "add",
                                "--detach", str(wt), ref],
                               capture_output=True, text=True, timeout=180)
            if r.returncode:
                return False, (r.stderr or r.stdout).strip()[:200]
            return True, f"worktree created at {wt}"
        sha = subprocess.run(["git", "-C", repo, "rev-parse", ref],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        r = subprocess.run(["git", "-C", str(wt), "reset", "--hard", sha],
                           capture_output=True, text=True, timeout=180)
        if r.returncode:
            return False, (r.stderr or r.stdout).strip()[:200]
        return True, f"worktree at {sha[:8]}"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)[:200]


def _record(name: str, **fields) -> Path:
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f"{name}.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    data.update(fields)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def worker(name: str, cwd: str, cmd: list[str]) -> int:
    """The detached half. Holds the wake lock and runs the job."""
    JOBS.mkdir(parents=True, exist_ok=True)
    log = JOBS / f"{name}.log"
    awake = keep_awake()
    started = time.time()
    _record(name, state="running", pid=os.getpid(), cmd=cmd, cwd=cwd,
            wake_lock=awake, started=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            fh.write(f"[detach] {' '.join(cmd)}\n[detach] cwd={cwd}\n"
                     f"[detach] wake_lock={awake}\n\n")
            fh.flush()
            proc = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace")
        code = proc.returncode
    except Exception as exc:                                    # noqa: BLE001
        code = 127
        try:
            log.open("a", encoding="utf-8").write(f"\n[detach] FAILED: {exc}\n")
        except OSError:
            pass
    _record(name, state="finished", exit=code,
            seconds=round(time.time() - started, 1),
            ended=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return code


def launch(name: str, cwd: str, cmd: list[str]) -> int:
    """Spawn the worker fully detached, then return immediately."""
    args = [sys.executable, str(Path(__file__).resolve()), "--worker", name, cwd, *cmd]
    flags = 0
    kwargs: dict = {}
    if os.name == "nt":
        # DETACHED_PROCESS: no console at all, so nothing appears in front of
        # you and closing the terminal cannot kill it.
        flags = 0x00000008 | 0x00000200          # DETACHED | NEW_PROCESS_GROUP
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, stdout=subprocess.DEVNULL,           # noqa: S603
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kwargs)
    return 0


def status() -> int:
    if not JOBS.is_dir() or not any(JOBS.glob("*.json")):
        print("no jobs recorded")
        return 0
    print(f"{'job':<16} {'state':<9} {'exit':>4} {'took':>8}  started")
    for path in sorted(JOBS.glob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        took = f"{d.get('seconds', '')}s" if d.get("seconds") else ""
        print(f"{path.stem:<16} {d.get('state', '?'):<9} "
              f"{str(d.get('exit', '')):>4} {took:>8}  {str(d.get('started', ''))[:19]}"
              + ("" if d.get("wake_lock", True) else "   [no wake lock]"))
    print(f"\nlogs: {JOBS}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__.strip())
        return 1

    if argv[0] == "--worker":
        return worker(argv[1], argv[2], argv[3:])

    if argv[0] == "status":
        return status()

    if argv[0] == "log":
        if len(argv) < 2:
            print("detach.py log <name>")
            return 1
        log = JOBS / f"{argv[1]}.log"
        if not log.is_file():
            print(f"no log for {argv[1]!r}")
            return 1
        print(log.read_text(encoding="utf-8", errors="replace")[-6000:])
        return 0

    if argv[0] != "run":
        print(f"unknown command {argv[0]!r}")
        return 1

    if "--" not in argv:
        print("detach.py run --name N [--repo R --worktree W] -- <command...>")
        return 1
    head, cmd = argv[1:argv.index("--")], argv[argv.index("--") + 1:]
    opts: dict = {}
    for i in range(0, len(head) - 1, 2):
        if head[i].startswith("--"):
            opts[head[i][2:]] = head[i + 1]

    name = opts.get("name")
    if not name or not cmd:
        print("detach.py run --name N [--repo R --worktree W] -- <command...>")
        return 1

    cwd = opts.get("worktree") or os.getcwd()
    if opts.get("repo") and opts.get("worktree"):
        ok, detail = snapshot(opts["repo"], opts["worktree"], opts.get("ref", "HEAD"))
        print(("  ok   " if ok else "  FAIL ") + detail)
        if not ok:
            return 1

    launch(name, cwd, cmd)
    print(f"  ok   started '{name}' detached — this shell is free now")
    print(f"       python scripts/brain/detach.py status")
    print(f"       python scripts/brain/detach.py log {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
