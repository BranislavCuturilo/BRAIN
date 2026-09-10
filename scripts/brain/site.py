#!/usr/bin/env python3
"""Render the whole documentation set into one browsable HTML page.

There are a dozen markdown files and one HTML dashboard; this puts everything
in a single self-contained page with a sidebar, so the documentation is
readable offline in a browser without GitHub and without a static-site
generator.

  site.py            write docs/index.html
  site.py --open     write it and open it

Pure standard library. The markdown subset covers what these docs actually
use: headings, fenced code, tables, lists, blockquotes, rules, and inline
code/bold/italic/links. Anything outside that renders as plain text rather
than silently wrong -- a renderer that guesses is worse than one with limits.
"""
from __future__ import annotations

import html
import re
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "index.html"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Order is editorial: what a newcomer needs first, then reference, then process.
PAGES = [
    ("Start here", ROOT / "README.md"),
    ("Setup", ROOT / "docs" / "SETUP.md"),
    ("Testing", ROOT / "docs" / "TESTING.md"),
    ("Test prompts", ROOT / "docs" / "TEST-PROMPT.md"),
    ("Agents", ROOT / "docs" / "AGENTS.md"),
    ("Skills", ROOT / "docs" / "SKILLS.md"),
    ("Dashboard (data)", ROOT / "docs" / "DASHBOARD.md"),
    ("Skills folder", ROOT / "skills" / "README.md"),
    ("Agents folder", ROOT / "agents" / "README.txt"),
    ("Workflows", ROOT / "workflows" / "README.md"),
    ("Scripts", ROOT / "scripts" / "README.md"),
    ("Journal", ROOT / "journal" / "README.md"),
    ("Paper outline", ROOT / "docs" / "paper" / "OUTLINE.md"),
]


