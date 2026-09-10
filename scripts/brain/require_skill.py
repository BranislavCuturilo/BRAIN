#!/usr/bin/env python3
"""
DIES WHEN: skills are selected deterministically -- by path, by file type, by an
explicit gate in the harness -- rather than by a model matching a description.
The whole reason this refuses is that description matching is probabilistic.
PreToolUse gate: refuse an edit until the governing skill has been invoked.

**Why this exists, and why a reminder was not enough.** The project it was
built for had FOURTEEN `PreToolUse` reminder hooks, one per domain, each
printing "REMINDER: load the X skill" on every Edit. Measured across a full day
of work: **zero skills invoked, across fourteen prompts.** At that volume a
reminder is not a prompt, it is wallpaper.

The brain's own constitution already says why: *"The hook had fired. The
reminder was on screen. The rules were recallable. None of that produced the
check."* Advice that can be skipped, gets skipped. So this one does not advise
-- it returns `permissionDecision: deny`, and the edit does not happen.

**How it decides.** Every hook payload carries `transcript_path`. A `Skill`
tool call is recorded there, so the transcript is the honest record of whether
a skill was actually loaded this session -- not a flag this script sets, which
could drift from reality.

**Fail OPEN on anything unexpected.** A gate that blocks work because it could
not read a file is worse than no gate. It denies only when it has positively
established that a required skill is missing.

Mapping comes from `.claude/skill-gate.json` in the project root when present:

    {"rules": [{"match": "glob", "skills": ["a", "b"], "why": "..."}]}

`skills` is any-of: loading one of them satisfies the rule.

**Which project root.** The session's cwd AND the project the edited file
belongs to -- the nearest ancestor of the file that carries
`.claude/skill-gate.json`. The first version read the cwd only: a session run
from the brain edited `acme-audit/tenants/context_processors.py`, the
project's `tenants/** -> tenant-safety` rule was never loaded, and the gate
was silent -- not because it was satisfied, because it never looked. A rule
found in the file's own project is matched against the path RELATIVE TO THAT
PROJECT, which is how its globs were written.

**The one exception to "Reading does not count".** A skill that lives in
another project's `.claude/skills/` is invisible to the Skill tool from this
cwd, so a rule from that project would block forever. For rules loaded from a
project other than the cwd only, a `Read` of that skill's SKILL.md in this
session satisfies the rule -- the deny message says exactly which file.
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path

#: Applies in every project, no configuration needed. Project rules are added
#: on top, never instead.
DEFAULT_RULES = [
    {"match": "**/test_*.py", "skills": ["craft-testing", "testing-rules"],
     "why": "a test"},
    {"match": "**/tests/**", "skills": ["craft-testing", "testing-rules"],
     "why": "a test"},
    {"match": "**/migrations/*.py", "skills": ["stack-django"],
     "why": "a migration"},
    {"match": "**/models*.py", "skills": ["stack-django", "craft-code"],
     "why": "a model"},
    {"match": "**/views*.py", "skills": ["stack-django", "craft-security"],
     "why": "a view (every by-pk endpoint is an authorization surface)"},
    {"match": "**/settings/*.py", "skills": ["stack-django"],
     "why": "settings"},
    # The service layer is the most load-bearing non-framework file there is,
    # and the first draft of these rules missed it entirely -- caught when a
    # test of the gate expected a deny on `services.py` and got an allow.
    {"match": "**/services*.py", "skills": ["stack-django", "craft-code"],
     "why": "the service layer"},
    {"match": "**/forms*.py", "skills": ["stack-django"], "why": "a form"},
    {"match": "**/*.html", "skills": ["ui-bootstrap", "i18n-rules",
                                      "ui-lists-tables"],
     "why": "a template"},
    {"match": "**/locale/**", "skills": ["craft-i18n", "i18n-rules"],
     "why": "a translation catalogue"},
]


def _gate_roots(cwd: Path, path: str) -> list[Path]:
    """The cwd, then the edited file's own project (nearest ancestor with a
    gate config), deduplicated. Bounded walk: a file six levels deep in a
    project is the deepest thing this has ever needed to see."""
    roots = [cwd]
    try:
        p = Path(path).resolve()
        for anc in list(p.parents)[:8]:
            if (anc / ".claude" / "skill-gate.json").is_file():
                if anc != cwd.resolve():
                    roots.append(anc)
                break
    except (OSError, ValueError):
        pass
    return roots


def _load_rules(cwd: Path, path: str = "") -> list[dict]:
    """Default rules (base ""), then each project's rules tagged with the
    project they came from, so `main` can match against the right path."""
    rules = [dict(r, base="") for r in DEFAULT_RULES]
    for base in _gate_roots(cwd, path):
        config = base / ".claude" / "skill-gate.json"
        try:
            if config.is_file():
                data = json.loads(config.read_text(encoding="utf-8"))
                rules += [dict(r, base=str(base)) for r in data.get("rules", []) if r.get("match")]
        except Exception:                                       # noqa: BLE001
            pass          # fail open: a broken config must not block every edit
    return rules


def _read_skill_files(transcript: str) -> set[str]:
    """Skills whose SKILL.md was READ this session -- the cross-project
    exception only. Same scan shape as _loaded_skills."""
    found: set[str] = set()
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"Read"' not in line or "SKILL.md" not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("name") != "Read":
                        continue
                    fp = str((block.get("input") or {}).get("file_path") or "").replace("\\", "/")
                    parts = fp.split("/")
                    if len(parts) >= 2 and parts[-1] == "SKILL.md":
                        found.add(parts[-2])
    except OSError:
        pass
    return found


def _relative_to(path: str, base: str) -> str:
    """The path as the rule's project sees it; unchanged when it is outside."""
    if not base:
        return path
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return path


