#!/usr/bin/env python3
"""Proof that the timing half pairs calls with results and never invents a saving.

Both bugs this pins were mine, made while writing it, and both reported a
plausible number instead of failing:

  * the cheap line pre-filter allowed `"assistant"` and `"tool_use"`. A
    `tool_result` is `type: "user"` and carries neither, so EVERY result was
    dropped before parsing and the report said 10,192 calls never returned.
  * `_epoch` was used without importing it. That one at least crashed.

The second property matters more than the first: when nothing was ever batched,
sequential and concurrent cost are equal BY CONSTRUCTION. A tool that presents
that as "no saving available" is lying by omission, so it has to say why.

  python scripts/brain/test_delegation.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import delegation                                                # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def use(ts: str, blocks: list[dict]) -> str:
    return json.dumps({"type": "assistant", "timestamp": ts,
                       "message": {"content": blocks}})


def result(ts: str, ids: list[str]) -> str:
    return json.dumps({"type": "user", "timestamp": ts, "message": {"content": [
        {"type": "tool_result", "tool_use_id": i, "content": "ok"} for i in ids]}})


def agent_block(tid: str, kind: str) -> dict:
    return {"type": "tool_use", "id": tid, "name": "Agent",
            "input": {"subagent_type": f"brain:{kind}"}}


def run(lines: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        proj.mkdir()
        (proj / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with mock.patch.object(delegation, "PROJECTS", Path(tmp)):
            return delegation.collect()


def run_meta(metas: list[tuple[str, dict]]) -> dict:
    """collect() over run records only. `metas` is (agent_id, meta dict).

    The agent's own id lives ONLY in the file name, which is why resolving a
    parent needs it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        (proj / "sess" / "subagents").mkdir(parents=True)
        (proj / "s.jsonl").write_text("\n", encoding="utf-8")
        for aid, meta in metas:
            (proj / "sess" / "subagents" / ("agent-" + aid + ".meta.json")
             ).write_text(json.dumps(meta), encoding="utf-8")
        with mock.patch.object(delegation, "PROJECTS", Path(tmp)):
            return delegation.collect()


def main() -> int:
    # --- a single call, 30 seconds -----------------------------------------
    r = run([
        use("2026-08-31T10:00:00.000Z", [agent_block("a1", "scout")]),
        result("2026-08-31T10:00:30.000Z", ["a1"]),
    ])
    ck("a tool_result is not dropped by the pre-filter",
       r["agent_wall_clock_s"] == 30.0)
    ck("the duration lands under its agent",
       r["agent_seconds"].get("scout") == [30.0])
    ck("nothing is reported as unreturned", r["unreturned_calls"] == 0)

    # --- two agents in ONE message: 20s and 50s ----------------------------
    # Sequential cost is the sum; one message costs the slowest.
    r = run([
        use("2026-08-31T10:00:00.000Z",
            [agent_block("b1", "security"), agent_block("b2", "qa")]),
        result("2026-08-31T10:00:20.000Z", ["b1"]),
        result("2026-08-31T10:00:50.000Z", ["b2"]),
    ])
    ck("a batch's sequential cost is the SUM", r["agent_wall_clock_s"] == 70.0)
    ck("a batch's real cost is the SLOWEST member", r["if_batched_s"] == 50.0)
    ck("width 2 is recorded as one launch", r["widths"] == {2: 1})

    # --- two agents in TWO messages: the queue -----------------------------
    r = run([
        use("2026-08-31T10:00:00.000Z", [agent_block("c1", "security")]),
        result("2026-08-31T10:00:20.000Z", ["c1"]),
        use("2026-08-31T10:00:20.000Z", [agent_block("c2", "qa")]),
        result("2026-08-31T10:01:10.000Z", ["c2"]),
    ])
    ck("solo launches cost the sum", r["agent_wall_clock_s"] == 70.0)
    # THE honest bit. Each message held one agent, so max == sum per batch and
    # the two totals are equal because nothing could have been saved, not
    # because the work was already optimal.
    ck("with nothing batched, the two totals are equal by construction",
       r["if_batched_s"] == 70.0)
    ck("and the shape says why: two launches of width 1", r["widths"] == {1: 2})

    # --- a call that never returns is NOT counted as zero ------------------
    r = run([
        use("2026-08-31T10:00:00.000Z", [agent_block("d1", "scout")]),
    ])
    ck("an unreturned call is counted, not silently zeroed",
       r["unreturned_calls"] == 1 and r["agent_wall_clock_s"] == 0.0)

    # --- non-agent tools are timed too, and kept separate ------------------
    r = run([
        use("2026-08-31T10:00:00.000Z",
            [{"type": "tool_use", "id": "e1", "name": "Bash", "input": {}}]),
        result("2026-08-31T10:00:05.000Z", ["e1"]),
    ])
    ck("a non-agent tool is timed", r["tool_seconds"].get("Bash") == 5.0)
    ck("a non-agent tool is not counted as agent wall-clock",
       r["agent_wall_clock_s"] == 0.0)

    # --- a subagent's own transcript must not double-count -----------------
    side = json.dumps({"type": "assistant", "isSidechain": True,
                       "timestamp": "2026-08-31T10:00:00.000Z",
                       "message": {"content": [agent_block("f1", "scout")]}})
    r = run([
        side,
        json.dumps({"type": "user", "isSidechain": True,
                    "timestamp": "2026-08-31T10:09:00.000Z",
                    "message": {"content": [
                        {"type": "tool_result", "tool_use_id": "f1"}]}}),
    ])
    ck("sidechain calls are not timed as this session's",
       r["agent_wall_clock_s"] == 0.0)

    # --- nesting is read from the keys the runtime actually writes --------
    # This reported "0 of 487 nested" for its whole life because it looked up
    # `depth`/`nestingDepth` and `parentAgentType`, and the runtime writes
    # `spawnDepth` and `parentAgentId`. That zero was then quoted in
    # ops-delegation and in the orchestrator's own brief as proof the harness
    # forbids nesting. Measured 2026-09-10 with the right keys: 33 of 496,
    # including 27 by an orchestrator on 2026-08-21 -- it had been working.
    r = run_meta([
        ("par1", {"agentType": "brain:orchestrator", "spawnDepth": 1}),
        ("kid1", {"agentType": "brain:scout", "spawnDepth": 2,
                  "parentAgentId": "par1"}),
        ("kid2", {"agentType": "brain:reviewer", "spawnDepth": 2,
                  "parentAgentId": "par1"}),
    ])
    ck("a nested run is counted from spawnDepth", r["nested_runs"] == 2)
    ck("the parent resolves from parentAgentId to its agent TYPE",
       r["by_parent"] == {"orchestrator": 2})
    ck("every run record is counted, nested or not", r["runs_with_meta"] == 3)

    r = run_meta([("only", {"agentType": "brain:scout", "spawnDepth": 1})])
    ck("depth 1 is not nesting", r["nested_runs"] == 0)

    r = run_meta([("old", {"agentType": "brain:scout", "depth": 2})])
    ck("a legacy `depth` key still counts, if a runtime writes one",
       r["nested_runs"] == 1)

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s)")
        return 1
    print("ok - pairs results, prices a batch honestly, never invents a saving")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
