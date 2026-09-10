#!/usr/bin/env python3
"""Is this a multi-agent system, or a queue that spawns one agent at a time?

`usage.py` answers "how often was each agent used". That number looks healthy
while the system behaves nothing like its design -- 55 runs of one agent, one
after another, is a queue with extra steps.

The thing that separates the two is **fan-out width**: how many `Agent` calls
appear in the SAME assistant message. Claude Code runs those concurrently; calls
in separate messages run one after the other, each one's output landing back in
the main context before the next begins. Width 1, repeated, IS the queue loop.

**And what it COSTS.** The shape argument was made without a stopwatch: "calls
in separate messages run one after another" is true, but "several times the
wall-clock" was an assertion. Tool calls in a Claude Code transcript carry a
`tool_use` id and a matching `tool_result`, both timestamped, so the time inside
every agent is measurable rather than estimated. The idea is borrowed from
zoetrope, which renders these same transcripts as a timeline; the measurement is
cheap enough to do here without the viewer.

Three numbers, and the design fails if any of them is wrong:

  width       how many agents were launched together
  turn        WHERE in the session delegation happened. The brain's own
              measurement: delegation on the first turn and on NO continuation
              turn afterwards, because "continue" carries no decision point.
  depth       did any agent spawn another. A background subagent is not given
              the `Agent` tool at all, so an orchestrator spawned the default
              way physically cannot delegate -- it can only do the work itself,
              which is exactly what the score recorded three times.

  delegation.py              the report
  delegation.py --days 30    recent only
  delegation.py --sessions   one line per session that delegated
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
PROJECTS = Path.home() / ".claude" / "projects"

sys.path.insert(0, str(Path(__file__).resolve().parent))
# ONE parser for a transcript timestamp. usage.py already had it, with the
# tolerance for the shapes these files actually contain; a second copy here
# would be the two-versions-of-one-rule defect in code rather than in prose.
from usage import _epoch                                        # noqa: E402


def collect(days: int | None = None) -> dict:
    cutoff = time.time() - days * 86400 if days else 0

    widths: Counter = Counter()          # calls-in-one-message -> how often
    turn_of_call: Counter = Counter()    # assistant-turn index -> delegations
    tool_secs: dict[str, float] = {}     # tool name -> seconds spent inside it
    tool_n: dict[str, int] = {}
    agent_secs: dict[str, list[float]] = {}
    batch_of: dict[str, float] = {}      # tool_use id -> its measured seconds
    batch_ids: list[list[str]] = []      # the ids launched in each one message
    unreturned = 0
    per_session: list[dict] = []
    pairs: Counter = Counter()           # agents launched together
    sessions_total = sessions_deleg = 0

    if not PROJECTS.is_dir():
        return {}

    for path in PROJECTS.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue

        turn = 0
        last_msg = ""        # one assistant message spans MANY records
        ids_msg = ""         # which message owns batch_ids[-1]
        names_msg = ""       # which message owns batches[-1]
        batches: list[tuple[int, list[str]]] = []
        # tool_use id -> (tool name, agent type, start). Paired with its
        # tool_result on the way past; an unmatched id is a call that never
        # returned, which is worth knowing and is NOT counted as zero.
        pending: dict[str, tuple[str, str, float]] = {}
        batch_spans: list[list[float]] = []
        open_batch: list[str] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    # The cheap pre-filter MUST let tool_result through: it is
                    # `type: "user"` and contains neither of the other two
                    # markers, so an earlier version silently dropped every
                    # result and reported 10,192 calls as never returning.
                    if ('"tool_use"' not in line and '"tool_result"' not in line
                            and '"assistant"' not in line):
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("isSidechain"):
                        continue            # a subagent's own transcript
                    content = (rec.get("message") or {}).get("content")
                    when = _epoch(rec.get("timestamp"))

                    if rec.get("type") == "user" and isinstance(content, list):
                        for b in content:
                            if not isinstance(b, dict) or b.get("type") != "tool_result":
                                continue
                            got = pending.pop(b.get("tool_use_id"), None)
                            if got and when:
                                name, agent, start = got
                                secs = max(0.0, when - start)
                                tool_secs[name] = tool_secs.get(name, 0.0) + secs
                                tool_n[name] = tool_n.get(name, 0) + 1
                                if agent:
                                    agent_secs.setdefault(agent, []).append(secs)
                                if b.get("tool_use_id") in open_batch:
                                    batch_of[b["tool_use_id"]] = secs
                        continue

                    if rec.get("type") != "assistant":
                        continue
                    if not isinstance(content, list):
                        continue
                    # Claude Code writes ONE RECORD PER BLOCK, every record of
                    # a message sharing its `message.id`. Counting per record
                    # made every batch read as a queue (326 of 326 "solo") and
                    # every turn index several times too large. See
                    # transcript.py for the measurement.
                    msg_id = str((rec.get("message") or {}).get("id") or "")
                    if not msg_id or msg_id != last_msg:
                        turn += 1
                        last_msg = msg_id
                    names = [
                        str((b.get("input") or {}).get("subagent_type")
                            or (b.get("input") or {}).get("agentType") or "").split(":")[-1]
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("name") in ("Task", "Agent")
                    ]
                    names = [n for n in names if n]

                    # Record every tool call's start so its result can close it.
                    ids_here = []
                    for b in content:
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        tool = str(b.get("name") or "?")
                        agent = ""
                        if tool in ("Task", "Agent"):
                            inp = b.get("input") or {}
                            agent = str(inp.get("subagent_type")
                                        or inp.get("agentType") or "").split(":")[-1]
                            ids_here.append(b.get("id"))
                        if b.get("id") and when:
                            pending[b["id"]] = (tool, agent, when)
                    if ids_here:
                        open_batch.extend(i for i in ids_here if i)
                        if msg_id and msg_id == ids_msg and batch_ids:
                            batch_ids[-1].extend(ids_here)
                        else:
                            batch_ids.append(ids_here)
                            ids_msg = msg_id

                    if names:
                        if msg_id and msg_id == names_msg and batches:
                            batches[-1][1].extend(names)
                        else:
                            batches.append((turn, names))
                            names_msg = msg_id
        except OSError:
            continue

        unreturned += len(pending)
        sessions_total += 1
        if not batches:
            continue
        sessions_deleg += 1

        for t, names in batches:
            widths[len(names)] += 1
            turn_of_call[t] += len(names)
            if len(names) > 1:
                for combo in sorted(set(names)):
                    pairs[combo] += 1

        per_session.append({
            "session": path.stem[:8],
            "project": path.parent.name[-28:],
            "turns": turn,
            "batches": len(batches),
            "agents": sum(len(n) for _t, n in batches),
            "max_width": max(len(n) for _t, n in batches),
            "first_turn": batches[0][0],
            "last_turn": batches[-1][0],
        })

    # --- nesting, from the run records rather than the transcript ----------
    # A nested spawn never appears in the main transcript at all.
    #
    # THE KEY NAMES WERE WRONG for the whole life of this script. It read
    # `depth`/`nestingDepth` and `parentAgentType`; the runtime writes
    # **`spawnDepth`** and **`parentAgentId`**. Every lookup missed, so this
    # printed "0 nested" as a constant -- and that zero was quoted in
    # ops-delegation and in the orchestrator's own brief as proof that the
    # harness forbids nesting. It does not. Measured 2026-09-10 with the right
    # keys: 33 of 496 runs were nested, and an orchestrator launched five units
    # at spawnDepth 2 in one message. A counter that cannot return anything but
    # zero is not evidence; see transcript.py for the same failure shape.
    #
    # The agent's own id appears ONLY in the file name, so resolving a parent
    # id to an agent type needs a first pass.
    nested = 0
    total_runs = 0
    by_parent: dict[str, int] = defaultdict(int)
    metas: list[dict] = []
    type_of: dict[str, str] = {}
    for meta in PROJECTS.glob("*/*/subagents/**/agent-*.meta.json"):
        try:
            if meta.stat().st_mtime < cutoff:
                continue
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        agent_id = meta.stem.split(".")[0][len("agent-"):]
        type_of[agent_id] = str(data.get("agentType") or "")
        metas.append(data)

    for data in metas:
        total_runs += 1
        depth = (data.get("spawnDepth") or data.get("depth")
                 or data.get("nestingDepth") or 0)
        try:
            if int(depth) > 1:
                nested += 1
        except (TypeError, ValueError):
            pass
        parent = str(data.get("parentAgentId") or "")
        if parent:
            name = (type_of.get(parent) or data.get("parentAgentType")
                    or parent[:8])
            by_parent[str(name).split(":")[-1]] += 1

    # What the queue actually cost. For each message that launched agents:
    # sequential is what you paid; concurrent is what one message would have
    # cost, which is the slowest member rather than the sum.
    seq = conc = 0.0
    for ids in batch_ids:
        spans = [batch_of[i] for i in ids if i in batch_of]
        if not spans:
            continue
        seq += sum(spans)
        conc += max(spans)

    return {
        "agent_wall_clock_s": round(seq, 1),
        "if_batched_s": round(conc, 1),
        "unreturned_calls": unreturned,
        "tool_seconds": {k: round(v, 1) for k, v in
                         sorted(tool_secs.items(), key=lambda kv: -kv[1])[:12]},
        "tool_calls": tool_n,
        "agent_seconds": {k: [round(x, 1) for x in v]
                          for k, v in agent_secs.items()},
        "sessions_total": sessions_total,
        "sessions_delegating": sessions_deleg,
        "widths": dict(sorted(widths.items())),
        "turn_of_call": dict(sorted(turn_of_call.items())),
        "per_session": sorted(per_session, key=lambda s: -s["agents"]),
        "parallel_members": pairs.most_common(12),
        "runs_with_meta": total_runs,
        "nested_runs": nested,
        "by_parent": dict(sorted(by_parent.items(), key=lambda kv: -kv[1])),
    }


def main() -> int:
    days = None
    if "--days" in sys.argv:
        i = sys.argv.index("--days")
        if i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit():
            days = int(sys.argv[i + 1])

    r = collect(days)
    if not r:
        print("no transcripts found")
        return 1

    if "--json" in sys.argv:
        print(json.dumps(r, indent=2))
        return 0

    total_calls = sum(n * c for n, c in r["widths"].items())
    batches = sum(r["widths"].values())
    solo = r["widths"].get(1, 0)

    print("=" * 70)
    print(f"DELEGATION SHAPE   ({r['sessions_delegating']}/{r['sessions_total']} "
          f"sessions delegated at all)")
    print("=" * 70)

    print("\nFAN-OUT WIDTH -- agents launched in ONE message (these run concurrently)\n")
    for width, count in r["widths"].items():
        bar = "#" * min(50, count)
        label = "sequential" if width == 1 else f"{width} in parallel"
        print(f"  {width:>2} agent(s)  {count:>4}x  {label:<16} {bar}")
    if batches:
        pct = 100 * solo / batches
        print(f"\n  {solo}/{batches} launches were ONE agent alone ({pct:.0f}%).")
        print(f"  {total_calls} agent(s) launched across {batches} message(s) "
              f"-- {total_calls / batches:.2f} per launch.")
        if pct > 80:
            print("\n  >80% solo launches is a QUEUE, not a swarm. Independent work")
            print("  sent one at a time pays full round-trip latency per agent, and")
            print("  every result lands back in the main context before the next.")

    print("\nWHERE IN THE SESSION -- delegations by assistant-turn index\n")
    early = sum(v for k, v in r["turn_of_call"].items() if k <= 3)
    late = sum(v for k, v in r["turn_of_call"].items() if k > 10)
    tot = sum(r["turn_of_call"].values()) or 1
    print(f"  turns 1-3    {early:>4}  ({100*early/tot:.0f}%)")
    print(f"  turns 4-10   {tot-early-late:>4}  ({100*(tot-early-late)/tot:.0f}%)")
    print(f"  turns 11+    {late:>4}  ({100*late/tot:.0f}%)")
    if early / tot > 0.6:
        print("\n  Front-loaded. 'Continue' carries no decision point, so the")
        print("  cheapest next move is always to carry on alone.")

    print("\nWHAT IT COST -- measured from tool_use/tool_result timestamps\n")
    seq, conc = r["agent_wall_clock_s"], r["if_batched_s"]
    if seq:
        h = seq / 3600
        print(f"  time inside agent calls      {seq:>10,.0f}s   ({h:.1f} h)")
        print(f"  the same work in batches     {conc:>10,.0f}s   ({conc/3600:.1f} h)")
        if seq > conc:
            print(f"  paid for running them alone  {seq - conc:>10,.0f}s   "
                  f"({(seq - conc)/3600:.1f} h)")
        else:
            print("  nothing was ever batched, so there is no saving to show --")
            print("  the two numbers are equal BY CONSTRUCTION, not by luck.")
    else:
        print("  no completed agent calls timed")
    if r["unreturned_calls"]:
        print(f"  calls that never returned    {r['unreturned_calls']:>10,}"
              f"   (not counted as zero)")

    if r["tool_seconds"]:
        print("\n  where the wall-clock went, by tool\n")
        for name, secs in list(r["tool_seconds"].items())[:8]:
            n = r["tool_calls"].get(name, 0)
            avg = secs / n if n else 0
            print(f"    {name:<16} {secs:>9,.0f}s  over {n:>4} call(s)"
                  f"   avg {avg:>6.1f}s")

    if r["agent_seconds"]:
        print("\n  per agent: runs, total, slowest\n")
        rows = sorted(r["agent_seconds"].items(),
                      key=lambda kv: -sum(kv[1]))[:8]
        for name, spans in rows:
            print(f"    {name:<20} {len(spans):>3} run(s)  "
                  f"{sum(spans):>8,.0f}s total   slowest {max(spans):>6.0f}s")

    print("\nNESTING -- did an agent ever spawn another\n")
    print(f"  {r['nested_runs']} of {r['runs_with_meta']} recorded runs were nested")
    if r["by_parent"]:
        for parent, n in list(r["by_parent"].items())[:6]:
            print(f"    spawned by {parent}: {n}")
    if r["nested_runs"] == 0:
        print("    NONE -- but check the agent's front matter before blaming")
        print("    the harness. `Agent` sitting in `disallowedTools` looks")
        print("    identical from here, and that is what it was: this counter")
        print("    read key names the runtime never wrote and printed 0 for")
        print("    its whole life. The real keys are spawnDepth/parentAgentId.")

    if r["parallel_members"]:
        print("\nAGENTS THAT APPEAR IN A PARALLEL BATCH\n")
        for name, n in r["parallel_members"]:
            print(f"  {n:>3}x  {name}")

    if "--sessions" in sys.argv:
        print("\nPER SESSION\n")
        print(f"  {'session':<10} {'project':<30} {'turns':>5} {'agents':>7} "
              f"{'batch':>6} {'wide':>5} {'first':>6} {'last':>5}")
        for s in r["per_session"][:25]:
            print(f"  {s['session']:<10} {s['project']:<30} {s['turns']:>5} "
                  f"{s['agents']:>7} {s['batches']:>6} {s['max_width']:>5} "
                  f"{s['first_turn']:>6} {s['last_turn']:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
