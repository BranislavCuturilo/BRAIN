#!/usr/bin/env python3
"""The brain's own graph: who ran what, under which rule, on which ticket.

Four questions used to need four tools and a person to join them by hand:

  this ticket looks like that one -- what did we actually do about it,
  which agent wrote the code, which rule came out of it, and did it hold?

Every edge for that already existed, spread across git trailers, agent
frontmatter, skill routers and the findings directory. Nothing here invents a
relationship; it reads the ones that are written down and joins them.

WHY THIS IS A SCRIPT AND NOT A DATABASE. Measured 2026-09-10, the whole brain
is 28 skills, 71 references, 56 agents, 493 commits, 57 tagged tickets and 15
findings -- under a thousand nodes. Neo4j is built for millions; at this size a
dict is faster, needs no server, and cannot be down when a hook asks it
something. That last point is not a preference: every gate in `hooks/` fails
OPEN, and a gate that needs a database fails CLOSED the moment the database is
not running.

WHEN THAT FLIPS. Two conditions, either one:
  * a question needs transitive closure -- "every path from an untrusted input
    to a write" over a codebase like acme-audit (8,718 Python files, 592
    view functions, measured the same day). Cypher does that in one query;
    grep cannot do it at all.
  * this graph passes roughly a hundred thousand edges.
Until then `--cypher` is the escape hatch, and it is only ever emitted when
asked for. `~/.claude/skills/graphify` already ships `--neo4j-push`, so the
loading half is solved too and does not need writing here.

  graph.py                     node and edge counts, by kind
  graph.py --json              the whole thing, for another tool
  graph.py trace DEMO#94313     one ticket -> commits -> agents -> skills -> findings
  graph.py trace agent:scout   works on any node id
  graph.py --dangling          edges whose target does not exist -- the rot check
  graph.py --cypher            MERGE statements for Neo4j; writes nothing itself
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ticket_ref  # noqa: E402  -- THE `<MODULE>#<id>` rule lives there, not here

#: `Brain-Agents: a, b` / `Brain-Skills: x, y` -- written by craft-git, read by blame.py
TRAILER_RE = re.compile(r"^Brain-(Agents|Skills):\s*(.+)$", re.M)
#: A router pointing at a reference. The owner prefix is OPTIONAL and that is
#: the whole difficulty: `references/lista.md` means this skill's own file,
#: while `ops-integrations/references/servers.md`, `brain/references/x.md` and
#: `../ops-delegation/references/consult-chain.md` all name someone else's.
#: Read without the prefix, five perfectly good cross-skill links looked like
#: broken pointers on the first run of this tool.
#: The trailing group allows ONE nested segment, because references nest
#: (`references/patterns/observer.md`) and a one-level pattern silently drops
#: them -- health.py learned the same lesson and switched to rglob.
REF_RE = re.compile(r"(?:\.\./)?(?:([a-z0-9-]+)/)?references/((?:[a-z0-9-]+/)?[a-z0-9-]+\.md)")
#: `DEMO#94313` in a commit subject or body
TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,15})#(\d+)\b")
#: `skills/<name>/` or `agents/<name>.md` inside a finding's free-text `where`
WHERE_SKILL_RE = re.compile(r"skills/([a-z0-9-]+)/")
WHERE_AGENT_RE = re.compile(r"agents/([a-z0-9-]+)\.md")


def _git(base: Path, *args: str) -> str:
    """Git output, or "" -- a graph that raises because the repo is mid-rebase
    is a graph nobody runs."""
    try:
        p = subprocess.run(["git", "-C", str(base), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _frontmatter_skills(text: str) -> list[str]:
    """The `skills:` list in an agent brief. Handles the block form the agents
    actually use and the inline form, because one file written the other way
    must not silently contribute no edges."""
    # `[ \t]*`, never `\s*`: `\s` matches newlines, so a greedy `\s*(.*)` after
    # `skills:` swallowed the blank rest of the line AND the first list item,
    # which then vanished from the graph while the remaining items looked fine.
    m = re.search(r"^skills:[ \t]*(.*)$", text, re.M)
    if not m:
        return []
    inline = m.group(1).strip()
    if inline.startswith("["):
        return [x.strip().strip("\"'") for x in inline.strip("[]").split(",") if x.strip()]
    out = []
    for line in text[m.end():].splitlines():
        if not line.strip():
            continue
        if not re.match(r"^\s+-\s+", line):
            break                      # the next frontmatter key ends the list
        out.append(line.split("-", 1)[1].strip().strip("\"'"))
    return out


def build(root: Path | None = None, log: str | None = None) -> dict:
    """Nodes keyed `kind:name`, plus a flat edge list. Deterministic: the same
    repository state yields byte-identical JSON.

    `log` overrides the git call with raw `%H\\x1f%s\\x1f%b\\x1e` records, so the
    trailer and ticket parsing can be tested against a fixed history instead of
    against whatever happens to be committed today."""
    base = Path(root) if root else ROOT
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(kind: str, name: str, declared: bool = False, **extra) -> str:
        """`declared` means something on disk defines this node -- a SKILL.md, an
        agent brief, a commit, a ticket in the store. A node created only
        because an EDGE named it stays undeclared, and that is the whole point:
        a trailer naming an agent that no longer exists must show up as rot, not
        be quietly conjured into a node that makes the graph look healthy."""
        nid = f"{kind}:{name}"
        if nid not in nodes:
            nodes[nid] = {"id": nid, "kind": kind, "name": name, "declared": declared, **extra}
        else:
            if declared:
                nodes[nid]["declared"] = True
            if extra:
                nodes[nid].update(extra)
        return nid

    def edge(src: str, rel: str, dst: str) -> None:
        edges.append({"src": src, "rel": rel, "dst": dst})

    # -- skills and the references they route to -----------------------------
    for sk in sorted((base / "skills").glob("*/SKILL.md")):
        sid = node("skill", sk.parent.name, declared=True)
        body = _read(sk)
        for owner, ref in sorted(set(REF_RE.findall(body))):
            # Named by skill AND file: two skills may each own a `lista.md`,
            # and collapsing them would invent an edge between the two skills.
            edge(sid, "points", node("reference", f"{owner or sk.parent.name}/{ref}"))

    # rglob, not glob: references nest, and a one-level sweep would register
    # `references/patterns/observer.md` as absent -- inventing rot instead of
    # finding it. Reporting which of these nothing routes to is health.py's
    # job (it already does, correctly); they are registered here only so the
    # graph is complete.
    for rf in sorted((base / "skills").rglob("references/*.md")) + \
              sorted((base / "skills").rglob("references/*/*.md")):
        try:
            skill_dir = rf.parents[len(rf.relative_to(base / "skills").parts) - 2]
            rel = rf.relative_to(skill_dir / "references").as_posix()
            node("reference", f"{skill_dir.name}/{rel}", declared=True)
        except (ValueError, IndexError):
            continue

    # -- agents and the skills they preload ----------------------------------
    for ag in sorted((base / "agents").glob("*.md")):
        aid = node("agent", ag.stem, declared=True)
        for s in _frontmatter_skills(_read(ag)):
            edge(aid, "preloads", node("skill", s.split(":")[-1]))

    # -- tickets that actually exist in the store ----------------------------
    # Registered BEFORE commits, so a commit naming a ticket the store has never
    # heard of stays undeclared and surfaces in --dangling.
    for tf in sorted((base / "tickets_store").glob("*.json")):
        try:
            data = json.loads(_read(tf) or "{}")
        except ValueError:
            continue
        for tid in (data.get("tickets") or {}):
            if ticket_ref.names_a_ticket(f"{tf.stem}#{tid}"):
                node("ticket", ticket_ref.key(f"{tf.stem}#{tid}"), declared=True)

    # -- commits: who ran, under which rules, about which ticket -------------
    raw = log if log is not None else _git(base, "log", "--format=%H%x1f%s%x1f%b%x1e")
    for rec in raw.split("\x1e"):
        if not rec.strip():
            continue
        parts = rec.lstrip("\n").split("\x1f")
        if len(parts) < 3:
            continue
        sha, subject, body = parts[0].strip(), parts[1], parts[2]
        if not sha:
            continue
        cid = node("commit", sha[:9], declared=True, subject=subject[:90])
        for kind, names in TRAILER_RE.findall(body):
            rel, nkind = ("ran", "agent") if kind == "Agents" else ("used", "skill")
            for n in (x.strip() for x in names.split(",")):
                if n:
                    edge(cid, rel, node(nkind, n.split(":")[-1]))
        for mod, tid in TICKET_RE.findall(f"{subject}\n{body}"):
            ref = f"{mod}#{tid}"
            # Through the shared rule, so this agrees with the visual-diff
            # pipeline instead of being a second opinion about the same string.
            if ticket_ref.names_a_ticket(ref):
                edge(cid, "about", node("ticket", ticket_ref.key(ref)))

    # -- findings, attached to whatever their `where` actually names ----------
    # Guarded on the directory existing: `findings.across()` carries its own
    # repo mapping and answers with THIS brain's findings whatever root it is
    # handed, so building a graph of some other tree would silently import
    # fifteen findings that have nothing to do with it.
    found = []
    if (base / ".claude" / "findings").is_dir():
        try:
            import findings as F
            found = F.across(base)
        except Exception:                                      # noqa: BLE001
            found = []
    for f in found:
        fid = node("finding", str(f.get("id") or "?"), declared=True,
                   status=str(f.get("status") or ""), repo=str(f.get("repo") or ""))
        where = str(f.get("where") or "")
        for s in set(WHERE_SKILL_RE.findall(where)):
            edge(fid, "concerns", node("skill", s))
        for a in set(WHERE_AGENT_RE.findall(where)):
            edge(fid, "concerns", node("agent", a))
        t = str(f.get("ticket") or "")
        if t and ticket_ref.names_a_ticket(t):
            edge(fid, "about", node("ticket", ticket_ref.key(t)))

    edges.sort(key=lambda e: (e["src"], e["rel"], e["dst"]))
    return {"nodes": nodes, "edges": edges}


def dangling(g: dict) -> list[dict]:
    """Edges pointing at something nothing on disk defines.

    A commit trailer naming an agent that has since been deleted, a skill
    preloading one that was renamed, a reference no file backs, a ticket the
    store has never seen -- each of these reads as a working link right up
    until someone follows it. The check only works because `node()` refuses to
    treat "mentioned" as "exists"; conjuring the node on first mention is how
    this whole class of rot stays invisible."""
    return [e for e in g["edges"]
            if not (g["nodes"].get(e["dst"]) or {}).get("declared")]


def neighbours(g: dict, nid: str, depth: int = 3) -> dict:
    """Breadth-first from one node, following edges in BOTH directions -- the
    useful question ("what came out of this ticket") runs against the arrows."""
    out: dict[str, int] = {nid: 0}
    frontier = {nid}
    for d in range(1, depth + 1):
        # The frontier is FROZEN for the pass. Expanding from `out` while
        # writing into it let a node reached this round immediately expand
        # again in the same round, so everything within `depth` collapsed onto
        # hop 1 -- a ticket reported 173 direct neighbours, agents included,
        # when it has eleven commits and nothing else touching it.
        nxt: set[str] = set()
        for e in g["edges"]:
            for a, b in ((e["src"], e["dst"]), (e["dst"], e["src"])):
                if a in frontier and b not in out:
                    nxt.add(b)
        if not nxt:
            break
        for b in nxt:
            out[b] = d
        frontier = nxt
    return out


def resolve(g: dict, raw: str) -> str:
    """A node id, or a bare ticket reference, or a bare name -- so `trace
    DEMO#94313` and `trace ticket:DEMO#94313` are the same request."""
    if raw in g["nodes"]:
        return raw
    if ticket_ref.names_a_ticket(raw):
        cand = f"ticket:{ticket_ref.key(raw)}"
        if cand in g["nodes"]:
            return cand
    for kind in ("skill", "agent", "finding", "commit", "ticket", "reference"):
        if f"{kind}:{raw}" in g["nodes"]:
            return f"{kind}:{raw}"
    return ""


def render_summary(g: dict) -> str:
    from collections import Counter
    nk = Counter(n["kind"] for n in g["nodes"].values())
    ek = Counter(e["rel"] for e in g["edges"])
    L = ["=" * 62, "BRAIN GRAPH — what is written down, joined up", "=" * 62,
         f"  {len(g['nodes'])} node(s), {len(g['edges'])} edge(s)", "", "  nodes"]
    for k, v in nk.most_common():
        L.append(f"    {k:<12} {v:>5}")
    L.append("")
    L.append("  edges")
    for k, v in ek.most_common():
        L.append(f"    {k:<12} {v:>5}")
    # Deliberately narrow. Broken `references/` links, unrouted references and
    # an agent preloading a missing skill are ALREADY health.py's, and it does
    # them better -- it walks nested references and separates archived from
    # deleted. A second implementation of the same check is how two answers to
    # one question come to exist. What only this tool can see is the history:
    # a commit trailer naming an agent, or a commit naming a ticket.
    d = [e for e in dangling(g) if e["src"].startswith("commit:")]
    if d:
        L.append("")
        L.append(f"  {len(d)} thing(s) named by a COMMIT that nothing defines now —")
        L.append("  a built-in agent, a ticket the store never had, or one of ours")
        L.append("  deleted since. Only visible here; health.py reads the tree, not the log:")
        for e in d[:10]:
            L.append(f"    {e['src']} -{e['rel']}-> {e['dst']}")
    L.append("")
    L.append("  Rot in skills, references and preloads belongs to health.py — not repeated here.")
    L.append("")
    L.append("  graph.py trace <ticket|agent:x|skill:y>   follow one thread")
    return "\n".join(L)


def render_trace(g: dict, nid: str) -> str:
    near = neighbours(g, nid)
    L = [f"TRACE  {nid}", "=" * 62]
    n0 = g["nodes"].get(nid) or {}
    if n0.get("subject"):
        L.append(f"  {n0['subject']}")
    by_depth: dict[int, list[str]] = {}
    for k, d in near.items():
        if d:
            by_depth.setdefault(d, []).append(k)
    if not by_depth:
        L.append("  nothing connects to it -- which is itself the answer")
        return "\n".join(L)
    # Capped per hop. An uncapped trace from a busy agent prints several hundred
    # commits, and a wall of output is read exactly as carefully as no output.
    CAP = 12
    for d in sorted(by_depth):
        got = sorted(by_depth[d])
        L.append(f"\n  {d} hop(s) — {len(got)}")
        for k in got[:CAP]:
            n = g["nodes"].get(k, {})
            extra = n.get("subject") or n.get("status") or ""
            L.append(f"    {k}{'  — ' + extra if extra else ''}")
        if len(got) > CAP:
            L.append(f"    … and {len(got) - CAP} more (graph.py --json for all)")
    return "\n".join(L)


def render_cypher(g: dict) -> str:
    """MERGE only, so re-running never duplicates. Emitted on demand and piped
    by hand -- nothing here opens a socket."""
    def esc(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace("'", "\\'")
    L = ["// generated by scripts/brain/graph.py -- MERGE, safe to re-run",
         "// n.declared=false means nothing on disk defines it: a trailer naming",
         "// a deleted agent, a pointer at a reference that is gone. Loading them",
         "// as if they were real is exactly how the rot stops being visible."]
    for n in g["nodes"].values():
        label = n["kind"].capitalize()
        L.append(f"MERGE (n:{label} {{id:'{esc(n['id'])}'}}) "
                 f"SET n.name='{esc(n['name'])}', n.declared={'true' if n.get('declared') else 'false'};")
    for e in g["edges"]:
        L.append(f"MATCH (a {{id:'{esc(e['src'])}'}}), (b {{id:'{esc(e['dst'])}'}}) "
                 f"MERGE (a)-[:{e['rel'].upper()}]->(b);")
    return "\n".join(L)


def main() -> int:
    args = sys.argv[1:]
    g = build()
    if args and args[0] == "trace":
        if len(args) < 2:
            print("trace what? e.g. graph.py trace DEMO#94313")
            return 2
        nid = resolve(g, args[1])
        if not nid:
            print(f"no node matches {args[1]!r} — try graph.py --json")
            return 1
        print(render_trace(g, nid))
        return 0
    if "--json" in args:
        print(json.dumps({"nodes": list(g["nodes"].values()), "edges": g["edges"]},
                         ensure_ascii=False, indent=2))
        return 0
    if "--cypher" in args:
        print(render_cypher(g))
        return 0
    if "--dangling" in args:
        d = dangling(g)
        for e in d:
            print(f"{e['src']} -{e['rel']}-> {e['dst']}")
        print(f"{len(d)} dangling edge(s)")
        return 1 if d else 0
    print(render_summary(g))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
