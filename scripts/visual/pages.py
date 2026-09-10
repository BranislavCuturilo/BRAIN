#!/usr/bin/env python3
"""The page inventory: which screens exist, at which URL, from which template.

Source of truth is the app's own map — for acme-audit that is
`razvojna-mapa/_data/*.json` (232 pages over 16 apps), each file
`{app, url_prefix, pages: [{id, title_sr, url, url_name, template, view, ...}]}`.
A repo without such a map puts an explicit list in its config instead
(`pages.kind == "list"`).

Two things are deliberately NOT silent:

* A URL with an unresolved parameter (`/audits/<pk>/`) cannot be fetched. 113 of
  the 232 pages are like this. They stay in the returned list carrying
  `skipped_reason`, so a caller can see and report what was not captured — a
  filtered-out page is a page nobody knows is missing.
* A `template` that is not a path (the map writes prose like
  "(JSON odgovor - bez templejta)" for endpoints that render nothing) means there
  is no screen. Same treatment: kept, marked, never quietly dropped.

`config.sample_ids` / `config.sample_ids_by_page` resolve parameters, per page
first and then globally.
"""
from __future__ import annotations

import json
import os
import re
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

#: `<int:pk>`, `<pk>`, `<str:token>` -> the parameter NAME (after the converter).
_PARAM = re.compile(r"<([^<>]+)>")


def _param_name(raw: str) -> str:
    return raw.split(":")[-1].strip()


def _title(p: dict) -> str:
    for k in ("title_sr", "title", "name"):
        v = str(p.get(k) or "").strip()
        if v:
            return v
    return str(p.get("id") or "").strip()


def _template(p: dict) -> str:
    """The template path, or "" when the map records prose instead of a path."""
    t = str(p.get("template") or "").strip().replace("\\", "/")
    return t if t.lower().endswith(".html") else ""


# --------------------------------------------------------------------------- #
#  Template name -> file on disk
#
#  The map does NOT record one shape. acme-audit writes a repo path for some
#  apps (`fault_reporting/templates/fault_reporting/hub.html`) and the Django
#  template NAME for most (`audits/audit_list.html`, 148 of 232). A changed file
#  arrives from git as a repo path, so matching on the recorded string alone
#  would silently miss two thirds of the app - and "no pages affected" is the
#  answer nobody questions. One index over the repo's template roots resolves
#  both shapes to the same file.
# --------------------------------------------------------------------------- #
_INDEX_CACHE = {}


def template_index(repo_root) -> dict:
    """`{"paths": {repo-relative path}, "by_tail": {template name: path}}` over
    `<repo>/templates/` and `<repo>/*/templates/`."""
    repo = Path(repo_root).resolve()
    key = str(repo)
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    paths, by_tail = set(), {}
    roots = [repo / "templates"] + [p / "templates" for p in repo.glob("*")
                                    if p.is_dir() and (p / "templates").is_dir()]
    for root in roots:
        if not root.is_dir():
            continue
        for fp in root.rglob("*.html"):
            rel = fp.relative_to(repo).as_posix()
            paths.add(rel)
            tail = fp.relative_to(root).as_posix()
            by_tail.setdefault(tail, rel)
    _INDEX_CACHE[key] = {"paths": paths, "by_tail": by_tail}
    return _INDEX_CACHE[key]


def resolve_template(repo_root, value: str) -> str:
    """The repo-relative FILE for a recorded template value (path or name), or
    "" when nothing on disk matches."""
    v = (value or "").replace("\\", "/").lstrip("./")
    if not v:
        return ""
    idx = template_index(repo_root)
    if v in idx["paths"]:
        return v
    if v in idx["by_tail"]:
        return idx["by_tail"][v]
    tail = template_tail(v)
    return idx["by_tail"].get(tail, "")


def _sample(cfg: dict, page_id: str, name: str) -> str:
    by_page = (cfg.get("sample_ids_by_page") or {}).get(page_id) or {}
    if name in by_page:
        return str(by_page[name])
    glob = cfg.get("sample_ids") or {}
    if name in glob:
        return str(glob[name])
    return ""


def resolve_url(url: str, cfg: dict, page_id: str):
    """(resolved_url, [unresolved parameter names]) — substitution is per page
    first, then the global map; anything left over makes the page unfetchable."""
    unresolved = []

    def sub(m):
        name = _param_name(m.group(1))
        val = _sample(cfg, page_id, name)
        if val == "":
            unresolved.append(name)
            return m.group(0)
        return val

    return _PARAM.sub(sub, url or ""), unresolved


def _is_route(url: str) -> bool:
    """A fetchable route, as opposed to the prose a map writes for a template
    that has no URL of its own. Absolute http(s) is allowed — some maps record
    a full address — everything else must be a rooted path."""
    u = (url or "").strip()
    if u.lower().startswith(("http://", "https://")):
        return True
    return u.startswith("/")