def inline(s: str) -> str:
    """Inline markdown. Code spans are extracted first so their contents are
    never treated as markup -- otherwise `**` inside a code span becomes bold."""
    spans: list[str] = []

    def stash(m):
        spans.append(html.escape(m.group(1)))
        return f"\x00{len(spans) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, s)
    s = html.escape(s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", s)
    s = re.sub(r"&amp;(\w+|#\d+);", r"&\1;", s)          # keep real entities
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{spans[int(m.group(1))]}</code>", s)


def render(md: str) -> str:
    out: list[str] = []
    lines = md.replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)

    while i < n:
        ln = lines[i]

        if ln.startswith("```"):
            lang = ln[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            note = ('<div class="mnote">mermaid diagram &mdash; renders on '
                    'GitHub</div>') if lang == "mermaid" else ""
            out.append(f'{note}<pre><code>{html.escape(chr(10).join(buf))}</code></pre>')
            continue

        if re.match(r"^\s*\|.*\|\s*$", ln) and i + 1 < n and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            def cells(row):
                return [c.strip() for c in row.strip().strip("|").split("|")]
            head = cells(ln)
            i += 2
            body = []
            while i < n and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                body.append(cells(lines[i]))
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            tr = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                         for r in body)
            out.append(f'<div class="tw"><table><thead><tr>{th}</tr></thead>'
                       f"<tbody>{tr}</tbody></table></div>")
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", ln):
            out.append("<hr>")
            i += 1
            continue

        if ln.lstrip().startswith("> "):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            out.append(f"<blockquote>{inline(' '.join(buf))}</blockquote>")
            continue

        m = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", ln)
        if m:
            tag = "ul" if m.group(1) in "-*" else "ol"
            items: list[str] = []
            while i < n:
                mm = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", lines[i])
                if mm:
                    items.append(mm.group(2))
                    i += 1
                elif lines[i].startswith(("   ", "\t")) and lines[i].strip() and items:
                    # Join the RAW text and inline once at the end: a bold or
                    # code span wrapped across the line break would otherwise be
                    # split in half and neither part would match.
                    items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            out.append(f"<{tag}>" + "".join(f"<li>{inline(it)}</li>" for it in items) + f"</{tag}>")
            continue

        if not ln.strip():
            i += 1
            continue

        buf = []
        while i < n and lines[i].strip() and not re.match(
                r"^\s*(#{1,4}\s|[-*]\s|\d+\.\s|>|```|\||-{3,})", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append(f"<p>{inline(' '.join(buf))}</p>")

    return "\n".join(out)


def main() -> int:
    sections, nav, missing = [], [], []
    for title, path in PAGES:
        if not path.exists():
            missing.append(str(path.relative_to(ROOT)))
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        nav.append(f'<a href="#{slug}">{html.escape(title)}</a>')
        sections.append(
            f'<section id="{slug}"><div class="src">{html.escape(str(path.relative_to(ROOT)))}</div>'
            f"{render(path.read_text(encoding='utf-8'))}</section>")

    css = """
:root{--bg:#fbfbfc;--panel:#fff;--fg:#16181d;--mut:#5f6672;--faint:#9aa1ab;
--line:#e4e6ea;--soft:#f3f5f8;--acc:#0f62c9}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--panel:#161920;--fg:#e8eaed;
--mut:#9aa1ab;--faint:#6b7280;--line:#262b34;--soft:#1c2029;--acc:#6aa8ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);display:flex;
font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased}
nav{position:sticky;top:0;height:100vh;width:225px;flex:0 0 225px;overflow-y:auto;
padding:1.5rem 0 3rem;border-right:1px solid var(--line);background:var(--panel)}
nav h1{font-size:.95rem;margin:0 1.25rem 1rem;letter-spacing:-.01em}
nav h1 span{display:block;color:var(--faint);font-size:.72rem;font-weight:400;margin-top:.15rem}
nav a{display:block;padding:.4rem 1.25rem;color:var(--mut);text-decoration:none;
font-size:.86rem;border-left:2px solid transparent}
nav a:hover{color:var(--acc);background:var(--soft)}
nav a.on{color:var(--acc);border-left-color:var(--acc);background:var(--soft)}
nav .ext{margin:1rem 1.25rem 0;padding-top:1rem;border-top:1px solid var(--line)}
nav .ext a{padding:.3rem 0;border:0}
main{flex:1;min-width:0;padding:2.5rem 3rem 6rem;max-width:900px}
section{padding-bottom:3rem;margin-bottom:3rem;border-bottom:1px solid var(--line)}
section:last-child{border:0}
.src{font-family:ui-monospace,Menlo,monospace;font-size:.72rem;color:var(--faint);
margin-bottom:1.25rem;letter-spacing:.02em}
h1{font-size:1.7rem;margin:.2rem 0 .8rem;letter-spacing:-.02em}
h2{font-size:1.18rem;margin:2rem 0 .7rem;letter-spacing:-.01em}
h3{font-size:1rem;margin:1.6rem 0 .5rem}
h4{font-size:.92rem;margin:1.3rem 0 .4rem;color:var(--mut)}
p{margin:.7rem 0}
a{color:var(--acc)}
ul,ol{margin:.7rem 0;padding-left:1.3rem}li{margin:.3rem 0}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.86em;
background:var(--soft);padding:.1rem .35rem;border-radius:4px;border:1px solid var(--line)}
pre{background:var(--soft);border:1px solid var(--line);border-radius:8px;
padding:.9rem 1.1rem;overflow-x:auto;margin:.9rem 0}
pre code{background:none;border:0;padding:0;font-size:.83rem;line-height:1.55}
blockquote{margin:1rem 0;padding:.7rem 1.1rem;border-left:3px solid var(--acc);
background:var(--soft);border-radius:0 8px 8px 0;color:var(--mut)}
blockquote strong{color:var(--fg)}
.tw{overflow-x:auto;margin:1rem 0}
table{border-collapse:collapse;width:100%;font-size:.87rem}
th{text-align:left;font-weight:600;color:var(--mut);font-size:.75rem;
text-transform:uppercase;letter-spacing:.04em;padding:.5rem .7rem;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:.5rem .7rem;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:nth-child(even){background:var(--soft)}
hr{border:0;border-top:1px solid var(--line);margin:2rem 0}
.mnote{font-size:.75rem;color:var(--faint);margin-bottom:-.6rem}
@media(max-width:820px){body{display:block}nav{position:static;width:auto;height:auto;
border-right:0;border-bottom:1px solid var(--line)}main{padding:1.5rem 1.25rem 4rem}}
"""

    js = """
const links=[...document.querySelectorAll('nav a[href^="#"]')];
const obs=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){
links.forEach(l=>l.classList.toggle('on',l.getAttribute('href')==='#'+e.target.id))}})},
{rootMargin:'-15% 0px -75% 0px'});
document.querySelectorAll('section').forEach(s=>obs.observe(s));
"""

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>brain &middot; documentation</title><style>{css}</style></head><body>
<nav><h1>brain<span>documentation</span></h1>
{"".join(nav)}
<div class="ext"><a href="dashboard.html">Dashboard (interactive) &rarr;</a>
<a href="https://github.com/BranislavCuturilo/BRAIN">Repository &rarr;</a></div>
</nav>
<main>{"".join(sections)}
<p class="src">Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by
scripts/brain/site.py &mdash; do not edit; edit the markdown and regenerate.</p>
</main><script>{js}</script></body></html>"""

    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(page):,} bytes, {len(sections)} pages)")
    if missing:
        print("  missing (skipped): " + ", ".join(missing))
    if "--open" in sys.argv:
        webbrowser.open(OUT.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