def _loaded_skills(transcript: str) -> set[str]:
    """Skill names invoked in this session, read from the transcript.

    Substring scan rather than a full JSON parse per line: a long session's
    transcript runs to tens of megabytes and this executes before every edit.
    """
    found: set[str] = set()
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"Skill"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (isinstance(block, dict)
                            and block.get("name") == "Skill"):
                        name = (block.get("input") or {}).get("skill")
                        if name:
                            found.add(str(name).split(":")[-1])
    except OSError:
        return set()
    return found


def _matches(rule_glob: str, path: str) -> bool:
    """Match a path against a glob, anchored or not.

    `**/locale/**` must also hit a path that STARTS with `locale/` — the
    project root is where half of these live, and `fnmatch` needs something
    before `**/` to match it. Caught by the gate's own test, which expected a
    deny on the translation catalogue and got an allow.
    """
    norm = path.replace("\\", "/").lstrip("./")
    bare = rule_glob[3:] if rule_glob.startswith("**/") else rule_glob
    for candidate in {rule_glob, bare, "**/" + bare}:
        if fnmatch.fnmatch(norm, candidate):
            return True
    return False


def main() -> int:
    try:
        raw = sys.stdin.read(200_000)
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:                                           # noqa: BLE001
        return 0                                                # fail open

    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    transcript = payload.get("transcript_path") or ""
    if not path or not transcript:
        return 0

    cwd = Path(payload.get("cwd") or os.getcwd())
    missing = []
    for rule in _load_rules(cwd, path):
        base = rule.get("base") or ""
        if not _matches(rule["match"], _relative_to(path, base)):
            continue
        wanted = [s for s in rule.get("skills", []) if s]
        if wanted:
            foreign = bool(base) and Path(base).resolve() != cwd.resolve()
            missing.append((wanted, rule.get("why", "this file"), base if foreign else ""))
    if not missing:
        return 0

    loaded = _loaded_skills(transcript)
    read = _read_skill_files(transcript)
    unmet = [(w, why, base) for w, why, base in missing
             if not (set(w) & loaded) and not (base and (set(w) & read))]
    if not unmet:
        return 0

    names = sorted({s for w, _why, _b in unmet for s in w})
    reasons = sorted({why for _w, why, _b in unmet})
    foreign = sorted({b for _w, _why, b in unmet if b})
    hint = ""
    if foreign:
        files = "; ".join(f"{b}/.claude/skills/<{'|'.join(w)}>/SKILL.md"
                          for w, _why, b in unmet if b)
        hint = (f" This rule comes from another project ({', '.join(foreign)}); if the "
                f"Skill tool cannot see its skill from this working directory, Read "
                f"{files} in this session -- for a rule from another project, and only "
                f"then, that counts.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"You are editing {', '.join(reasons)} and have not loaded the "
            f"rules for it in this session. Invoke ONE of these with the "
            f"Skill tool first, then retry: {', '.join(names)}. "
            f"(Reading the skill file with Read does not count — rules "
            f"read after you have decided what to do are not rules that "
            f"guided the decision.)" + hint),
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
