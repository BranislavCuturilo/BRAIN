#!/usr/bin/env python3
"""Diff -> which screens changed, and WHERE on them to look.

The plan's binding decision: **the code diff decides**, never a model looking at
pictures. So both halves of the answer come from the same place:

  pages    the changed template/CSS/JS mapped onto the page inventory
  anchors  the id / class / {% trans %} string / CSS selector taken from the
           ADDED LINES of the hunks - this is what draws the box for the customer

An anchor that comes from a picture is a guess with a rectangle around it. An
anchor that comes from the hunk is a fact.

`from_diff(repo_root, files=None, diff_text=None)` works three ways, all through
ONE hunk parser: an explicit unified diff (tests, hooks that already have one),
an explicit file list (added lines read from git, or the whole file when git
cannot say), or the working tree (`git diff HEAD` + staged + untracked).

The whole-file fallback is the trap this module has to keep visible: when git
yields no hunk the "added lines" are the ENTIRE file, and an anchor read from
them names an element nobody touched. Every anchor therefore carries
`from_diff`, and only `from_diff` anchors may draw the customer's box.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# THE PROLOGUE (visual/__init__.py): run as a script, `sys.path[0]` is THIS
# directory, so the engine's module names answer for the app's - a repo asking
# for its own `config` package got `visual/config.py`. Swap that entry for the
# package's parent and import relatively. Copy this block verbatim into any new
# entry point (every module here with a `__main__` block carries it).
if __package__ in (None, ""):                                   # run as a script
    _DIR = os.path.dirname(os.path.abspath(__file__))
    _NC = os.path.normcase(_DIR)
    sys.path[:] = [p for p in sys.path
                   if p and os.path.normcase(os.path.abspath(p)) != _NC]
    sys.path.insert(0, os.path.dirname(_DIR))
    __package__ = os.path.basename(_DIR)

from . import catalog as vcatalog
from . import config as vconfig                                 # noqa: E402
from . import pages as vpages                                   # noqa: E402

#: Anchor kinds, best first: `pair()` boxes the FIRST one that locates, so the
#: order here is the order of usefulness to a customer looking at a screenshot.
ANCHOR_RANK = {"id": 0, "field": 1, "text": 2, "css_selector": 3, "class": 4}

#: Where a file's "added lines" came from, PER FILE. Only `HUNK` lines are
#: evidence of THIS change. `WHOLE_FILE` means git could not produce a hunk (the
#: file is untracked, the edit is not made yet, or it is already committed) and
#: the "added lines" are every line of the file - so an anchor read from them is
#: about an element nobody touched. The distinction used to exist only in the
#: aggregated `source` string, which nothing downstream could read per file:
#: `after` on an already-committed change fell back to the whole file, the
#: longest untouched string won the ranking, and the customer got a zoom of an
#: icon nobody had edited (DEMO#05513). Every anchor now carries it.
HUNK = "hunk"
WHOLE_FILE = "whole_file"

_RE_ID = re.compile(r'\bid\s*=\s*"([A-Za-z][\w:.-]*)"')
_RE_CLASS = re.compile(r'\bclass\s*=\s*"([^"{}]+)"')
_RE_TRANS = re.compile(r'{%\s*trans(?:late)?\s+"([^"]{2,80})"')
_RE_BLOCKTRANS = re.compile(r'{%\s*blocktrans[^%]*%}(.{2,80}?){%\s*endblocktrans')
_RE_TAGTEXT = re.compile(r'>([^<>{}]{3,80})<')
_RE_TEMPLATE_SYNTAX = re.compile(r'{[{%].*?[%}]}', re.S)
#: `{{ form.parent }}`, `{{ form.parent.id_for_label }}`, `{% if form.parent.errors %}`.
#: A Django form field is rendered by the FORM, not by the template, so its id,
#: its label and its help text exist only at run time - the hunk that adds a
#: field carries nothing but `form-label` and `form-text`, which every other
#: field on the page carries too. This is the one thing in such a hunk that
#: names the element: Django renders the field as `name="parent" id="id_parent"`.
#: Without it a form change can only be pointed at as "somewhere in this form".
_RE_FORM_FIELD = re.compile(r'{[{%][^{}%]*\bform\.([A-Za-z_]\w*)')
#: Attributes of the FORM, never fields on it.
FORM_META = {"errors", "non_field_errors", "media", "instance", "cleaned_data",
             "fields", "hidden_fields", "visible_fields", "is_valid", "as_p",
             "as_table", "as_ul", "initial", "prefix", "data", "empty_permitted"}
_RE_INCLUDE = re.compile(r'{%\s*(?:include|extends)\s+["\']([^"\']+)["\']')
_RE_EXTENDS = re.compile(r'{%\s*extends\s+["\']([^"\']+)["\']')
_RE_CSS_SELECTOR_LINE = re.compile(r'^\s*([^@{};/][^{};]*?)\s*\{')
_RE_TOKEN = re.compile(r'[.#]([A-Za-z][\w-]*)')
_RE_JS_SELECTOR = re.compile(r'["\']([.#][A-Za-z][\w-]*)["\']')
_RE_JS_BY_ID = re.compile(r'getElementById\(\s*["\']([A-Za-z][\w-]*)["\']')

#: Layout/utility classes carry no meaning for a customer ("look at the mb-3").
#: They are still emitted - a spacing change IS a real change - but ranked last
#: so a meaningful anchor always wins the box.
_UTILITY_CLASS = re.compile(
    r'^(?:col|row|d|m[trblxy]?|p[trblxy]?|g|gap|w|h|mw|mh|text|bg|border|rounded'
    r'|fs|fw|align|justify|flex|order|position|top|start|end|bottom|z|overflow'
    r'|float|shadow|opacity|user|pe|ms|me|mt|mb|ps|pe)(?:-|$)')


# --------------------------------------------------------------------------- #
#  glob -> regex, with real `**` semantics
#
#  fnmatch treats `*` as crossing `/`, so `templates/**/*.html` would silently
#  fail to match `templates/base.html` while matching things it should not. The
#  globs come from the repo config and decide whether a change is visual at all,
#  so they get a translator that means what the operator wrote.
# --------------------------------------------------------------------------- #
def _glob_re(pat: str):
    out, i, n = ["^"], 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if pat[i:i + 3] == "**/":
                out.append(r"(?:.*/)?")
                i += 3
                continue
            if pat[i:i + 2] == "**":
                out.append(r".*")
                i += 2
                continue
            out.append(r"[^/]*")
        elif c == "?":
            out.append(r"[^/]")
        elif c == ".":
            out.append(r"\.")
        else:
            out.append(re.escape(c))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def _matches(rel: str, patterns) -> bool:
    return any(_glob_re(p).match(rel) for p in patterns)


def classify(rel: str, cfg: dict) -> str:
    """"template" | "css" | "js" | "" - only the app's own globs decide."""
    rel = rel.replace("\\", "/")
    if _matches(rel, cfg.get("template_globs") or []):
        return "template"
    if _matches(rel, cfg.get("static_globs") or []):
        if rel.lower().endswith(".css"):
            return "css"
        if rel.lower().endswith(".js"):
            return "js"
    return ""


