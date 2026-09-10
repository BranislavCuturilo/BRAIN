#!/usr/bin/env python3
"""The gate reads the rules of the project the FILE belongs to, not the cwd's.

The incident: a session run from the brain edited
acme-audit/tenants/context_processors.py. The project's own
`tenants/** -> tenant-safety` rule lives in acme-audit/.claude/skill-gate.json;
the gate read only the cwd's config, so the rule was never loaded and the
edit went through -- silence that read as "satisfied" and meant "never
looked". This runs the real hook as a subprocess, the way Claude Code does,
with a payload whose cwd is one directory and whose file is in another.

  python scripts/brain/test_require_skill.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "require_skill.py"
FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def run(payload: dict) -> dict:
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8")
    if not r.stdout.strip():
        return {}
    return json.loads(r.stdout)


def transcript(tmp: Path, name: str, blocks: list[dict]) -> str:
    fp = tmp / f"{name}.jsonl"
    lines = [json.dumps({"message": {"content": [b]}}) for b in blocks]
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(fp)


def skill_call(skill: str) -> dict:
    return {"type": "tool_use", "name": "Skill", "input": {"skill": skill}}


def read_call(path: str) -> dict:
    return {"type": "tool_use", "name": "Read", "input": {"file_path": path}}


def decision(out: dict) -> str:
    return ((out.get("hookSpecificOutput") or {}).get("permissionDecision") or "allow")


def reason(out: dict) -> str:
    return ((out.get("hookSpecificOutput") or {}).get("permissionDecisionReason") or "")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        project = tmp / "acme-audit"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "skill-gate.json").write_text(json.dumps({"rules": [
            {"match": "tenants/**", "skills": ["tenant-safety"], "why": "tenant code"},
        ]}), encoding="utf-8")
        target = project / "tenants" / "context_processors.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1\n", encoding="utf-8")
        brain = tmp / "brain"
        brain.mkdir()
        skill_md = str(project / ".claude" / "skills" / "tenant-safety" / "SKILL.md")

        print("cwd elsewhere, file in a project with its own gate")
        empty = transcript(tmp, "empty", [])
        out = run({"tool_input": {"file_path": str(target)}, "transcript_path": empty, "cwd": str(brain)})
        ok(decision(out) == "deny", "denied: the project's rule was loaded from the FILE's project")
        ok("tenant-safety" in reason(out), f"names the skill: {reason(out)[:90]}")
        ok("another project" in reason(out) and "SKILL.md" in reason(out),
           "says the rule is foreign and which file to Read")

        loaded = transcript(tmp, "loaded", [skill_call("tenant-safety")])
        out = run({"tool_input": {"file_path": str(target)}, "transcript_path": loaded, "cwd": str(brain)})
        ok(decision(out) == "allow", "Skill(tenant-safety) in the transcript -> allowed")

        readt = transcript(tmp, "read", [read_call(skill_md)])
        out = run({"tool_input": {"file_path": str(target)}, "transcript_path": readt, "cwd": str(brain)})
        ok(decision(out) == "allow", "Read of the foreign project's SKILL.md -> allowed (the one exception)")

        print("cwd IS the project: the exception does not apply")
        out = run({"tool_input": {"file_path": str(target)}, "transcript_path": readt, "cwd": str(project)})
        ok(decision(out) == "deny", "same Read, cwd = project -> still denied (Reading does not count at home)")
        ok("another project" not in reason(out), "no foreign hint when the rule is the cwd's own")
        out = run({"tool_input": {"file_path": str(target)}, "transcript_path": loaded, "cwd": str(project)})
        ok(decision(out) == "allow", "Skill call at home -> allowed")

        print("a file outside every gated project")
        other = tmp / "elsewhere" / "tenants" / "x.py"
        other.parent.mkdir(parents=True)
        other.write_text("y = 2\n", encoding="utf-8")
        out = run({"tool_input": {"file_path": str(other)}, "transcript_path": empty, "cwd": str(brain)})
        ok(decision(out) == "allow", "no gate config above the file, none in cwd -> allowed")

        print("default rules still fire from the cwd alone")
        view = tmp / "elsewhere" / "app" / "views.py"
        view.parent.mkdir(parents=True)
        view.write_text("z = 3\n", encoding="utf-8")
        out = run({"tool_input": {"file_path": str(view)}, "transcript_path": empty, "cwd": str(brain)})
        ok(decision(out) == "deny" and "craft-security" in reason(out), "views.py -> default rule denies")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
