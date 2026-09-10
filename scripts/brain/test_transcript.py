#!/usr/bin/env python3
"""Proof that a batch survives the transcript format.

The format is the whole point. Claude Code writes ONE RECORD PER BLOCK, so two
agents launched in a single assistant message arrive as two `type: "assistant"`
records that share a `message.id`. Counting blocks per record therefore returns
1 forever -- not as a measurement, as an arithmetic fact about the file.

That is what `fanout_nudge.py` and `delegation.py` both did, and it is why the
brain believed "326 of 326 launches were ONE agent alone (100%)" and told every
session so. The first case below is written in the real format and fails
against per-record counting, which is kept here as `per_record_widths` so the
bug cannot come back quietly.

No database, no network, no subprocess: every case is a temp file and a pure
function.

  python scripts/brain/test_transcript.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import transcript                                                # noqa: E402

FAILS: list[str] = []
TMP: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def rec(mid: str, tool: str, agent: str = "", *, sidechain: bool = False,
        ts: str = "2026-09-10T10:00:00.000Z", with_id: bool = True) -> str:
    """One transcript record: exactly one tool_use block, as Claude Code writes."""
    block: dict = {"type": "tool_use", "id": f"tu_{mid}_{tool}{agent}",
                   "name": tool, "input": {}}
    if agent:
        block["input"] = {"subagent_type": agent}
    msg: dict = {"content": [block]}
    if with_id:
        msg["id"] = mid
    out: dict = {"type": "assistant", "timestamp": ts, "message": msg}
    if sidechain:
        out["isSidechain"] = True
    return json.dumps(out)


def write(lines: list[str]) -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8")
    fh.write("\n".join(lines) + "\n")
    fh.close()
    TMP.append(fh.name)
    return fh.name


def per_record_widths(path: str) -> list[int]:
    """The OLD, broken counter. Kept so the bug cannot return unnoticed."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("isSidechain") or r.get("type") != "assistant":
                continue
            c = (r.get("message") or {}).get("content") or []
            n = sum(1 for b in c if b.get("type") == "tool_use"
                    and b.get("name") in ("Task", "Agent"))
            if n:
                out.append(n)
    return out


# --- the case the old counter cannot see -----------------------------------
# One solo launch, then TWO agents in a single message (shared message.id).
batched = write([
    rec("msg_solo", "Agent", "scout"),
    rec("msg_batch", "Agent", "scout"),
    rec("msg_batch", "Agent", "researcher"),
])

ck("grouping by message.id sees the batch as width 2",
   transcript.widths(batched) == [1, 2])
ck("the old per-record counter reports a queue that never happened",
   per_record_widths(batched) == [1, 1, 1])
ck("agent_batches returns the names, not just the count",
   transcript.agent_batches(batched) == [["scout"], ["scout", "researcher"]])

# --- a genuine queue must still read as a queue -----------------------------
queued = write([
    rec("msg_a", "Agent", "scout"),
    rec("msg_b", "Agent", "reviewer"),
    rec("msg_c", "Agent", "qa"),
])
ck("three separate messages still read as [1, 1, 1]",
   transcript.widths(queued) == [1, 1, 1])

# --- non-agent tools must not be counted as agents --------------------------
mixed = write([
    rec("msg_x", "Edit"),
    rec("msg_x", "Edit"),
    rec("msg_y", "Agent", "scout"),
])
ck("Edit blocks are not counted as agent launches",
   transcript.widths(mixed) == [1])
ck("but messages() still exposes every tool block for other counters",
   [len(b) for _m, b, _w in transcript.messages(mixed)] == [2, 1])

# --- a subagent's own transcript is not this session's work ------------------
side = write([
    rec("msg_main", "Agent", "scout"),
    rec("msg_side", "Agent", "qa", sidechain=True),
])
ck("sidechain records are skipped", transcript.widths(side) == [1])

# --- degrade safely rather than collapsing everything into one batch --------
noid = write([
    rec("msg_1", "Agent", "scout", with_id=False),
    rec("msg_2", "Agent", "qa", with_id=False),
])
ck("without message.id it falls back to per-record, not one giant batch",
   transcript.widths(noid) == [1, 1])

# --- a missing file is silence, not a crash (a hook must never break a turn) -
ck("a missing transcript yields nothing instead of raising",
   transcript.widths(str(Path(tempfile.gettempdir()) / "nope-xyz-4f21.jsonl")) == [])

for _p in TMP:
    try:
        Path(_p).unlink()
    except OSError:
        pass

print()
print(f"{len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