# --------------------------------------------------------------------------- #
#  Changed files + added lines
# --------------------------------------------------------------------------- #
def _git(repo, *args):
    """Run one git command and return its stdout, or None.

    DECODE AS UTF-8 EXPLICITLY. `text=True` alone decodes with the locale
    codec, which on a Windows console is cp1252 - so on any repository whose
    source contains non-ASCII (this brain's own projects are Serbian: c/c/s/z/d)
    a perfectly good `git diff` raised UnicodeDecodeError, `_git` returned None
    or blew up, `changed_files()` set `have_git = False`, and EVERY file fell
    back to whole-file anchors. That is the fallback that made the engine box
    elements nobody had changed - the loudest defect the operator reported, and
    it was loudest precisely on the repositories that trip this codec.

    `errors="replace"` because a diff is evidence to read, not data to round
    trip: one undecodable byte must not cost the whole hunk list.
    """
    try:
        r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=30)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def parse_unified_diff(text: str) -> dict:
    """`{path: [(lineno, added_line_text), ...]}` from a unified diff.

    The ONE parser: `git diff` output and a diff handed in by a caller go through
    it, so a test exercises the same code the hook does.
    """
    out, path, lineno = {}, None, 0
    for line in (text or "").splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p.startswith("b/"):
                p = p[2:]
            path = None if p == "/dev/null" else p.replace("\\", "/")
            out.setdefault(path, []) if path else None
            continue
        if line.startswith("--- ") or line.startswith("diff --git"):
            continue
        if line.startswith("@@"):
            m = re.search(r'\+(\d+)', line)
            lineno = int(m.group(1)) if m else 1
            continue
        if path is None:
            continue
        if line.startswith("+"):
            out.setdefault(path, []).append((lineno, line[1:]))
            lineno += 1
        elif line.startswith("-") or line.startswith("\\"):
            continue
        else:
            lineno += 1
    return out


