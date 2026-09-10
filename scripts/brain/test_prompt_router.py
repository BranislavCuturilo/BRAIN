#!/usr/bin/env python3
"""Proof that the router names agents that FIT, and stops naming ones it lost.

Two defects, both measured in one conversation about token accounting:

  * `dj-update-view` was suggested THREE times, on the bare words "edit" and
    "izmene". Every `dj-*` pattern is keyed on a verb, and those verbs are
    everyday words in this repo's own working language. `screenshot-reader`
    came up too, for a prompt with no image.

    The first fix required a Django NOUN beside the verb and broke two eval
    cases immediately -- a real request names a BUSINESS object ("trebovanje",
    "lokacija"), and that vocabulary has no end. Both of those prompts are
    pinned below, because the gate is now inverted and could regress back.
  * Nothing suppressed a suggestion that had already been declined, so the
    same wrong name arrived every turn. A router that is wrong that often
    teaches the main loop to skip reading it -- and only 10 of 37 sessions
    delegated at all.

Driven through the REAL interface: a JSON payload on stdin, JSON on stdout.
Importing `main()` would not prove the hook works. No database, no network.

  python scripts/brain/test_prompt_router.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTER = HERE / "prompt_router.py"

FAILS: list[str] = []
TMP: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def route(prompt: str, transcript: str | None = None) -> list[str]:
    """The agent names the hook actually offers for this prompt."""
    payload: dict = {"prompt": prompt}
    if transcript:
        payload["transcript_path"] = transcript
    proc = subprocess.run([sys.executable, str(ROUTER)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise AssertionError(f"router exited {proc.returncode}: {proc.stderr}")
    if not proc.stdout.strip():
        return []
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    marker = "Agents that fit this task: "
    if marker not in ctx:
        return []
    tail = ctx.split(marker, 1)[1]
    return [n.strip() for n in tail.split(". Delegating", 1)[0].split(",")]


def transcript(prompts: list[str], launched: list[str]) -> str:
    """A fake session: what the human typed, and what was actually launched."""
    lines = []
    for i, p in enumerate(prompts):
        lines.append(json.dumps({
            "type": "user", "timestamp": "2026-09-10T10:00:00.000Z",
            "message": {"role": "user", "content": p}, "uuid": f"u{i}"}))
    for i, a in enumerate(launched):
        lines.append(json.dumps({
            "type": "assistant", "timestamp": "2026-09-10T10:01:00.000Z",
            "message": {"id": f"msg_{i}", "content": [
                {"type": "tool_use", "id": f"tu{i}", "name": "Agent",
                 "input": {"subagent_type": f"brain:{a}"}}]}}))
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8")
    fh.write("\n".join(lines) + "\n")
    fh.close()
    TMP.append(fh.name)
    return fh.name


# --- a verb alone must not summon a Django agent ---------------------------
# These three are verbatim from the conversation that exposed the defect.
a1 = route("da li je moguce zameniti edit/write tool koji koristi claude tako "
           "da gemini api sumira napisan kod, optimizacija input tokena")
ck("token-accounting prompt does NOT get dj-update-view",
   "dj-update-view" not in a1)
ck("...but still gets the agent that does fit", "optimizer" in a1)

a2 = route("gemini api da procita fajl, izdvoji bitno, pa claude napravi plan "
           "i upisao izmene")
ck('"izmene" alone does NOT get dj-update-view', "dj-update-view" not in a2)
ck("...while planner still fits", "planner" in a2)

# --- but real Django work must still route --------------------------------
a3 = route("izmeni formu za unos artikala")
ck('a real edit request still gets dj-update-view', "dj-update-view" in a3)

a4 = route("izmeni ekran sa listom radnih naloga")
ck('...and so does one phrased about a screen', "dj-update-view" in a4)

# The first attempt at this gate required a Django NOUN next to the verb, and
# these two prompts broke it on the spot: both name a BUSINESS object, which is
# the vocabulary a real request actually uses. They are the reason the test is
# inverted to suppress tooling prompts instead. Verbatim from evals/fanout.yaml.
a3b = route("dodaj dugme da rukovodilac moze da odobri ili odbije trebovanje")
ck("a business-object prompt still reaches dj-action-view",
   "dj-action-view" in a3b)

a3c = route("treba da se moze obrisati lokacija iz sifarnika")
ck("...and dj-delete-view", "dj-delete-view" in a3c)

# The eval this must not break: a class name has no word boundary before
# "Service" (evals/router.yaml, "a service-layer report is caught without a
# word boundary").
a5 = route("DeadlineService racuna pogresan rok")
ck("DeadlineService still routes to dj-service", "dj-service" in a5)

# --- suppression: a name offered three times and never taken --------------
declined = transcript(
    prompts=["nadji gde je definisan Order",
             "nadji sve pozive te funkcije",
             "pretrazi gde se koristi tenant_id"],
    launched=[])
a6 = route("nadji gde se pravi faktura", declined)
ck("an agent declined in 3 earlier prompts is no longer offered",
   "scout" not in a6)

# --- but one that was actually launched keeps being offered ---------------
taken = transcript(
    prompts=["nadji gde je definisan Order",
             "nadji sve pozive te funkcije",
             "pretrazi gde se koristi tenant_id"],
    launched=["scout"])
a7 = route("nadji gde se pravi faktura", taken)
ck("an agent that WAS launched stays eligible", "scout" in a7)

# --- and a fresh session suppresses nothing -------------------------------
a8 = route("nadji gde se pravi faktura", transcript(prompts=[], launched=[]))
ck("a session with no history suppresses nothing", "scout" in a8)

# --- the WIDE branch's advice is only true while the grant holds ------------
# The router now tells the caller it can hand a wide job to brain:orchestrator.
# That advice is worthless if the orchestrator cannot launch anything, which is
# exactly the state the brain was in until 2026-09-10: `Agent` sat in its own
# `disallowedTools` while the brief blamed the harness, and 21 of 55 agents
# were never once invoked as a result. Pinned here because the router case in
# evals/fanout.yaml only proves the WORD appears, not that it can act.
brief = (HERE.parent.parent / "agents" / "orchestrator.md").read_text(
    encoding="utf-8")
front = brief.split("---", 2)[1] if brief.count("---") >= 2 else ""
tools_line = next((l for l in front.splitlines()
                   if l.startswith("tools:")), "")
denied_line = next((l for l in front.splitlines()
                    if l.startswith("disallowedTools:")), "")
ck("orchestrator is granted the Agent tool", "Agent" in tools_line)
ck("orchestrator does not also deny it", "Agent" not in denied_line)

for _p in TMP:
    try:
        Path(_p).unlink()
    except OSError:
        pass

print()
print(f"{len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
