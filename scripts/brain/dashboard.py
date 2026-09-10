#!/usr/bin/env python3
"""One HTML page: what exists, what it costs, how often it is used, how it scores.

Three things are measured separately and only mean something together:

  usage.py     how often -- counted from session transcripts
  budget.py    what it costs -- always-on descriptions, and per-invocation preload
  score.py     how well -- outcomes recorded by hand

A cheap agent nobody uses and an expensive one used constantly look identical in
any single table. Side by side, the decisions become obvious.

  dashboard.py            write docs/dashboard.html
  dashboard.py --open     write it and open it

Self-contained: no CDN, no build step, works from a file:// URL, follows the
system light/dark theme.
"""
from __future__ import annotations

import html
import json
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage as usage_mod                                    # noqa: E402
import docs as docs_mod                                      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "dashboard.html"
MD_OUT = ROOT / "docs" / "DASHBOARD.md"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def esc(x) -> str:
    return html.escape(str(x))


#: A geometric icon per agent GROUP — a small SVG glyph, NOT an emoji. Emoji
#: render differently on every OS and are the default every AI dashboard reaches
#: for; a drawn mark is consistent, themeable (it inherits currentColor), and
#: reads as belonging to this page. Each glyph is a 16x16 line drawing keyed to
#: what the group DOES, so the shape carries meaning: an eye for readers, a
#: shield for review, a server stack for backend, and so on. `currentColor`
#: means the surrounding `.gi-<slug>` colour flows straight into the stroke.
_ICON_PATHS = {
    # coordination — a hub with three spokes
    "coordination": '<circle cx="8" cy="8" r="2.2"/><path d="M8 5.8V2M8 10.2v3.8M5.9 6.9 3 4M10.1 6.9 13 4"/>',
    # reading & analysis — an eye
    "reading": '<path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z"/><circle cx="8" cy="8" r="2"/>',
    # consultants — a speech bubble
    "consultants": '<path d="M2 3.5h12v7H6l-3 2.5v-2.5H2Z"/><path d="M5 6h6M5 8h4"/>',
    # review & quality — a shield with a check
    "review": '<path d="M8 1.8 13 3.5v4C13 11 8 14 8 14S3 11 3 7.5v-4Z"/><path d="M5.8 7.7 7.3 9.3 10.3 6"/>',
    # backend — a stack of database plates
    "backend": '<ellipse cx="8" cy="3.8" rx="5" ry="1.8"/><path d="M3 3.8v8.4C3 13.2 5.2 14 8 14s5-.8 5-1.8V3.8M3 8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8"/>',
    # views & crud — a browser window
    "views": '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 6h12M4.3 4.5h.01M6 4.5h.01"/>',
    # front end — a paint / layout square
    "front": '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M2.5 6.5h11M6.5 6.5v7"/>',
    # whole slices — layered sheets
    "slices": '<path d="M8 2 14 5 8 8 2 5Z"/><path d="M2 8l6 3 6-3M2 11l6 3 6-3"/>',
    # operations — a gear
    "operations": '<circle cx="8" cy="8" r="2.3"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4"/>',
    # the brain itself — a pen nib
    "brain": '<path d="M4 12 3 13M4.5 11.5 11 5l2 2-6.5 6.5-2.8.8Z"/><path d="M9.5 3.5 11 2l3 3-1.5 1.5Z"/>',
}

#: group title (as in docs.AGENT_GROUPS) -> icon key + colour-slug.
GROUP_ICON = {
    "Coordination": ("coordination", "coordination"),
    "Reading & analysis": ("reading", "reading"),
    "Consultants (advise, never edit)": ("consultants", "consultants"),
    "Review & quality": ("review", "review"),
    "Backend": ("backend", "backend"),
    "Views & CRUD": ("views", "views"),
    "Front end": ("front", "front"),
    "Whole slices": ("slices", "slices"),
    "Operations": ("operations", "operations"),
    "The brain itself": ("brain", "brain"),
}


def group_icon(group: str) -> str:
    key, slug = GROUP_ICON.get(group, ("coordination", "ungrouped"))
    path = _ICON_PATHS.get(key, _ICON_PATHS["coordination"])
    return (f'<svg class="gi gi-{slug}" viewBox="0 0 16 16" width="16" '
            f'height="16" fill="none" stroke="currentColor" stroke-width="1.4" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{path}</svg>')


