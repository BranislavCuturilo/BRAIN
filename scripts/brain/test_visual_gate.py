#!/usr/bin/env python3
"""visual_gate: the before/after gate for VISUAL changes. Offline, temp dirs
only — a temp git repo for the commit checkpoint, never the real store, never
the network.

  python test_visual_gate.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import visual_gate as vg  # noqa: E402

FAILS = []


def ck(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  -- " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def run_gate(payload, shots_root, work_env=None):
    """Drive the gate as the harness does: JSON on stdin, JSON or nothing out."""
    env = dict(os.environ)
    env["BRAIN_SHOTS_DIR"] = str(shots_root)
    env.pop("BRAIN_WORK_ID", None)
    if work_env:
        env["BRAIN_WORK_ID"] = work_env
    r = subprocess.run([sys.executable, str(HERE / "visual_gate.py")],
                       input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env, timeout=60)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    try:
        return r.returncode, (json.loads(out) if out else None)
    except Exception:                                           # noqa: BLE001
        return r.returncode, {"raw": out}


def _repo(tmp):
    repo = Path(tmp) / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / "myapp" / "templates" / "myapp").mkdir(parents=True)
    (repo / "static" / "css").mkdir(parents=True)
    return repo


def _state(shots, work_id, before=None, after=None):
    d = Path(shots) / work_id
    d.mkdir(parents=True, exist_ok=True)
    body = {"work_id": work_id}
    if before is not None:
        body["before"] = {"at": "2026-08-20T00:00:00", "files": before}
    if after is not None:
        body["after"] = {"at": "2026-08-20T00:10:00", "files": after}
    (d / "state.json").write_text(json.dumps(body), encoding="utf-8")


def test_which_files_count_as_visual():
    yes = ["e:/app/myapp/templates/myapp/list.html", "e:/app/static/css/app.css",
           "e:/app/static/js/tickets.js", "e:/app/x/static/y/a.scss"]
    no = ["e:/app/myapp/models.py", "e:/app/myapp/services.py",
          "e:/app/myapp/migrations/0002_x.py", "e:/app/myapp/tests/test_x.py",
          "e:/app/myapp/templates/myapp/../../tests/test_page.html",
          "e:/app/staticfiles/css/app.css", "e:/app/venv/lib/site-packages/x/static/a.css",
          "e:/app/README.md"]
    for p in yes:
        ck("visual: %s" % p.rsplit("/", 1)[-1], vg.is_visual(p), p)
    for p in no:
        ck("not visual: %s" % p.rsplit("/", 2)[-1], not vg.is_visual(p), p)


def test_edit_without_ticket_work_is_never_gated():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo(tmp)
        f = repo / "myapp" / "templates" / "myapp" / "list.html"
        f.write_text("<div>x</div>", encoding="utf-8")
        code, out = run_gate({"tool_name": "Edit", "session_id": "s-none",
                              "tool_input": {"file_path": str(f)}}, Path(tmp) / "shots")
        ck("no work_id: a visual edit is ALLOWED (HUD/brain work must not be gated)",
           code == 0 and out is None, f"{code} {out}")


def test_edit_on_a_ticket_without_a_before_is_denied_with_the_command():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo(tmp)
        f = repo / "myapp" / "templates" / "myapp" / "list.html"
        f.write_text("<div>x</div>", encoding="utf-8")
        shots = Path(tmp) / "shots"
        code, out = run_gate({"tool_name": "Edit", "session_id": "s1",
                              "tool_input": {"file_path": str(f)}}, shots, work_env="w1")
        hso = (out or {}).get("hookSpecificOutput") or {}
        reason = hso.get("permissionDecisionReason") or ""
        ck("ticket + no before: DENIED", hso.get("permissionDecision") == "deny", str(out)[:200])
        ck("deny names the shoot command with before, the repo, the file and the work id",
           "shoot.py before" in reason and "myapp/templates/myapp/list.html" in reason
           and "w1" in reason, reason[:300])
        ck("deny explains the missing repo config (no visual-diff.json here)",
           "needs_config" in reason and "AskUserQuestion" in reason, reason[:300])
        # a NON-visual file on the same ticket is untouched
        code, out = run_gate({"tool_name": "Edit", "session_id": "s1",
                              "tool_input": {"file_path": str(repo / "myapp" / "models.py")}},
                             shots, work_env="w1")
        ck("ticket + models.py: ALLOWED (implementation without UI is out of scope)",
           code == 0 and out is None, f"{code} {out}")


def test_a_covered_file_is_allowed_and_a_sibling_is_not():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo(tmp)
        a = repo / "myapp" / "templates" / "myapp" / "list.html"
        b = repo / "myapp" / "templates" / "myapp" / "detail.html"
        a.write_text("x", encoding="utf-8")
        b.write_text("y", encoding="utf-8")
        shots = Path(tmp) / "shots"
        _state(shots, "w2", before=["myapp/templates/myapp/list.html"])
        code, out = run_gate({"tool_name": "Edit", "session_id": "s2",
                              "tool_input": {"file_path": str(a)}}, shots, work_env="w2")
        ck("covered by before: ALLOWED", code == 0 and out is None, f"{code} {out}")
        code, out = run_gate({"tool_name": "Write", "session_id": "s2",
                              "tool_input": {"file_path": str(b)}}, shots, work_env="w2")
        ck("a SECOND visual file still needs its own before (its original is still on screen)",
           ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision") == "deny", str(out)[:160])


def test_commit_checkpoint():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "app"
        (repo / "myapp" / "templates" / "myapp").mkdir(parents=True)
        (repo / "myapp" / "templates" / "myapp" / "list.html").write_text("v1", encoding="utf-8")
        (repo / "myapp" / "models.py").write_text("x = 1", encoding="utf-8")
        for args in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"]):
            subprocess.run(args, cwd=str(repo), capture_output=True, timeout=60)
        shots = Path(tmp) / "shots"
        payload = {"tool_name": "Bash", "session_id": "s3", "cwd": str(repo),
                   "tool_input": {"command": "git add -A && git commit -m 'x'"}}

        # 1. nothing changed yet -> allowed
        _state(shots, "w3", before=["myapp/templates/myapp/list.html"])
        code, out = run_gate(payload, shots, work_env="w3")
        ck("commit with no visual change in the diff: ALLOWED", code == 0 and out is None, f"{code} {out}")

        # 2. the template changed, before exists, no after -> denied
        (repo / "myapp" / "templates" / "myapp" / "list.html").write_text("v2", encoding="utf-8")
        code, out = run_gate(payload, shots, work_env="w3")
        reason = (((out or {}).get("hookSpecificOutput")) or {}).get("permissionDecisionReason") or ""
        ck("commit with a changed template and no after: DENIED",
           (((out or {}).get("hookSpecificOutput")) or {}).get("permissionDecision") == "deny", str(out)[:160])
        ck("deny names shoot after + the work id", "shoot.py after" in reason and "w3" in reason, reason[:200])

        # 3. after covers it -> allowed
        _state(shots, "w3", before=["myapp/templates/myapp/list.html"],
               after=["myapp/templates/myapp/list.html"])
        code, out = run_gate(payload, shots, work_env="w3")
        ck("commit after the AFTER capture: ALLOWED", code == 0 and out is None, f"{code} {out}")

        # 4. only a python file changed -> allowed even with a before on record
        _state(shots, "w3", before=["myapp/templates/myapp/list.html"])
        (repo / "myapp" / "templates" / "myapp" / "list.html").write_text("v1", encoding="utf-8")
        (repo / "myapp" / "models.py").write_text("x = 2", encoding="utf-8")
        code, out = run_gate(payload, shots, work_env="w3")
        ck("commit of a NON-visual change: ALLOWED", code == 0 and out is None, f"{code} {out}")


def test_it_fails_open_on_garbage():
    with tempfile.TemporaryDirectory() as tmp:
        for payload in ({}, {"tool_name": "Edit"}, {"tool_name": "Edit", "tool_input": {"file_path": 123}},
                        {"tool_name": "Bash", "tool_input": {}}):
            code, out = run_gate(payload, Path(tmp) / "shots", work_env="w9")
            ck("fail open: %s" % json.dumps(payload)[:40], code == 0 and out is None, f"{code} {out}")


def main() -> int:
    for fn in (test_which_files_count_as_visual,
               test_edit_without_ticket_work_is_never_gated,
               test_edit_on_a_ticket_without_a_before_is_denied_with_the_command,
               test_a_covered_file_is_allowed_and_a_sibling_is_not,
               test_commit_checkpoint,
               test_it_fails_open_on_garbage):
        try:
            fn()
        except Exception as exc:                                # noqa: BLE001
            ck(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
