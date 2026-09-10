#!/usr/bin/env python3
"""Proof that the session record says what actually happened, and only that.

The expensive failure here is not a missing field -- it is an INVENTED one. A
`type: "user"` turn is also every tool result, every injected skill body and
every compaction summary, all of them carrying real prose. Counting one of
those writes a request the operator never made into a permanent record that
`archivist` will later reason from, and nothing downstream can tell it apart
from a real one.

Caught on the first smoke run: "Base directory for this skill: ..." was
recorded as something the operator had asked for.

  python scripts/brain/test_episode.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import episode                                                  # noqa: E402

failures: list[str] = []


def ck(label: str, ok: bool) -> None:
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)


def user(text: str, *, meta: bool = False, source: str | None = "user") -> str:
    rec: dict = {"type": "user", "timestamp": "2026-08-31T10:00:00Z",
                 "message": {"content": [{"type": "text", "text": text}]}}
    if meta:
        rec["isMeta"] = True
    if source:
        rec["promptSource"] = source
    return json.dumps(rec)


_MSG = [0]


def assistant(blocks: list[dict]) -> str:
    """One assistant message, written the way Claude Code actually writes it.

    ONE RECORD PER BLOCK, every record carrying the same `message.id`. This
    used to pack every block into a single record, which is a shape the
    runtime never emits -- and that hid a real defect: `episode.py` reported
    a batch of two agents as two solo launches into `journal/episodes/`,
    where `archivist` reads it, while the assertion below stayed green.
    Verified 2026-09-10 against `transcript.widths()` on the same file.
    """
    _MSG[0] += 1
    mid = "msg_%03d" % _MSG[0]
    return "\n".join(
        json.dumps({"type": "assistant", "timestamp": "2026-08-31T10:01:00Z",
                    "message": {"id": mid, "content": [blk]}})
        for blk in blocks)


def tool(name: str, **inp) -> dict:
    return {"type": "tool_use", "name": name, "input": inp}


def write(lines: list[str]) -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8")
    fh.write("\n".join(lines) + "\n")
    fh.close()
    return fh.name


def main() -> int:
    # --- the record carries what happened ---------------------------------
    path = write([
        user("popravi izvestaj za DEMO#70103"),
        assistant([tool("Skill", skill="brain:craft-security"),
                   tool("Edit", file_path="/repo/apps/warehouse/views.py")]),
        assistant([tool("Agent", subagent_type="brain:security"),
                   tool("Agent", subagent_type="brain:qa")]),
        assistant([tool("Bash", command="python scripts/brain/evals.py")]),
    ])
    r = episode.build(path, "abcdef1234", "/proj/warehouse")
    ck("records the skills that loaded", r["skills"] == {"craft-security": 1})
    ck("records both agents from one message",
       r["agents"] == {"security": 1, "qa": 1})
    ck("records the file touched", "views.py" in r["files_touched"])
    ck("fan-out width 2 is recorded as a BATCH, not two launches",
       r["fanout"] == {"launches": 1, "max_width": 2, "solo": 0})
    ck("notices which tool verified the work", r["verified_with"] == ["evals.py"])
    ck("extracts the ticket reference", r["tickets"] == ["DEMO#70103"])
    ck("keeps the prompt actually typed",
       len(r["asked"]) == 1 and "DEMO#70103" in r["asked"][0])

    # --- and NOTHING else -------------------------------------------------
    path = write([
        user("stvarni zahtev"),
        user("Base directory for this skill: C:/x/skills/ui-bootstrap\n# UI craft",
             meta=True),
        user("This session is being continued from a previous conversation...",
             source=None),
        assistant([tool("Skill", skill="brain:ui-bootstrap")]),
    ])
    r = episode.build(path, "s", "/p")
    ck("an injected skill body is NOT recorded as a request",
       all("Base directory" not in a for a in r["asked"]))
    ck("a compaction summary is NOT recorded as a request",
       all("continued from a previous" not in a for a in r["asked"]))
    ck("the one real prompt survives (presence twin for the two absences)",
       r["asked"] == ["stvarni zahtev"])

    # --- solo launches are visible as such --------------------------------
    path = write([
        user("q"),
        assistant([tool("Agent", subagent_type="brain:scout")]),
        assistant([tool("Agent", subagent_type="brain:scout")]),
        assistant([tool("Agent", subagent_type="brain:scout")]),
    ])
    r = episode.build(path, "s", "/p")
    ck("three separate launches record as 3 solo, width 1",
       r["fanout"] == {"launches": 3, "max_width": 1, "solo": 3})

    # --- a subagent's own transcript must not be folded in ----------------
    side = json.dumps({"type": "assistant", "isSidechain": True,
                       "message": {"content": [tool("Edit", file_path="/x/a.py")]}})
    path = write([user("q"), assistant([tool("Skill", skill="brain:brain")]), side])
    r = episode.build(path, "s", "/p")
    ck("sidechain edits are not counted as this session's", not r["files_touched"])

    # --- a question is not an episode -------------------------------------
    ck("a session that did nothing records NOTHING",
       episode.build(write([user("koliko je sati")]), "s", "/p") is None)

    # --- never the thing that fails ---------------------------------------
    ck("a missing transcript returns None rather than raising",
       episode.build("/nonexistent/x.jsonl", "s", "/p") is None)
    ck("unparseable lines do not raise",
       episode.build(write([user("q"), "{broken",
                            assistant([tool("Skill", skill="brain:brain")])]),
                     "s", "/p") is not None)

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("ok - the record is honest, and records nothing that was not asked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
