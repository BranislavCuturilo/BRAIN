#!/usr/bin/env python3
"""Score skills and agents by recorded outcomes.

Nothing measures this automatically — Claude Code has no telemetry for "did
this skill help". Every number here comes from an outcome someone recorded,
so the data is exactly as good as the discipline. That is why recording is
one command.

  score.py                          the table
  score.py record skill craft-code helped "caught the dispatch-key bug"
  score.py record agent  qa hindered "wrote tests for the wrong layer"
  score.py record workflow authz-sweep hindered "1.28M tokens, zero findings"
  score.py review                   what to retire, improve, or trust more

Outcomes: helped | hindered | neutral
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY = ROOT / "registry.json"
OUTCOMES = ("helped", "hindered", "neutral")
# Stock agents, recordable as controls. They are not brain components and are
# never synced from disk, so `sync()` must not treat them as deleted.
BASELINES = ("general-purpose", "Explore", "Plan", "claude")

# A band is a decision, not a grade. MIN_USES exists because three uses is
# not evidence — an unproven skill is not a bad one.
MIN_USES = 5


def load() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {"skills": {}, "agents": {}, "workflows": {}}


def save(data: dict) -> None:
    REGISTRY.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")


def discover() -> tuple[set[str], set[str]]:
    skills = {p.parent.name for p in ROOT.glob("skills/*/SKILL.md")}
    agents = {p.stem for p in ROOT.glob("agents/*.md") if p.stem.lower() != "readme"}
    workflows = {p.stem for p in ROOT.glob("workflows/*.js")}
    return skills, agents, workflows


def blank() -> dict:
    return {"helped": 0, "hindered": 0, "neutral": 0, "last": None, "notes": []}


def sync(data: dict) -> dict:
    """Add entries for anything new; keep entries for anything deleted so the
    history of a removed skill is not silently lost."""
    skills, agents, workflows = discover()
    data.setdefault("workflows", {})
    for kind, live in (("skills", skills), ("agents", agents), ("workflows", workflows)):
        for name in live:
            data[kind].setdefault(name, blank())
        for name, entry in data[kind].items():
            # A baseline lives outside the brain, so "not on disk" is its normal
            # state -- not the deletion `present` is meant to flag.
            entry["present"] = name in live or bool(entry.get("baseline"))
    return data


def stats(entry: dict) -> tuple[int, float | None]:
    uses = entry["helped"] + entry["hindered"] + entry["neutral"]
    if uses == 0:
        return 0, None
    return uses, (entry["helped"] - entry["hindered"]) / uses


def band(uses: int, ratio: float | None) -> str:
    if uses < MIN_USES:
        return "unproven"
    assert ratio is not None
    if ratio >= 0.6:
        return "proven"
    if ratio >= 0.2:
        return "working"
    if ratio > -0.2:
        return "weak"
    return "retire"


def show(data: dict) -> None:
    for kind in ("skills", "agents", "workflows"):
        rows = []
        for name, entry in data[kind].items():
            uses, ratio = stats(entry)
            rows.append((band(uses, ratio), uses, ratio, name, entry))
        order = {"retire": 0, "weak": 1, "unproven": 2, "working": 3, "proven": 4}
        rows.sort(key=lambda r: (order[r[0]], -r[1]))

        print(f"\n{'=' * 70}\n{kind.upper()}\n{'=' * 70}")
        print(f"  {'band':<9} {'uses':>4} {'score':>6}  name")
        for b, uses, ratio, name, entry in rows:
            gone = "" if entry.get("present", True) else "  (deleted)"
            score = f"{ratio:+.2f}" if ratio is not None else "   -- "
            print(f"  {b:<9} {uses:>4} {score:>6}  {name}{gone}")


def review(data: dict) -> None:
    print("\nRETIRE - negative track record over enough uses:")
    found = False
    for kind in ("skills", "agents", "workflows"):
        for name, entry in data[kind].items():
            uses, ratio = stats(entry)
            if band(uses, ratio) == "retire" and entry.get("present", True):
                found = True
                print(f"  {kind[:-1]:<6} {name}  ({entry['hindered']} hindered / {uses})")
                for n in entry["notes"][-3:]:
                    print(f"           - {n}")
    if not found:
        print("  none")

    print("\nIMPROVE - used often, only sometimes helping:")
    found = False
    for kind in ("skills", "agents", "workflows"):
        for name, entry in data[kind].items():
            uses, ratio = stats(entry)
            if band(uses, ratio) == "weak" and entry.get("present", True):
                found = True
                print(f"  {kind[:-1]:<6} {name}  (score {ratio:+.2f} over {uses})")
    if not found:
        print("  none")

    print("\nUNPROVEN - not enough recorded outcomes to judge:")
    names = [n for k in ("skills", "agents") for n, e in data[k].items()
             if stats(e)[0] < MIN_USES and e.get("present", True)]
    print("  " + (", ".join(sorted(names)) if names else "none"))
    print(f"\n  (a skill is unproven below {MIN_USES} uses - that is not a bad score,")
    print("   it means nobody has recorded enough to say either way)")

    print("\nNOTE: an agent's `skills:` list is static frontmatter. A score cannot")
    print("change what gets preloaded at runtime - it tells you which frontmatter")
    print("to edit. Nothing here rewrites an agent for you.")


def main(argv: list[str]) -> int:
    data = sync(load())

    if len(argv) >= 4 and argv[0] == "record":
        kind, name, outcome = argv[1], argv[2], argv[3]
        note = " ".join(argv[4:]).strip()
        kind = kind if kind.endswith("s") else kind + "s"
        if kind not in ("skills", "agents", "workflows"):
            print("kind must be skill, agent or workflow")
            return 2
        if outcome not in OUTCOMES:
            print(f"outcome must be one of {', '.join(OUTCOMES)}")
            return 2
        if name not in data[kind]:
            # A baseline is not part of the brain and never will be, but without
            # somewhere to record it the scoreboard can only ever compare brain
            # components to each other -- it can never show one LOSING to the
            # stock agent, which is the comparison most worth having.
            if kind == "agents" and name in BASELINES:
                data[kind][name] = {"helped": 0, "hindered": 0, "neutral": 0,
                                    "notes": [], "baseline": True}
            else:
                print(f"unknown {kind[:-1]}: {name}")
                print(f"(baselines you may record against: {', '.join(sorted(BASELINES))})")
                return 2
        entry = data[kind][name]
        entry[outcome] += 1
        entry["last"] = date.today().isoformat()
        if note:
            entry["notes"] = (entry["notes"] + [f"{date.today().isoformat()} {outcome}: {note}"])[-10:]
        save(data)
        uses, ratio = stats(entry)
        print(f"{name}: {outcome} recorded - {uses} uses, score {ratio:+.2f}, band {band(uses, ratio)}")
        return 0

    save(data)
    if argv and argv[0] == "review":
        review(data)
    else:
        show(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
