#!/usr/bin/env python3
"""What the brain costs, measured.

Two different budgets, and confusing them is how a skill system quietly gets
expensive:

  ALWAYS  every skill's description + when_to_use, and every agent's
          description, are in context in every session whether used or not.
          This is the number to keep small.

  ON LOAD a SKILL.md's body enters context when the skill loads, and stays
          for the rest of the session. references/ files cost nothing until
          read — that is what they are for.

  PRELOAD an agent's own body plus the full body of every skill in its
          `skills:` list, paid on every single invocation of that agent.

Run:  python scripts/budget.py [--verbose]

Token figures are ~chars/4. Good enough to compare and to spot growth; not
exact. For an exact count use the Claude API count_tokens endpoint.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

# Budgets are advisory ceilings, not hard limits — but crossing one is the
# signal to split into references/ or to evict a rule that stopped earning
# its place.
MAX_DESCRIPTION = 600      # chars, per skill (description + when_to_use)
MAX_SKILL_BODY = 12_000    # chars, per SKILL.md body
MAX_PRELOAD = 40_000       # chars, per agent invocation


def split_front_matter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body). Hand-parsed: no yaml dependency."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 3)
    if end < 0:
        return {}, text
    raw, body = text[4:end], text[end + 5:]

    meta: dict[str, object] = {}
    key: str | None = None
    for line in raw.splitlines():
        if line.startswith("  - "):                       # list item
            if key:
                # The header line left an empty string here; a list starting
                # under it replaces that, or every item is silently dropped.
                if not isinstance(meta.get(key), list):
                    meta[key] = []
                meta[key].append(line[4:].strip())
            continue
        if line.startswith((" ", "\t")) and key:          # folded continuation
            prev = meta.get(key, "")
            if isinstance(prev, str):
                meta[key] = f"{prev} {line.strip()}".strip()
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # "", ">" and "|" all mean "the content is on the following lines"
            meta[key] = "" if value in {">", "|", ">-", "|-"} else value
    return meta, body


def tok(n: int) -> str:
    return f"{n // 4:>6,}"


def main() -> int:
    skills, agents = {}, {}

    for path in sorted(ROOT.glob("skills/*/SKILL.md")):
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        name = path.parent.name
        refs = sorted((path.parent / "references").glob("*.md"))
        skills[name] = {
            "desc": len(str(meta.get("description", "")) + str(meta.get("when_to_use", ""))),
            "body": len(body),
            "hidden": str(meta.get("disable-model-invocation", "")).lower() == "true",
            "refs": {r.name: len(r.read_text(encoding="utf-8")) for r in refs},
        }

    for path in sorted(ROOT.glob("agents/*.md")):
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        pre = meta.get("skills") or []
        agents[path.stem] = {
            "desc": len(str(meta.get("description", ""))),
            "body": len(body),
            "model": str(meta.get("model", "inherit")),
            "effort": str(meta.get("effort", "inherit")),
            # `brain:x` is how a plugin skill is named; the folder is `x`.
            "pre": [s.split(":", 1)[-1] for s in pre],
        }

    print("=" * 74)
    print("ALWAYS IN CONTEXT  (paid every session, used or not)")
    print("=" * 74)
    visible = {k: v for k, v in skills.items() if not v["hidden"]}
    hidden = {k: v for k, v in skills.items() if v["hidden"]}
    s_desc = sum(v["desc"] for v in visible.values())
    a_desc = sum(v["desc"] for v in agents.values())
    print(f"  {len(visible):>3} skill descriptions        {s_desc:>7,} chars  ~{s_desc//4:,} tok")
    print(f"  {len(agents):>3} agent descriptions        {a_desc:>7,} chars  ~{a_desc//4:,} tok")
    print(f"      TOTAL                     {s_desc + a_desc:>7,} chars  ~{(s_desc + a_desc)//4:,} tok")
    if hidden:
        saved = sum(v["desc"] for v in hidden.values())
        print(f"  ({len(hidden)} user-invoked skill(s) hidden from context, saving ~{saved//4:,} tok)")

    over = [(k, v["desc"]) for k, v in visible.items() if v["desc"] > MAX_DESCRIPTION]
    for name, n in sorted(over, key=lambda x: -x[1]):
        print(f"  ! description over {MAX_DESCRIPTION}: {name} ({n})")

    print()
    print("=" * 74)
    print("PER AGENT INVOCATION  (own body + every preloaded skill body)")
    print("=" * 74)
    print(f"  {'agent':<20} {'model':<8} {'effort':<7} {'chars':>8} {'~tok':>7}  preloads")
    for name, a in sorted(agents.items(), key=lambda kv: -(kv[1]["body"] + sum(
            skills.get(s, {}).get("body", 0) for s in kv[1]["pre"]))):
        missing = [s for s in a["pre"] if s not in skills]
        total = a["body"] + sum(skills.get(s, {}).get("body", 0) for s in a["pre"])
        flag = " !" if total > MAX_PRELOAD else "  "
        print(f"{flag}{name:<20} {a['model']:<8} {a['effort']:<7} {total:>8,} {tok(total)}  "
              f"{', '.join(a['pre']) or '-'}")
        if missing:
            print(f"    ! preloads a skill that does not exist: {', '.join(missing)}")

    print()
    print("=" * 74)
    print("SKILL BODIES  (cost once, then stay for the whole session)")
    print("=" * 74)
    for name, s in sorted(skills.items(), key=lambda kv: -kv[1]["body"]):
        ref_total = sum(s["refs"].values())
        flag = " !" if s["body"] > MAX_SKILL_BODY else "  "
        extra = f"  + {len(s['refs'])} refs on demand ({ref_total//4:,} tok)" if s["refs"] else ""
        print(f"{flag}{name:<20} {s['body']:>7,} chars {tok(s['body'])}{extra}")
        if VERBOSE and s["refs"]:
            for rn, rl in sorted(s["refs"].items(), key=lambda kv: -kv[1]):
                print(f"      {rn:<28} {rl:>6,} chars {tok(rl)}")

    print()
    print("Reduce the ALWAYS number by: tightening descriptions, merging agents")
    print("that are never chosen apart, and setting disable-model-invocation on")
    print("skills only ever invoked by hand.")
    print("Reduce PER AGENT by: moving detail out of a SKILL.md into references/,")
    print("and preloading routers rather than large bodies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
