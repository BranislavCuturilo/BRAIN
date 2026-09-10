#!/usr/bin/env python3
"""PreToolUse gate for VISUAL changes: no edit without a "before" shot, no
commit without an "after" shot.

DIES WHEN: the harness captures a before/after pair for a UI change on its own,
or the helpdesk accepts something other than a description so the pair no longer
has to be assembled here.

Why a gate and not a reminder: `require_skill.py` already records what a day of
reminder hooks is worth (fourteen reminders, zero skills loaded). A before/after
pair is worthless if it is remembered *after* the template was already changed —
the original screen is gone. So this denies, exactly like the skill gate.

It fires ONLY when all of these hold, and fails OPEN on anything else:

  * the file is a VISUAL file — a template, a stylesheet, front-end JS — never a
    model, service, migration or test (an implementation with no UI must not pay
    for this);
  * the session is working on a TICKET (`BRAIN_WORK_ID`, or a work-log interval
    that names this session — the F2 machinery), because the pair exists for the
    customer's comment. Editing the HUD itself, or any repo work outside a
    ticket, is never blocked;
  * that file is not already covered by a "before" capture of this work.

The second checkpoint is `git commit`: with visual files in the diff and a
"before" on record, an "after" must exist first — the plan's "pre git
commit/push kao poslednji izvor".

STATE CONTRACT (read here, written by scripts/visual/shoot.py):
`<shots_root>/<work_id>/state.json`

    {"work_id", "repo", "ticket",
     "before": {"at": iso, "files": [repo-relative paths covered]},
     "after":  {"at": iso, "files": [...]}}

`shots_root` = env BRAIN_SHOTS_DIR, else `<brain>/agent_view/.shots`
(gitignored; the HUD serves the gallery from there).
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[2]

#: A change to one of these can change what the customer SEES. Everything else
#: (models, services, migrations, tests, management commands) is out of scope by
#: design — the operator asked for this on visual edits only.
VISUAL_GLOBS = (
    "**/templates/**/*.html",
    "**/static/**/*.css",
    "**/static/**/*.js",
    "**/static/**/*.scss",
)
#: Never gate these even though they match above.
VISUAL_EXCLUDE = ("**/tests/**", "**/test_*", "**/node_modules/**", "**/venv/**",
                  "**/.venv/**", "**/staticfiles/**", "**/site-packages/**")

SHOOT = "python ~/.claude/skills/brain/scripts/visual/shoot.py"


def _payload() -> dict:
    try:
        raw = sys.stdin.read(200_000)
        return json.loads(raw) if raw.strip() else {}
    except Exception:                                           # noqa: BLE001
        return {}


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/")


def is_visual(path: str) -> bool:
    p = _norm(path).lower()
    if not p:
        return False
    if any(fnmatch.fnmatch(p, g.lower()) for g in VISUAL_EXCLUDE):
        return False
    return any(fnmatch.fnmatch(p, g.lower()) for g in VISUAL_GLOBS)


def repo_root(start: Path) -> Path | None:
    """The git repo `start` lives in, or None."""
    cur = start if start.is_dir() else start.parent
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return None


def shots_root() -> Path:
    return Path(os.environ.get("BRAIN_SHOTS_DIR") or (BRAIN / "agent_view" / ".shots"))


def work_id_for(session_id: str) -> str:
    """The ticket work this session is measured under: the env the HUD sets when
    it launches Claude FOR tickets, else an open work-log interval that names
    this session (F2's prompt-detected work). "" when this is not ticket work —
    and then the gate does nothing at all."""
    wid = (os.environ.get("BRAIN_WORK_ID") or "").strip()
    if wid:
        return wid
    if not session_id:
        return ""
    try:
        sys.path.insert(0, str(BRAIN / "scripts" / "tickets"))
        import store                                            # noqa: E402
        import worklog                                          # noqa: E402
        root = os.environ.get("TICKETS_STORE") or str(store.default_store())
        for iv in worklog.open_intervals(root):
            if iv.get("session") == session_id and iv.get("work_id"):
                return str(iv["work_id"])
    except Exception:                                           # noqa: BLE001
        return ""
    return ""


def ticket_for(session_id: str) -> str:
    """The helpdesk ticket this session is measured under, or "".

    `shoot.py` REFUSES to capture without one (exit 9, "no ticket, no
    pictures"), so the gate has to pass it or every edit is denied with a
    command that cannot succeed. The work-log interval already carries it; only
    the work id was ever read back out.

    "" rather than a guess: one work id can span several tickets, and a wrong
    ticket puts one customer's screenshots on another's ticket.
    """
    if not session_id:
        return ""
    try:
        sys.path.insert(0, str(BRAIN / "scripts" / "tickets"))
        import store                                            # noqa: E402
        import worklog                                          # noqa: E402
        root = os.environ.get("TICKETS_STORE") or str(store.default_store())
        for iv in worklog.open_intervals(root):
            if iv.get("session") == session_id and iv.get("ticket"):
                # QUALIFIED WITH ITS MODULE. The interval carries both, but
                # only the id was ever read back, so the gate suggested
                # `--ticket 53464` — a bare number names NO ticket: the
                # gallery files works by (module, id), and a moduleless
                # reference has no key, so such a run captured, printed
                # success, and was invisible in the gallery for ever.
                # `shoot.py` now refuses it (scripts/brain/ticket_ref.py),
                # which would have made the gate's own suggested command fail.
                mod = str(iv.get("module") or "").strip()
                tid = str(iv["ticket"]).strip()
                return (mod + "#" + tid) if mod else tid
    except Exception:                                           # noqa: BLE001
        return ""
    return ""


def _ticket_arg(session_id: str) -> str:
    """` --ticket X` when known, else "" - the refusal explains what to add."""
    tkt = ticket_for(session_id)
    return (" --ticket " + tkt) if tkt else ""


def state_for(work_id: str) -> dict:
    fp = shots_root() / work_id / "state.json"
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:                                           # noqa: BLE001
        return {}


def covered(state: dict, key: str, rel: str) -> bool:
    files = ((state.get(key) or {}).get("files") or [])
    rel = _norm(rel).lstrip("./").lower()
    return any(_norm(f).lstrip("./").lower() == rel for f in files)


def deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def _changed_visual(repo: Path) -> list:
    """Visual files in the working tree + index, repo-relative."""
    out = []
    for args in (["git", "diff", "--name-only", "HEAD"],
                 ["git", "diff", "--name-only", "--cached"]):
        try:
            r = subprocess.run(args, cwd=str(repo), capture_output=True,
                               text=True, timeout=6)
        except Exception:                                       # noqa: BLE001
            continue
        for line in (r.stdout or "").splitlines():
            rel = line.strip()
            if rel and is_visual(str(repo / rel)) and rel not in out:
                out.append(rel)
    return out


def gate_edit(payload: dict) -> int:
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not is_visual(path):
        return 0
    work_id = work_id_for(str(payload.get("session_id") or ""))
    if not work_id:
        return 0                                    # not ticket work -> never gate
    repo = repo_root(Path(path))
    if repo is None:
        return 0
    rel = _norm(os.path.relpath(path, repo))
    state = state_for(work_id)
    if covered(state, "before", rel):
        return 0
    cfg = repo / ".claude" / "visual-diff.json"
    extra = "" if cfg.exists() else (
        f" This repo has no {cfg.as_posix()} yet — the command will report "
        f"`needs_config` with the questions it needs; ASK THE OPERATOR those "
        f"questions with AskUserQuestion, write the config, then run it again. ")
    return deny(
        f"VISUAL change on a ticket ({work_id}) and there is no BEFORE screenshot "
        f"of {rel} yet — once you edit it, the original screen is gone. Run this "
        f"first, then retry the edit:\n\n"
        f"  {SHOOT} before --repo {repo.as_posix()} --files {rel} --work-id {work_id}{_ticket_arg(str(payload.get('session_id') or ''))}\n\n"
        f"{extra}It captures the affected pages AND their modal/dropdown/tab "
        f"states. If it exits non-zero it prints one JSON line with `error` and "
        f"`questions` — do not work around it, ask the operator. "
        f"(Non-visual files — models, services, migrations, tests — are never gated.)")


def gate_bash(payload: dict) -> int:
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    if "git commit" not in cmd:
        return 0
    work_id = work_id_for(str(payload.get("session_id") or ""))
    if not work_id:
        return 0
    repo = repo_root(Path(payload.get("cwd") or os.getcwd()))
    if repo is None:
        return 0
    changed = _changed_visual(repo)
    if not changed:
        return 0
    state = state_for(work_id)
    if not (state.get("before") or {}).get("files"):
        return 0            # nothing was captured before; nothing to compare to
    missing = [f for f in changed if not covered(state, "after", f)]
    if not missing:
        return 0
    return deny(
        f"This commit carries VISUAL changes ({', '.join(missing[:5])}"
        f"{'…' if len(missing) > 5 else ''}) and there is no AFTER screenshot yet. "
        f"The pair is what the customer gets on the ticket. Run:\n\n"
        f"  {SHOOT} after --repo {repo.as_posix()} --work-id {work_id}{_ticket_arg(str(payload.get('session_id') or ''))}\n\n"
        f"then commit. It re-shoots the same pages and states, marks WHERE the "
        f"change is (from the diff, not by looking at the pictures) and offers the "
        f"pairs in the HUD (🖼 Snimci) for your approval.")


def main() -> int:
    payload = _payload()
    tool = str(payload.get("tool_name") or "")
    try:
        if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            return gate_edit(payload)
        if tool in ("Bash", "PowerShell"):
            return gate_bash(payload)
    except Exception:                                           # noqa: BLE001
        return 0                                    # fail OPEN, always
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
