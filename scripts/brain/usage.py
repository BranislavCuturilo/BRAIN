#!/usr/bin/env python3
"""How often each agent and skill is ACTUALLY used, from the session transcripts.

Measured, not self-reported: Claude Code records every tool call, so a `Task`
invocation names its subagent and a `Skill` invocation names its skill. This
reads those.

  usage.py                 the table
  usage.py --days 30       recent only
  usage.py --json          for dashboard.py

Agents are counted from the RUN RECORDS every spawned agent writes --
`<project>/<session>/subagents/**/agent-*.meta.json` -- because they are the
only source that covers all three ways an agent gets started:

  the main loop delegating            (appears in the transcript too)
  a WORKFLOW spawning it              (never appears as a Task call)
  ANOTHER AGENT delegating to it      (spawnDepth > 1, invisible from the top)

Counting the transcript alone reported a 14-agent sweep as 2, and made an
orchestrator delegating to five juniors register as one agent and the juniors
as zero. Transcript Task calls are still used, but only for calls with no run
record (older sessions), matched by tool-use id so nothing is counted twice.
Skills still come from the transcript, since a Skill call is not a spawn.

CAVEATS, and they matter:
  - a zero means "not seen", never "never used": deleted sessions are gone.
  - a PRELOADED skill (an agent's `skills:` frontmatter) is injected at
    startup and never calls the Skill tool, so it is invisible here however
    heavily it is used. `preloadReach` below is the honest substitute:
    how many runs of agents that preload it actually happened.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECTS = Path.home() / ".claude" / "projects"
ROOT = Path(__file__).resolve().parent.parent.parent


def preloads(root: Path | None = None) -> dict[str, list[str]]:
    """agent name -> the skills its frontmatter preloads.

    Parsed here rather than imported, so this stays a leaf module. `root` is a
    parameter so the parser can be tested against known input -- an empty parse
    result is indistinguishable from "preloads nothing" once it reaches a table.
    """
    out: dict[str, list[str]] = {}
    for md in ((root or ROOT) / "agents").glob("*.md"):
        try:
            head = md.read_text(encoding="utf-8", errors="replace").split("---")[1]
        except (OSError, IndexError):
            continue
        got: list[str] = []
        lines = head.splitlines()
        for i, line in enumerate(lines):
            if not line.startswith("skills:"):
                continue
            # Both YAML spellings, because the files use both. Reading only the
            # header line is how budget.py silently reported every agent as
            # preloading nothing -- an empty result that looks like an answer.
            inline = line.split(":", 1)[1].strip().strip("[]")
            if inline:
                got = [s.strip().strip("'\"") for s in inline.split(",") if s.strip()]
            else:
                for nxt in lines[i + 1:]:
                    if not nxt.startswith((" ", "-", "	")):
                        break
                    item = nxt.strip().lstrip("-").strip().strip("'\"")
                    if item:
                        got.append(item)
            break
        # Plugin-scoped names (brain:craft-security) index by bare skill name.
        out[md.stem] = [s.split(":")[-1] for s in got]
    return out


IDLE_GAP_S = 300


def activity(days: int | None = None) -> dict:
    """How long the work actually took, and what it cost.

    Wall-clock between the first and last record is useless -- it counts the
    hours a session sat open. So time is summed over the GAPS between
    consecutive records, ignoring any gap longer than `IDLE_GAP_S`: that is
    time somebody was waiting for a reply or a test, not time a laptop was
    idle overnight.

    Tokens come from the `usage` block the API returns on each assistant
    message. Cache reads are reported separately because they are the bulk of
    the volume and a fraction of the price -- adding them to the total makes
    every number look alarming and none of them mean anything.
    """
    cutoff = time.time() - days * 86400 if days else 0
    stamps: list[float] = []
    tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    per_day: dict[str, dict] = defaultdict(
        lambda: {"minutes": 0.0, "output": 0, "input": 0})

    if not PROJECTS.is_dir():
        return {"minutes": 0, "tokens": tok, "days": {}}

    for path in PROJECTS.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        session: list[tuple[float, str]] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"timestamp"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    raw = rec.get("timestamp")
                    if not raw:
                        continue
                    try:
                        when = datetime.fromisoformat(
                            raw.replace("Z", "+00:00")).timestamp()
                    except (TypeError, ValueError):
                        continue
                    if cutoff and when < cutoff:
                        continue
                    day = raw[:10]
                    session.append((when, day))
                    u = ((rec.get("message") or {}).get("usage") or {})
                    if u:
                        tok["input"] += u.get("input_tokens", 0) or 0
                        tok["output"] += u.get("output_tokens", 0) or 0
                        tok["cache_read"] += u.get(
                            "cache_read_input_tokens", 0) or 0
                        tok["cache_write"] += u.get(
                            "cache_creation_input_tokens", 0) or 0
                        per_day[day]["output"] += u.get("output_tokens", 0) or 0
                        per_day[day]["input"] += u.get("input_tokens", 0) or 0
        except OSError:
            continue
        session.sort()
        for (a, _), (b, day) in zip(session, session[1:]):
            gap = b - a
            if 0 < gap <= IDLE_GAP_S:
                stamps.append(gap)
                per_day[day]["minutes"] += gap / 60

    return {
        "minutes": round(sum(stamps) / 60, 1),
        "tokens": tok,
        "days": {d: {"minutes": round(v["minutes"], 1),
                     "output": v["output"], "input": v["input"]}
                 for d, v in sorted(per_day.items(), reverse=True)},
    }


def _epoch(stamp: str | None) -> float:
    """ISO-8601 from a transcript -> epoch seconds, so it can be compared with
    a run record's mtime. Returns 0 on anything unparseable."""
    if not stamp:
        return 0.0
    try:
        return datetime.fromisoformat(
            str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _prompt_text(content) -> str:
    """The user's own words out of a user message, or '' if there are none.

    A transcript's `type: "user"` covers three different things: what the
    person typed, every tool RESULT, and injected reminders. Only the first is
    a prompt, and counting the other two turned a single request into forty.
    """
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        return ""
    parts = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "")
        # Strip injected blocks before deciding the message is empty: a turn
        # that is nothing but a system reminder is not a prompt.
        for tag in ("system-reminder", "local-command-stdout", "ide_opened_file",
                    "ide_selection", "task-notification", "command-name",
                    "command-message", "command-args"):
            while f"<{tag}>" in text and f"</{tag}>" in text:
                head, rest = text.split(f"<{tag}>", 1)
                text = head + rest.split(f"</{tag}>", 1)[1]
        text = text.strip()
        # A background-task completion is delivered as a user turn and carries
        # a real `promptSource`, so the tag alone does not exclude it — the
        # prose wrapped around the tag is what is left.
        if text.startswith("[SYSTEM NOTIFICATION"):
            continue
        parts.append(text)
    return " ".join(p for p in parts if p).strip()


