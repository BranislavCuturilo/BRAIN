#!/usr/bin/env python3
"""The graph refuses to invent nodes, and attributes a reference to its owner.

Both assertions exist because the first version of graph.py got them wrong, and
both failures looked like success:

  * `node()` created a node on first mention, so a commit trailer naming a
    deleted agent produced a healthy-looking node and `--dangling` found
    nothing. A rot check that cannot report rot is worse than none.
  * the reference regex ignored the owner prefix, so five perfectly good
    cross-skill links (`ops-integrations/references/servers.md`) were reported
    as broken pointers -- an alarm that cries wolf gets switched off.

Everything is built in a temp tree, and the commit history is injected rather
than read, so this test says the same thing next year.

  python scripts/brain/test_graph.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph as G  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def rec(sha: str, subject: str, body: str = "") -> str:
    """One record in the format build() expects from `git log`."""
    return f"{sha}\x1f{subject}\x1f{body}\x1e"


def tree(base: Path) -> None:
    (base / "skills" / "alpha" / "references").mkdir(parents=True)
    (base / "skills" / "beta" / "references").mkdir(parents=True)
    (base / "agents").mkdir()
    (base / "tickets_store").mkdir()

    (base / "skills" / "alpha" / "SKILL.md").write_text(
        "# alpha\n"
        "Read `references/own.md` for the detail.\n"
        # The trap: beta's file, named from alpha. Attributing this to alpha
        # invents `alpha/shared.md`, which does not exist -> a false alarm.
        "The boundary is `beta/references/shared.md`.\n"
        "Also `../beta/references/shared.md` written the other way.\n"
        "And one that really is gone: `references/vanished.md`.\n",
        encoding="utf-8")
    (base / "skills" / "alpha" / "references" / "own.md").write_text("x", encoding="utf-8")
    (base / "skills" / "beta" / "SKILL.md").write_text("# beta\n", encoding="utf-8")
    (base / "skills" / "beta" / "references" / "shared.md").write_text("x", encoding="utf-8")
    # Nothing routes to this one.
    (base / "skills" / "beta" / "references" / "lonely.md").write_text("x", encoding="utf-8")

    (base / "agents" / "worker.md").write_text(
        "---\nname: worker\nskills:\n  - brain:alpha\n  - brain:gone\ntools: Read\n---\nbrief\n",
        encoding="utf-8")
    (base / "agents" / "inline.md").write_text(
        "---\nname: inline\nskills: [brain:beta]\nmodel: haiku\n---\nbrief\n",
        encoding="utf-8")
    (base / "tickets_store" / "DEMO.json").write_text(
        '{"tickets": {"111": {"title": "t"}}}', encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        tree(base)
        log = (rec("aaaaaaaaaa1", "fix: thing for DEMO#111",
                   "Brain-Agents: worker, ghost\nBrain-Skills: alpha\n")
               + rec("bbbbbbbbbb2", "chore: unrelated DEMO#999", ""))
        g = G.build(base, log=log)
        ids = g["nodes"]

        print("a reference belongs to the skill that OWNS it, not the one naming it")
        ok("reference:beta/shared.md" in ids, "the cross-skill link resolves to beta")
        ok("reference:alpha/shared.md" not in ids,
           "and NOT to alpha -- that is the false alarm this test exists for")
        ok(any(e["src"] == "skill:alpha" and e["dst"] == "reference:beta/shared.md"
               for e in g["edges"]), "alpha still gets an edge to it")
        ok(ids["reference:beta/shared.md"]["declared"], "beta/shared.md is backed by a file")

        print("a genuinely missing reference is still caught")
        ok("reference:alpha/vanished.md" in ids and not ids["reference:alpha/vanished.md"]["declared"],
           "vanished.md is a node, but undeclared")
        d = G.dangling(g)
        ok(any(e["dst"] == "reference:alpha/vanished.md" for e in d), "and it is reported")

        print("mention is not existence -- the whole point of `declared`")
        ok("agent:ghost" in ids and not ids["agent:ghost"]["declared"],
           "an agent named only by a trailer stays undeclared")
        ok(ids["agent:worker"]["declared"], "one with a brief is declared")
        ok("skill:gone" in ids and not ids["skill:gone"]["declared"],
           "a preloaded skill that does not exist is undeclared")
        ok(any(e["dst"] == "agent:ghost" for e in d), "the ghost shows up in dangling()")
        ok(any(e["dst"] == "skill:gone" for e in d), "so does the missing skill")

        print("tickets: in the store or not")
        ok(ids["ticket:DEMO#111"]["declared"], "a ticket the store holds is declared")
        ok("ticket:DEMO#999" in ids and not ids["ticket:DEMO#999"]["declared"],
           "one only a commit names is not")

        print("frontmatter: both list forms, and the next key ends the list")
        pre = {e["dst"] for e in g["edges"] if e["src"] == "agent:worker" and e["rel"] == "preloads"}
        ok(pre == {"skill:alpha", "skill:gone"}, f"block form: {pre}")
        ok(G._frontmatter_skills("---\nskills: [brain:beta]\nmodel: x\n---\n") == ["brain:beta"],
           "inline form")
        ok(G._frontmatter_skills("skills:\n  - a\ntools: Read\n  - b\n") == ["a"],
           "`tools:` ends the list -- `- b` below it is NOT a skill")
        ok(G._frontmatter_skills("name: x\n") == [], "no skills key -> nothing")

        print("an unrouted reference is a NODE here, and health.py's finding to report")
        # graph.py deliberately does not report orphans: health.py already does,
        # with nested references handled. Two implementations of one check is
        # how two answers to one question come to exist. The node still has to
        # exist, or the graph would be a partial picture.
        ok("reference:beta/lonely.md" in ids and ids["reference:beta/lonely.md"]["declared"],
           "the unrouted file is present and declared")
        ok(not any(e["dst"] == "reference:beta/lonely.md" for e in g["edges"]),
           "with no edge into it -- which is what 'unrouted' means")
        ok("orphan" not in G.render_summary(g).lower(),
           "and the summary does not repeat health.py's job")

        print("resolve(): a bare ticket, a bare name, or a full id")
        ok(G.resolve(g, "DEMO#111") == "ticket:DEMO#111", "bare ticket reference")
        ok(G.resolve(g, "worker") == "agent:worker", "bare name")
        ok(G.resolve(g, "skill:alpha") == "skill:alpha", "full id")
        ok(G.resolve(g, "nothing-like-this") == "", "unknown -> empty, not a guess")

        print("neighbours() walks AGAINST the arrows too")
        # commit -ran-> worker. Asking the ticket what came of it has to travel
        # ticket <-about- commit -ran-> worker, which is backwards then forwards.
        near = G.neighbours(g, "ticket:DEMO#111")
        commit = next((k for k in near if k.startswith("commit:")), "")
        ok(near.get(commit) == 1, f"the commit is ONE hop from the ticket: {commit} @ {near.get(commit)}")
        # The depth has to be right, not merely reachable. Expanding from the
        # live set instead of a frozen frontier collapsed every node inside the
        # limit onto hop 1, which made the whole hop count meaningless.
        ok(near.get("agent:worker") == 2,
           f"and the agent is TWO -- ticket <- commit -> agent: {near.get('agent:worker')}")
        ok(near.get("skill:alpha") == 2, f"a skill the commit used is two: {near.get('skill:alpha')}")
        ok(G.neighbours(g, "ticket:DEMO#111", depth=1) == {"ticket:DEMO#111": 0, commit: 1},
           "depth=1 stops at the commit and goes no further")

        print("cypher: MERGE only, and the rot arrives LABELLED rather than dropped")
        cy = G.render_cypher(g)
        ok("CREATE " not in cy, "no CREATE -- re-running must not duplicate")
        ok(cy.count("MERGE (n:") == len(ids), "one node statement per node")
        ghost = [ln for ln in cy.splitlines() if "agent:ghost" in ln and ln.startswith("MERGE")]
        ok(len(ghost) == 1 and "n.declared=false" in ghost[0],
           f"the ghost loads marked false, not dropped and not pretended real: {ghost}")
        worker = [ln for ln in cy.splitlines() if "agent:worker" in ln and ln.startswith("MERGE")]
        ok(worker and "n.declared=true" in worker[0], "a real agent loads as declared")
        ok(any(ln.startswith("MATCH") and "agent:ghost" in ln for ln in cy.splitlines()),
           "the edge to it is kept -- that edge IS the finding")
        ok("\\'" in G.render_cypher({"nodes": {"x": {"id": "a'b", "kind": "skill",
                                                     "name": "a'b", "declared": True}},
                                     "edges": []}),
           "a quote in a name is escaped, not left to break the statement")

        print("nothing raises on an empty or broken tree")
        empty = G.build(base / "nope", log="")
        ok(empty["nodes"] == {} and empty["edges"] == [],
           f"a missing tree is empty, not this brain's findings: {len(empty['nodes'])} node(s)")
        ok(isinstance(G.render_summary(empty), str), "and it still renders")
        ok(G.build(base, log="garbage with no separators")["nodes"], "junk log -> files still load")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