def load_scores() -> dict:
    p = ROOT / "registry.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def band(entry: dict) -> tuple[str, int, str]:
    n = sum(entry.get(k, 0) for k in ("helped", "hindered", "neutral"))
    if n == 0:
        return "unproven", 0, ""
    ratio = (entry.get("helped", 0) - entry.get("hindered", 0)) / n
    if n < 5:
        return "unproven", n, f"{ratio:+.2f}"
    for cut, label in ((0.6, "proven"), (0.2, "working"), (-0.2, "weak")):
        if ratio >= cut:
            return label, n, f"{ratio:+.2f}"
    return "retire", n, f"{ratio:+.2f}"


def pill(b: str, n: int, ratio: str) -> str:
    inner = esc(b) + (f' <span class="sm">{esc(ratio)}&middot;{n}</span>' if n else "")
    return f'<span class="pill p-{b}">{inner}</span>'


def bar(value: int, peak: int) -> str:
    pct = 0 if not peak else max(2, round(value / peak * 100))
    heavy = " hot" if peak and value / peak > 0.6 else ""
    return (f'<span class="bar{heavy}"><i style="width:{pct}%"></i></span>'
            f'<span class="num">{value:,}</span>')


def used(n: int) -> str:
    return f'<b class="hit">{n}</b>' if n else '<span class="zero">0</span>'


def write_markdown(agents, skills, use, scores, always, hot) -> None:
    """A GitHub-renderable twin of the dashboard.

    GitHub sanitizes HTML in markdown -- no CSS, no JS -- so the .html page
    cannot render in a README. Tables and mermaid do render, so the same data
    goes out in a form GitHub will actually show.
    """
    md = MD_OUT
    top_a = sorted(agents.items(), key=lambda kv: -(kv[1]["body"] + sum(
        skills.get(s, {}).get("body", 0) for s in kv[1]["pre"])))[:12]

    L = ["# Dashboard", "",
         "Generated by `scripts/brain/dashboard.py` - do not edit by hand.",
         "The interactive version is `dashboard.html` (sortable, filterable); this",
         "one exists because GitHub renders markdown and mermaid, not HTML.", "",
         f"| | |", "|---|---|",
         f"| agents | **{len(agents)}** |",
         f"| skills | **{len(skills)}** |",
         f"| references | **{sum(len(s['refs']) for s in skills.values())}** |",
         f"| tokens always in context | **~{always // 4:,}** |",
         f"| seen in a transcript | **{hot}** |", "",
         "```mermaid", "pie showData title Where the always-on context goes"]
    a_desc = sum(len(a["desc"]) for a in agents.values()) // 4
    s_desc = sum(len(s["desc"]) for s in skills.values() if not s["hand"]) // 4
    L += [f'    "agent descriptions" : {a_desc}',
          f'    "skill descriptions" : {s_desc}', "```", "",
          "## Most expensive per invocation", "",
          "Own brief plus every preloaded skill body, paid on **every call**.", "",
          "| agent | grade | tok / call | preloads |", "|---|---|---:|---|"]
    for name, a in top_a:
        cost = (a["body"] + sum(skills.get(s, {}).get("body", 0) for s in a["pre"])) // 4
        L.append(f"| `{name}` | {a['grade']} | {cost:,} | "
                 f"{', '.join('`' + p + '`' for p in a['pre']) or '-'} |")

    L += ["", "## Skills by body size", "",
          "A router's body enters context when the skill loads and stays for the",
          "session. References cost nothing until read.", "",
          "| skill | tok on load | refs | preloaded by | invoked | via preload |",
          "|---|---:|---:|---:|---:|---:|"]
    for name, s in sorted(skills.items(), key=lambda kv: -kv[1]["body"]):
        by = sum(1 for a in agents.values() if name in a["pre"])
        u = use["skills"].get(name, {})
        L.append(f"| `/brain:{name}` | {s['body'] // 4:,} | {len(s['refs'])} | {by} | "
                 f"{u.get('n', 0)} | {u.get('preloadReach', 0)} |")

    seen_a = {n: r["n"] for n, r in use["agents"].items()}
    seen_s = {n: r["n"] for n, r in use["skills"].items()}
    L += ["", "## Actually used", "",
          "Counted from session transcripts AND from workflow agent records. **A**",
          "**zero means not seen** -- deleted sessions are gone -- never that",
          "something was never used. A skill an agent PRELOADS never calls the",
          "Skill tool at all, so read its *via preload* figure instead of *invoked*.", ""]
    if seen_a or seen_s:
        L += ["| what | name | times | last |", "|---|---|---:|---|"]
        for n, c in sorted(seen_a.items(), key=lambda kv: -kv[1]):
            L.append(f"| agent | `{n}` | {c} | {use['agents'][n]['last']} |")
        for n, c in sorted(seen_s.items(), key=lambda kv: -kv[1]):
            L.append(f"| skill | `{n}` | {c} | {use['skills'][n]['last']} |")
    else:
        L.append("*Nothing has been invoked yet -- see [TESTING.md](TESTING.md).*")

    L += ["", "---", "",
          "`budget.py` says what it costs, `usage.py` counts how often it is used,",
          "`score.py` records how well it worked. A cheap agent nobody uses and an",
          "expensive one used constantly look identical in any one of them.", ""]
    md.write_text(chr(10).join(L) + chr(10), encoding="utf-8")
    print(f"wrote {md.relative_to(ROOT)}  (renders on GitHub)")


