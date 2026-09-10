#!/usr/bin/env python3
"""Is a ticket's chain of artefacts actually complete?

**Why this is not a new set of files.** The AI-native SDLC playbook chains
`intent.md -> spec.md -> plan.md -> diff -> review -> incident`. That chain
already exists here, in the ticket store, under different names:

  intent    analysis.said / means / needs      what they asked vs what it is
  spec      analysis.suggested_skills          which rules govern this one
            + the module's project.ai_note     the operator's standing rules
  plan      analysis.plan                      ordered steps
            + analysis.suggested_agents        and who writes each part
  diff      commits naming the ticket          + their Brain-Agents trailers
  outcome   triage.resolution (the CUSTOMER)   + triage.report (us)
  rule      a `capture:` commit citing the id  what outlives the ticket

Adding markdown files for the same things would put two versions of each in the
repository, and the brain's own law is one source of truth. So this reads the
chain that exists and reports where it is broken.

**A broken link is not a scolding.** Each one has a cost that has already been
paid at least once: a ticket closed with no `triage.resolution` stays OPEN on
the helpdesk; work that ignored `suggested_agents` was work done by whoever had
the keyboard; a closed ticket that produced no rule is a defect free to recur.

  chain.py VEZ 70103      one ticket, link by link
  chain.py --gaps         every ticket whose chain is broken
  chain.py --gaps --done  only the ones already closed (the expensive ones)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
STORE = ROOT / "tickets_store"
SKIP_FILES = {"modules.json", "ai_log.json", "estimates.csv"}

OK, GAP, NA = "ok ", "GAP", " - "


def _load(module: str) -> dict:
    path = STORE / f"{module}.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _commits_for(tid: str) -> list[str]:
    """Commits whose subject or body names this ticket id."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-500", "--format=%h%x00%s%x00%b%x00<<>>"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return []
    hits = []
    for chunk in out.split("<<>>"):
        parts = chunk.strip().split("\x00")
        if len(parts) < 3:
            continue
        sha, subject, body = parts[0].strip(), parts[1], parts[2]
        if re.search(rf"#0*{re.escape(str(int(tid)))}\b", subject + body) or \
           re.search(rf"\b{re.escape(tid)}\b", subject):
            hits.append(f"{sha} {subject}")
    return hits


def links(module: str, tid: str, ticket: dict, project: dict) -> list[tuple[str, str, str]]:
    """Return [(state, name, detail)] for each link in the chain."""
    a = ticket.get("analysis") or {}
    tr = ticket.get("triage") or {}
    cl = ticket.get("closed") if isinstance(ticket.get("closed"), dict) else {}
    closed = bool(cl) or ticket.get("status") == "done"
    rows: list[tuple[str, str, str]] = []

    # --- intent ---------------------------------------------------------
    if a.get("means") and a.get("needs"):
        rows.append((OK, "intent", f"means/needs recorded ({a.get('confidence', '?')} confidence)"))
    elif a.get("means") or a.get("needs"):
        rows.append((GAP, "intent", "only half of means/needs — the other half is the reading nobody wrote down"))
    else:
        rows.append((GAP, "intent", "no analysis: the ticket has never been read past its title"))

    if a.get("missing"):
        rows.append((GAP, "intent", f"open question never asked: {str(a['missing'])[:70]}"))

    # --- spec -----------------------------------------------------------
    skills = a.get("suggested_skills") or []
    if skills:
        rows.append((OK, "spec", f"governed by: {', '.join(skills[:5])}"))
    else:
        rows.append((GAP, "spec", "no skill named — nothing says which rules govern this"))

    if (project.get("ai_note") or "").strip():
        rows.append((OK, "spec", "the module carries standing project rules (/brain:project-rules)"))

    # --- plan -----------------------------------------------------------
    plan = a.get("plan") or []
    agents = a.get("suggested_agents") or []
    if plan:
        rows.append((OK, "plan", f"{len(plan)} step(s)"))
    else:
        rows.append((GAP, "plan", "no plan — work would start by deciding what to do while doing it"))
    if agents:
        rows.append((OK, "plan", f"names who writes it: {', '.join(agents[:5])}"))
    else:
        rows.append((GAP, "plan", "no agent named — this defaults to whoever has the keyboard"))

    # --- diff -----------------------------------------------------------
    commits = _commits_for(tid)
    if commits:
        rows.append((OK, "diff", f"{len(commits)} commit(s): {commits[0][:60]}"))
        ran = set()
        for line in commits:
            ran |= set(re.findall(r"[a-z][a-z0-9-]+", line.split(":", 1)[-1]))
        # Only meaningful when the analysis actually named agents.
        if agents:
            named = {x.lower() for x in agents}
            trailed = _trailer_agents(tid)
            if trailed:
                missed = sorted(named - trailed)
                rows.append((OK if not missed else GAP, "diff",
                             f"agents that ran: {', '.join(sorted(trailed))}"
                             + (f" — planned but absent: {', '.join(missed)}" if missed else "")))
            else:
                rows.append((NA, "diff", "no Brain-Agents trailer — cannot tell what ran"))
    elif closed:
        rows.append((GAP, "diff", "closed, but no commit names this ticket"))
    else:
        rows.append((NA, "diff", "not started"))

    # --- outcome --------------------------------------------------------
    resolution = (tr.get("resolution") or cl.get("resolution") or "").strip()
    if closed and not resolution:
        rows.append((GAP, "outcome", "DONE WITH NO RESOLUTION — this stays OPEN on the helpdesk"))
    elif resolution:
        rows.append((OK, "outcome", f"resolution for the customer ({len(resolution)} chars)"))
    else:
        rows.append((NA, "outcome", "not closed yet"))

    if closed:
        if (tr.get("report") or "").strip():
            rows.append((OK, "outcome", "internal report written"))
        else:
            rows.append((GAP, "outcome",
                         "no internal report — nothing to read in six months"))

    # --- rule -----------------------------------------------------------
    if closed:
        captures = [c for c in commits if re.match(r"^\w+ capture", c)]
        if captures:
            rows.append((OK, "rule", captures[0][:70]))
        else:
            rows.append((NA, "rule",
                         "no capture commit — fine if nothing general was learned, "
                         "a recurrence waiting to happen if something was"))
    return rows


