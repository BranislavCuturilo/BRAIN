#!/usr/bin/env python3
"""What the extension's tickets say about docs/pages -- and what to write next.

Every ticket BugReporter sends carries a trace tag (ebr_tag.py): the
screens the session touched and whether each had a context file, the role and
flags, the extension's verdict (`kind`), whether the session was split, and
which fields the reporter corrected. sync.py pulls the description into the
store, and the close/triage records say what actually happened. Read together
they answer, with no model and no key:

  coverage    which screens generate tickets WITHOUT a context file -- ranked,
              so the next file to write is the one that would have mattered most
  lies        `kind: limitation` closed as FIXED -- the page said "on purpose"
              about a real defect; the `limits` entry is wrong and the reporter
              was told there was nothing to fix
  missing     `kind: bug` closed as BY DESIGN / NO RIGHTS -- the page (or its
              `requires`) was silent where it should have spoken
  edits       which fields the reporter corrected, per skill and per screen --
              where the extension's draft keeps being wrong
  split       sessions the extension split by root cause, and whether the
              siblings were then resolved separately (the split was right) or
              together (it was not)

**Why a report and not an edit.** Learning here is a ranked list of gaps that
a person or an agent turns into docs/pages edits with a numbered "da" -- the
same gate ops-prune uses. A model rewriting the source of truth in the
background is rot nobody sees; this prints proposals and writes nothing.

**What "fixed" and "by design" mean here.** Keyword classes over the
resolution text, Serbian and English, listed in FIXED / BY_DESIGN below. They
are signals to check, not verdicts -- each proposal names the ticket so the
close note can be read.

  ebr_review.py                 the report, over tickets_store/
  ebr_review.py --root DIR      another store (tests)
  ebr_review.py --json          for health.py and the dashboard
  cadence.py did ebr-review     record that it ran (every 14 days)
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ebr_tag  # noqa: E402
import store  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: resolution text that says a defect was corrected
FIXED = ("ispravlj", "popravlj", "resen", "rešen", "fixed", "fix ", "fix.", "otklonj",
         "bug", "greska je", "greška je")
#: resolution text that says nothing was wrong with the code
BY_DESIGN = ("po dizajnu", "namerno", "nije greska", "nije greška", "ogranicenj",
             "ograničenj", "nema pravo", "nema prava", "nemate pravo", "by design",
             "won't fix", "wontfix", "nije bug", "tako je zamislj", "ocekivano", "očekivano",
             "flag", "modul nije ukljuc", "modul nije uključ")

TOP = 8
UNMARKED = "?"     # the extension's prefix for a screen the app did not mark (see page-context.js)


def _fold(s: str) -> str:
    return str(s or "").lower()


def _ver(s: str) -> tuple:
    """Tag version as numbers. Compared as a STRING, "0.10" sorts before "0.4"
    and the tenth revision of the tag would silently drop out of every
    version-gated count."""
    return tuple(int(x) if x.isdigit() else 0 for x in str(s or "").split("."))


def classify(resolution: str) -> str:
    """'fixed' | 'by_design' | 'other' | '' (no resolution)."""
    t = _fold(resolution)
    if not t.strip():
        return ""
    if any(k in t for k in BY_DESIGN):
        return "by_design"
    if any(k in t for k in FIXED):
        return "fixed"
    return "other"


def _resolution(t: dict) -> str:
    cl = t.get("closed") if isinstance(t.get("closed"), dict) else {}
    tr = t.get("triage") if isinstance(t.get("triage"), dict) else {}
    return str(cl.get("resolution") or tr.get("report") or tr.get("resolution") or "").strip()


def load_rows(root: Path) -> list[dict]:
    """One row per TAGGED ticket in the store. Untagged tickets are not the
    extension's and say nothing about it."""
    rows = []
    for fp in sorted(Path(root).glob("*.json")):
        if not store.is_module_file(fp):
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for tid, t in (data.get("tickets") or {}).items():
            if not isinstance(t, dict):
                continue
            tag = ebr_tag.of_ticket(t)
            if not tag:
                continue
            o = t.get("original") if isinstance(t.get("original"), dict) else {}
            cl = t.get("closed") if isinstance(t.get("closed"), dict) else {}
            res = _resolution(t)
            rows.append({
                "module": fp.stem, "id": str(tid), "title": str(t.get("title") or o.get("title") or ""),
                "tag": tag, "resolution": res, "outcome": classify(res),
                "closed_at": str(cl.get("at") or ""),
                # What a thin ticket actually costs: rounds of comments before
                # anyone could act on it, and fields the reporter had to fix
                # before sending. Both are already in the store; neither needed
                # new instrumentation.
                "rounds": len(t.get("comments") or []),
                "edits": len(tag["edited"]),
                "page": str((t.get("reading") or {}).get("page") or "") if isinstance(t.get("reading"), dict) else "",
            })
    return rows