def main() -> int:
    agents = docs_mod.read_agents()
    skills = docs_mod.read_skills()
    use = usage_mod.collect()
    scores = load_scores()

    group_of = {n: title for title, names in docs_mod.AGENT_GROUPS for n in names}
    sgroup_of = {n: title for title, names in docs_mod.SKILL_GROUPS for n in names}

    # ---- agents -----------------------------------------------------------
    arows = []
    for name, a in agents.items():
        cost = (a["body"] + sum(skills.get(s, {}).get("body", 0) for s in a["pre"])) // 4
        arows.append({
            "name": name, "group": group_of.get(name, "Ungrouped"),
            "grade": a["grade"], "model": f'{a["model"]}/{a["effort"]}',
            "used": use["agents"].get(name, {}).get("n", 0),
            "last": use["agents"].get(name, {}).get("last") or "",
            "cost": cost, "always": len(a["desc"]) // 4,
            "band": band(scores.get("agents", {}).get(name, {})),
            "pre": a["pre"], "mem": a["memory"],
        })
    peak_a = max((r["cost"] for r in arows), default=1)
    arows.sort(key=lambda r: (-r["used"], -r["cost"]))

    at = "".join(
        f'<tr class="arow gc-{GROUP_ICON.get(r["group"], ("", "ungrouped"))[1]}" '
        f'data-s="{esc(r["name"] + " " + r["group"] + " " + r["grade"])}">'
        f'<td class="nm"><b>{group_icon(r["group"])}<span class="anm">{esc(r["name"])}</span></b>'
        f'{" <span class=sm title=\'keeps notes across sessions\'>mem</span>" if r["mem"] else ""}'
        f'<span class="grp">{esc(r["group"])}</span></td>'
        f'<td data-v="{r["grade"]}"><span class="gr g-{r["grade"]}">{esc(r["grade"])}</span></td>'
        f'<td class="mono sm" data-v="{esc(r["model"])}">{esc(r["model"])}</td>'
        f'<td class="ta" data-v="{r["used"]}">{used(r["used"])}</td>'
        f'<td class="sm" data-v="{esc(r["last"])}">{esc(r["last"]) if r["last"] else "&mdash;"}</td>'
        f'<td class="ta" data-v="{r["cost"]}">{bar(r["cost"], peak_a)}</td>'
        f'<td class="ta sm" data-v="{r["always"]}">{r["always"]}</td>'
        f'<td data-v="{r["band"][0]}">{pill(*r["band"])}</td>'
        f'<td class="mono sm">{", ".join(esc(p) for p in r["pre"]) or "&mdash;"}</td></tr>'
        for r in arows)

    # ---- skills -----------------------------------------------------------
    srows = []
    for name, s in skills.items():
        srows.append({
            "name": name, "group": sgroup_of.get(name, "Ungrouped"),
            "used": use["skills"].get(name, {}).get("n", 0),
            # An agent's preloaded skill never calls the Skill tool, so `used`
            # is structurally 0 for the skills the system leans on hardest.
            "reach": use["skills"].get(name, {}).get("preloadReach", 0),
            "last": use["skills"].get(name, {}).get("last") or "",
            "body": s["body"] // 4, "always": len(s["desc"]) // 4,
            "refs": len(s["refs"]),
            "by": sum(1 for a in agents.values() if name in a["pre"]),
            "band": band(scores.get("skills", {}).get(name, {})),
            "hand": s["hand"],
        })
    peak_s = max((r["body"] for r in srows), default=1)
    srows.sort(key=lambda r: (-r["used"] - r["reach"], -r["body"]))

    st = "".join(
        f'<tr data-s="{esc(r["name"] + " " + r["group"])}">'
        f'<td class="nm"><b>/brain:{esc(r["name"])}</b>'
        f'{" <span class=sm>manual</span>" if r["hand"] else ""}'
        f'<span class="grp">{esc(r["group"])}</span></td>'
        f'<td class="ta" data-v="{r["used"]}">{used(r["used"])}</td>'
        f'<td class="ta sm" data-v="{r["reach"]}">{r["reach"] or "&mdash;"}</td>'
        f'<td class="sm" data-v="{esc(r["last"])}">{esc(r["last"]) if r["last"] else "&mdash;"}</td>'
        f'<td class="ta" data-v="{r["body"]}">{bar(r["body"], peak_s)}</td>'
        f'<td class="ta sm" data-v="{r["always"]}">{r["always"]}</td>'
        f'<td class="ta sm" data-v="{r["refs"]}">{r["refs"]}</td>'
        f'<td class="ta sm" data-v="{r["by"]}">{r["by"]}</td>'
        f'<td data-v="{r["band"][0]}">{pill(*r["band"])}</td></tr>'
        for r in srows)

    act = use.get("activity") or {"minutes": 0, "tokens": {}, "days": {}}
    act_tok = act.get("tokens", {})
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    act_hours = f'{act.get("minutes", 0) / 60:,.0f}'
    act_today = f'{act.get("days", {}).get(today, {}).get("minutes", 0):,.0f}'
    out_m = f'{act_tok.get("output", 0) / 1_000_000:.1f}'
    _cache = act_tok.get("cache_read", 0)
    _fresh = act_tok.get("input", 0) + act_tok.get("cache_write", 0)
    cache_pct = round(100 * _cache / (_cache + _fresh)) if (_cache + _fresh) else 0
    idle_min = usage_mod.IDLE_GAP_S // 60

    always = sum(len(a["desc"]) for a in agents.values()) + \
             sum(len(s["desc"]) for s in skills.values() if not s["hand"])
    cold_a = sorted(r["name"] for r in arows if not r["used"])
    cold_s = sorted(r["name"] for r in srows if not r["used"] and not r["reach"])
    hot = sum(1 for r in arows if r["used"]) + \
          sum(1 for r in srows if r["used"] or r["reach"])

    def chips(names):
        return "".join(f'<span class="chip">{esc(n)}</span>' for n in names) or "<i>none</i>"

    # Per REQUEST, not per month. `collect()` answers "which agents earn their
    # place over time"; this answers "what did it just do for the thing I
    # asked", which is the only question worth asking while testing — and the
    # two need opposite aggregations, so they are two tables.
    prompts = usage_mod.per_prompt(limit=25, days=14)
    ptot_a = sum(p["nAgents"] for p in prompts)
    ptot_s = sum(p["nSkills"] for p in prompts)
    pt = "".join(
        f'<tr><td class="sm nw" data-v="{esc(p["when"])}">{esc(p["when"])}</td>'
        f'<td class="pq">{esc(p["prompt"])}</td>'
        f'<td class="ta" data-v="{p["nAgents"]}">'
        f'<b class="{"zero" if not p["nAgents"] else "hit"}">{p["nAgents"]}</b></td>'
        f'<td class="sm">{chips(p["agents"]) if p["agents"] else ""}</td>'
        f'<td class="ta" data-v="{p["nSkills"]}">'
        f'<b class="{"zero" if not p["nSkills"] else "hit"}">{p["nSkills"]}</b></td>'
        f'<td class="sm">{chips(p["skills"]) if p["skills"] else ""}</td></tr>'
        for p in prompts) or '<tr><td colspan="6"><i>no prompts in the window</i></td></tr>'

    css = """
:root{--bg:#fbfbfc;--panel:#fff;--fg:#16181d;--mut:#6b7280;--faint:#9aa1ab;
--line:#e4e6ea;--soft:#f3f5f8;--acc:#0f62c9;--ok:#0e9f5c;--warn:#c98600;--bad:#d64545}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--panel:#161920;--fg:#e8eaed;
--mut:#9aa1ab;--faint:#6b7280;--line:#262b34;--soft:#1c2029;--acc:#6aa8ff;
--ok:#3ddc97;--warn:#f0b429;--bad:#ff6b6b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1280px;margin:0 auto;padding:2.5rem 1.5rem 5rem}
h1{font-size:1.75rem;margin:0;letter-spacing:-.02em}
h2{font-size:1.05rem;margin:0;letter-spacing:-.01em}
.lede{color:var(--mut);margin:.4rem 0 0;font-size:.92rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
gap:.85rem;margin:1.75rem 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:.9rem 1.05rem}
.card b{display:block;font-size:1.75rem;line-height:1.15;letter-spacing:-.02em}
.card span{color:var(--mut);font-size:.78rem;display:block;margin-top:.15rem}
.note{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--acc);
padding:.85rem 1.05rem;border-radius:0 10px 10px 0;font-size:.88rem;color:var(--mut);margin:1.5rem 0}
.note b{color:var(--fg)}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;
margin:1.75rem 0;overflow:hidden}
.head{display:flex;align-items:center;gap:1rem;padding:1rem 1.15rem;border-bottom:1px solid var(--line);
flex-wrap:wrap}
.head p{margin:0;color:var(--mut);font-size:.84rem;flex:1}
input.f{border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:7px;
padding:.4rem .7rem;font-size:.85rem;width:190px;font-family:inherit}
input.f:focus{outline:none;border-color:var(--acc)}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th{position:sticky;top:0;z-index:2;background:var(--panel);color:var(--mut);font-weight:600;
font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;cursor:pointer;user-select:none;
padding:.6rem .8rem;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th:hover{color:var(--acc)}
td{padding:.55rem .8rem;border-bottom:1px solid var(--line);vertical-align:middle}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:var(--soft)}
.ta{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.nm{min-width:210px}.nm b{font-weight:600}
.grp{display:block;color:var(--faint);font-size:.72rem;margin-top:.1rem}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;color:var(--mut)}
.sm{font-size:.75rem;color:var(--mut)}
.nw{white-space:nowrap}
.pq{max-width:38ch;font-size:.82rem;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.hit{color:var(--acc);font-size:1rem}.zero{color:var(--faint)}
.bar{display:inline-block;width:52px;height:5px;border-radius:3px;background:var(--soft);
overflow:hidden;vertical-align:middle;margin-right:.5rem}
.bar i{display:block;height:100%;background:var(--acc);opacity:.55}
.bar.hot i{background:var(--warn);opacity:.85}
.num{font-variant-numeric:tabular-nums}
.pill,.gr{display:inline-block;padding:.1rem .5rem;border-radius:20px;font-size:.72rem;
border:1px solid var(--line);white-space:nowrap}
.p-proven{color:var(--ok);border-color:var(--ok)}
.p-working{color:var(--acc);border-color:var(--acc)}
.p-unproven{color:var(--faint)}
.p-weak{color:var(--warn);border-color:var(--warn)}
.p-retire{color:var(--bad);border-color:var(--bad)}
.g-principal{color:var(--bad);border-color:var(--bad)}
.g-senior{color:var(--warn);border-color:var(--warn)}
.g-junior{color:var(--acc);border-color:var(--acc)}
.g-mechanical{color:var(--faint)}
.nm b{display:inline-flex;align-items:center;gap:.45rem}
.gi{flex:none;vertical-align:middle;border-radius:5px;padding:2px;
background:color-mix(in srgb,currentColor 12%,transparent)}
.gi-coordination{color:#8b5cf6}.gi-reading{color:#0ea5e9}
.gi-consultants{color:#14b8a6}.gi-review{color:#e11d48}
.gi-backend{color:#f59e0b}.gi-views{color:#3b82f6}
.gi-front{color:#ec4899}.gi-slices{color:#22c55e}
.gi-operations{color:#64748b}.gi-brain{color:#a855f7}
.gi-ungrouped{color:var(--faint)}
@media(prefers-color-scheme:dark){
.gi-coordination{color:#a78bfa}.gi-reading{color:#38bdf8}
.gi-consultants{color:#2dd4bf}.gi-review{color:#fb7185}
.gi-backend{color:#fbbf24}.gi-views{color:#60a5fa}
.gi-front{color:#f472b6}.gi-slices{color:#4ade80}
.gi-operations{color:#94a3b8}.gi-brain{color:#c084fc}}
/* per-group row colour: a coloured left edge, a tinted name, and a hover
   wash keyed to the group — the row itself now carries the group's identity,
   not just the icon. --gc is the one knob each group sets. */
.arow{--gc:var(--faint)}
.arow td:first-child{border-left:3px solid var(--gc)}
.arow .anm{color:var(--gc);font-weight:600}
.arow:hover{background:color-mix(in srgb,var(--gc) 8%,transparent)}
.gc-coordination{--gc:#8b5cf6}.gc-reading{--gc:#0ea5e9}
.gc-consultants{--gc:#14b8a6}.gc-review{--gc:#e11d48}
.gc-backend{--gc:#f59e0b}.gc-views{--gc:#3b82f6}
.gc-front{--gc:#ec4899}.gc-slices{--gc:#22c55e}
.gc-operations{--gc:#64748b}.gc-brain{--gc:#a855f7}
@media(prefers-color-scheme:dark){
.gc-coordination{--gc:#a78bfa}.gc-reading{--gc:#38bdf8}
.gc-consultants{--gc:#2dd4bf}.gc-review{--gc:#fb7185}
.gc-backend{--gc:#fbbf24}.gc-views{--gc:#60a5fa}
.gc-front{--gc:#f472b6}.gc-slices{--gc:#4ade80}
.gc-operations{--gc:#94a3b8}.gc-brain{--gc:#c084fc}}
.chips{padding:1rem 1.15rem;display:flex;flex-wrap:wrap;gap:.35rem}
.chip{background:var(--soft);border:1px solid var(--line);border-radius:5px;
padding:.15rem .5rem;font-size:.76rem;font-family:ui-monospace,Menlo,monospace;color:var(--mut)}
.read{padding:1rem 1.15rem}
.read dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:.5rem 1rem;align-items:baseline}
.read dt{font-weight:600;font-size:.84rem;white-space:nowrap}
.read dd{margin:0;color:var(--mut);font-size:.86rem}
"""

    js = r"""
function sortT(th){const t=th.closest('table'),b=t.tBodies[0],i=[...th.parentNode.children].indexOf(th);
const d=t.dataset.c==i&&t.dataset.d!='desc'?'desc':'asc';
const v=r=>{const c=r.cells[i],x=c.dataset.v!==undefined?c.dataset.v:c.textContent.trim();
const s=String(x).replace(/[, ]/g,'');
const n=/^-?\d+(\.\d+)?$/.test(s)?parseFloat(s):NaN;return isNaN(n)?s.toLowerCase():n};
[...b.rows].sort((a,z)=>{const p=v(a),q=v(z);return(p<q?-1:p>q?1:0)*(d=='asc'?1:-1)})
.forEach(r=>b.appendChild(r));t.dataset.c=i;t.dataset.d=d}
function filt(inp){const t=document.getElementById(inp.dataset.t),q=inp.value.toLowerCase();
let n=0;[...t.tBodies[0].rows].forEach(r=>{const m=r.dataset.s.toLowerCase().includes(q);
r.hidden=!m;if(m)n++});inp.dataset.n=n}
document.addEventListener('DOMContentLoaded',()=>{
document.querySelectorAll('th').forEach(th=>th.onclick=()=>sortT(th));
document.querySelectorAll('input.f').forEach(i=>i.oninput=()=>filt(i))})
"""

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>brain &middot; dashboard</title><style>{css}</style></head>
<body><div class="wrap">