def per_prompt(limit: int = 20, days: int = 7) -> list[dict]:
    """One row per thing you actually asked for: agents and skills used on it.

    Kept separate from `collect()` on purpose. That answers "which agents earn
    their place over months"; this answers "what did it just do for THIS
    request", which is the question while testing — and the two need opposite
    aggregations.

    Agents are attributed by TIME, not by tool-use id: a run record's mtime is
    matched into the turn it falls inside. That is what makes an agent spawned
    by another agent land on the right prompt — it never appears in the main
    transcript at all, which is exactly the blindness that reported a
    fourteen-agent sweep as three.
    """
    cutoff = time.time() - days * 86400
    turns: list[dict] = []
    if not PROJECTS.is_dir():
        return []

    for path in PROJECTS.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        session, project = path.stem, path.parent.name
        session_turns: list[dict] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("isSidechain"):
                        continue          # a subagent's own transcript
                    when = _epoch(rec.get("timestamp"))
                    content = (rec.get("message") or {}).get("content")

                    if rec.get("type") == "user":
                        # `promptSource` is the exact discriminator, and it is
                        # worth using rather than guessing from the text: an
                        # injected skill body and a compaction summary are both
                        # `type: user` with real prose in them, and counting
                        # either one invents a request that was never made.
                        # Injected content carries `isMeta`; an interruption
                        # carries neither key.
                        if rec.get("isMeta") or not rec.get("promptSource"):
                            continue
                        text = _prompt_text(content)
                        if text:
                            session_turns.append({
                                "at": when, "project": project,
                                "session": session,
                                "prompt": text[:160],
                                "agents": [], "skills": []})
                        continue

                    if not session_turns or not isinstance(content, list):
                        continue
                    for block in content:
                        if (not isinstance(block, dict)
                                or block.get("type") != "tool_use"):
                            continue
                        inp = block.get("input") or {}
                        if (block.get("name") == "Skill"
                                and inp.get("skill")):
                            session_turns[-1]["skills"].append(
                                str(inp["skill"]).split(":")[-1])
        except OSError:
            continue

        if not session_turns:
            continue

        # Run records for THIS session, bucketed into the turn they fall in.
        records = []
        for meta in (PROJECTS / project / session).glob(
                "subagents/**/agent-*.meta.json"):
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                ran_at = meta.stat().st_mtime
            except (OSError, ValueError):
                continue
            kind = str(info.get("agentType", "")).split(":")[-1]
            if kind and kind != "workflow-subagent":
                records.append((ran_at, kind))

        bounds = [t["at"] for t in session_turns]
        for ran_at, kind in sorted(records):
            # The last turn that had already started when this agent ran.
            idx = -1
            for i, start in enumerate(bounds):
                if start <= ran_at:
                    idx = i
                else:
                    break
            if idx >= 0:
                session_turns[idx]["agents"].append(kind)
        turns.extend(session_turns)

    turns.sort(key=lambda t: t["at"], reverse=True)
    for turn in turns:
        turn["nAgents"] = len(turn["agents"])
        turn["nSkills"] = len(turn["skills"])
        turn["when"] = (datetime.fromtimestamp(turn["at"]).strftime(
            "%Y-%m-%d %H:%M") if turn["at"] else "")
        turn["agents"] = sorted(set(turn["agents"]))
        turn["skills"] = sorted(set(turn["skills"]))
    return turns[:limit]