def review(root: Path | None = None) -> dict:
    root = Path(root) if root else store.default_store()
    rows = load_rows(root)
    with_ctx: Counter = Counter()
    without: Counter = Counter()
    for r in rows:
        for s in r["tag"]["with_context"]:
            with_ctx[s] += 1
        for s in r["tag"]["without_context"]:
            without[s] += 1
    screens = set(with_ctx) | set(without)
    covered = {s for s in screens if s in with_ctx and s not in without}
    coverage = round(100 * len(covered) / len(screens)) if screens else None

    lies = [r for r in rows if r["tag"]["kind"] == "limitation" and r["outcome"] == "fixed"]
    missing = [r for r in rows if r["tag"]["kind"] == "bug" and r["outcome"] == "by_design"]
    question_fixed = [r for r in rows if r["tag"]["kind"] == "question" and r["outcome"] == "fixed"]

    edits_by_skill: dict[str, Counter] = defaultdict(Counter)
    edits_by_screen: dict[str, Counter] = defaultdict(Counter)
    edited_total = Counter()
    for r in rows:
        for f in r["tag"]["edited"]:
            edited_total[f] += 1
            for s in r["tag"]["skills"] or ["(no skill)"]:
                edits_by_skill[s][f] += 1
            for s in ebr_tag.screens(r["tag"]) or ["(no screen)"]:
                edits_by_screen[s][f] += 1

    # Does the interview earn its keep? Only tags that CAN carry `skip` are
    # comparable: in a 0.3 tag the field is absent, and reading absent as
    # "the interview was finished" would invent the very number this exists
    # to measure. So the arms are drawn from 0.4+ only, and when there is
    # nothing to compare the report says so instead of printing 0.0 vs 0.0.
    comparable = [r for r in rows if _ver(r["tag"]["version"]) >= (0, 4)]

    def _arm(rs: list[dict]) -> dict:
        n = len(rs)
        if not n:
            return {"tickets": 0, "rounds_avg": None, "edits_avg": None, "clean_pct": None}
        return {
            "tickets": n,
            "rounds_avg": round(sum(x["rounds"] for x in rs) / n, 1),
            "edits_avg": round(sum(x["edits"] for x in rs) / n, 1),
            # Closed with nobody having to ask anything -- the cheapest ticket
            # there is, and the one the interview is supposed to produce.
            "clean_pct": round(100 * sum(1 for x in rs if x["rounds"] == 0) / n),
        }

    interview = {
        "comparable": len(comparable),
        "skipped": _arm([r for r in comparable if "pitanja" in r["tag"]["skipped"]]),
        "finished": _arm([r for r in comparable if "pitanja" not in r["tag"]["skipped"]]),
    }

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["tag"]["split_total"] > 1:
            key = (r["module"], tuple(ebr_tag.screens(r["tag"])), r["tag"]["split_total"])
            groups[key].append(r)
    splits = []
    for (module, scr, n), rs in groups.items():
        outs = {(x["outcome"], x["closed_at"][:10]) for x in rs if x["outcome"]}
        splits.append({"module": module, "screens": list(scr), "parts": n, "seen": len(rs),
                       "ids": [x["id"] for x in rs],
                       "verdict": ("separate" if len(outs) > 1 else "together" if len(outs) == 1
                                   else "open")})

    def _target(screen: str, action: str) -> tuple[str, str]:
        """A file for a declared screen; adoption for an unmarked one; a
        question for a tag with no screen at all -- never a file named `?`."""
        if not screen:
            return ("find the screen", "(the tag recorded no screen; read the ticket)")
        if screen.startswith(UNMARKED):
            return ("adopt page context", f"the app serving {screen[1:]}")
        return (action, f"docs/pages/{screen.replace(':', '-')}.md")

    proposals = []
    for r in lies:
        for s in r["tag"]["with_context"] or [""]:
            a, tg = _target(s, "fix limits")
            proposals.append({"action": a, "target": tg,
                              "because": f"{r['module']}#{r['id']} was called a limitation and closed as fixed: {r['resolution'][:80]}"})
    for r in missing:
        for s in ebr_tag.screens(r["tag"]) or [""]:
            a, tg = _target(s, "add limits/requires")
            proposals.append({"action": a, "target": tg,
                              "because": f"{r['module']}#{r['id']} was called a bug and closed by design: {r['resolution'][:80]}"})
    for s, n in without.most_common(TOP):
        if s.startswith(UNMARKED):
            proposals.append({"action": "adopt page context", "target": f"the app serving {s[1:]}",
                              "because": f"{n} ticket(s) from a screen the app does not mark (no data-page): "
                                         f"render page-context there before any file can help"})
        else:
            proposals.append({"action": "write", "target": f"docs/pages/{s.replace(':', '-')}.md",
                              "because": f"{n} ticket(s) from a session on this screen carried no context file"})
    seen = set()
    proposals = [p for p in proposals if not (p["target"], p["action"]) in seen
                 and not seen.add((p["target"], p["action"]))]

    return {
        "root": str(root), "tickets_tagged": len(rows),
        "coverage_pct": coverage, "screens": len(screens), "covered": sorted(covered),
        "write_next": [{"screen": s.lstrip(UNMARKED), "tickets": n, "unmarked": s.startswith(UNMARKED)}
                       for s, n in without.most_common(TOP)],
        "lies": [{"module": r["module"], "id": r["id"], "screens": r["tag"]["with_context"],
                  "resolution": r["resolution"]} for r in lies],
        "missing": [{"module": r["module"], "id": r["id"], "screens": ebr_tag.screens(r["tag"]),
                     "resolution": r["resolution"]} for r in missing],
        "question_fixed": [{"module": r["module"], "id": r["id"]} for r in question_fixed],
        "edited_fields": dict(edited_total),
        "edits_by_skill": {k: dict(v) for k, v in edits_by_skill.items()},
        "edits_by_screen": {k: dict(v) for k, v in edits_by_screen.items()},
        "splits": splits,
        "interview": interview,
        "proposals": [dict(p, n=i + 1) for i, p in enumerate(proposals)],
    }


