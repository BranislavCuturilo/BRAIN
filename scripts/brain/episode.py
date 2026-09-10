#!/usr/bin/env python3
"""SessionEnd: write down what this session actually did, without being asked.

**Why.** The brain's design says work is remembered by `capture` writing a rule
and `archivist` writing a retrospective. Measured across 42 sessions: `journal/`
held **zero** entries and `archivist` had been invoked **zero** times. The
design was not wrong -- it was never triggered, because both need someone to
stop and call them, and that is exactly what does not happen at the end of a
long session.

**What this is, and what it is not.** It is not a retrospective; a script cannot
write why a decision was made. It is the RAW MATERIAL: which tickets, which
agents, which skills, which files, what failed. `archivist` then writes the
"why" from a record instead of from memory that is already gone -- and can do so
days later, which is the point. The judgement stays human; only the bookkeeping
is automatic.

Deterministic, stdlib only, no model, no embeddings. The ticket store holds 50
tickets, not 50,000: the reason ruflo reaches for a vector database does not
apply at this size, and a JSONL file that `grep` can read outlives any index.

ONE FILE PER DEVICE, for the reason `scripts/tickets/worklog.py` already gives:
this repo is used from two machines, and a single shared append-only file is
appended to on both and conflicts on every pull -- a line-level conflict nobody
can merge by hand, over data that is pure history. The device name comes from
the one definition of it, `store.device_id`.

  episode.py                 write one record from the transcript on stdin's payload
  episode.py --last 5        show recent episodes (every device, merged)
  episode.py --path <file>   write from a specific transcript (for testing)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usage import _prompt_text                                  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "tickets"))
try:
    from store import device_id                                 # noqa: E402
except ImportError:                                             # pragma: no cover
    def device_id() -> str:                                     # type: ignore
        import socket
        return (socket.gethostname() or "device").lower()[:64]

EPISODES_DIR = ROOT / "journal" / "episodes"
MAX_BYTES = 40_000_000          # a very long transcript must not hang SessionEnd


def read_transcript(path: str) -> dict:
    """Walk one transcript once and return what the session actually did."""
    agents: Counter = Counter()
    skills: Counter = Counter()
    files: Counter = Counter()
    commands: list[str] = []
    errors: list[str] = []
    widths: list[int] = []
    # Claude Code writes ONE RECORD PER BLOCK, every record of a message
    # sharing `message.id`, so agent COUNT is correct per record but batch
    # WIDTH is not: two agents launched together arrive as two records and
    # read as two solo launches. Verified 2026-09-10 -- build() returned
    # max_width 1 for a real batch of two while transcript.widths() returned
    # [2] on the same file, and test_episode stayed green because its fixture
    # packed both blocks into one record. Only this quantity is per-message;
    # the agents, skills, files and commands Counters are totals and stay
    # per-record.
    agents_by_msg: dict[str, int] = {}
    prompts: list[str] = []
    first = last = None

    try:
        size = os.path.getsize(path)
    except OSError:
        return {}
    if size > MAX_BYTES:
        return {"truncated": True}

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("isSidechain"):
                    continue
                stamp = rec.get("timestamp")
                if stamp:
                    first = first or stamp
                    last = stamp

                content = (rec.get("message") or {}).get("content")

                if rec.get("type") == "user":
                    # Reuse the ONE implementation of "what did the person
                    # actually type", and the SAME discriminator usage.py
                    # already earned: an injected skill body and a compaction
                    # summary are both `type: user` with real prose in them, so
                    # guessing from the text invents a request that was never
                    # made. Injected content carries `isMeta`; a real prompt
                    # carries `promptSource`. Written without this first, and
                    # the first smoke test recorded "Base directory for this
                    # skill: ..." as something the operator had asked for.
                    if not (rec.get("isMeta") or not rec.get("promptSource")):
                        text = _prompt_text(content)
                        if text:
                            prompts.append(text[:300])
                if not isinstance(content, list):
                    continue

                if rec.get("type") == "user":
                    # Tool results carry the failures worth remembering.
                    for b in content:
                        if isinstance(b, dict) and b.get("is_error"):
                            body = b.get("content")
                            if isinstance(body, list):
                                body = " ".join(str(x.get("text", "")) for x in body
                                                if isinstance(x, dict))
                            errors.append(str(body)[:200])
                    continue

                if rec.get("type") != "assistant":
                    continue
                n_agents = 0
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name, inp = b.get("name"), (b.get("input") or {})
                    if name in ("Task", "Agent"):
                        n_agents += 1
                        key = inp.get("subagent_type") or inp.get("agentType")
                        if key:
                            agents[str(key).split(":")[-1]] += 1
                    elif name == "Skill" and inp.get("skill"):
                        skills[str(inp["skill"]).split(":")[-1]] += 1
                    elif name in ("Edit", "Write", "NotebookEdit"):
                        p = inp.get("file_path") or inp.get("notebook_path")
                        if p:
                            files[str(p).replace("\\", "/").split("/")[-1]] += 1
                    elif name in ("Bash", "PowerShell"):
                        cmd = str(inp.get("command", ""))[:120]
                        if cmd:
                            commands.append(cmd)
                if n_agents:
                    mid = str((rec.get("message") or {}).get("id") or "")
                    if mid:
                        agents_by_msg[mid] = agents_by_msg.get(mid, 0) + n_agents
                    else:
                        widths.append(n_agents)   # no id: degrade per-record
    except OSError:
        return {}

    widths.extend(agents_by_msg.values())

    return {
        "agents": dict(agents.most_common()),
        "skills": dict(skills.most_common()),
        "files": dict(files.most_common(25)),
        "commands": commands,
        "errors": errors[:10],
        "fanout_widths": widths,
        "prompts": prompts[:3],
        "started": first,
        "ended": last,
    }


def tickets_in(texts: list[str]) -> list[str]:
    """`<MODULE>#<id>` mentions -- the ticket reference rule, kept loose here."""
    found: set[str] = set()
    for t in texts:
        for m in re.finditer(r"\b([A-Z]{2,8})\s*#\s*(\d{4,6})\b", t):
            found.add(f"{m.group(1)}#{m.group(2)}")
        for m in re.finditer(r"(?<![A-Za-z0-9])#(\d{4,6})\b", t):
            found.add(f"#{m.group(1)}")
    return sorted(found)


def commits_since(started: str | None) -> list[str]:
    if not started:
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", "--since", started[:19],
             "--format=%h %s", "-30"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [l for l in out.strip().splitlines() if l][:30]


def build(path: str, session: str, project: str) -> dict | None:
    data = read_transcript(path)
    if not data or data.get("truncated"):
        return None
    # A session that ran no agent, loaded no skill and wrote no file is not an
    # episode -- it is someone asking a question. Recording those would bury
    # the ones worth reading, which is how a journal becomes unreadable.
    if not (data["agents"] or data["skills"] or data["files"]):
        return None

    cmds = data.pop("commands")
    verified = sorted({
        m for c in cmds
        for m in re.findall(r"(tests\.py|evals\.py|health\.py|outcomes\.py|"
                            r"chain\.py|delegation\.py|budget\.py)", c)
    })
    widths = data.pop("fanout_widths")

    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": session[:8],
        "project": project,
        "started": data.pop("started"),
        "ended": data.pop("ended"),
        "tickets": tickets_in(data["prompts"] + list(data["files"])),
        "agents": data["agents"],
        "skills": data["skills"],
        "files_touched": data["files"],
        "fanout": {"launches": len(widths), "max_width": max(widths) if widths else 0,
                   "solo": sum(1 for w in widths if w == 1)},
        "verified_with": verified,
        "errors": data["errors"],
        "commits": commits_since(data.get("started")),
        "asked": data["prompts"],
    }


def main() -> int:
    if "--last" in sys.argv:
        i = sys.argv.index("--last")
        n = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit() else 5
        files = sorted(EPISODES_DIR.glob("*.jsonl")) if EPISODES_DIR.is_dir() else []
        if not files:
            print("no episodes recorded yet")
            return 0
        # Merge every device's file on READ -- each machine only ever appends
        # to its own, so nothing here can conflict.
        rows = sorted(
            (json.loads(l) for f in files
             for l in f.read_text(encoding="utf-8").splitlines() if l.strip()),
            key=lambda r: r.get("at", ""))
        for r in rows[-n:]:
            print(f"\n{r['at'][:16]}  {r['project'][-30:]}  session {r['session']}")
            if r["tickets"]:
                print(f"  tickets   {', '.join(r['tickets'])}")
            if r["agents"]:
                print(f"  agents    {', '.join(f'{k}x{v}' for k, v in r['agents'].items())}")
            if r["skills"]:
                print(f"  skills    {', '.join(r['skills'])}")
            f = r["fanout"]
            if f["launches"]:
                print(f"  fan-out   {f['launches']} launch(es), widest {f['max_width']}, "
                      f"{f['solo']} solo")
            if r["verified_with"]:
                print(f"  verified  {', '.join(r['verified_with'])}")
            if r["commits"]:
                print(f"  commits   {len(r['commits'])}")
            if r["errors"]:
                print(f"  errors    {len(r['errors'])}")
        return 0

    path = session = project = ""
    if "--path" in sys.argv:
        i = sys.argv.index("--path")
        path = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        session, project = "manual", "manual"
    else:
        try:
            raw = sys.stdin.read(200_000)
            payload = json.loads(raw) if raw.strip() else {}
        except Exception:                                       # noqa: BLE001
            return 0                                            # fail silent
        path = payload.get("transcript_path") or ""
        session = str(payload.get("session_id") or "")
        project = str(payload.get("cwd") or "").replace("\\", "/")

    if not path or not os.path.isfile(path):
        return 0

    try:
        record = build(path, session, project)
    except Exception:                                           # noqa: BLE001
        return 0            # a SessionEnd hook must never be the thing that fails
    if not record:
        return 0

    try:
        EPISODES_DIR.mkdir(parents=True, exist_ok=True)
        with (EPISODES_DIR / f"{device_id()}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
