#!/usr/bin/env python3
"""Set the brain up on a new machine — or tell you exactly what is missing.

**Written to be driven by Claude, not only read by a person.** Every check
prints one line with a verdict, and every failure prints the exact command that
fixes it. Nothing here guesses, and nothing here does anything a human would
want to have been asked about first.

  bootstrap.py            check everything, report, exit 1 if anything is broken
  bootstrap.py --fix      also do the SAFE fixes (user-level, reversible)
  bootstrap.py --json     machine-readable

## What it will NEVER do, and why

- **No `sudo`, ever.** A package manager install is system-wide and is the
  human's call. It prints the command instead.
- **No secrets.** `GEMINI_API_KEY`, helpdesk credentials and `PROJECTS_ROOT`
  are reported as missing with a pointer; they are never invented or written.
- **No `git clone` over an existing directory**, and no destructive git.

Everything it *does* fix under `--fix` is user-level and reversible: a
`~/.local/bin/python` symlink, a `pip install --user`, creating a directory.

## The three that actually break installs

1. **`python` (bare) is not on PATH.** Every hook in `hooks.json` invokes
   `python`, not `python3`. On a distro that ships only `python3` the hooks
   fail silently -- they are all `|| true` or fail-open by design -- so the
   brain looks installed and enforces nothing.
2. **`PYTHONIOENCODING` is unset.** The ticket store is full of č/ć/š/ž/đ and
   the scripts write to a pipe, so Python picks ASCII and the CLI dies on the
   first Serbian ticket title.
3. **`PROJECTS_ROOT` is unset.** `store.repo_path` then falls back to the
   legacy Windows root, and on POSIX `"C:/projects"` is not even an absolute
   path -- so every module resolves to a directory that cannot exist.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BRAIN = Path.home() / ".claude" / "skills" / "brain"
REMOTE = "https://github.com/BranislavCuturilo/BRAIN.git"
POSIX = os.name != "nt"

OK, WARN, BAD, ASK = "ok  ", "warn", "FAIL", "ASK "
results: list[dict] = []


def say(state: str, name: str, detail: str, fix: str = "") -> None:
    results.append({"state": state.strip(), "check": name, "detail": detail, "fix": fix})
    print(f"  {state} {name:<28} {detail}")
    if fix and state in (BAD, WARN, ASK):
        for line in fix.splitlines():
            print(f"         {line}")


def run(*args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


# --------------------------------------------------------------------------

def check_platform() -> None:
    say(OK, "platform", f"{platform.system()} {platform.release()}, "
                        f"python {platform.python_version()}")


def check_git() -> None:
    if not shutil.which("git"):
        say(BAD, "git", "not on PATH",
            "Debian/Ubuntu: sudo apt install git\n"
            "macOS:         xcode-select --install")
        return
    code, out = run("git", "--version")
    say(OK, "git", out.strip() if code == 0 else "present")


def check_python(fix: bool) -> None:
    """The documented snag: the hooks call bare `python`."""
    found = shutil.which("python")
    if found:
        code, out = run(found, "--version")
        say(OK, "python (bare)", f"{found} -> {out.strip()}")
        return

    py3 = shutil.which("python3")
    if not py3:
        say(BAD, "python (bare)", "neither `python` nor `python3` on PATH",
            "Debian/Ubuntu: sudo apt install python3\nmacOS:         brew install python")
        return

    target = Path.home() / ".local" / "bin" / "python"
    fix_cmd = (f"mkdir -p {target.parent} && ln -s {py3} {target}\n"
               f"# then ensure {target.parent} is on PATH\n"
               f"# Debian/Ubuntu alternative: sudo apt install python-is-python3")
    if not fix or not POSIX:
        say(BAD, "python (bare)",
            f"only python3 ({py3}); EVERY hook calls `python` and will fail SILENTLY",
            fix_cmd)
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.symlink_to(py3)
        on_path = str(target.parent) in os.environ.get("PATH", "")
        say(OK if on_path else WARN, "python (bare)",
            f"symlinked {target} -> {py3}"
            + ("" if on_path else "; its directory is NOT on PATH yet"),
            "" if on_path else f'export PATH="{target.parent}:$PATH"   # add to ~/.bashrc or ~/.zshrc')
    except OSError as exc:
        say(BAD, "python (bare)", f"could not symlink: {exc}", fix_cmd)


def check_encoding() -> None:
    val = os.environ.get("PYTHONIOENCODING", "")
    if val.lower().replace("-", "") in ("utf8", "utf8:replace"):
        say(OK, "PYTHONIOENCODING", val)
    else:
        say(WARN, "PYTHONIOENCODING", f"{val or 'unset'} -- č/ć/š/ž/đ will crash a piped script",
            'export PYTHONIOENCODING=utf-8      # add to ~/.bashrc or ~/.zshrc')


def check_projects_root() -> None:
    val = os.environ.get("PROJECTS_ROOT", "")
    if not val:
        say(ASK, "PROJECTS_ROOT", "unset -- the ticket store cannot resolve any repo",
            "This is the ONE value only you know: the directory your project\n"
            "repositories live in. Ask, then:\n"
            '  export PROJECTS_ROOT="$HOME/posao"   # add to ~/.bashrc or ~/.zshrc')
        return
    p = Path(val)
    say(OK if p.is_dir() else WARN, "PROJECTS_ROOT",
        f"{val}{'' if p.is_dir() else '  (does not exist yet)'}")


def check_global_claude_md() -> None:
    """The always-loaded personal file, which this repo cannot carry.

    `~/.claude/CLAUDE.md` sits OUTSIDE the brain, deliberately -- the brain
    repo holds only the brain, and `history.jsonl`, `sessions/` and
    `projects/*/memory` must never be committed to it. The consequence is that
    anything living there does not travel: a rule written on one machine is
    simply absent on the next, with nothing to say so.

    It matters for one rule in particular. How to write to this reader is not
    a preference -- it is an accessibility requirement, it applies to every
    reply in every project, and a selective memory is the wrong mechanism for
    a rule that always applies: the memory selector picks a handful per query
    and is told to be conservative with user-profile entries, so a question
    about a Django view would never retrieve it.
    """
    f = Path.home() / ".claude" / "CLAUDE.md"
    if not f.is_file():
        say(ASK, "~/.claude/CLAUDE.md", "missing -- nothing personal loads here",
            "Copy it from the machine that has it. It is not in this repo and\n"
            "never will be; see the docstring for why.")
        return
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if "dyslexia" in text.lower():
        say(OK, "~/.claude/CLAUDE.md", f"{len(text)} chars, reading style present")
    else:
        say(ASK, "~/.claude/CLAUDE.md",
            f"{len(text)} chars, but it does not mention the reading style",
            "This reader has dyslexia and ADHD and asked for short, scannable\n"
            "answers in EVERY reply. That belongs here, not in a memory --\n"
            "memories are retrieved selectively and this must always apply.\n"
            "Copy the 'How to write to me' section from the other machine.")


def check_clone() -> None:
    if not BRAIN.is_dir():
        say(BAD, "brain clone", f"{BRAIN} does not exist",
            f"git clone {REMOTE} {BRAIN}\n"
            f"# the path must be EXACTLY this -- it is how the plugin is discovered")
        return
    if not (BRAIN / ".claude-plugin" / "plugin.json").is_file():
        say(BAD, "brain clone", f"{BRAIN} exists but has no .claude-plugin/plugin.json",
            "This is not the brain, or the clone is incomplete. Move it aside and re-clone.")
        return
    code, out = run("git", "-C", str(BRAIN), "remote", "get-url", "origin")
    say(OK, "brain clone", f"{BRAIN}  ({out.strip() if code == 0 else 'no remote'})")

    code, out = run("git", "-C", str(BRAIN), "status", "--porcelain")
    if code == 0 and out.strip():
        say(WARN, "brain worktree", f"{len(out.strip().splitlines())} uncommitted change(s)",
            "Expected on a working machine (the ticket store syncs). On a FRESH\n"
            "install it means the clone is dirty -- look before pulling.")


def check_optional(fix: bool) -> None:
    for mod, why, pkg in (
        ("yaml", "health.py frontmatter validation + evals.py case loading", "pyyaml"),
        ("PIL", "the visual-diff engine (annotate/compare/shoot)", "pillow"),
    ):
        try:
            __import__(mod)
            say(OK, f"python: {pkg}", why)
            continue
        except ImportError:
            pass
        if fix:
            code, out = run(sys.executable, "-m", "pip", "install", "--user",
                            "--quiet", pkg, timeout=180)
            if code == 0:
                say(OK, f"python: {pkg}", f"installed -- {why}")
                continue
            say(WARN, f"python: {pkg}", f"install failed: {out.strip()[:80]}",
                f"pip install --user {pkg}")
        else:
            say(WARN, f"python: {pkg}", f"missing -- {why}",
                f"pip install --user {pkg}")

    say(OK if shutil.which("node") else WARN, "node",
        shutil.which("node") or "absent -- health.py skips the workflow syntax check")


def check_claude_cli() -> None:
    if not shutil.which("claude"):
        say(BAD, "claude CLI", "not on PATH -- nothing here loads without it",
            "https://claude.com/claude-code   then re-run this script")
        return
    code, out = run("claude", "--version")
    say(OK, "claude CLI", out.strip() if code == 0 else "present")

    code, out = run("claude", "plugin", "list", timeout=60)
    if code != 0:
        say(WARN, "plugin loaded", "could not list plugins (is Claude Code configured?)",
            "claude plugin list")
    elif "brain" in out:
        say(OK, "plugin loaded", "brain@skills-dir")
    else:
        say(BAD, "plugin loaded", "the brain is NOT loaded",
            f"The clone must be exactly at {BRAIN}. Then:\n"
            f"  claude plugin validate {BRAIN} --strict")


def check_health() -> None:
    if not BRAIN.is_dir():
        return
    code, out = run(sys.executable, str(BRAIN / "scripts" / "brain" / "health.py"),
                    "--no-git", "--quiet", timeout=60)
    if code == 0:
        say(OK, "health", "no problems")
    else:
        first = (out.strip().splitlines() or ["?"])[0]
        say(WARN, "health", f"reports problems: {first}",
            "python scripts/brain/health.py       # the full report")


def check_tests() -> None:
    if not BRAIN.is_dir():
        return
    runner = BRAIN / "scripts" / "brain" / "tests.py"
    if not runner.is_file():
        return
    code, out = run(sys.executable, str(runner), "--fast", "--quiet", timeout=600)
    tail = [l for l in out.strip().splitlines() if "passed" in l]
    detail = tail[-1] if tail else ("all passed" if code == 0 else "see output")
    if code == 0:
        say(OK, "tests", detail)
    else:
        say(WARN, "tests", detail,
            "python scripts/brain/tests.py --fast\n"
            "# On Linux, five files are a KNOWN pre-existing gap --\n"
            "# see docs/SETUP-linux-macos.md, 'Known gap'.")


# --------------------------------------------------------------------------

def main() -> int:
    fix = "--fix" in sys.argv

    print("=" * 70)
    print("BRAIN BOOTSTRAP" + ("  (--fix: safe, user-level changes allowed)" if fix
                               else "  (checking only; --fix to repair what is safe)"))
    print("=" * 70)

    print("\n[1] the machine")
    check_platform()
    check_git()
    check_python(fix)
    check_encoding()

    print("\n[2] the install")
    check_clone()
    check_claude_cli()
    check_optional(fix)

    print("\n[3] your data")
    check_global_claude_md()
    check_projects_root()

    print("\n[4] does it actually work")
    check_health()
    check_tests()

    if "--json" in sys.argv:
        print("\n" + json.dumps(results, indent=2))

    bad = [r for r in results if r["state"] == "FAIL"]
    ask = [r for r in results if r["state"] == "ASK"]
    warn = [r for r in results if r["state"] == "warn"]

    print("\n" + "=" * 70)
    if bad:
        print(f"{len(bad)} BLOCKING: " + ", ".join(r["check"] for r in bad))
        print("Fix these in order; each one prints its own command above.")
    if ask:
        print(f"{len(ask)} NEEDS A HUMAN: " + ", ".join(r["check"] for r in ask))
        print("Do not guess these values. Ask, then export them.")
    if warn and not bad:
        print(f"{len(warn)} non-blocking: " + ", ".join(r["check"] for r in warn))
    if not bad and not ask:
        print("Ready. Start Claude Code in any project and run /brain:brain.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
