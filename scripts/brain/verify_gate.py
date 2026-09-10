#!/usr/bin/env python3
"""Stop hook: refuse to end the turn while an edit to the brain is unverified.

**The two incidents, both in one session.** A change was made to the brain and
the turn ended on a green report that was not green. Once because the suite was
piped to `tail -5` and `tail`'s exit code was read instead of the suite's --
19 passed and 1 failed became "20 passed, 0 failed". Once because a patch
script silently failed to apply, so "all passed" was a real result describing
the *previous* version of the file.

Neither was a knowledge failure. `craft-testing` already carries the `tail`
rule, and it had been read. The rule was recalled and the mistake was made
anyway, which is the same finding `require_skill.py` was built on: **advice
that can be skipped, gets skipped.** So this does not advise either. It returns
Claude Code's `decision: "block"` and the turn does not end.

**What it actually checks -- and why it is not "did you say done".** Whether a
report is honest is not mechanically decidable. Whether a verification is OLDER
THAN THE EDIT IT CLAIMS TO COVER is. That single comparison catches both
incidents: a suite that never ran, and one that ran before the change. It is
the whole mechanism.

**Why it blocks exactly once and can never wedge.** Claude Code sets
`stop_hook_active` on a stop that a stop hook already caused, so a second stop
passes straight through. One block puts the command in front of the model; if
it runs it, the staleness clears on its own and the next stop is silent. If it
stops again regardless, the turn ends. A counter with a release threshold was
considered and rejected: it needs per-session state on disk, and the failure it
guards against is one this cannot have.

**It is silent outside the brain.** The gates it names -- `tests.py`,
`evals.py` -- exist here. Guessing a verification command for a Django project
would produce a block nobody can satisfy, and a gate that cannot be satisfied
is worse than no gate at all.
"""
from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "brain"))

#: pattern -> (gate name, the command that satisfies it)
#: Only paths a gate actually covers. Editing docs/, journal/ or tickets_store/
#: proves nothing about the suite, and blocking on them would make the hook
#: fire on ticket syncs -- which is how a gate becomes wallpaper.
GATED = [
    ("scripts/*.py", "tests", "python scripts/brain/tests.py --fast"),
    ("scripts/*/*.py", "tests", "python scripts/brain/tests.py --fast"),
    ("scripts/*/*/*.py", "tests", "python scripts/brain/tests.py --fast"),
    ("skills/*/SKILL.md", "evals", "python scripts/brain/evals.py"),
    ("skills/*/references/*.md", "evals", "python scripts/brain/evals.py"),
    ("agents/*.md", "evals", "python scripts/brain/evals.py"),
    ("hooks/hooks.json", "evals", "python scripts/brain/evals.py"),
    ("evals/*.yaml", "evals", "python scripts/brain/evals.py"),
]

#: An edit is only "recent" if it happened in the last few hours. Without this,
#: opening a session in a tree someone edited last week would block on work this
#: session never touched.
WINDOW = 6 * 3600


def edited(transcript: str) -> list[str]:
    """Absolute paths written by Edit/Write/MultiEdit in this session."""
    out: list[str] = []
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    if b.get("name") not in ("Edit", "Write", "MultiEdit",
                                             "NotebookEdit"):
                        continue
                    p = (b.get("input") or {}).get("file_path")
                    if isinstance(p, str) and p:
                        out.append(p)
    except OSError:
        return []
    return out


def git_changed() -> list[str]:
    """Paths git reports as changed in the brain's working tree.

    **The hole this closes, measured.** `edited()` reads the transcript for
    Edit/Write tool calls, and a file written any other way is invisible to it
    -- a `sed`, a heredoc, a Python one-liner run through Bash. On the day this
    was added, 9 of the 29 files committed to this repo had been written that
    way, including `scripts/visual/affected.py` and two skill files. The gate
    was blind to 31% of the work it exists to gate, and nothing said so.

    Git sees every one of them regardless of how they were written. The two
    sources are unioned rather than swapped: the transcript still catches a
    file edited and then committed inside the same turn, which `git status` no
    longer reports.

    `--porcelain=v1 -z` and no `.strip()`: porcelain pads the status field with
    a leading space for a worktree-only change, and stripping it once ate
    exactly the first path of a run -- one file in twenty-three came out as
    `ocs/DASHBOARD.md`.
    """
    try:
        p = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain=v1", "-z",
             "--untracked-files=all"],
            capture_output=True, timeout=30)
        if p.returncode:
            return []
        out = p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return []

    paths, parts = [], out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if not path:
            continue
        # A rename carries its source as the NEXT NUL-separated field; skip it
        # so a source path is never mistaken for a changed file.
        if "R" in code or "C" in code:
            i += 1
        # Deleted files have nothing left to verify.
        if code.strip() == "D":
            continue
        paths.append(str(ROOT / path))
    return paths


