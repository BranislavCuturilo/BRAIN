#!/usr/bin/env python3
"""Commit and push the brain, from whatever project you happened to be in.

**The failure this fixes, measured.** `skills/craft-testing/SKILL.md` sat
uncommitted for two days carrying a rule earned in a real session — that a
pipeline's exit code is the last command's, so two failing suites had been
reported as green and one had been pushed. On the other desktop that rule did
not exist. A captured rule that never leaves the machine it was captured on is
a rule that will be learned twice.

It happens structurally, not carelessly. `capture` writes into the brain from a
session working on `acme-audit`; that session has no reason to commit a
different repository, and it ends. The SessionStart check then reports "N
uncommitted changes" every morning until it is wallpaper.

**Why this does not break "commit only when asked".** That rule protects a
PROJECT repo, where a commit is a claim about work the operator will be asked
to stand behind. The brain is their own knowledge store, and here the risk runs
the other way: not committing loses the rule. What the rule actually forbids is
a commit whose MESSAGE describes your work while sweeping in someone else's —
so every message written here says plainly that it was automatic, names the
files, and claims nothing about intent.

**And never `git add -A`.** CLAUDE.md is explicit: the tree routinely carries a
live ticket sync, a regenerated dashboard and a half-finished edit at once.
Paths are classified and committed in groups; anything unrecognised is left
alone and reported.

  sync.py                 classify, commit each group, pull --rebase, push
  sync.py --dry-run       say what it would do, touch nothing
  sync.py --no-push       commit only
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
MARKER = ROOT / "journal" / "sync-last.json"

#: (group, prefixes, what the message calls it). Order matters: first match
#: wins, so the specific generated files are listed before `docs/`.
GROUPS = [
    ("generated", ("docs/AGENTS.md", "docs/SKILLS.md", "docs/DASHBOARD.md",
                   "docs/dashboard.html", "docs/reference.html"),
     "regenerated catalogues and dashboard"),
    ("data", ("tickets_store/", "journal/"),
     "ticket store, worklog and session records"),
    ("knowledge", ("skills/", "agents/", "evals/", "hooks/", "scripts/",
                   "workflows/", "docs/", "CLAUDE.md", "README.md",
                   ".github/", "registry.json"),
     "rules, agents and tooling"),
]


def git(*args: str, timeout: int = 60, raw: bool = False) -> tuple[int, str]:
    """`raw=True` returns the output UNSTRIPPED.

    `--porcelain` is a POSITIONAL format: two status characters, a space, then
    the path. Stripping the combined output eats the leading space of the FIRST
    line only, so exactly one file per run was parsed as `ocs/DASHBOARD.md` and
    silently dropped into "unclassified" while the other twenty-two were fine.
    A convenience `.strip()` over fixed-width data is never a convenience.
    """
    try:
        p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8",
                           errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out if raw else out.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def changed() -> list[tuple[str, str]]:
    code, out = git("status", "--porcelain", raw=True)
    if code:
        return []
    rows = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2].strip(), line[3:].strip().strip('"')
        if " -> " in path:                       # a rename
            path = path.split(" -> ")[-1]
        rows.append((status, path.replace("\\", "/")))
    return rows


def classify(rows) -> tuple[dict, list]:
    groups: dict[str, list[str]] = {}
    other: list[str] = []
    for _status, path in rows:
        for name, prefixes, _label in GROUPS:
            if any(path == p or path.startswith(p) for p in prefixes):
                groups.setdefault(name, []).append(path)
                break
        else:
            other.append(path)
    return groups, other


def recent_projects(limit: int = 3) -> list[str]:
    """Which projects were worked on recently, from the episode records.

    A knowledge change arrives here FROM somewhere -- `capture` writing a rule
    during a session on another repo. Naming that repo turns "automatic sync"
    into something a reader can trace.
    """
    d = ROOT / "journal" / "episodes"
    if not d.is_dir():
        return []
    rows = []
    for f in d.glob("*.jsonl"):
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda r: r.get("at", ""), reverse=True)
    seen, out = set(), []
    for r in rows[:12]:
        p = str(r.get("project") or "").rstrip("/").split("/")[-1]
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out[:limit]


def message(group: str, paths: list[str], label: str) -> str:
    head = {"generated": "chore(docs): regenerate",
            "data": "chore(store): sync",
            "knowledge": "chore(brain): sync"}[group]
    lines = [f"{head} — {len(paths)} file(s), committed automatically",
             "",
             f"Written during work and left uncommitted: {label}.",
             ""]
    for p in sorted(paths)[:20]:
        lines.append(f"  {p}")
    if len(paths) > 20:
        lines.append(f"  … {len(paths) - 20} more")
    lines += ["",
              "This message makes NO claim about intent — it was generated by",
              "scripts/brain/sync.py, which commits by path group and never",
              "`git add -A`. The reasoning, if there was any, is in the change",
              "itself."]
    if group == "knowledge":
        projs = recent_projects()
        if projs:
            lines += ["",
                      f"Recent sessions were on: {', '.join(projs)} — a rule "
                      f"captured there lands here."]
    lines += ["", "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"]
    return "\n".join(lines)


def main() -> int:
    dry = "--dry-run" in sys.argv
    quiet = "--quiet" in sys.argv

    if not (ROOT / ".git").exists():
        return 0

    rows = changed()
    groups, other = classify(rows)

    if not groups and not other:
        code, ahead = git("rev-list", "--count", "@{u}..HEAD")
        if ahead.isdigit() and int(ahead) > 0 and not dry and "--no-push" not in sys.argv:
            c, out = git("push", timeout=120)
            print(f"  pushed {ahead} commit(s)" if c == 0 else f"  PUSH FAILED: {out[:160]}")
            _mark(c == 0, out if c else "")
            return 0 if c == 0 else 1
        if not quiet:
            print("  nothing to sync")
        return 0

    print(f"  {len(rows)} change(s): " +
          ", ".join(f"{g} {len(p)}" for g, p in groups.items()) +
          (f", unclassified {len(other)}" if other else ""))

    if other:
        print("  LEFT ALONE (not the brain's own paths — look at these yourself):")
        for p in other[:10]:
            print(f"    {p}")

    if dry:
        for g, paths in groups.items():
            print(f"\n  --- would commit [{g}] ---")
            print("  " + message(g, paths, dict((n, l) for n, _p, l in GROUPS)[g])
                  .splitlines()[0])
        return 0

    committed = 0
    for name, _prefixes, label in GROUPS:
        paths = groups.get(name)
        if not paths:
            continue
        code, out = git("add", "--", *paths)
        if code:
            print(f"  add failed for {name}: {out[:120]}")
            continue
        code, out = git("commit", "-m", message(name, paths, label))
        if code == 0:
            committed += 1
            print(f"  committed [{name}] {len(paths)} file(s)")
        elif "nothing to commit" not in out:
            print(f"  commit failed for {name}: {out[:160]}")

    if "--no-push" in sys.argv or not committed:
        return 0

    # Rebase before pushing so the other machine's commits are not clobbered,
    # and NEVER force: craft-git's first rule. A conflict stops here and is
    # reported, because an automatic resolution is a guess about two people's
    # intent.
    # --autostash because this working tree is NEVER clean: the live ticket sync,
    # the regenerated dashboard and the journal are written continuously, so
    # between the commits above and this line the tree is dirty again and a plain
    # rebase refuses. Measured, not guessed: it refused at 14:52 with "You have
    # unstaged changes" and again at 18:03, and the refusal is reproducible on
    # demand. `origin main` is explicit because a bare `git pull` leaves git to
    # resolve what to rebase onto, which is where "Cannot rebase onto multiple
    # branches" comes from. Neither flag rewrites history -- craft-git holds.
    code, out = git("pull", "--rebase", "--autostash", "origin", "main", timeout=120)
    if code:
        git("rebase", "--abort")
        print(f"  PULL --rebase FAILED, nothing pushed: {out.splitlines()[0][:140]}")
        # An autostash that could not be reapplied is still on the stack. Say so:
        # a stash nobody was told about is work that reads as lost.
        _c, _stash = git("stash", "list")
        if _stash.strip():
            print(f"  STASH STILL HELD: {_stash.splitlines()[0][:120]}")
        print("  Resolve by hand: /brain:ops-sync")
        _mark(False, out[:400])
        return 1

    code, out = git("push", timeout=120)
    print("  pushed" if code == 0 else f"  PUSH FAILED: {out[:160]}")
    _mark(code == 0, "" if code == 0 else out[:400])
    return 0 if code == 0 else 1


def _mark(ok: bool, detail: str) -> None:
    """Record the outcome so health.py can report a sync that has been failing.

    A hook that fails quietly is the thing this module exists to replace; it
    must not become one itself.
    """
    try:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ok": ok, "detail": detail,
            "host": os.environ.get("COMPUTERNAME") or os.uname().nodename
            if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "?"),
        }, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
