#!/usr/bin/env python3
"""Assemble a month of real work into evidence a review can act on.

Not a report -- an EVIDENCE PACK plus the prompt that reviews it. You run the
prompt in a fresh session (no memory of having built any of this) and bring the
answer back. A system reviewing itself in the context that produced it grades
its own homework.

  retro.py                       last 30 days, this directory's repo
  retro.py --days 90
  retro.py --repo C:/projects/x
  retro.py --prompt-only         just the prompt, for pasting

What it gathers, and why each one is here:

  agent + skill usage      what actually ran, at any spawn depth
  score coverage           how many of those runs got an outcome recorded.
                           THE key number: 40 runs and 3 records means the
                           scoreboard is noise, and every band built on it is
                           a guess wearing a number.
  attributed commits       which agents shipped code, and how much of the
                           period's work went through the brain at all
  brain changes            which rules were added, and what evidence each cited
  cold roster              what never ran -- never proof of bloat, only a
                           question worth asking out loud
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage as usage_mod                                       # noqa: E402
import docs as docs_mod                                         # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
TRAILER = re.compile(r"^Brain-(Agents|Skills):\s*(.+)$", re.MULTILINE)


def git(repo: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def commits(repo: str, days: int) -> list[dict]:
    """Commits in the window, with whatever attribution they carry."""
    sep = "\x1e"
    raw = git(repo, "log", f"--since={days}.days.ago", f"--format=%H{sep}%ad{sep}%s{sep}%b\x1d",
              "--date=short")
    out = []
    for chunk in raw.split("\x1d"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        parts = chunk.split(sep)
        if len(parts) < 3:
            continue
        sha, when, subject = parts[0], parts[1], parts[2]
        body = parts[3] if len(parts) > 3 else ""
        entry = {"sha": sha[:9], "date": when, "subject": subject,
                 "agents": [], "skills": []}
        for kind, names in TRAILER.findall(body):
            entry[kind.lower()] = [n.strip() for n in names.split(",") if n.strip()]
        out.append(entry)
    return out


def collect(repo: str, days: int) -> dict:
    use = usage_mod.collect(days)
    agents = docs_mod.read_agents()
    skills = docs_mod.read_skills()

    reg = {}
    reg_path = ROOT / "registry.json"
    if reg_path.exists():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))

    floor = (date.today() - timedelta(days=days)).isoformat()
    recorded = 0
    verdicts: list[str] = []
    for kind in ("agents", "skills", "workflows"):
        for name, e in reg.get(kind, {}).items():
            for note in e.get("notes", []):
                if note[:10] >= floor:
                    recorded += 1
                    verdicts.append(f"{kind[:-1]} {name}: {note}")

    log = commits(repo, days)
    attributed = [c for c in log if c["agents"] or c["skills"]]
    shipped: dict[str, int] = defaultdict(int)
    for c in attributed:
        for a in c["agents"]:
            shipped[a] += 1

    runs = sum(r["n"] for r in use["agents"].values())
    cold = sorted(n for n in agents if use["agents"].get(n, {}).get("n", 0) == 0)

    brain_log = [ln for ln in git(str(ROOT), "log", f"--since={days}.days.ago",
                                  "--format=%ad  %s", "--date=short").splitlines() if ln.strip()]

    return {
        "days": days, "repo": repo,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "runs": runs, "byAgent": use["agents"], "skills": use["skills"],
        "recorded": recorded, "verdicts": verdicts,
        "commits": len(log), "attributed": len(attributed), "shipped": dict(shipped),
        "cold": cold, "rosterSize": len(agents), "skillCount": len(skills),
        "brainChanges": brain_log,
        "unattributed": [c for c in log if not (c["agents"] or c["skills"])][:15],
    }


def render(d: dict) -> str:
    L = [f"# Practice review -- last {d['days']} days",
         f"", f"Repo: {d['repo']}    Generated: {d['generated']}", ""]

    cover = f"{d['recorded']}/{d['runs']}" if d["runs"] else "0/0"
    pct = (100 * d["recorded"] // d["runs"]) if d["runs"] else 0
    L += ["## The numbers", "",
          f"- agent runs: **{d['runs']}** across {len([a for a in d['byAgent'] if d['byAgent'][a]['n']])} kinds "
          f"-- counted across EVERY project on this machine, not just this repo",
          f"- outcomes recorded: **{cover}** ({pct}%)",
          f"- commits: **{d['commits']}**, of which **{d['attributed']}** carry attribution "
          f"-- from `{d['repo']}` ALONE",
          "",
          "> Runs and commits above are two different populations, so **their ratio is "
          "not a statistic**. A review read the two adjacently and derived a 19.6% "
          "attribution rate for the brain; the runs in that figure were mostly "
          "another repo's. Judge attribution per repo, with the same repo on both "
          "sides of the fraction.",
          "",
          f"- roster: {d['rosterSize']} agents, {len(d['cold'])} never ran in this window",
          f"- brain changed {len(d['brainChanges'])} time(s)", ""]

    if pct < 50:
        L += [f"> **Score coverage is {pct}%.** Every band and every retire/keep",
              "> decision below rests on that fraction. Treat the scoreboard as",
              "> anecdote until this is over half.", ""]

    if d["byAgent"]:
        L += ["## What ran", "", "| agent | runs | shipped commits |", "|---|---:|---:|"]
        for name, r in list(d["byAgent"].items())[:25]:
            if r["n"]:
                L.append(f"| {name} | {r['n']} | {d['shipped'].get(name, 0)} |")
        L.append("")

    if d["verdicts"]:
        L += ["## Outcomes recorded in this window", ""]
        L += [f"- {v}" for v in d["verdicts"][:40]] + [""]
    else:
        L += ["## Outcomes recorded in this window", "",
              "**None.** Nothing was recorded, so nothing below can be concluded",
              "from scores -- only from reading the work itself.", ""]

    if d["brainChanges"]:
        L += ["## What changed in the brain", ""]
        L += [f"- {c}" for c in d["brainChanges"][:30]] + [""]

    if d["unattributed"]:
        L += ["## Commits with no attribution", "",
              "Work that did not go through the brain, or went through it without",
              "recording. Both are findings; they are different findings.", ""]
        L += [f"- `{c['sha']}` {c['date']} {c['subject'][:70]}" for c in d["unattributed"]] + [""]

    if d["cold"]:
        L += ["## Never ran in this window", "",
              ", ".join(f"`{c}`" for c in d["cold"]), "",
              "Not proof of bloat. The question is whether the work never came up",
              "or the selection never reached them -- and only the commits above",
              "can tell you which.", ""]
    return "\n".join(L)


PROMPT = """\
# Review a month of real work with this framework

