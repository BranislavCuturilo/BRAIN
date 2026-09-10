#!/usr/bin/env python3
"""One assistant message is MANY transcript records -- group them by message.id.

**The bug this exists to prevent.** Claude Code writes one JSONL record per
content block, not per assistant message. Five `Edit` calls emitted in a single
turn are written as five `type: "assistant"` records, each holding exactly one
`tool_use` block, all carrying the SAME `message.id`.

Every counter that grouped per RECORD therefore measured a constant. Across the
2,013 tool-calling records in this project's transcripts, **not one held two
blocks** -- so "calls in this message" was 1 by construction, never by
observation:

- `fanout_nudge.py` tested `any(w > 1)` to stay quiet. That is unreachable, so
  it announced "none launched together" in every session it ever ran, including
  the sessions that did batch.
- `delegation.py` reported "326 of 326 launches were ONE agent alone (100%)".
  That number went into `ops-delegation`, `evals/fanout.yaml` and the router's
  own comments as an earned rule.

Regrouped by `message.id`, the same four transcripts give
`{1: 1622, 2: 116, 3: 22, 4: 9, 5: 6, 6: 2, 7: 1, 8: 1}` for all tools, and
`{1: 27, 2: 3, 3: 2, 5: 1}` for agents -- six real agent batches, one of them
five researchers at once. Batching had been happening the whole time and
nothing in the brain could see it.

So: **never count `tool_use` blocks per record.** Come through here.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterator

#: The two names Claude Code has used for the subagent tool.
AGENT_TOOLS = ("Task", "Agent")


def epoch(stamp: Any) -> float | None:
    """ISO-8601 timestamp -> epoch seconds, or None when unparseable."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def agent_name(block: dict) -> str:
    """The agent type a Task/Agent block launched, without its plugin prefix."""
    inp = block.get("input") or {}
    raw = inp.get("subagent_type") or inp.get("agentType") or ""
    return str(raw).split(":")[-1]


def messages(path: str) -> Iterator[tuple[str, list[dict], float | None]]:
    """Assistant messages in order, each with ALL its tool_use blocks.

    Yields ``(message_id, blocks, first_seen_epoch)``. Records are grouped by
    ``message.id`` because Claude Code splits one message across many records
    (see the module docstring). Sidechain records are skipped -- those are a
    subagent's own transcript, not this session's.

    Messages with no tool_use blocks are not yielded.
    """
    # Insertion-ordered, so messages come back in the order they were started
    # even if the runtime ever interleaves records from two of them.
    groups: dict[str, list[dict]] = {}
    when: dict[str, float | None] = {}
    seq = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("isSidechain") or rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                blocks = [b for b in content
                          if isinstance(b, dict) and b.get("type") == "tool_use"]
                if not blocks:
                    continue
                # Fall back to the record uuid, then to a running counter, so a
                # transcript without message.id degrades to the old per-record
                # behaviour rather than collapsing blocks into one giant batch.
                # NOT `id(rec)`: CPython reuses an object id once the previous
                # dict is freed, which silently merged unrelated messages and
                # made a nine-launch queue read as batched. test_fanout_nudge
                # caught it; keep the counter.
                seq += 1
                mid = str(msg.get("id") or rec.get("uuid") or f"#rec{seq}")
                groups.setdefault(mid, []).extend(blocks)
                when.setdefault(mid, epoch(rec.get("timestamp")))
    except OSError:
        return
    for mid, blocks in groups.items():
        yield mid, blocks, when.get(mid)


def agent_batches(path: str) -> list[list[str]]:
    """Agent names launched per assistant MESSAGE. ``[[a], [b, c]]`` is one
    solo launch followed by a real batch of two."""
    out: list[list[str]] = []
    for _mid, blocks, _when in messages(path):
        names = [agent_name(b) for b in blocks
                 if b.get("name") in AGENT_TOOLS]
        names = [n for n in names if n]
        if names:
            out.append(names)
    return out


def widths(path: str) -> list[int]:
    """Agent calls per assistant message, in order. ``[1, 1, 1]`` is a queue."""
    return [len(names) for names in agent_batches(path)]


def launched(path: str) -> set[str]:
    """Every agent type this session actually launched."""
    return {n for names in agent_batches(path) for n in names}


def user_prompts(path: str) -> list[str]:
    """The human's own prompts, oldest first.

    Tool results are also ``type: "user"`` records, and hook output arrives as
    extra content on a real prompt -- neither is something the person typed, so
    both are excluded. Used to re-derive what a hook already said in this
    session instead of keeping a state file that would drift.
    """
    out: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("isSidechain") or rec.get("type") != "user":
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, str):
                    out.append(content)
                elif isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            out.append(str(b.get("text") or ""))
    except OSError:
        return []
    return [p for p in out if p.strip()]
