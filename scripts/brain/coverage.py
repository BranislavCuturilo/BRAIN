#!/usr/bin/env python3
"""Can each agent be REACHED when its moment arrives?

**The metric this replaces, and why it was wrong.** `usage.py` counts how often
an agent ran, and 21 of 55 agents had never run. Read as waste, that says
"delete them". Read correctly it says almost nothing: an agent covering a case
you have not hit yet *should* be at zero. A Docker agent with no runs on a repo
with no Dockerfile is not dead weight -- it is insurance whose premium is a
description.

The question that actually matters is coverage: **when the case finally
occurs, does anything name the right agent?** That is mechanical and checkable,
and it does not need the case to have happened yet.

## The four ways an agent gets chosen, weakest last

  router     `prompt_router.py` names it from what the prompt SAYS, before the
             first tool call. The only path that fires without anyone deciding.
  routed     a skill names it -- a roster row, a read-on-demand table, a
             recipe. Reachable once the governing skill is loaded.
  workflow   a workflow spawns it.
  desc-only  nothing names it anywhere. It is reachable only if Claude reads
             its description among 55 others and picks it unprompted.

`desc-only` is not a bug by itself -- for a rare, unmistakable case a good
description is enough. It becomes a bug when the case is one this repo hits
often, because the measured outcome of relying on descriptions alone was
`general-purpose` absorbing 81 runs while the narrow agents sat unused.

  coverage.py            the report
  coverage.py --weak     only the agents nothing names
  coverage.py --json     machine-readable
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docs as docs_mod                                         # noqa: E402
import usage as usage_mod                                       # noqa: E402


def router_named() -> set[str]:
    """Agents `prompt_router.py` can name. Read from the module, not the file:
    a regex list parsed out of source drifts the moment someone reformats it."""
    try:
        import prompt_router                                     # noqa: PLC0415
    except Exception:                                            # noqa: BLE001
        return set()
    out: set[str] = set()
    for _pattern, names in getattr(prompt_router, "AGENTS", []):
        out |= {str(n).split(":")[-1] for n in names}
    return out


def skill_named(agents: set[str]) -> dict[str, list[str]]:
    """agent -> the skill files that name it."""
    hits: dict[str, list[str]] = {}
    for path in sorted(ROOT.glob("skills/**/*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for name in agents:
            # Word-bounded: `qa` must not match "quality", `security` must not
            # match the skill name `craft-security`.
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
                hits.setdefault(name, []).append(rel)
    return hits


def workflow_named(agents: set[str]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in sorted(ROOT.glob("workflows/*.js")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in agents:
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
                hits.setdefault(name, []).append(path.name)
    return hits


def collect() -> dict:
    agents = docs_mod.read_agents()
    names = set(agents)
    routed_by_router = router_named() & names
    by_skill = skill_named(names)
    by_workflow = workflow_named(names)

    try:
        use = usage_mod.collect()["agents"]
    except Exception:                                            # noqa: BLE001
        use = {}

    rows = []
    for name in sorted(names):
        # An agent naming only itself (its own file lives under agents/, not
        # skills/) does not count; skill_named already only reads skills/.
        skills = [s for s in by_skill.get(name, [])]
        paths = []
        if name in routed_by_router:
            paths.append("router")
        if skills:
            paths.append("routed")
        if by_workflow.get(name):
            paths.append("workflow")
        rows.append({
            "agent": name,
            "paths": paths or ["desc-only"],
            "skills": skills[:3],
            "runs": use.get(name, {}).get("n", 0),
        })
    return {"agents": rows,
            "total": len(rows),
            "weak": [r for r in rows if r["paths"] == ["desc-only"]]}


def main() -> int:
    r = collect()
    if "--json" in sys.argv:
        print(json.dumps(r, indent=2))
        return 0

    weak = r["weak"]
    if "--weak" in sys.argv:
        rows = weak
        title = "NOTHING NAMES THESE"
    else:
        rows = r["agents"]
        title = "AGENT REACHABILITY"

    print("=" * 74)
    print(f"{title}   ({r['total'] - len(weak)}/{r['total']} agents have a "
          f"deterministic path)")
    print("=" * 74)
    print(f"\n  {'agent':<24} {'reachable via':<24} {'runs':>5}  named in")
    for row in rows:
        via = ", ".join(row["paths"])
        where = ", ".join(s.replace("skills/", "").replace("/SKILL.md", "")
                          for s in row["skills"])
        mark = "  " if row["paths"] != ["desc-only"] else "! "
        print(f"{mark}{row['agent']:<24} {via:<24} {row['runs']:>5}  {where[:40]}")

    if weak and "--weak" not in sys.argv:
        print(f"\n{len(weak)} agent(s) reachable only if Claude picks them out of "
              f"{r['total']} descriptions:")
        print("  " + ", ".join(w["agent"] for w in weak))

    print("\nRuns are context, NOT the verdict. An agent for a case this repo has")
    print("not hit should be at zero and still be reachable. The defect is a")
    print("`desc-only` agent for a case that DOES occur here -- fix it by adding")
    print("a prompt_router pattern or a roster row, never by deleting the agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