You are reviewing a system you did not build, in a session with no memory of
building it. That is deliberate. Be willing to conclude that parts of it are not
earning their keep -- a framework nobody can criticise is a framework nobody is
measuring.

The evidence pack is at:

    {pack}

Read it, then read what it points at. Answer these, in order, with evidence:

1. **Did the work go through the framework at all?** Compare attributed commits
   to total commits. If most work bypassed it, everything else here is noise and
   that is the finding -- say so first and explain what the bypass suggests.

2. **Is the scoreboard worth anything?** Score coverage is in the pack. Below
   50%, treat every band as anecdote and say which decisions you are therefore
   refusing to make.

3. **Which agents earned their place?** For each agent that ran: what did it
   ship, and did anything go wrong that traces to its brief rather than to the
   code? Use `blame.py --agent <name>` to see everything it shipped -- **the same
   defect class in three files is a defect in the agent; one is an incident.**

4. **Which agents never ran, and why?** Distinguish "the work never came up" from
   "selection never reached it". Read a few of the unattributed commits: was there
   work an idle agent should have been chosen for? Retire only on a recorded loss,
   never on silence.

5. **Did delegation beat doing it directly?** Look for tasks run both ways. If
   there is no control anywhere in the month, say so -- that is a process failure
   worth fixing before the next month.

6. **What did the brain learn, and was it true?** For each rule added in this
   window: what incident produced it, and has it fired since? A rule added from
   one incident that never recurred is a candidate for deletion.

Finish with **WHAT SHOULD CHANGE**, each item naming a file. Prefer deleting to
adding: this system's failure mode is accumulating rules nobody reads. If your
honest conclusion is that the framework is not beating a plain session with good
skills, say that plainly and give the evidence -- that conclusion is allowed and
is more useful than a polite one.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--out")
    ap.add_argument("--prompt-only", action="store_true", dest="prompt_only")
    args = ap.parse_args()

    if not git(args.repo, "rev-parse", "--git-dir"):
        print(f"{args.repo} is not a git repository")
        return 2

    out = Path(args.out) if args.out else ROOT / "docs" / "RETRO.md"
    if not args.prompt_only:
        data = collect(args.repo, args.days)
        out.write_text(render(data), encoding="utf-8")
        print(f"wrote {out}")
        print(f"  {data['runs']} agent run(s), {data['recorded']} outcome(s) recorded, "
              f"{data['attributed']}/{data['commits']} commit(s) attributed")

    print("\n" + "=" * 70)
    print("Paste this into a FRESH session:")
    print("=" * 70)
    print(PROMPT.format(pack=out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