<h1>brain &middot; dashboard</h1>
<p class="lede">{datetime.now().astimezone():%Y-%m-%d %H:%M %Z} &middot;
{use['scanned']} session transcripts scanned &middot; click any column to sort</p>

<div class="cards">
  <div class="card"><b>{len(agents)}</b><span>agents</span></div>
  <div class="card"><b>{len(skills)}</b><span>skills</span></div>
  <div class="card"><b>{sum(len(s['refs']) for s in skills.values())}</b><span>references</span></div>
  <div class="card"><b>~{always // 4:,}</b><span>tokens always in context</span></div>
  <div class="card"><b>{hot}</b><span>seen in a transcript</span></div>
</div>

<div class="cards">
  <div class="card"><b>{act_hours}</b><span>hours worked, all time</span></div>
  <div class="card"><b>{act_today}</b><span>minutes worked today</span></div>
  <div class="card"><b>{out_m}M</b><span>output tokens</span></div>
  <div class="card"><b>{cache_pct}%</b><span>of input served from cache</span></div>
</div>

<div class="note"><b>Time is summed over gaps, not wall-clock.</b> A session left
open all night would otherwise read as eight hours of work, so consecutive records
more than {idle_min} minutes apart are not counted &mdash; what remains is time
somebody was waiting on a reply or a test. <b>Cache reads are shown separately</b>
because they are most of the volume and a fraction of the price; folding them into
one total makes every figure look alarming and none of them mean anything.</div>

<div class="note"><b>Usage is counted, score is claimed.</b> <i>invoked</i> comes from real
<code>Task</code>/<code>Skill</code> calls in transcripts plus agents a <b>workflow</b> spawned
(those are recorded separately and were missing from this page until 2026-08-03, which is why a
14-agent sweep once showed as 2). A skill an agent <b>preloads</b> is injected at startup and
<b>never calls the Skill tool</b>, so its <i>invoked</i> is structurally 0 however hard the system
leans on it &mdash; <i>via preload</i> counts the runs of the agents carrying it instead. Deleted
sessions are gone, so <b>0 means &ldquo;not seen&rdquo;, never &ldquo;never used&rdquo;.</b>
Score is recorded by hand with <code>score.py record</code>, so it is only as good as the
discipline behind it. <i>Unproven</i> is not a bad score.</div>

<section>
  <div class="head"><h2>Per request</h2>
    <p>last {len(prompts)} things you asked for &middot; {ptot_a} agents and {ptot_s} skills across them
       &middot; agents attributed by when they ran, so ones spawned <b>by another agent</b> land here too</p>
    <input class="f" data-t="tp" placeholder="filter by prompt, agent or skill&hellip;"></div>
  <div class="tw"><table id="tp"><thead><tr>
    <th>when</th><th>you asked</th><th class="ta">agents</th><th>which</th>
    <th class="ta">skills</th><th>which</th>
  </tr></thead><tbody>{pt}</tbody></table></div>
</section>

<section>
  <div class="head"><h2>Agents</h2>
    <p>{len(agents)} &middot; sorted by usage, then cost per invocation</p>
    <input class="f" data-t="ta" placeholder="filter by name or group&hellip;"></div>
  <div class="tw"><table id="ta"><thead><tr>
    <th>agent</th><th>grade</th><th>model</th><th class="ta">used</th><th>last</th>
    <th class="ta">tok / call</th><th class="ta">always</th><th>score</th><th>preloads</th>
  </tr></thead><tbody>{at}</tbody></table></div>
</section>

<section>
  <div class="head"><h2>Skills</h2>
    <p>{len(skills)} routers &middot; references cost nothing until read</p>
    <input class="f" data-t="ts" placeholder="filter&hellip;"></div>
  <div class="tw"><table id="ts"><thead><tr>
    <th>skill</th><th class="ta">invoked</th><th class="ta">via preload</th><th>last</th><th class="ta">tok on load</th>
    <th class="ta">always</th><th class="ta">refs</th><th class="ta">preloaded by</th><th>score</th>
  </tr></thead><tbody>{st}</tbody></table></div>
</section>

<section>
  <div class="head"><h2>Not seen in any transcript</h2>
    <p>{len(cold_a)} agents, {len(cold_s)} skills &mdash; check the caveat above before acting</p></div>
  <div class="chips">{chips(cold_a)}</div>
  <div class="chips" style="border-top:1px solid var(--line)">{chips(cold_s)}</div>
</section>

<section>
  <div class="head"><h2>Reading it</h2></div>
  <div class="read"><dl>
    <dt>costs a lot, never used</dt><dd>the description is not triggering &mdash; rewrite it, or delete the thing</dd>
    <dt>used a lot, expensive per call</dt><dd>split what it preloads into <code>references/</code></dd>
    <dt>used a lot, scores weak</dt><dd>almost always too general to act on &mdash; sharpen or split it</dd>
    <dt>scores retire</dt><dd>delete it; a rule proven unhelpful is worse than a missing one</dd>
    <dt>unproven for months</dt><dd>it is not being triggered &mdash; the description is the problem</dd>
  </dl></div>
</section>

</div><script>{js}</script></body></html>"""

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    write_markdown(agents, skills, use, scores, always, hot)
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(page):,} bytes, self-contained)")
    print(f"  {len(agents)} agents, {len(skills)} skills, {use['scanned']} transcripts, "
          f"~{always // 4:,} tokens always in context, {hot} seen in use")
    if "--open" in sys.argv:
        webbrowser.open(OUT.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