def stale(paths: list[str]) -> dict[str, tuple[str, float, str]]:
    """gate -> (command, newest edit time it must cover, an example file).

    A path counts only if it is inside the brain, matches a gated pattern, and
    still exists. A file edited and then deleted has nothing left to verify.
    """
    import verified                                             # noqa: PLC0415

    ledger = verified.load()
    now = time.time()
    need: dict[str, tuple[str, float, str]] = {}

    for raw in paths:
        try:
            f = Path(raw).resolve()
            rel = f.relative_to(ROOT).as_posix()
        except (OSError, ValueError):
            continue
        if not f.is_file():
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if now - mtime > WINDOW:
            continue
        for pat, gate, cmd in GATED:
            if not fnmatch.fnmatch(rel, pat):
                continue
            entry = ledger.get(gate) or {}
            ran = float(entry.get("ts") or 0)
            # Green but older than the edit is the exact shape of both
            # incidents, so it counts as unverified rather than as evidence.
            if entry.get("ok") and ran >= mtime:
                break
            prev = need.get(gate)
            if prev is None or mtime > prev[1]:
                need[gate] = (cmd, mtime, rel)
            break
    return need


def main() -> int:
    # Every silent path returns the same nothing, so `--explain` says WHICH
    # one. Without it a hook that stayed quiet for a bad payload looks exactly
    # like one that stayed quiet because the work was verified -- and a gate
    # you cannot tell apart from a broken gate is not a gate.
    say = print if "--explain" in sys.argv else (lambda *a, **k: None)

    # Fail OPEN on anything unexpected. A hook that blocks the turn because it
    # could not parse its own input is a worse defect than the one it guards.
    try:
        raw = sys.stdin.read(200_000)
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:                                    # noqa: BLE001
        say(f"silent: payload unreadable ({exc})")
        return 0

    if payload.get("stop_hook_active"):
        say("silent: stop_hook_active -- already blocked once this turn")
        return 0

    transcript = payload.get("transcript_path") or ""
    if not transcript:
        say("silent: no transcript_path in the payload")
        return 0

    try:
        # Both sources, unioned. The transcript alone missed every file
        # written through Bash, which was 31% of a real day's work.
        touched = list(dict.fromkeys(edited(transcript) + git_changed()))
        need = stale(touched)
    except Exception as exc:                                    # noqa: BLE001
        say(f"silent: {type(exc).__name__} while reading the transcript ({exc})")
        return 0

    say(f"{len(touched)} file(s) changed (transcript + git), "
        f"{len(need)} gate(s) stale")
    if not need:
        say("silent: nothing edited that a gate covers, or every gate "
            "already passed AFTER the edit")
        return 0

    import verified                                             # noqa: PLC0415
    ledger = verified.load()

    lines = []
    for gate, (cmd, _mtime, example) in sorted(need.items()):
        e = ledger.get(gate) or {}
        if not e:
            why = "has not run in this tree at all"
        elif not e.get("ok"):
            why = f"last run FAILED ({e.get('detail', '')})"
        else:
            why = (f"last passed at {e.get('at', '?')}, which is BEFORE "
                   f"{example} was edited -- that result describes the "
                   f"previous version of the file")
        lines.append(f"  {gate}: {why}\n    run: {cmd}")

    reason = (
        "BRAIN: this turn edited the brain and no gate has verified it.\n\n"
        + "\n".join(lines)
        + "\n\nRun the command(s) above and read the LAST line of the output, "
        "not a piped tail -- `suite | tail` reports tail's exit code, and that "
        "is how '19 passed, 1 failed' was once reported as '20 passed, 0 "
        "failed'. If a gate fails, fix it or say plainly that it fails; do not "
        "end on a green summary you did not read.\n\n"
        "If verifying is genuinely not wanted here -- the edit was a comment, "
        "or the user asked to stop -- say so in one line and stop again. This "
        "blocks once and then lets go."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