def _entry(cfg: dict, app: str, p: dict, repo_root=None) -> dict:
    page_id = str(p.get("page_id") or p.get("id") or "").strip()
    if not page_id:
        page_id = "%s:%s" % (app or "app", str(p.get("url_name") or p.get("url") or "?"))
    raw_url = str(p.get("url") or "").strip()
    url, unresolved = resolve_url(raw_url, cfg, page_id)
    template = _template(p)

    reasons = []
    if not template:
        reasons.append("no_template")
    if unresolved:
        reasons.append("unresolved_params:" + ",".join(sorted(set(unresolved))))
    if not raw_url:
        reasons.append("no_url")
    elif not _is_route(raw_url):
        # The map records PROSE where a shared template has no route of its own
        # ("(template, nije ruta)"). It is non-empty and carries no <param>, so
        # it used to pass as fetchable, and the run then spent a page load
        # navigating to it and reported `Cannot navigate to invalid URL` — on a
        # BASE template, i.e. on every ticket that touches the site layout.
        # Same treatment as a `template` that is prose: kept, marked, never
        # quietly dropped.
        reasons.append("not_a_route")

    return {
        "page_id": page_id,
        "url": url,
        "url_raw": raw_url,
        "title": _title(p),
        "template": template,
        "template_path": resolve_template(repo_root, template) if repo_root else template,
        "app": app or str(p.get("app") or ""),
        "kind": str(p.get("kind") or ""),
        "url_name": str(p.get("url_name") or ""),
        # How a person REACHES this screen, in the mapper's own words. Carried
        # raw; `nav_steps()` is the only thing that turns it into text a customer
        # reads, and it refuses far more often than it accepts.
        "reached_from": [str(r).strip() for r in (p.get("reached_from_sr") or [])
                         if str(r or "").strip()],
        "skipped_reason": ",".join(reasons),
    }


