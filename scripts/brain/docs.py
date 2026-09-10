#!/usr/bin/env python3
"""Regenerate docs/AGENTS.md and docs/SKILLS.md from the files themselves.

A hand-maintained table of 45 agents is wrong within a week. This reads the
frontmatter, so the tables cannot drift from what actually exists -- which is
the whole reason a script does this instead of an agent.

  docs.py            write the tables
  docs.py --check    exit 1 if they are out of date (for a hook or CI)
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / "docs"

# Grouping is editorial: the table is for a human deciding who to ask, so it is
# ordered by "what kind of job is this", not alphabetically.
AGENT_GROUPS = [
    ("Coordination", ["orchestrator", "synthesizer", "planner", "pm"]),
    ("Reading & analysis", ["scout", "repo-reader", "dj-model-reader",
                            "dj-view-reader", "ticket-reader", "researcher",
                            "project-expert", "impact-mapper", "screenshot-reader"]),
    ("Consultants (advise, never edit)", ["mysql-consultant",
                                          "data-model-consultant",
                                          "inventory-consultant"]),
    ("Review & quality", ["reviewer", "security", "debugger", "optimizer",
                          "refactorer", "qa", "appsec-reviewer", "brain-keeper"]),
    ("Backend", ["backend-junior", "backend-senior", "dj-models", "dj-service",
                 "dj-manager", "dj-signals", "dj-migrations", "dj-command",
                 "dj-settings", "dj-mixin"]),
    ("Views & CRUD", ["dj-list-view", "dj-detail-view", "dj-create-view",
                      "dj-update-view", "dj-delete-view", "dj-action-view",
                      "dj-views"]),
    ("Front end", ["ui-ux", "frontend-junior", "frontend-senior",
                   "dj-templates", "dj-forms", "dj-templatetag"]),
    ("Whole slices", ["dj-model-slice", "dj-screen-slice"]),
    ("Operations", ["devops", "merge-resolver", "watchdog"]),
    ("The brain itself", ["scribe", "archivist"]),
]

SKILL_GROUPS = [
    ("The system", ["brain", "capture", "research", "onboard", "release"]),
    ("Craft (any language)", ["craft-code", "craft-security", "craft-reuse",
                              "craft-testing", "craft-git"]),
    ("Stack", ["stack-django", "ui-bootstrap", "arch-seams"]),
    ("Operations", ["ops-models", "ops-delegation", "ops-seniority",
                    "ops-scoring", "ops-sync", "ops-workflows",
                    "ops-integrations", "ops-maintain", "ops-postmortem",
                    "ops-prune"]),
    ("Domain workflows", ["tickets"]),
]

GRADE = {("haiku", "low"): "mechanical", ("sonnet", "low"): "junior",
         ("sonnet", "medium"): "junior", ("sonnet", "high"): "junior",
         ("opus", "high"): "senior", ("opus", "xhigh"): "senior",
         ("opus", "max"): "principal",
         ("fable", "high"): "senior", ("fable", "xhigh"): "senior",
         ("fable", "max"): "principal"}


def front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 3)
    if end < 0:
        return {}, text
    meta: dict = {}
    key = None
    for line in text[4:end].splitlines():
        if line.startswith("  - "):
            if key:
                if not isinstance(meta.get(key), list):
                    meta[key] = []
                meta[key].append(line[4:].strip())
            continue
        if line.startswith((" ", "\t")) and key:
            if isinstance(meta.get(key), str):
                meta[key] = f"{meta[key]} {line.strip()}".strip()
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            meta[key] = "" if value in {">", "|", ">-", "|-"} else value
    return meta, text[end + 5:]


def cell(s: str, limit: int = 150) -> str:
    s = " ".join(str(s).split()).replace("|", "/")
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def read_agents() -> dict:
    out = {}
    for path in sorted(ROOT.glob("agents/*.md")):
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        model = meta.get("model", "inherit")
        effort = meta.get("effort", "inherit")
        out[path.stem] = {
            "desc": meta.get("description", ""),
            "model": model,
            "effort": effort,
            "grade": GRADE.get((model, effort), "-"),
            "memory": bool(meta.get("memory")),
            "body": len(body),
            "pre": [s.split(":", 1)[-1] for s in (meta.get("skills") or [])],
        }
    return out


def read_skills() -> dict:
    out = {}
    for path in sorted(ROOT.glob("skills/*/SKILL.md")):
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        # rglob: references nest now (references/patterns/observer.md), and a
        # one-level count silently under-reports what a skill actually carries.
        refs = sorted(p.relative_to(path.parent / "references").as_posix()[:-3]
                      for p in (path.parent / "references").rglob("*.md"))
        out[path.parent.name] = {
            "desc": meta.get("description", ""),
            # Claude Code concatenates when_to_use onto the description before
            # it applies the per-skill cap -- `zDe()` in the binary returns
            # `description - whenToUse`. So it is not free extra routing
            # signal; it spends the same budget.
            "when": meta.get("when_to_use", "") or meta.get("whenToUse", ""),
            "hand": str(meta.get("disable-model-invocation", "")).lower() == "true",
            "body": len(body),
            "refs": refs,
        }
    return out


def render_agents(agents: dict) -> str:
    L = ["# Agents", "",
         "Generated by `scripts/brain/docs.py` - do not edit by hand.", "",
         f"{len(agents)} agents. **Grade** is model tier plus effort: a claim about how much",
         "judgement the task needs (`ops-seniority`). **Preloads** are the skills injected",
         "into the agent at startup - its knowledge, since a subagent remembers nothing",
         "between invocations. **Memory** means it keeps notes across sessions.", ""]
    seen = set()
    for title, names in AGENT_GROUPS:
        rows = [(n, agents[n]) for n in names if n in agents]
        if not rows:
            continue
        L += [f"## {title}", "",
              "| Agent | Grade | Model / effort | Preloads | What it does |",
              "|---|---|---|---|---|"]
        for name, a in rows:
            seen.add(name)
            mem = " *(memory)*" if a["memory"] else ""
            pre = ", ".join(f"`{p}`" for p in a["pre"]) or "-"
            L.append(f"| **`{name}`**{mem} | {a['grade']} | {a['model']} / {a['effort']} "
                     f"| {pre} | {cell(a['desc'])} |")
        L.append("")
    missing = sorted(set(agents) - seen)
    if missing:
        L += ["## Ungrouped", "",
              "*Not yet placed in a group in `docs.py` - add them there.*", "",
              "| Agent | Grade | Model / effort | What it does |", "|---|---|---|---|"]
        for n in missing:
            a = agents[n]
            L.append(f"| **`{n}`** | {a['grade']} | {a['model']} / {a['effort']} | {cell(a['desc'])} |")
        L.append("")
    return "\n".join(L)


def render_skills(skills: dict) -> str:
    total_refs = sum(len(s["refs"]) for s in skills.values())
    L = ["# Skills", "",
         "Generated by `scripts/brain/docs.py` - do not edit by hand.", "",
         f"{len(skills)} skills and {total_refs} references. A **skill** is a router: its",
         "description is in context every session, and its body loads when the skill does.",
         "A **reference** costs nothing until it is read, which is why there are many more",
         "of them (`brain` -> `references/splitting.md`).", ""]
    seen = set()
    for title, names in SKILL_GROUPS:
        rows = [(n, skills[n]) for n in names if n in skills]
        if not rows:
            continue
        L += [f"## {title}", "", "| Skill | References | What it covers |", "|---|---|---|"]
        for name, s in rows:
            seen.add(name)
            hand = " *(you invoke it)*" if s["hand"] else ""
            refs = ", ".join(f"`{r}`" for r in s["refs"]) or "-"
            L.append(f"| **`/brain:{name}`**{hand} | {refs} | {cell(s['desc'])} |")
        L.append("")
    missing = sorted(set(skills) - seen)
    if missing:
        L += ["## Ungrouped", "", "| Skill | References | What it covers |", "|---|---|---|"]
        for n in missing:
            s = skills[n]
            L.append(f"| **`/brain:{n}`** | {', '.join(s['refs']) or '-'} | {cell(s['desc'])} |")
        L.append("")
    return "\n".join(L)


def main(argv: list[str]) -> int:
    DOCS.mkdir(exist_ok=True)
    targets = {DOCS / "AGENTS.md": render_agents(read_agents()),
               DOCS / "SKILLS.md": render_skills(read_skills())}

    if "--check" in argv:
        stale = [p.name for p, body in targets.items()
                 if not p.exists() or p.read_text(encoding="utf-8") != body]
        if stale:
            print("out of date: " + ", ".join(stale))
            print("run: python scripts/brain/docs.py")
            return 1
        print("docs up to date")
        return 0

    for path, body in targets.items():
        path.write_text(body, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} ({len(body.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