def render(r: dict) -> str:
    L = []
    L.append("=" * 74)
    L.append("EBR REVIEW — what the extension's tickets say about docs/pages")
    L.append("=" * 74)
    L.append(f"  {r['tickets_tagged']} tagged ticket(s) in {r['root']}")
    if not r["tickets_tagged"]:
        L.append("  Nothing to measure yet: no ticket in the store carries an [ebr ...] tag.")
        return "\n".join(L)
    cov = "n/a" if r["coverage_pct"] is None else f"{r['coverage_pct']}%"
    L.append(f"  coverage: {cov} of {r['screens']} screen(s) seen had a context file every time")
    if r["write_next"]:
        L.append("\n  WRITE NEXT (screens generating tickets with no file; * = the app does not mark the screen at all):")
        for w in r["write_next"]:
            L.append(f"    {w['tickets']:>3}  {w['screen']}{' *' if w.get('unmarked') else ''}")
    if r["lies"]:
        L.append("\n  CONTEXT LIES (called a limitation, closed as fixed) -- ALARM:")
        for x in r["lies"]:
            L.append(f"    {x['module']}#{x['id']}  {', '.join(x['screens']) or '?'}  -- {x['resolution'][:70]}")
    if r["missing"]:
        L.append("\n  CONTEXT MISSING (called a bug, closed by design / no rights):")
        for x in r["missing"]:
            L.append(f"    {x['module']}#{x['id']}  {', '.join(x['screens']) or '?'}  -- {x['resolution'][:70]}")
    if r["question_fixed"]:
        L.append("\n  called a rights question, closed as fixed: "
                 + ", ".join(f"{x['module']}#{x['id']}" for x in r["question_fixed"]))
    if r["edited_fields"]:
        L.append("\n  REPORTER CORRECTIONS (the extension's draft was wrong in):")
        L.append("    " + ", ".join(f"{k} ×{v}" for k, v in sorted(r["edited_fields"].items(), key=lambda kv: -kv[1])))
        for s, c in sorted(r["edits_by_skill"].items(), key=lambda kv: -sum(kv[1].values()))[:TOP]:
            L.append(f"    skill {s}: " + ", ".join(f"{k} ×{v}" for k, v in c.items()))
    iv = r.get("interview") or {}
    if iv:
        L.append("\n  DOES THE INTERVIEW PAY? (skipping is recorded from tag 0.4 on)")
        if not iv.get("comparable"):
            L.append("    Nothing to compare yet -- no ticket carries a 0.4 tag. Older tickets")
            L.append("    have no `skip` field, and absent is not the same as 'not skipped'.")
        else:
            L.append(f"    {'':<10}{'tickets':>8}{'rounds':>9}{'edits':>8}{'closed clean':>14}")
            for name, key in (("skipped", "skipped"), ("finished", "finished")):
                a = iv.get(key) or {}
                if not a.get("tickets"):
                    L.append(f"    {name:<10}{0:>8}{'--':>9}{'--':>8}{'--':>14}")
                    continue
                L.append(f"    {name:<10}{a['tickets']:>8}{a['rounds_avg']:>9}"
                         f"{a['edits_avg']:>8}{str(a['clean_pct']) + '%':>14}")
            # Two arms of one and three are a story, not a measurement.
            if min((iv["skipped"]["tickets"], iv["finished"]["tickets"])) < 5:
                L.append("    Too few in one arm to conclude anything -- keep collecting.")
    if r["splits"]:
        L.append("\n  SPLITS (one session, several tickets):")
        for s in r["splits"]:
            L.append(f"    {s['module']} {'/'.join(s['ids'])}  {s['parts']} part(s), {s['seen']} seen  -> resolved {s['verdict']}")
    if r["proposals"]:
        L.append("\n  PROPOSALS (say the numbers to apply; nothing is written by this tool):")
        for p in r["proposals"]:
            L.append(f"    {p['n']:>2}. {p['action']:<20} {p['target']}")
            L.append(f"        {p['because']}")
    L.append("\n  Record the run: python scripts/brain/cadence.py did ebr-review")
    return "\n".join(L)


def main() -> int:
    args = sys.argv[1:]
    root = None
    if "--root" in args:
        i = args.index("--root")
        root = Path(args[i + 1])
    r = review(root)
    if "--json" in args:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(render(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