def inventory_report(config: dict, repo_root) -> dict:
    """`{"pages": [...], "files_read": [...], "files_skipped": [{file, reason}]}`.

    `inventory()` is the list-only view of this; the report is what a manifest
    records, so "the map file was not a page file" is visible after the fact.
    """
    repo = Path(repo_root)
    src = config.get("pages") or {}
    kind = src.get("kind")
    out, read, skipped_files = [], [], []

    if kind == "list":
        for p in src.get("pages") or []:
            if isinstance(p, dict):
                out.append(_entry(config, str(p.get("app") or ""), p, repo))
        return {"pages": out, "files_read": ["<config.pages.pages>"],
                "files_skipped": skipped_files}

    root = repo / str(src.get("path") or "")
    for fp in sorted(root.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            skipped_files.append({"file": fp.name, "reason": "unreadable: %s"
                                  % type(exc).__name__})
            continue
        if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
            # e.g. processes.json - a map file that is not a page inventory.
            skipped_files.append({"file": fp.name, "reason": "no_pages_key"})
            continue
        read.append(fp.name)
        app = str(data.get("app") or fp.stem)
        for p in data["pages"]:
            if isinstance(p, dict):
                out.append(_entry(config, app, p, repo))

    out.sort(key=lambda e: e["page_id"])
    return {"pages": out, "files_read": read, "files_skipped": skipped_files}


def inventory(config: dict, repo_root) -> list:
    """Every page in the app: `[{page_id, url, title, template, app, kind,
    url_raw, url_name, skipped_reason}]`. Pages that cannot be captured are IN
    the list with a non-empty `skipped_reason` — filter with `usable()`."""
    return inventory_report(config, repo_root)["pages"]


def usable(pages) -> list:
    """The capturable subset — those with no `skipped_reason`."""
    return [p for p in pages if not p.get("skipped_reason")]


def by_template(pages) -> dict:
    """`{key: [page, ...]}` for the diff mapping, where a page is filed under
    EVERY name it can be recognised by: its resolved repo path, the string the
    map recorded, and the template name (the tail after `templates/`). A changed
    file is looked up by its path and by its tail, so the two shapes the map
    mixes both hit."""
    idx = {}
    for p in pages:
        keys = set()
        for value in (p.get("template_path"), p.get("template")):
            v = (value or "").replace("\\", "/").lstrip("./")
            if not v:
                continue
            keys.add(v)
            tail = template_tail(v)
            if tail:
                keys.add(tail)
        for k in keys:
            idx.setdefault(k, []).append(p)
    return idx


def template_tail(path: str) -> str:
    """The Django template NAME — what `{% include %}` writes — from a repo path.
    `fault_reporting/templates/fault_reporting/hub.html` -> `fault_reporting/hub.html`.
    Returns "" when the path is not under a `templates/` directory."""
    p = (path or "").replace("\\", "/")
    marker = "/templates/"
    if p.startswith("templates/"):
        return p[len("templates/"):]
    i = p.find(marker)
    return p[i + len(marker):] if i >= 0 else ""


# --------------------------------------------------------------------------- #
#  How a screen is REACHED — the click path for a page that is NEW
#
#  A page that did not exist before the change has no "before" picture: the
#  customer gets one screenshot and a sentence, and the sentence has to say where
#  the screen lives. The ONLY source for that is the app's own map
#  (`reached_from_sr`, written by whoever mapped the app) — never the URL, never
#  the template path, never a guess off the menu.
#
#  Nothing below invents anything. Every rule either lifts text the map wrote or
#  refuses the WHOLE path: `[]` — which the customer's comment prints as an empty
#  "Do nje:" line for the operator to fill in — is the designed outcome for any
#  line that does not read as a click path. A wrong click-path sent to a customer
#  is worse than a missing one (operator's decision, 2026-08-23).
# --------------------------------------------------------------------------- #
#: The map writes both arrows. Normalised to one before anything is split.
_NAV_ARROW = "→"
#: A path is at least two steps. One segment is prose the mapper wrote instead of
#: a path ("Link iz emaila za reset lozinke", "Programski poziv (cron)"), and
#: printing it as a click path would send the customer looking for a menu item
#: that does not exist. 358 `reached_from_sr` lines in acme-audit; the ones
#: that are genuinely paths all carry an arrow.
_NAV_MIN_STEPS = 2
_NAV_MAX_STEPS = 8
_NAV_STEP_MAX = 60          # longer than this is a sentence, not a menu item
#: A step carrying any of these is the mapper talking to a developer. One of them
#: anywhere blanks the WHOLE path rather than dropping the step: a chain with a
#: link removed still reads as a chain, and it is then a wrong one.
#:
#: Two lists, because a substring test on a bare English word is wrong in the
#: other direction: `view` inside `Faults → Overview` refused a menu path that
#: is perfectly real. Anything that can be a fragment of a Serbian menu label is
#: matched as a WORD; only the shapes no label ever contains (an extension, a
#: dotted permission, a query string) are matched as substrings.
_NAV_TECHNICAL_SUB = ("templejt", "can.", ".html", ".py", "http", "?", "_sr", "::")
_NAV_TECHNICAL_WORD = re.compile(
    r"\b(url|urls|view|views|template|templates|mixin|cron|redirect|"
    r"management command|vidljivo|programski)\b", re.IGNORECASE)
_NAV_PAREN = re.compile(r"\(([^()]*)\)")
#: `'Grupni unos'` -> `„Grupni unos“` — the quotes the rest of the customer's
#: comment uses. The map is written in developer ASCII quotes.
_NAV_QUOTED = re.compile(r"['\"]([^'\"]{1,40})['\"]")


def _nav_split(text: str) -> list:
    """Split on the arrow OUTSIDE parentheses. A parenthetical carries its own
    arrows (`Stablo lokacija (Podešavanja → Lokacije)`) and must not be torn in
    half by this pass."""
    out, depth, buf = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == _NAV_ARROW and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _nav_segment(seg: str) -> str:
    """One arrow-separated segment as the customer reads it, or "".

    `Lista gradova (Podešavanja → Gradovi, deljeni templejt …)` is a screen NAME
    qualified by the menu path to it — and the menu path is the part a customer
    can follow, so a parenthetical that carries an arrow REPLACES the label it
    qualifies (cut at its first comma, which is where the mapper's technical tail
    starts). Every other parenthetical is a note to a developer (`ikona
    table_view, vidljivo uz can.settings_edit`) and is dropped.
    """
    for m in _NAV_PAREN.finditer(seg):
        head = m.group(1).split(",")[0].strip()
        if _NAV_ARROW in head:
            return re.sub(r"\s+", " ", head).strip(" .;:")
    return re.sub(r"\s+", " ", _NAV_PAREN.sub(" ", seg)).strip(" .;:")


def nav_steps(page) -> list:
    """The click path to `page` as a list of steps, or `[]` when the map does not
    honestly give one. The FIRST `reached_from` line decides — several lines are
    several ways in, and choosing between them would be the guess this refuses to
    make."""
    raw = [r for r in (page or {}).get("reached_from") or [] if str(r or "").strip()]
    if not raw:
        return []
    steps = []
    for seg in _nav_split(str(raw[0]).replace("->", _NAV_ARROW)):
        # the replacement text may itself be a path, so it is split again
        steps.extend(s for s in _nav_split(_nav_segment(seg)) if s)
    if not _NAV_MIN_STEPS <= len(steps) <= _NAV_MAX_STEPS:
        return []
    for s in steps:
        low = s.lower()
        if (len(s) > _NAV_STEP_MAX or _NAV_TECHNICAL_WORD.search(s)
                or any(t in low for t in _NAV_TECHNICAL_SUB)):
            return []
    return [_NAV_QUOTED.sub("„\\1“", s) for s in steps]


def main() -> int:
    import argparse
    from . import config as vconfig
    ap = argparse.ArgumentParser(description="Print an app's page inventory.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--all", action="store_true", help="include skipped pages")
    a = ap.parse_args()
    cfg = vconfig.load_config(a.repo)
    if vconfig.is_error(cfg):
        print(json.dumps(cfg, ensure_ascii=True))
        return 2
    rep = inventory_report(cfg, a.repo)
    rows = rep["pages"] if a.all else usable(rep["pages"])
    print(json.dumps({"count": len(rows), "total": len(rep["pages"]),
                      "files_skipped": rep["files_skipped"], "pages": rows},
                     ensure_ascii=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