def collect(days: int | None = None) -> dict:
    """Walk every transcript once, counting agent and skill invocations."""
    cutoff = time.time() - days * 86400 if days else 0
    agents: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "projects": set(), "last": None, "depths": set()})
    skills: dict[str, dict] = defaultdict(lambda: {"n": 0, "projects": set(), "last": None})
    task_calls: list[tuple] = []
    sessions = 0

    if not PROJECTS.is_dir():
        return {"agents": {}, "skills": {}, "sessions": 0, "scanned": 0}

    scanned = 0
    for path in PROJECTS.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        project = path.parent.name
        sessions += 1
        scanned += 1
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    # Cheap pre-filter: most lines are not tool calls at all.
                    if '"tool_use"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    content = (rec.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    stamp = rec.get("timestamp")
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        inp = block.get("input") or {}
                        name = block.get("name")
                        if name in ("Task", "Agent"):
                            key = inp.get("subagent_type") or inp.get("agentType")
                            if key:
                                # Held, not counted: the run record below is the
                                # authoritative source and this would double it.
                                task_calls.append((block.get("id"), str(key).split(":")[-1],
                                                   project, stamp))
                        elif name == "Skill" and inp.get("skill"):
                            key = str(inp["skill"]).split(":")[-1]
                            skills[key]["n"] += 1
                            skills[key]["projects"].add(project)
                            if stamp and (skills[key]["last"] or "") < stamp:
                                skills[key]["last"] = stamp
        except OSError:
            continue

    def clean(d: dict) -> dict:
        return {k: {"n": v["n"], "projects": sorted(v["projects"]),
                    "last": (v["last"] or "")[:10]}
                for k, v in sorted(d.items(), key=lambda kv: -kv[1]["n"])}

    # --- agent runs -------------------------------------------------------
    # Every spawned agent writes a run record, whatever spawned it: the main
    # loop, a workflow, or ANOTHER AGENT. Counting only the main transcript
    # made an orchestrator that delegates to five juniors register as one agent
    # and the juniors as zero -- blind to precisely the delegation pattern the
    # whole design rests on.
    seen_ids: set[str] = set()
    wf_runs = nested = 0
    for meta in PROJECTS.glob("*/*/subagents/**/agent-*.meta.json"):
        try:
            # The window applies here as well. Filtering only the transcripts
            # made `--days 1` report agents last run weeks earlier, which is
            # the opposite of what the flag says.
            if cutoff and meta.stat().st_mtime < cutoff:
                continue
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        kind = str(info.get("agentType", "")).split(":")[-1]
        if not kind or kind == "workflow-subagent":
            continue
        if info.get("toolUseId"):
            seen_ids.add(info["toolUseId"])
        depth = info.get("spawnDepth") or 1
        if depth > 1:
            nested += 1
        if "workflows" in meta.parts:
            wf_runs += 1
        agents[kind]["n"] += 1
        agents[kind]["depths"].add(depth)
        # Run records carry no timestamp; the file's mtime is when it ran.
        try:
            when = datetime.fromtimestamp(meta.stat().st_mtime, timezone.utc).isoformat()
            if (agents[kind]["last"] or "") < when:
                agents[kind]["last"] = when
        except OSError:
            pass
        # projects/<project>/<session>/subagents/...  -- walk up to the project.
        parts = meta.parts
        agents[kind]["projects"].add(
            parts[parts.index("subagents") - 2] if "subagents" in parts else "unknown")

    # Older sessions predate the run records; count those calls from the
    # transcript, matched by tool-use id so nothing is counted twice.
    legacy = 0
    for call_id, kind, project, stamp in task_calls:
        if call_id and call_id in seen_ids:
            continue
        legacy += 1
        agents[kind]["n"] += 1
        agents[kind]["projects"].add(project)
        agents[kind]["depths"].add(1)
        if stamp and (agents[kind]["last"] or "") < stamp:
            agents[kind]["last"] = stamp

    out_agents = clean(agents)
    for name, row in out_agents.items():
        row["nested"] = max(agents[name]["depths"]) > 1
    out_skills = clean(skills)

    # A preloaded skill never calls the Skill tool. Attribute the runs of every
    # agent that carries it instead -- otherwise the most-used skills in the
    # system read as completely unused, which is the opposite of the truth.
    reach: dict[str, int] = defaultdict(int)
    carriers: dict[str, set] = defaultdict(set)
    for agent_name, pre in preloads().items():
        runs = out_agents.get(agent_name, {}).get("n", 0)
        for skill in pre:
            carriers[skill].add(agent_name)
            reach[skill] += runs
    for name in set(out_skills) | set(reach):
        row = out_skills.setdefault(name, {"n": 0, "projects": [], "last": ""})
        row["preloadReach"] = reach.get(name, 0)
        row["carriers"] = len(carriers.get(name, ()))
    out_skills = dict(sorted(out_skills.items(),
                             key=lambda kv: (-kv[1]["n"], -kv[1]["preloadReach"])))
    return {"agents": out_agents, "skills": out_skills,
            "sessions": sessions, "scanned": scanned, "workflowAgents": wf_runs,
            "nestedAgents": nested, "legacyAgents": legacy,
            "activity": activity(days),
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    data = collect(args.days)
    if args.as_json:
        print(json.dumps(data, indent=2))
        return 0

    window = f"last {args.days} days" if args.days else "all time"
    print(f"{data['scanned']} transcripts scanned ({window})\n")

    for label, rows in (("AGENTS", data["agents"]), ("SKILLS", data["skills"])):
        print("=" * 62)
        print(label)
        print("=" * 62)
        if not rows:
            print("  nothing recorded")
        for name, r in rows.items():
            tail = ((f"{len(r['projects'])} project(s)"
                     + (" [delegated to]" if r.get("nested") else ""))
                    if label == "AGENTS" else
                    (f"+{r['preloadReach']} preloaded run(s) via {r['carriers']} agent(s)"
                     if r.get("preloadReach") else
                     (f"preloaded by {r['carriers']} agent(s), none run yet"
                      if r.get("carriers") else "not preloaded anywhere")))
            print(f"  {r['n']:>4}x  {name:<26} {r['last'] or '-':<12} {tail}")
        print()

    print("A zero means NOT SEEN in the records on disk -- deleted sessions are")
    print("gone, so it is not proof that something was never used.")
    print("A PRELOADED skill never calls the Skill tool and so cannot appear here")
    print("at all, however heavily an agent relies on it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
