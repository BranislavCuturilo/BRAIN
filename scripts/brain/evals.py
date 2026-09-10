#!/usr/bin/env python3
"""Regression tests for the BRAIN ITSELF -- the skills, hooks and routing.

**The hole this fills.** `scripts/**/test_*.py` proves the *scripts* work.
Nothing proved the *brain* works. A skill can be rewritten into something that
no longer fires, a hook's glob can stop matching the file it was written for,
a rule can quietly come to exist in two layers -- and every one of those is
invisible until a real ticket goes wrong months later.

**Two tiers, and the split is deliberate.** The same instinct that runs Gemini
over the bulk of the ticket queue and saves Claude for the hard ones applies
here:

  static      free, deterministic, no model. Drives the real hooks through
              their real stdin/stdout contract and reads the real files.
              Runs on every commit. Catches most config regressions.
  behaviour   spends tokens: runs `claude -p` against a fixture and asserts on
              what it actually wrote. The only tier that can prove a rule
              changed the OUTCOME. Opt-in, because it costs money.

**Every case must cite an origin.** A rule here is only worth testing if it
traces to something that actually broke -- the same law the skills follow. A
case with no `origin` is a case written from general knowledge, and it fails
validation rather than passing silently.

  evals.py                     run the static tier
  evals.py --behaviour         run the paid tier too
  evals.py --kind gate         one kind only
  evals.py -k by-pk            cases whose name or file matches
  evals.py --quiet             failures only; exit 1 if any (CI, hooks)
  evals.py --record            append the run to evals/results.jsonl
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
EVALS = ROOT / "evals"
RESULTS = EVALS / "results.jsonl"
KINDS = ("gate", "router", "hook", "rule", "behaviour")
BEHAVIOUR_TIMEOUT = 600


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_cases(pattern: str | None, kind: str | None) -> tuple[list[dict], list[str]]:
    """Return (cases, problems). A malformed case is a problem, never a skip."""
    try:
        import yaml
    except ImportError:
        return [], ["PyYAML is not installed -- `pip install pyyaml`"]

    cases, problems = [], []
    for path in sorted(EVALS.rglob("*.yaml")):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
        except Exception as exc:                                # noqa: BLE001
            problems.append(f"{rel}: will not parse -- {exc}")
            continue
        # A file may hold one case, a list of cases, or several YAML documents
        # of either. Flatten before validating so the shape of the file is not
        # a thing the author has to think about.
        flat: list = []
        for doc in docs:
            flat.extend(doc if isinstance(doc, list) else [doc])
        for doc in flat:
            if not isinstance(doc, dict):
                problems.append(f"{rel}: a case is {type(doc).__name__}, expected a mapping")
                continue
            doc["_file"] = rel
            if not doc.get("name"):
                problems.append(f"{rel}: a case has no `name`")
                continue
            if doc.get("kind") not in KINDS:
                problems.append(f"{rel}: {doc['name']!r} has kind "
                                f"{doc.get('kind')!r}, expected one of {', '.join(KINDS)}")
                continue
            # The law: a rule worth testing traces to something that broke.
            if not doc.get("origin"):
                problems.append(f"{rel}: {doc['name']!r} has no `origin` -- "
                                f"a case written from general knowledge is not evidence")
                continue
            if kind and doc["kind"] != kind:
                continue
            if pattern and pattern not in doc["name"] and pattern not in rel:
                continue
            cases.append(doc)
    return cases, problems


# --------------------------------------------------------------------------
# kind: gate -- drive require_skill.py through its real contract
# --------------------------------------------------------------------------

def run_gate(case: dict) -> tuple[bool, str]:
    spec = case.get("case") or {}
    path = spec.get("path")
    if not path:
        return False, "case.path is required"

    with tempfile.TemporaryDirectory() as tmp:
        # The gate reads loaded skills from a transcript, so a fake transcript
        # is how a case says "these skills were already invoked this session".
        transcript = Path(tmp) / "transcript.jsonl"
        lines = []
        for skill in spec.get("loaded") or []:
            lines.append(json.dumps({"message": {"content": [
                {"type": "tool_use", "name": "Skill", "input": {"skill": skill}}]}}))
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        payload = {
            "tool_name": spec.get("tool", "Edit"),
            "tool_input": {"file_path": path},
            "transcript_path": str(transcript),
            "cwd": spec.get("cwd") or tmp,
        }
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "brain" / "require_skill.py")],
            input=json.dumps(payload), capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace")

    out = (proc.stdout or "").strip()
    decision = "allow"
    named: list[str] = []
    if out:
        try:
            hook = json.loads(out).get("hookSpecificOutput") or {}
            decision = hook.get("permissionDecision", "allow")
            reason = hook.get("permissionDecisionReason", "")
            named = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", reason)
        except ValueError:
            return False, f"hook printed something that is not JSON: {out[:120]}"

    want = case.get("expect")
    if decision != want:
        return False, f"expected {want} for {path}, got {decision}"

    for skill in case.get("expect_skills") or []:
        if skill not in named:
            return False, (f"denied, but did not name {skill!r} as a way out "
                           f"(named: {', '.join(sorted(set(named))) or 'nothing'})")
    return True, f"{decision} for {path}"


# --------------------------------------------------------------------------
# kind: router -- drive prompt_router.py through its real contract
# --------------------------------------------------------------------------

def run_router(case: dict) -> tuple[bool, str]:
    prompt = (case.get("case") or {}).get("prompt")
    if not prompt:
        return False, "case.prompt is required"

    payload = {"prompt": prompt, "cwd": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "brain" / "prompt_router.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=30, encoding="utf-8", errors="replace")

    out = (proc.stdout or "").strip()
    context = ""
    if out:
        try:
            context = (json.loads(out).get("hookSpecificOutput") or {}).get(
                "additionalContext", "")
        except ValueError:
            return False, f"router printed something that is not JSON: {out[:120]}"

    missing = [w for w in (case.get("expect_names") or []) if w not in context]
    if missing:
        return False, (f"router did not name {', '.join(missing)} for this prompt. "
                       f"It said: {context[:200] or '(nothing)'}")
    for forbidden in case.get("expect_absent") or []:
        if forbidden in context:
            return False, f"router named {forbidden!r}, which is wrong for this prompt"
    if case.get("expect_names") is None and case.get("expect_absent") is None:
        return False, "a router case needs expect_names or expect_absent"
    return True, f"named {', '.join(case.get('expect_names') or []) or 'nothing wrong'}"


# --------------------------------------------------------------------------
# kind: hook -- does hooks.json still match the path it was written for
# --------------------------------------------------------------------------

def _if_matches(expr: str, path: str, tool: str) -> bool:
    """Evaluate an `if` clause like `Edit(**/*.py) | Write(**/*.py)`."""
    norm = path.replace("\\", "/").lstrip("./")
    for term in expr.split("|"):
        term = term.strip()
        m = re.fullmatch(r"(\w+)\((.+)\)", term)
        if not m:
            continue
        if m.group(1) != tool:
            continue
        glob = m.group(2).strip()
        bare = glob[3:] if glob.startswith("**/") else glob
        if any(fnmatch.fnmatch(norm, c) for c in {glob, bare, "**/" + bare}):
            return True
    return False


def run_hook(case: dict) -> tuple[bool, str]:
    spec = case.get("case") or {}
    path, tool = spec.get("path"), spec.get("tool", "Edit")
    if not path:
        return False, "case.path is required"

    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    fired: list[str] = []
    for group in hooks.get("hooks", {}).get("PreToolUse", []):
        for hook in group.get("hooks", []):
            expr = hook.get("if")
            if expr and _if_matches(expr, path, tool):
                fired.append(hook.get("command", ""))

    blob = " ".join(fired)
    missing = [w for w in (case.get("expect_names") or []) if w not in blob]
    if missing:
        return False, (f"no reminder mentioning {', '.join(missing)} fires for {path} "
                       f"({len(fired)} hook(s) matched)")
    return True, f"{len(fired)} reminder(s) fire for {path}"


# --------------------------------------------------------------------------
# kind: rule -- the routing law, checked against the files
# --------------------------------------------------------------------------

def run_rule(case: dict) -> tuple[bool, str]:
    spec = case.get("case") or {}
    phrase = spec.get("phrase")
    if not phrase:
        return False, "case.phrase is required"

    rx = re.compile(phrase, re.IGNORECASE | re.MULTILINE)
    hits: list[str] = []
    texts: dict[str, str] = {}
    for path in sorted(ROOT.glob("skills/**/*.md")):
        # README.md is prose for a human reader by convention in this repo
        # ("This file is for you, not for Claude"), not a rule file.
        if path.name.lower() == "readme.md":
            continue
        text = path.read_text(encoding="utf-8")
        if rx.search(text):
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
            hits.append(rel)
            texts[rel] = text

    owner = case.get("expect_owner")
    if owner:
        if not any(h.startswith(f"skills/{owner}/") for h in hits):
            return False, (f"{owner} does not carry this rule "
                           f"(found in: {', '.join(hits) or 'nowhere'})")
        # One source of truth. But the law is "cross-reference by name, never
        # restate" -- so a file that mentions the rule AND names the owner is
        # doing exactly what it should. Only a mention that claims the rule as
        # its own is the defect.
        allowed = case.get("also_allowed_in") or []
        strays = []
        for h in hits:
            if h.startswith(f"skills/{owner}/"):
                continue
            if any(h.startswith(f"skills/{a}/") for a in allowed):
                continue
            if re.search(rf"\b{re.escape(owner)}\b", texts[h]):
                continue                    # cites the owner: a cross-reference
            strays.append(h)
        if strays:
            return False, (f"owned by {owner} but restated without citing it in: "
                           f"{', '.join(strays)} -- cross-reference, do not restate")

    want = case.get("expect_files")
    if want is not None and sorted(hits) != sorted(want):
        return False, f"expected it in {want}, found it in {hits}"
    if owner is None and want is None:
        return False, "a rule case needs expect_owner or expect_files"
    return True, f"owned by {owner or hits}"


# --------------------------------------------------------------------------
# kind: behaviour -- the only tier that proves a rule changed the outcome
# --------------------------------------------------------------------------

def run_behaviour(case: dict) -> tuple[bool, str]:
    spec = case.get("case") or {}
    prompt = spec.get("prompt")
    if not prompt:
        return False, "case.prompt is required"

    fixture = spec.get("fixture")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "work"
        if fixture:
            src = EVALS / "fixtures" / fixture
            if not src.is_dir():
                return False, f"fixture {fixture!r} does not exist at evals/fixtures/{fixture}"
            import shutil
            shutil.copytree(src, work)
        else:
            work.mkdir()

        # Resolve the executable rather than trusting the bare name. What sits
        # on PATH here is `claude.CMD`; plain `claude` is an extensionless
        # shell script, which Python's subprocess cannot exec on Windows. So
        # this tier -- the ONLY one that proves a rule changed an outcome
        # rather than merely fired -- had never once run, and it reported that
        # as two failing rules rather than as a tier that could not start.
        # `shutil.which` applies PATHEXT and finds the .CMD.
        import shutil                                           # noqa: PLC0415
        exe = shutil.which("claude") or shutil.which("claude.cmd")
        if not exe:
            return False, ("the `claude` CLI is not on PATH -- this tier "
                           "CANNOT RUN, which is not the same as a rule "
                           "failing, and must not be read as one")
        cmd = [exe, "-p", prompt,
               "--permission-mode", "acceptEdits",
               "--output-format", "text"]
        if spec.get("model"):
            cmd += ["--model", spec["model"]]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, cwd=str(work),
                timeout=BEHAVIOUR_TIMEOUT, encoding="utf-8", errors="replace",
                env={**os.environ, "CLAUDE_EVAL": "1"})
        except (FileNotFoundError, OSError) as exc:
            return False, (f"the `claude` CLI could not be executed at {exe} "
                           f"({exc}) -- the tier cannot run")
        except subprocess.TimeoutExpired:
            return False, f"timed out after {BEHAVIOUR_TIMEOUT}s"

        # Assert on what was WRITTEN, not on what the transcript claimed. A
        # session that says it added the scope filter and did not is exactly
        # the failure this tier exists to catch.
        written = "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(work.rglob("*"))
            if p.is_file() and p.stat().st_size < 400_000)
        # NOT `written + stdout`. Appending the transcript let an agent that
        # merely SAID "locations_for" satisfy a must_match without writing it
        # -- precisely the "what the transcript claimed" failure the comment
        # above exists to prevent. The transcript is kept separately, because
        # what the agent SAID while getting it wrong is the useful half of a
        # failure: pasted verbatim into the skill, it turns an authored rule
        # into one with real provenance.
        haystack = written
        said = (proc.stdout or "").strip()

    def why(verdict: str) -> str:
        """The failure, with the agent's own reasoning attached.

        Borrowed from obra/superpowers' skill-testing loop, which is the one
        idea in that repo worth having: dispatch an agent into a real
        scenario, and when it breaks the rule, capture its RATIONALISATION
        verbatim. A rule that answers a sentence an agent actually produced
        has provenance; a rule invented from general knowledge reads exactly
        the same in the file and is the thing this brain's constitution
        rejects. This is the only way to manufacture that provenance on
        demand rather than waiting for the next incident.
        """
        if not said:
            return verdict
        tail = said[-700:]
        return (f"{verdict}\n     --- what it said while doing it "
                f"(paste the excuse into the skill, verbatim) ---\n     "
                + "\n     ".join(tail.splitlines()[-8:]))

    for rx in case.get("must_match") or []:
        if not re.search(rx, haystack, re.MULTILINE):
            return False, why(f"nothing WRITTEN matches {rx!r}")
    for rx in case.get("must_not_match") or []:
        m = re.search(rx, haystack, re.MULTILINE)
        if m:
            return False, why(f"wrote {m.group(0)!r}, which {rx!r} forbids")
    return True, "wrote what the rule requires"


RUNNERS = {"gate": run_gate, "router": run_router, "hook": run_hook,
           "rule": run_rule, "behaviour": run_behaviour}


# --------------------------------------------------------------------------

def main() -> int:
    args = sys.argv[1:]
    quiet = "--quiet" in args
    record = "--record" in args
    behaviour = "--behaviour" in args
    kind = None
    if "--kind" in args:
        i = args.index("--kind")
        kind = args[i + 1] if i + 1 < len(args) else None
    pattern = None
    if "-k" in args:
        i = args.index("-k")
        pattern = args[i + 1] if i + 1 < len(args) else None

    if not EVALS.is_dir():
        print("no evals/ directory")
        return 1

    cases, problems = load_cases(pattern, kind)
    for p in problems:
        print(f"BAD  {p}")

    passed = failed = skipped = 0
    failures: list[tuple[dict, str]] = []

    for case in cases:
        if case["kind"] == "behaviour" and not behaviour:
            skipped += 1
            continue
        try:
            ok, detail = RUNNERS[case["kind"]](case)
        except Exception as exc:                                # noqa: BLE001
            ok, detail = False, f"the case itself raised: {exc}"
        if ok:
            passed += 1
            if not quiet:
                print(f"ok   [{case['kind']:<9}] {case['name']}")
        else:
            failed += 1
            failures.append((case, detail))
            print(f"FAIL [{case['kind']:<9}] {case['name']}")
            print(f"     {detail}")
            print(f"     origin: {case['origin']}")
            print(f"     case:   {case['_file']}")

    total = passed + failed
    rate = (passed / total) if total else 0.0
    if not quiet or failed or problems:
        print()
        print(f"{passed}/{total} passed"
              + (f", {skipped} behaviour case(s) skipped (--behaviour to run)" if skipped else "")
              + (f", {len(problems)} malformed" if problems else ""))

    if record and total:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        with RESULTS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "passed": passed, "failed": failed, "skipped": skipped,
                "rate": round(rate, 3), "behaviour": behaviour,
                "failing": [c["name"] for c, _ in failures],
            }) + "\n")

    try:
        import verified                                         # noqa: PLC0415
        verified.record("evals", not (failed or problems),
                        f"{passed}/{total} passed"
                        + (f", {len(problems)} malformed" if problems else ""),
                        scope="behaviour" if behaviour else "static")
    except Exception:                                           # noqa: BLE001
        pass

    return 1 if (failed or problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
