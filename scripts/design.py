#!/usr/bin/env python3
"""DESIGN.md for an agent, generated from the stylesheet that already decides.

**Why generated and not written.** A hand-written design document is a second
copy of the palette, and the second copy is the one that goes stale -- the same
failure the brain calls a stale cited coordinate, which reads as verified
precisely because someone wrote it down. `tokens.css` is already declared the
single source of truth in its own header. This lifts it into prose rather than
restating it.

**Why the comments come with it.** The intent is already in the stylesheet and
nowhere else: that zebra rows are *never* pure white because a client asked for
a visible difference, that the location tint scale is deliberately clamped
because eight shades of that range are indistinguishable, that `body` and
`::selection` are deliberately left alone because touching them broke the older
Bootstrap-only templates. A generator that emits values and drops those
comments produces a palette an agent will follow and a rulebook it cannot see.

**What this does NOT contain.** How to build a screen -- the card body wrapper,
the dropdown clipped inside a card, verifying CSS actually rendered -- belongs
to `/brain:ui-bootstrap` and is cited, never copied. One rule, one place.

  design.py <tokens.css>              write DESIGN.md beside it
  design.py <tokens.css> -o PATH      somewhere else
  design.py <tokens.css> --check      exit 1 if the file on disk is stale
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: token prefix -> which section of the document it belongs in. Order is the
#: order of the document; a token matching nothing lands in "Other" rather than
#: being dropped, because a token nobody classified is still a token an agent
#: will meet in the markup.
SECTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Status colours", "status", ("status",)),
    ("Color palette", "colour",
     ("primary", "accent", "text", "border", "bg", "white", "row", "tint", "leaf")),
    ("Typography", "type",
     ("font", "h1", "h2", "h3", "h4", "h5", "h6", "leading")),
    ("Spacing", "space", ("space", "padding", "max-width")),
    ("Radii", "radius", ("radius",)),
    ("Depth and elevation", "shadow", ("shadow", "ring")),
    ("Icons", "icon", ("icon",)),
    ("Motion", "motion", ("transition",)),
    ("Layering", "z", ("z-",)),
]


def strip_comments(css: str) -> tuple[str, dict[int, str], dict[int, str]]:
    """CSS with comments blanked, plus the comments split by how they attach.

    The split matters and getting it wrong was the first bug here. A TRAILING
    comment belongs to its own line only:

        --tree-tint-1: #E7EDF4;   /* npr. Grad */
        --tree-tint-2: #F0F4F8;

    Without the distinction, tint-2 and tint-3 both inherited "npr. Grad" by
    looking a couple of lines up, and a generated document confidently labelled
    three different depths as the same thing.

    A STANDALONE comment sits on its own line(s) and describes what follows.
    Returns (css, inline_by_line, standalone_by_END_line).
    """
    inline: dict[int, str] = {}
    standalone: dict[int, str] = {}
    for m in re.finditer(r"/\*(.*?)\*/", css, re.S):
        start = css[:m.start()].count("\n")
        end = start + m.group(0).count("\n")
        line_head = css[css.rfind("\n", 0, m.start()) + 1:m.start()]
        body = re.sub(r"^\s*[=\-*]{3,}\s*$", "", m.group(1), flags=re.M)
        body = "\n".join(l.strip(" *\t") for l in body.splitlines())
        body = re.sub(r"\n{2,}", "\n", body).strip()
        body = re.sub(r"^\d+\.\s+", "", body)      # drop "3. STATUS BOJE" numbering
        if not body:
            continue
        (inline if line_head.strip() else standalone)[end] = body
    blanked = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), css, flags=re.S)
    return blanked, inline, standalone


def block(css: str, selector: str) -> list[tuple[str, str, int]]:
    """[(token, value, line)] declared in the first `selector { … }` block."""
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    if not m:
        return []
    start = css[:m.start(1)].count("\n")
    out = []
    for i, raw in enumerate(m.group(1).splitlines()):
        d = re.match(r"\s*(--[\w-]+)\s*:\s*([^;]+);", raw)
        if d:
            out.append((d.group(1), d.group(2).strip(), start + i))
    return out


def classify(token: str) -> str:
    name = token.lstrip("-")
    for _title, key, prefixes in SECTIONS:
        for p in prefixes:
            if p in name:
                return key
    return "other"


def nearest_note(line: int, inline: dict[int, str], standalone: dict[int, str],
                 decl_lines: set[int]) -> tuple[str, int | None]:
    """(text, standalone_key) for the comment that describes THIS declaration.

    A trailing comment on the same line, or a standalone comment immediately
    above it with no other declaration in between. Nothing else: a banner
    twenty lines up explains the section, and attaching it to every token below
    drowns the two comments that carry a real client decision.

    **Length is not a filter.** An earlier version returned "" for anything over
    300 characters, which silently deleted precisely the notes worth keeping --
    the one saying the location tint scale is deliberately finite because eight
    shades of that range are indistinguishable. Long notes are returned whole
    and the caller places them under the table.

    The key comes back so the caller can tell which standalone comments were
    claimed. The unclaimed ones are not leftovers.
    """
    if line in inline:
        return inline[line], None
    for gap in (1, 2, 3):
        end = line - gap
        if end in standalone:
            if any(d in decl_lines for d in range(end + 1, line)):
                return "", None            # another token claimed it first
            return standalone[end], end
    return "", None


def render(css_path: Path) -> str:
    raw = css_path.read_text(encoding="utf-8", errors="replace")
    css, inline, standalone = strip_comments(raw)

    # Remember WHICH comment the header was, not just its text. Excluding it
    # from the orphan list by position instead ("drop the first one") deleted a
    # real note whenever the header was short enough to be filtered out first.
    header_key = next((k for k in sorted(standalone) if k < 12), None)
    header = (standalone[header_key] if header_key is not None else "").splitlines()
    title = next((l for l in header if l and not l.startswith("-")), css_path.stem)

    root = block(css, ":root")
    tenants = {m.group(1): block(css, f".tenant-{m.group(1)}")
               for m in re.finditer(r"\.tenant-([\w-]+)\s*\{", css)}

    decl_lines = {ln for _t, _v, ln in root}
    by_section: dict[str, list] = {}
    for tok, val, line in root:
        by_section.setdefault(classify(tok), []).append((tok, val, line))

    L: list[str] = []
    add = L.append

    add(f"# {title} — design system for an agent")
    add("")
    add(f"**Generated from `{css_path.name}` on {date.today().isoformat()}. Do not edit "
        f"this file — edit the stylesheet and regenerate:**")
    add("")
    add("```bash")
    add(f"python scripts/design.py {css_path.as_posix()}")
    add("```")
    add("")
    add("A hand-edited copy of a palette is a second source of truth, and the copy "
        "is the one that goes stale while still reading as authoritative.")
    add("")

    intent = "\n".join(header[1:]).strip()
    if intent:
        add("## What the stylesheet says about itself")
        add("")
        for line in intent.splitlines():
            if line.strip():
                add(f"> {line.strip()}")
        add("")

    add("## Visual theme")
    add("")
    # Find the brand colour by SHAPE, not by a hardcoded prefix. The one
    # stylesheet this was written against uses `--hr-*` -- inherited from the
    # HRIS app its design was copied from, and unrelated to what that project
    # does. Baking that prefix in here would spread an accident of history to
    # every project this is ever pointed at.
    prim = next((v for t, v, _ in root
                 if t.endswith("-primary") or t == "--primary"), "")
    add(f"A calm, dense, information-first product surface: soft neutral ground, "
        f"one saturated brand colour{f' (`{prim}`)' if prim else ''}, generous "
        f"radii and layered low-opacity shadows rather than borders for depth. "
        f"It is a working tool, not a marketing page — nothing competes with the "
        f"data.")
    add("")

    #: A note too long for a table cell is not a note to throw away -- it is
    #: usually the one carrying a client decision or the reason a scale is
    #: clamped. Truncating it at 110 characters silently deleted exactly those.
    #: It goes UNDER the table instead of into it.
    CELL = 110
    claimed: set[int] = set()

    for title_s, key, _ in SECTIONS:
        rows = by_section.get(key)
        if not rows:
            continue
        add(f"## {title_s}")
        add("")
        add("| Token | Value | Note |")
        add("|---|---|---|")
        long_notes: list[tuple[str, str]] = []
        for tok, val, line in rows:
            note, src = nearest_note(line, inline, standalone, decl_lines)
            if src is not None:
                claimed.add(src)
            flat = note.replace("\n", " ").replace("|", "\\|").strip()
            if len(flat) > CELL:
                add(f"| `{tok}` | `{val}` | see below |")
                long_notes.append((tok, note.strip()))
            else:
                add(f"| `{tok}` | `{val}` | {flat} |")
        add("")
        for tok, note in long_notes:
            add(f"**`{tok}`**")
            add("")
            for para in note.splitlines():
                if para.strip():
                    add(f"> {para.strip()}")
            add("")

    other = by_section.get("other")
    if other:
        add("## Other tokens")
        add("")
        add("| Token | Value |")
        add("|---|---|")
        for tok, val, _ in other:
            add(f"| `{tok}` | `{val}` |")
        add("")

    if tenants:
        add("## Per-tenant brands")
        add("")
        add("Every tenant inherits the whole system and overrides only what is "
            "listed. A tenant that overrides a token not shown here is a change "
            "to the stylesheet, not to this file.")
        add("")
        add("| Tenant | Overrides |")
        add("|---|---|")
        for name in sorted(tenants):
            over = ", ".join(f"`{t}` = `{v}`" for t, v, _ in tenants[name])
            add(f"| `.tenant-{name}` | {over[:400]} |")
        add("")

    add("## Do's and don'ts")
    add("")
    add("- **Every colour is a token.** A hex literal in a template is invisible "
        "to theming, to per-tenant branding and to dark mode. The only hex "
        "allowed is a documented sentinel, marked as reserved.")
    add("- **Every size is a token.** The spacing ladder is a 4px scale; a value "
        "off the ladder is a value nobody can change globally later.")
    add("- **Read the Note column before overriding anything in it.** Those notes "
        "are the client decisions and the reasons a value is what it is — they "
        "were lifted from the stylesheet, not invented here.")
    add("- **How to BUILD a screen is not in this file.** The card body wrapper, "
        "a dropdown clipped inside a card, verifying that CSS actually rendered "
        "— those are `/brain:ui-bootstrap`, and they are cited here rather than "
        "copied so there is one copy to correct.")
    add("")

    #: A standalone comment no declaration claimed. Banners fall out by length;
    #: what survives is prose -- and in this stylesheet that prose is the note
    #: saying `body`, `::selection` and the scrollbar are deliberately left
    #: alone because overriding them broke the older Bootstrap-only templates.
    #: Dropping it leaves an agent free to "fix" the exact thing that was
    #: decided, and to believe it was an oversight.
    orphans = [standalone[k] for k in sorted(standalone)
               if k not in claimed and k != header_key
               and len(standalone[k]) >= 120]
    if orphans:
        add("## Decisions recorded in the stylesheet")
        add("")
        add(f"Prose from `{css_path.name}` that belongs to no single token. It is "
            f"here because it says what was decided and why, which no list of "
            f"values can.")
        add("")
        for o in orphans:
            for para in o.splitlines():
                if para.strip():
                    add(f"> {para.strip()}")
            add("")

    add("## Agent prompt guide")
    add("")
    add("When asked to build or restyle a screen in this system:")
    add("")
    add("1. Name the tokens you will use before writing markup. If a needed "
        "token does not exist, say so — do not invent a value.")
    add("2. Reuse an existing component class before adding CSS. New CSS is the "
        "last resort, not the first move.")
    add("3. Never write a hex, a px size or a shadow inline.")
    add("4. State which tenants you checked. A change that looks right in one "
        "brand can fail in another that overrides the radius or the font.")
    add("5. Say what you did not verify. Rendered and written are different "
        "claims.")
    return "\n".join(L) + "\n"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__.strip())
        return 1
    src = Path(args[0]).resolve()
    if not src.is_file():
        print(f"  no such stylesheet: {src}")
        return 1

    out = Path(sys.argv[sys.argv.index("-o") + 1]).resolve() \
        if "-o" in sys.argv and sys.argv.index("-o") + 1 < len(sys.argv) \
        else src.parent / "DESIGN.md"

    text = render(src)

    if "--check" in sys.argv:
        # Compare everything but the generated-on date, or a file regenerated on
        # a different day always reports stale and the check becomes noise.
        def norm(s: str) -> str:
            return re.sub(r"on \d{4}-\d{2}-\d{2}", "on DATE", s)
        current = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
        if norm(current) == norm(text):
            print(f"  up to date: {out}")
            return 0
        print(f"  STALE: {out} does not match {src.name} — regenerate it")
        return 1

    out.write_text(text, encoding="utf-8")
    n = len([l for l in text.splitlines() if l.startswith("| `--")])
    print(f"  wrote {out}")
    print(f"  {n} tokens, {len(text):,} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