def _trailer_agents(tid: str) -> set[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-500", "--format=%s%n%b%n<<>>"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    found: set[str] = set()
    for chunk in out.split("<<>>"):
        if not re.search(rf"#0*{re.escape(str(int(tid)))}\b", chunk):
            continue
        for line in chunk.splitlines():
            if line.startswith("Brain-Agents:"):
                found |= {x.strip().lower() for x in line.split(":", 1)[1].split(",") if x.strip()}
    return found


def show(module: str, tid: str) -> int:
    data = _load(module)
    ticket = (data.get("tickets") or {}).get(tid)
    if not ticket:
        print(f"{module}#{tid} not found in the store")
        return 1
    rows = links(module, tid, ticket, data.get("project") or {})
    print("=" * 68)
    print(f"CHAIN  {module}#{tid}  [{ticket.get('status', '?')}]  {str(ticket.get('title'))[:40]}")
    print("=" * 68)
    last = None
    for state, name, detail in rows:
        print(f"  {state}  {name if name != last else '':<8} {detail}")
        last = name
    gaps = sum(1 for s, _n, _d in rows if s == GAP)
    print()
    print("chain complete" if not gaps else f"{gaps} broken link(s)")
    return 0


def gaps(only_done: bool) -> int:
    worst: list[tuple[str, str, int, str]] = []
    for path in sorted(STORE.glob("*.json")):
        if path.name in SKIP_FILES:
            continue
        data = _load(path.stem)
        project = data.get("project") or {}
        for tid, t in (data.get("tickets") or {}).items():
            if not isinstance(t, dict):
                continue
            closed = bool(t.get("closed")) or t.get("status") == "done"
            if only_done and not closed:
                continue
            rows = links(path.stem, tid, t, project)
            bad = [d for s, _n, d in rows if s == GAP]
            if bad:
                worst.append((path.stem, tid, len(bad), bad[0]))
    worst.sort(key=lambda r: -r[2])
    print("=" * 68)
    print(f"BROKEN CHAINS  ({len(worst)} ticket(s)"
          + (", closed only)" if only_done else ")"))
    print("=" * 68)
    for module, tid, n, first in worst[:40]:
        print(f"  {n}  {module}#{tid:<8} {first[:64]}")
    if not worst:
        print("  none")
    print("\nOne ticket, link by link:  chain.py <MODULE> <id>")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--gaps" in sys.argv:
        return gaps("--done" in sys.argv)
    if len(args) == 2:
        return show(args[0].upper(), args[1])
    print(__doc__.strip().splitlines()[-3])
    print("  chain.py <MODULE> <id> | --gaps [--done]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