def _whole_file(repo, rel):
    try:
        txt = (Path(repo) / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return list(enumerate(txt.splitlines(), 1))


def changed_files(repo, files=None, diff_text=None):
    """(`{path: [(lineno, text)]}`, source, provenance) - the changed paths,
    their ADDED lines, and PER FILE where those lines came from.

    `provenance[path]` is `HUNK` or `WHOLE_FILE`; `source` stays the one-line
    summary for the report. The per-file answer is the load-bearing one: a
    caller that draws a box must use HUNK anchors ONLY, and the summary string
    cannot say which of twenty files fell back.
    """
    if diff_text:
        parsed = parse_unified_diff(diff_text)
        return parsed, "diff_text", {rel: HUNK for rel in parsed}

    if files:
        rels = [str(f).replace("\\", "/") for f in files]
        # ONE git call per side for the whole list: on Windows a subprocess per
        # file turns a 20-file commit into 40 process spawns.
        hunks, have_git = {}, True
        for args in (("diff", "-U0", "HEAD", "--", *rels),
                     ("diff", "-U0", "--cached", "--", *rels)):
            txt = _git(repo, *args)
            if txt is None:
                have_git = False
                break
            for path, lines in parse_unified_diff(txt).items():
                hunks.setdefault(path, []).extend(lines)

        added, provenance = {}, {}
        for rel in rels:
            if have_git and hunks.get(rel):
                added[rel] = hunks[rel]
                provenance[rel] = HUNK
            else:
                # New/untracked file, no git at all, or a change that is already
                # committed: everything in it counts as "added", and the caller
                # is told so PER FILE - never only in the summary.
                added[rel] = _whole_file(repo, rel)
                provenance[rel] = WHOLE_FILE
        fell_back = any(v == WHOLE_FILE for v in provenance.values())
        src = ("git" if have_git else "no_git") + ("+whole_file" if fell_back else "")
        return added, src, provenance

    names = set()
    for args in (("diff", "--name-only", "HEAD"),
                 ("diff", "--name-only", "--cached"),
                 ("ls-files", "--others", "--exclude-standard")):
        txt = _git(repo, *args)
        if txt is None:
            return {}, "no_git", {}
        names.update(n.strip().replace("\\", "/") for n in txt.splitlines() if n.strip())
    # A CLEAN tree is an answer: nothing changed. Without this the empty list
    # falls into the `if files:` branch below being falsy, lands back here, and
    # recurses until Python gives up - `shoot.py before --repo X` on a committed
    # tree died with a RecursionError raised from inside subprocess, naming
    # nothing. Reachable any time `before` runs without `--files`.
    if not names:
        return {}, "git", {}
    added, _, provenance = changed_files(repo, files=sorted(names))
    return added, "git", provenance


# --------------------------------------------------------------------------- #
#  Anchors
# --------------------------------------------------------------------------- #
def _clean_text(s: str) -> str:
    s = _RE_TEMPLATE_SYNTAX.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _template_anchors(lines) -> list:
    out = []
    for _, raw in lines:
        for m in _RE_ID.finditer(raw):
            out.append(("id", m.group(1)))
        for m in _RE_TRANS.finditer(raw):
            out.append(("text", m.group(1).strip()))
        for m in _RE_BLOCKTRANS.finditer(raw):
            t = _clean_text(m.group(1))
            if t:
                out.append(("text", t))
        for m in _RE_TAGTEXT.finditer(raw):
            t = _clean_text(m.group(1))
            if len(t) >= 3 and not t.isdigit() and re.search(r"[A-Za-z]", t):
                out.append(("text", t))
        for m in _RE_FORM_FIELD.finditer(raw):
            name = m.group(1)
            if name not in FORM_META:
                out.append(("field", name))
        for m in _RE_CLASS.finditer(raw):
            for tok in m.group(1).split():
                if tok and not tok.startswith("{"):
                    out.append(("class", tok))
    return out


def _clean_selector(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()


def _css_line_selectors(text: str) -> list:
    """Per source line (1-based), the STACK of block selectors open on it,
    outermost first: `('@media (max-width: 768px)', '.stat-card')`.

    A brace walk, not a line scan, because the enclosing rule of a changed
    property is what draws the customer's box and `-U0` context cannot show it.
    A stack rather than one string because a rule inside `@media` has TWO
    enclosing blocks and only the inner one is a selector - the previous version
    returned the `@media` line and then dropped it for starting with `@`, so
    every responsive rule mapped to no screen at all.

    Comments and quoted strings are skipped, so a `{` inside them cannot shift
    the whole file by one level.
    """
    out, stack, buf = [()], [], []
    deepest = []
    in_comment, quote = False, ""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            out.append(tuple(deepest))
            deepest = list(stack)
            i += 1
            continue
        if in_comment:
            if ch == "*" and nxt == "/":
                in_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            buf.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "*":
            in_comment = True
            i += 2
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "{":
            stack.append(_clean_selector("".join(buf)))
            buf = []
            if len(stack) >= len(deepest):
                deepest = list(stack)
        elif ch == "}":
            if len(stack) > len(deepest):
                deepest = list(stack)
            if stack:
                stack.pop()
            buf = []
        elif ch == ";":
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append(tuple(deepest))
    return out


def _selector_at(stacks, lineno: int) -> str:
    """The INNERMOST real selector open at `lineno` - walking out of `@media`,
    `@supports` and friends, which select no element."""
    stack = stacks[lineno] if 0 < lineno < len(stacks) else ()
    for sel in reversed(stack):
        if sel and not sel.startswith("@"):
            return sel
    return ""


def _media_at(stacks, lineno: int) -> str:
    """The innermost `@media` condition open at `lineno`, or "".

    `_selector_at` walks OUT of `@media` to find the element a change draws a
    box around, and the condition it walked past was then dropped. It is the
    one fact that says WHICH WIDTHS a change can possibly affect: a rule inside
    `@media (max-width: 768px)` cannot change anything on a desktop, and
    shooting it there is 429 of 430 images wasted.
    """
    stack = stacks[lineno] if 0 < lineno < len(stacks) else ()
    for sel in reversed(stack):
        if sel and sel.startswith("@media"):
            return sel
    return ""


#: Widths a `@media` condition makes interesting. A rule bounded at 768px is
#: worth seeing ON the boundary and one pixel past it -- those are the two
#: renderings it distinguishes, and every other width in the range renders the
#: same CSS.
_RE_MEDIA_BOUND = re.compile(r"(min|max)-width\s*:\s*(\d+(?:\.\d+)?)\s*(px|em|rem)",
                             re.I)


def media_matches(cond: str, width: int) -> bool:
    """Is a rule under `cond` active at `width`?

    Only width bounds are read. A condition on orientation, hover or print is
    treated as active, because the honest answer for a bound this cannot
    evaluate is "it might apply" -- narrowing on a guess would skip the width
    that shows the defect.
    """
    ok = True
    for kind, num, unit in _RE_MEDIA_BOUND.findall(cond or ""):
        px = float(num) * (16.0 if unit.lower() in ("em", "rem") else 1.0)
        if kind.lower() == "max":
            ok = ok and width <= px
        else:
            ok = ok and width >= px
    return ok


def media_widths(conditions) -> list:
    """Sorted widths worth rendering, for a set of `@media` conditions.

    `em`/`rem` are converted at the browser default of 16px. That is an
    assumption and it is stated here rather than hidden: a project that changes
    the root font size will have these off by that ratio, which moves the
    sample a few pixels and never changes which side of the boundary it is on.
    """
    out = set()
    for cond in conditions:
        for kind, num, unit in _RE_MEDIA_BOUND.findall(cond or ""):
            px = float(num) * (16.0 if unit.lower() in ("em", "rem") else 1.0)
            n = int(round(px))
            if n <= 0 or n > 4096:
                continue
            if kind.lower() == "max":
                out.update({n, n + 1})          # inside the rule, and just out
            else:
                out.update({n, max(1, n - 1)})  # inside the rule, and just out
    return sorted(out)


def _css_anchors(repo, rel, lines, cache=None, media_out=None) -> list:
    """Anchors for the changed CSS lines.

    `media_out`, when given, collects the `@media` conditions those lines sit
    inside. Without it the condition is computed and thrown away, which is
    what made every responsive change look like a change at every width.
    """
    text = _read(repo, rel, cache if cache is not None else {})
    stacks = _css_line_selectors(text) if text else [()]
    out, seen = [], set()
    for lineno, raw in lines:
        m = _RE_CSS_SELECTOR_LINE.match(raw)
        sel = _clean_selector(m.group(1)) if m else _selector_at(stacks, lineno)
        if media_out is not None:
            cond = _media_at(stacks, lineno)
            if cond:
                media_out.add(cond)
        if sel and sel not in seen and not sel.startswith("@"):
            seen.add(sel)
            out.append(("css_selector", sel))
    return out


def _js_anchors(lines) -> list:
    out = []
    for _, raw in lines:
        for m in _RE_JS_BY_ID.finditer(raw):
            out.append(("id", m.group(1)))
        for m in _RE_JS_SELECTOR.finditer(raw):
            v = m.group(1)
            out.append(("id" if v.startswith("#") else "class", v[1:]))
    return out


def _rank(a) -> tuple:
    """Best first. PROVENANCE leads: an anchor that came out of a hunk outranks
    every whole-file anchor whatever its kind, because only the first kind is
    about the change. Anything without the flag (an anchor merged in from an
    older manifest) is treated as whole-file - the safe side."""
    kind, value = a["kind"], a["value"]
    utility = 1 if (kind == "class" and _UTILITY_CLASS.match(value)) else 0
    return (0 if a.get("from_diff") else 1,
            utility, ANCHOR_RANK.get(kind, 9), -len(value))


# --------------------------------------------------------------------------- #
#  Mapping changed files onto pages
# --------------------------------------------------------------------------- #
def _read(repo, rel, cache):
    if rel not in cache:
        try:
            cache[rel] = (Path(repo) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            cache[rel] = ""
    return cache[rel]


def _body(repo, page, cache) -> str:
    """A page's template source. `template_path` is the RESOLVED file; the raw
    `template` field is often a Django template name that is not a path."""
    rel = page.get("template_path") or ""
    return _read(repo, rel, cache) if rel else ""


# --------------------------------------------------------------------------- #
#  The template graph
#
#  A page does not render one file. It renders its own template, everything that
#  template extends, everything those include, and so on. In acme-audit a
#  segment page extends `<app>/base_<segment>.html`, which extends `base.html` -
#  so a ONE-LEVEL walk from base.html reaches one page out of seventeen, and a
#  partial included by a partial reaches none. Both halves of the answer (which
#  page changed, and which page uses this CSS class) run off the TRANSITIVE
#  closure instead.
# --------------------------------------------------------------------------- #
_EDGES_KEY = "//edges"                  # namespaced: `cache` is keyed by rel path
_CLOSURE_KEY = "//closures"
_TOKEN_KEY = "//tails_with_token"


def _edges(repo, cache) -> dict:
    """`{template tail: {tails it extends or includes}}` over EVERY template in
    the repo - the intermediate ones are not pages, which is the whole point."""
    if _EDGES_KEY not in cache:
        out = {}
        for tail, rel in vpages.template_index(repo)["by_tail"].items():
            body = _read(repo, rel, cache)
            out[tail] = {m.group(1).replace("\\", "/")
                         for m in _RE_INCLUDE.finditer(body)}
        cache[_EDGES_KEY] = out
    return cache[_EDGES_KEY]


def _closure(repo, tail, cache) -> set:
    """`tail` plus everything it pulls in, transitively. Cycle-guarded: a
    template that includes itself (directly or through a partial) must not hang
    the pre-commit hook."""
    edges = _edges(repo, cache)
    seen, stack = set(), [tail]
    while stack:
        cur = stack.pop()
        if not cur or cur in seen:
            continue
        seen.add(cur)
        stack.extend(edges.get(cur) or ())
    return seen


def _page_closures(repo, inv, cache) -> dict:
    """`{page_id: {tails the page renders}}`, own template included."""
    if _CLOSURE_KEY not in cache:
        out = {}
        for p in inv:
            tail = _own_tail(p)
            out[p["page_id"]] = _closure(repo, tail, cache) if tail else set()
        cache[_CLOSURE_KEY] = out
    return cache[_CLOSURE_KEY]


def _own_tail(page) -> str:
    return vpages.template_tail(page.get("template_path") or "") or \
        (page.get("template") or "").replace("\\", "/")


def _pages_including(repo, inv, tail, cache) -> list:
    """Pages that render `tail` WITHOUT owning it - transitively, so a partial
    two levels down and a base two levels up both reach their screens."""
    if not tail:
        return []
    closures = _page_closures(repo, inv, cache)
    return [p for p in inv
            if tail in closures.get(p["page_id"], ()) and _own_tail(p) != tail]


def _tails_with_token(repo, kind, token, cache) -> set:
    """Template names carrying `id="token"` / `class="... token ..."`.

    Searched per TEMPLATE and memoised, not per page: a base and its partials
    are rendered by dozens of pages, and re-scanning them once per page turns a
    stylesheet diff into minutes."""
    memo = cache.setdefault(_TOKEN_KEY, {})
    key = (kind, token)
    if key in memo:
        return memo[key]
    if kind == "id":
        pat = re.compile(r'\bid\s*=\s*"' + re.escape(token) + r'"')
    else:
        pat = re.compile(r'\bclass\s*=\s*"[^"]*(?<![\w-])' + re.escape(token) + r'(?![\w-])')
    hit = {tail for tail, rel in vpages.template_index(repo)["by_tail"].items()
           if pat.search(_read(repo, rel, cache))}
    memo[key] = hit
    return hit


def _pages_using_token(repo, inv, kind, token, cache) -> list:
    """Pages that RENDER the token, in any template they reach - a class used
    only in `base.html` or in a partial belongs to every page that pulls it in,
    and searching each page's own file for it answers "nobody uses this"."""
    tails = _tails_with_token(repo, kind, token, cache)
    if not tails:
        return []
    closures = _page_closures(repo, inv, cache)
    return [p for p in inv if closures.get(p["page_id"], set()) & tails]


def _layout_tails(repo, cache) -> set:
    """Template names that other templates `{% extends %}` - the layouts.
    Repo-wide, because a segment base is itself extended and is not a page."""
    out = set()
    for tail, rel in vpages.template_index(repo)["by_tail"].items():
        for m in _RE_EXTENDS.finditer(_read(repo, rel, cache)):
            out.add(m.group(1).replace("\\", "/"))
    return out


def _pages_linking(repo, inv, rel, cache) -> list:
    """Pages whose OWN template links the static file (a `{% static %}` line).

    A LAYOUT is excluded on purpose. The global stylesheet is linked once, in
    `base.html`, and `base.html` is a page in the map — so without this the
    answer to "which screen does `style.css` change?" is "the login page",
    confidently and wrongly. When nothing is left, the file belongs in
    `unlocated`: the plan already has a name for that case (a CSS change that
    touches everything is offered without a box), and a truthful "we cannot say"
    beats a precise-looking lie.
    """
    base = Path(rel).name
    layouts = _layout_tails(repo, cache)
    out = []
    for p in inv:
        tail = _own_tail(p)
        if tail and tail in layouts:
            continue
        if base in _body(repo, p, cache):
            out.append(p)
    return out


def from_diff(repo_root, files=None, diff_text=None, config=None) -> dict:
    """The whole answer:

    ``{"pages": [{page_id,url,title,template,why}],
       "anchors": [{file,kind,value,page_ids,from_diff}],
       "unlocated": [{file,kind,reason,anchors}],
       "changed_files": [...], "considered": [...], "overflow": [...],
       "provenance": {file: "hunk"|"whole_file"}, "source": "git|diff_text|...",
       "media": ["@media (max-width: 768px)", ...], "widths": [768, 769, ...]}``

    `from_diff` records HOW the anchor was read: True out of an added line of a
    hunk, False off a file git could not diff, where it names an element that may
    well be untouched. **Only a `from_diff` anchor may decide where to crop**
    (`anchors_for_page`); the rest earn their place by saying WHICH PAGES a
    stylesheet or a script reaches (`_pages_using_token`).

    A visual file that maps to no page lands in `unlocated` with its anchors —
    the operator sees that something changed and that we could not say where.
    """
    repo = Path(repo_root)
    cfg = config if config is not None else vconfig.load_config(repo)
    if vconfig.is_error(cfg):
        return dict(cfg)

    added, source, provenance = changed_files(repo, files=files, diff_text=diff_text)
    #: `@media` conditions the changed CSS lines sit inside, filled by
    #: `_css_anchors`. Empty means the change renders the same at every width.
    media_conditions: set = set()
    inv = vpages.usable(vpages.inventory(cfg, repo))
    tmpl_index = vpages.by_template(inv)
    cache = {}

    by_page, anchors, unlocated, considered = {}, [], [], []

    def add_page(p, why):
        row = by_page.setdefault(p["page_id"], {
            "page_id": p["page_id"], "url": p["url"], "title": p["title"],
            "template": p["template"], "why": []})
        if why not in row["why"]:
            row["why"].append(why)

    for rel in sorted(added):
        kind = classify(rel, cfg)
        if not kind:
            continue
        considered.append(rel)
        lines = added[rel]
        hit_pages = []

        if kind == "template":
            raw_anchors = _template_anchors(lines)
            norm = rel.lstrip("./")
            tail = vpages.template_tail(rel)
            direct = []
            for lookup in (norm, tail):
                for p in tmpl_index.get(lookup, []) if lookup else []:
                    if p["page_id"] not in {d["page_id"] for d in direct}:
                        direct.append(p)
            for p in direct:
                add_page(p, "template changed")
                hit_pages.append(p)
            for p in _pages_including(repo, inv, tail, cache):
                add_page(p, "includes %s" % tail)
                hit_pages.append(p)
        else:                                                   # css | js
            if kind == "css":
                raw_anchors = _css_anchors(repo, rel, lines, cache,
                                           media_out=media_conditions)
                tokens = [("id" if sel[m.start()] == "#" else "class", m.group(1))
                          for _, sel in raw_anchors for m in _RE_TOKEN.finditer(sel)]
            else:
                raw_anchors = _js_anchors(lines)
                tokens = list(raw_anchors)
            for tk, tok in tokens:
                for p in _pages_using_token(repo, inv, tk, tok, cache):
                    add_page(p, "uses %s%s" % ("#" if tk == "id" else ".", tok))
                    hit_pages.append(p)
            # Only when the selectors located nothing: the link is the weakest
            # evidence we have, and it must never outvote a real usage.
            if not hit_pages:
                for p in _pages_linking(repo, inv, rel, cache):
                    add_page(p, "links %s" % Path(rel).name)
                    hit_pages.append(p)

        file_anchor_rows, seen = [], set()
        from_hunk = provenance.get(rel, WHOLE_FILE) == HUNK
        for akind, value in raw_anchors:
            value = str(value).strip()
            if not value or (akind, value) in seen:
                continue
            seen.add((akind, value))
            row = {"file": rel, "kind": akind, "value": value,
                   "from_diff": from_hunk,
                   "page_ids": sorted({p["page_id"] for p in hit_pages})}
            if akind == "text":
                # THE MSGID IS NOT WHAT THE PAGE SAYS. `{% trans "Postal code" %}`
                # renders "Postanski broj", so the anchor the diff gives us can
                # never match the DOM - and the only text anchors that ever did
                # were the ones gettext leaves alone, i.e. material-icon
                # ligatures. The rendered forms travel with the anchor and the
                # matcher tries them all (`catalog.py`).
                alternates = vcatalog.translations_of(repo, value)
                if alternates:
                    row["alternates"] = alternates
            file_anchor_rows.append(row)
        file_anchor_rows.sort(key=_rank)
        anchors.extend(file_anchor_rows[:40])

        if not hit_pages:
            # Never a silent zero: the operator has to be able to tell "nothing
            # on any screen uses this" from "we could not read a selector out of
            # the change at all", because the two need different answers.
            if not file_anchor_rows:
                reason = ("no id/class/selector could be read from the changed "
                          "lines")
            else:
                named = ", ".join(a["value"] for a in file_anchor_rows[:5])
                reason = "no page in the inventory renders %s" % named
            unlocated.append({"file": rel, "kind": kind, "reason": reason,
                              "anchors": file_anchor_rows[:10]})

    rows = list(by_page.values())
    rows.sort(key=lambda r: (0 if "template changed" in r["why"] else
                             1 if any(w.startswith("includes") for w in r["why"]) else 2,
                             r["page_id"]))
    limit = int(cfg.get("max_pages") or 20)
    overflow = [r["page_id"] for r in rows[limit:]]
    rows = rows[:limit]
    keep = {r["page_id"] for r in rows}
    for a in anchors:
        a["page_ids"] = [pid for pid in a["page_ids"] if pid in keep]

    return {"pages": rows, "anchors": anchors, "unlocated": unlocated,
            "changed_files": sorted(added), "considered": considered,
            "overflow": overflow, "provenance": provenance, "source": source,
            # The widths this change can possibly alter. EMPTY means the CSS
            # that changed sits in no media query, so it renders the same
            # everywhere and one width is enough -- not that no width matters.
            "media": sorted(media_conditions),
            "widths": media_widths(media_conditions)}


def anchors_for_page(result: dict, page_id: str, only_from_diff=True) -> list:
    """The anchors that belong to one page, best first.

    `only_from_diff` (the default) keeps the HUNK ones alone - the only list that
    may decide where to CROP. When git yields no hunk the whole file becomes
    "added lines", and those anchors name elements nobody touched: that is how a
    material-icon name off an untouched line once got the rectangle on both
    halves of an identical zoom. Empty is a legitimate answer and means "we
    cannot say where on this screen the change is", never "use the next best
    thing" - the pair is then two full pictures and no crop.
    """
    rows = [a for a in result.get("anchors") or [] if page_id in (a.get("page_ids") or [])]
    if only_from_diff:
        rows = [a for a in rows if a.get("from_diff")]
    rows.sort(key=_rank)
    return rows


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Changed files -> affected pages + anchors.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--files", nargs="*", default=None)
    a = ap.parse_args()
    print(json.dumps(from_diff(a.repo, files=a.files), ensure_ascii=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
