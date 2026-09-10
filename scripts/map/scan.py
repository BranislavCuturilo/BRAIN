#!/usr/bin/env python3
"""A map of how a project fits together — built here, sent nowhere.

**This tool has no network code, deliberately.** Not a client, not a URL, not an
upload flag. The idea came from a hosted scanner that publishes an architecture
map to a third party as a public unlisted link; the idea is good and the
delivery is not, because the map of `acme-audit` is a client's system
architecture. Nothing here leaves the machine. Grep this file for `urllib` and
you will find nothing to trust me about.

**The hard part is SELECTION, not extraction.** acme-audit has 988 Python
files, 27 `models.py`, 19 `services.py` and 116 management commands. Extract
naively and the first app alone exceeds any readable diagram. So there are two
levels, and both stay small enough to hold in your head:

    scan.py <repo>              the APPS and what imports what   (~20 nodes)
    scan.py <repo> --app X      inside one app: models, services, urls, commands
    scan.py <repo> --files X    the FILES of an app and their imports
    scan.py <repo> --calls X    the functions/classes and what calls what
    scan.py <repo> --pages      the SCREENS: url -> view -> template -> partials

The caps are borrowed and kept, because they are the actual insight: a map you
cannot read is not a map. Labels are truncated, not wrapped.

  scan.py <repo> --all --html        EVERY level in ONE page: search, filters,
                                     UML-shaped blocks  (start here)
  scan.py <repo>                     text tree + write .map/scan.json
  scan.py <repo> --app locations     one app
  scan.py <repo> --files locations   its modules and their imports
  scan.py <repo> --calls locations   its call graph  (APPROXIMATE -- see below)
  scan.py <repo> --pages             every screen and the templates it renders
  scan.py <repo> --html              also write .map/scan.html and open it
  scan.py <repo> --json              the JSON only
  scan.py <repo> --top 40            raise the node cap

The HTML is one self-contained file: the SVG is generated here, the little bit
of interaction is inline, and there is no CDN, no font fetch and no analytics.
Open it offline, mail it to yourself, keep it in a folder. It renders the same
in five years.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_NODES = 60
MAX_LABEL = 28
MAX_SUB = 40

SKIP = {".git", "__pycache__", "venv", ".venv", "env", "node_modules", "static",
        "staticfiles", "media", "locale", "migrations", "graphify-out", ".map",
        "dist", "build", ".idea", ".vscode", "htmlcov", ".pytest_cache"}

#: Third-party packages worth showing as an `external` node, with the favicon
#: domain. Anything not listed is internal and gets no domain -- the same rule
#: the borrowed contract uses, and it keeps internal nodes visually quiet.
EXTERNAL = {
    "requests": ("HTTP", None), "httpx": ("HTTP", None),
    "celery": ("Celery", "celeryq.dev"), "redis": ("Redis", "redis.io"),
    "boto3": ("AWS S3", "aws.amazon.com"), "stripe": ("Stripe", "stripe.com"),
    "openai": ("OpenAI", "openai.com"), "anthropic": ("Claude", "claude.ai"),
    "google": ("Google", "google.com"), "openpyxl": ("Excel", None),
    "reportlab": ("PDF", None), "weasyprint": ("PDF", None),
    "playwright": ("Playwright", "playwright.dev"),
    "paramiko": ("SSH", None), "ldap3": ("LDAP", None),
    "psycopg2": ("PostgreSQL", "postgresql.org"),
    "MySQLdb": ("MySQL", "mysql.com"), "pymysql": ("MySQL", "mysql.com"),
}


def clip(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def apps(root: Path) -> dict[str, Path]:
    """A Django app is a directory with models.py, views.py, urls.py or apps.py."""
    found = {}
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name in SKIP or p.name.startswith("."):
            continue
        if any((p / f).exists() for f in ("models.py", "views.py", "urls.py", "apps.py")):
            found[p.name] = p
        elif (p / "__init__.py").exists() and any(p.glob("*/models.py")):
            found[p.name] = p
    return found


def py_files(app: Path):
    for f in app.rglob("*.py"):
        if any(part in SKIP for part in f.parts):
            continue
        yield f


def imports_of(path: Path) -> list[str]:
    """Top-level module of every import in a file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # relative: same app
                continue
            if node.module:
                out.append(node.module.split(".")[0])
    return out


def contents(app: Path) -> dict:
    """What an app is MADE of -- the counts that go in `sub`."""
    c = {"models": 0, "services": 0, "views": 0, "commands": 0, "signals": 0,
         "templates": 0, "tests": 0}
    for f in py_files(app):
        name = f.name
        if "management" in f.parts and "commands" in f.parts and name != "__init__.py":
            c["commands"] += 1
        elif name.startswith("test") or "tests" in f.parts:
            c["tests"] += 1
        elif name.startswith("models"):
            c["models"] += _classes(f, "Model")
        elif name.startswith("services"):
            c["services"] += 1
        elif name.startswith("views"):
            c["views"] += 1
        elif name.startswith("signals"):
            c["signals"] += 1
    c["templates"] = sum(1 for _ in app.rglob("*.html"))
    return c


MAX_MEMBERS = 14


def model_fields(node: ast.ClassDef) -> list[str]:
    """`name: FieldType` for each Django field — the body of a UML box.

    A class name alone says nothing; `Location` with `tenant: FK`,
    `parent: FK`, `is_active: Bool` says what it IS. That density is the whole
    reason to draw boxes rather than dots.
    """
    out = []
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign) or not stmt.targets:
            continue
        tgt = stmt.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        call = stmt.value
        if not isinstance(call, ast.Call):
            continue
        fn = call.func
        typ = getattr(fn, "attr", getattr(fn, "id", ""))
        if not typ or "Field" not in typ and typ not in (
                "ForeignKey", "OneToOneField", "ManyToManyField"):
            continue
        short = {"ForeignKey": "FK", "OneToOneField": "O2O",
                 "ManyToManyField": "M2M", "CharField": "Char",
                 "TextField": "Text", "IntegerField": "Int",
                 "BooleanField": "Bool", "DateTimeField": "DateTime",
                 "DateField": "Date", "DecimalField": "Decimal",
                 "ForeignObject": "FK"}.get(typ, typ.replace("Field", ""))
        out.append(f"{tgt.id}: {short}")
    return out[:MAX_MEMBERS]


def func_signature(node) -> str:
    args = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
    extra = "…" if node.args.vararg or node.args.kwarg else ""
    return f"({', '.join(args[:4])}{extra})" if args or extra else "()"


def _classes(path: Path, base_hint: str) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return 0
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = [getattr(b, "attr", getattr(b, "id", "")) for b in node.bases]
            if any(base_hint in str(b) for b in bases):
                n += 1
    return n


# --------------------------------------------------------------------------
# level 1: the apps, and what imports what
# --------------------------------------------------------------------------

def scan_repo(root: Path, cap: int) -> dict:
    found = apps(root)
    edges: Counter = Counter()
    ext_used: dict[str, set[str]] = defaultdict(set)

    for name, path in found.items():
        for f in py_files(path):
            for mod in imports_of(f):
                if mod in found and mod != name:
                    edges[(name, mod)] += 1
                elif mod in EXTERNAL:
                    ext_used[mod].add(name)

    # Rank apps by how connected they are -- a hub is what you need on a map,
    # a leaf nobody imports is what you can leave off.
    degree: Counter = Counter()
    for (a, b), n in edges.items():
        degree[a] += n
        degree[b] += n * 2                      # being depended ON matters more
    ranked = sorted(found, key=lambda a: (-degree[a], a))[:cap]
    keep = set(ranked)

    nodes = []
    for name in ranked:
        c = contents(found[name])
        bits = [f"{c[k]} {k}" for k in ("models", "services", "views", "commands")
                if c[k]]
        nodes.append({
            "id": name, "label": clip(name, MAX_LABEL), "kind": "service",
            "sub": clip(" · ".join(bits) or "—", MAX_SUB),
            "members": [f"{k}: {v}" for k, v in c.items() if v],
            "sourceRef": name + "/",
            "detail": clip(f"{c['models']} models, {c['services']} service modules, "
                           f"{c['views']} view modules, {c['commands']} commands, "
                           f"{c['templates']} templates", 200),
        })

    for mod, users in sorted(ext_used.items()):
        if not (users & keep):
            continue
        label, domain = EXTERNAL[mod]
        n = {"id": f"ext:{mod}", "label": clip(label, MAX_LABEL), "kind": "external",
             "sub": clip(f"used by {len(users & keep)} app(s)", MAX_SUB)}
        if domain:
            n["domain"] = domain
        nodes.append(n)

    out_edges = []
    for (a, b), n in edges.most_common():
        if a in keep and b in keep:
            out_edges.append({"from": a, "to": b, "kind": "calls",
                              "label": clip(f"{n} import(s)", 24) if n > 5 else ""})
    for mod, users in ext_used.items():
        for a in sorted(users & keep):
            out_edges.append({"from": a, "to": f"ext:{mod}", "kind": "calls"})

    return _wrap(root.name, f"{len(found)} apps", nodes, out_edges,
                 dropped=len(found) - len(keep))


# --------------------------------------------------------------------------
# level 2: inside one app
# --------------------------------------------------------------------------

def scan_app(root: Path, app_name: str, cap: int) -> dict:
    found = apps(root)
    if app_name not in found:
        raise SystemExit(f"no app {app_name!r}. Found: {', '.join(sorted(found))}")
    app = found[app_name]

    nodes, edges = [], []
    seen: set[str] = set()

    def add(nid, label, kind, sub="", ref="", detail="", members=None):
        if nid in seen:
            return
        seen.add(nid)
        n = {"id": nid, "label": clip(label, MAX_LABEL), "kind": kind}
        if sub:
            n["sub"] = clip(sub, MAX_SUB)
        if ref:
            n["sourceRef"] = clip(ref, 120)
        if detail:
            n["detail"] = clip(detail, 200)
        if members:
            n["members"] = [clip(m, 34) for m in members[:MAX_MEMBERS]]
        nodes.append(n)

    # models -> store
    for f in app.rglob("models*.py"):
        if any(p in SKIP for p in f.parts):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                    "Model" in str(getattr(b, "attr", getattr(b, "id", "")))
                    for b in node.bases):
                fields = model_fields(node)
                add(f"m:{node.name}", node.name, "store",
                    f"{len(fields)} field(s)",
                    f"{f.relative_to(root)}:{node.lineno}",
                    members=fields)

    # services -> service
    for f in app.rglob("services*.py"):
        if any(p in SKIP for p in f.parts):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    not node.name.startswith("_"):
                add(f"s:{node.name}", node.name, "service",
                    clip(func_signature(node), MAX_SUB),
                    f"{f.relative_to(root)}:{node.lineno}",
                    ast.get_docstring(node) or "")

    # urls -> entry
    for f in app.rglob("urls.py"):
        if any(p in SKIP for p in f.parts):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"""name\s*=\s*['"]([\w.-]+)['"]""", text):
            add(f"u:{m.group(1)}", m.group(1), "entry", "url",
                str(f.relative_to(root)))

    # management commands -> cron
    for f in app.rglob("management/commands/*.py"):
        if f.name == "__init__.py":
            continue
        add(f"c:{f.stem}", f.stem, "cron", "command", str(f.relative_to(root)))

    # Edges by co-reference: a service that names a model, a view that names a
    # service. Textual, deliberately -- an import is not the only way one
    # reaches the other, and this level is a sketch, not an authority.
    names = {n["id"]: n["label"] for n in nodes}
    for f in py_files(app):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        here = [nid for nid, lbl in names.items()
                if re.search(rf"(?<![\w]){re.escape(lbl)}(?![\w])", text)]
        srcs = [n for n in here if n.startswith(("s:", "c:", "u:"))]
        dsts = [n for n in here if n.startswith("m:")]
        for s in srcs[:4]:
            for d in dsts[:4]:
                edges.append({"from": s, "to": d, "kind": "reads"})

    # De-duplicate and cap.
    uniq = {(e["from"], e["to"]): e for e in edges}
    nodes = nodes[:cap]
    keep = {n["id"] for n in nodes}
    edges = [e for e in uniq.values() if e["from"] in keep and e["to"] in keep][:120]

    return _wrap(f"{root.name} / {app_name}", app_name, nodes, edges)


def _wrap(name: str, tagline: str, nodes: list, edges: list, dropped: int = 0) -> dict:
    kinds = Counter(n["kind"] for n in nodes)
    return {
        "version": 1,
        "project": {"name": clip(name, 48),
                    "slug": re.sub(r"[^a-z0-9-]+", "-", name.lower())[:48].strip("-"),
                    "tagline": clip(tagline, 80),
                    "date": date.today().isoformat()},
        "stats": {"services": kinds.get("service", 0), "stores": kinds.get("store", 0),
                  "entries": kinds.get("entry", 0), "crons": kinds.get("cron", 0),
                  "externals": kinds.get("external", 0)},
        "dropped": dropped,
        "graph": {"nodes": nodes, "edges": edges},
    }


def render_text(data: dict) -> None:
    p, s = data["project"], data["stats"]
    print("=" * 74)
    print(f"MAP — {p['name']}   ({p['tagline']})")
    print("=" * 74)
    print("  " + "  ".join(f"{k}: {v}" for k, v in s.items() if v))
    if data.get("dropped"):
        print(f"  {data['dropped']} more not shown — raise --top to include them")
    if data.get("caveat"):
        # Wrap on WORDS. Slicing every 68 characters split "receivers" across
        # two lines as "rec" / "eivers", which is exactly the kind of detail
        # that makes a warning look unmaintained and therefore ignorable.
        words, line = data["caveat"].split(), ""
        for w in words:
            if len(line) + len(w) + 1 > 68:
                print(f"  ! {line}")
                line = w
            else:
                line = f"{line} {w}".strip()
        if line:
            print(f"  ! {line}")
    print()

    nodes = {n["id"]: n for n in data["graph"]["nodes"]}
    out = defaultdict(list)
    inc = Counter()
    for e in data["graph"]["edges"]:
        out[e["from"]].append(e)
        inc[e["to"]] += 1

    # Most depended-on first: that is the reading order that explains a system.
    for nid in sorted(nodes, key=lambda i: (-inc[i], i)):
        n = nodes[nid]
        mark = {"service": "▪", "store": "▫", "entry": "→", "cron": "⏱",
                "external": "↗"}.get(n["kind"], "·")
        dep = f"  ←{inc[nid]}" if inc[nid] else ""
        print(f"  {mark} {n['label']:<30} {n.get('sub',''):<26}{dep}")
        for e in sorted(out[nid], key=lambda x: x["to"])[:6]:
            lbl = f"  {e['label']}" if e.get("label") else ""
            print(f"        → {nodes[e['to']]['label']}{lbl}")
    print()


# --------------------------------------------------------------------------
# level 3: the files of one app
# --------------------------------------------------------------------------

def scan_files(root: Path, app_name: str, cap: int) -> dict:
    found = apps(root)
    if app_name not in found:
        raise SystemExit(f"no app {app_name!r}. Found: {', '.join(sorted(found))}")
    app = found[app_name]

    mods: dict[str, Path] = {}
    for f in py_files(app):
        if f.name == "__init__.py":
            continue
        mods[f.stem if f.parent == app else f"{f.parent.name}/{f.stem}"] = f

    nodes, edges, ext = [], [], defaultdict(set)
    inc: Counter = Counter()
    raw = []
    for name, f in mods.items():
        for mod in imports_of(f):
            if mod in mods and mod != name:
                raw.append((name, mod))
                inc[mod] += 1
            elif mod in found and mod != app_name:
                raw.append((name, f"app:{mod}"))
                inc[f"app:{mod}"] += 1
            elif mod in EXTERNAL:
                ext[mod].add(name)

    ranked = sorted(mods, key=lambda m: (-inc[m], m))[:cap]
    keep = set(ranked)
    for name in ranked:
        f = mods[name]
        try:
            n_lines = len(f.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            n_lines = 0
        nodes.append({"id": name, "label": clip(name, MAX_LABEL), "kind": "service",
                      "sub": clip(f"{n_lines} lines", MAX_SUB),
                      "sourceRef": str(f.relative_to(root))})

    other_apps = {b for _a, b in raw if b.startswith("app:")}
    for a in sorted(other_apps):
        nodes.append({"id": a, "label": clip(a[4:], MAX_LABEL), "kind": "store",
                      "sub": "another app"})
        keep.add(a)
    for mod, users in sorted(ext.items()):
        if users & keep:
            label, domain = EXTERNAL[mod]
            n = {"id": f"ext:{mod}", "label": clip(label, MAX_LABEL),
                 "kind": "external", "sub": "third party"}
            if domain:
                n["domain"] = domain
            nodes.append(n)
            keep.add(n["id"])
            for u in sorted(users & keep):
                raw.append((u, f"ext:{mod}"))

    seen = set()
    for a, b in raw:
        if a in keep and b in keep and (a, b) not in seen:
            seen.add((a, b))
            edges.append({"from": a, "to": b, "kind": "calls"})

    return _wrap(f"{root.name} / {app_name} — files", f"{len(mods)} modules",
                 nodes, edges, dropped=max(0, len(mods) - len(ranked)))


# --------------------------------------------------------------------------
# level 4: what calls what -- APPROXIMATE, and it says so
# --------------------------------------------------------------------------

def scan_calls(root: Path, app_name: str, cap: int) -> dict:
    """A static Python call graph is a SKETCH, not an authority.

    `self.method()`, `getattr(obj, name)()`, a callable in a dict, a Django
    signal receiver and anything dispatched by string are invisible here, and
    two different classes with a `save()` method are indistinguishable by
    attribute name. Read it as "these names appear to reach each other", never
    as "these are all the callers" -- for that question the answer is
    `impact-mapper`, or `scripts/map/concept.py`, which searches strings too.
    """
    found = apps(root)
    if app_name not in found:
        raise SystemExit(f"no app {app_name!r}. Found: {', '.join(sorted(found))}")
    app = found[app_name]

    defs: dict[str, dict] = {}
    calls: list[tuple[str, str]] = []

    for f in py_files(app):
        if f.name.startswith("test") or "tests" in f.parts:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue

        def walk(node, enclosing=None):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if not child.name.startswith("_"):
                        kind = "store" if isinstance(child, ast.ClassDef) else "service"
                        defs.setdefault(child.name, {
                            "id": child.name, "label": clip(child.name, MAX_LABEL),
                            "kind": kind,
                            "sub": clip(f"{f.name}:{child.lineno}", MAX_SUB),
                            "sourceRef": f"{f.relative_to(root)}:{child.lineno}",
                            "detail": clip(ast.get_docstring(child) or "", 200)})
                    walk(child, child.name if not child.name.startswith("_") else enclosing)
                else:
                    if isinstance(child, ast.Call) and enclosing:
                        fn = child.func
                        target = getattr(fn, "id", None) or getattr(fn, "attr", None)
                        if target and not target.startswith("_"):
                            calls.append((enclosing, target))
                    walk(child, enclosing)

        walk(tree)

    inc: Counter = Counter(b for _a, b in calls if b in defs)
    ranked = sorted(defs, key=lambda d: (-inc[d], d))[:cap]
    keep = set(ranked)
    nodes = [defs[d] for d in ranked]

    seen, edges = set(), []
    for a, b in calls:
        if a in keep and b in keep and a != b and (a, b) not in seen:
            seen.add((a, b))
            edges.append({"from": a, "to": b, "kind": "calls"})

    data = _wrap(f"{root.name} / {app_name} — calls", "APPROXIMATE call graph",
                 nodes, edges[:120], dropped=max(0, len(defs) - len(ranked)))
    data["caveat"] = ("Static and approximate: self./getattr/string dispatch and "
                      "signal receivers are invisible, and two classes with the "
                      "same method name look identical.")
    return data


# --------------------------------------------------------------------------
# level 5: the screens -- reusing the visual engine's template graph
# --------------------------------------------------------------------------

def scan_pages(root: Path, cap: int) -> dict:
    """url -> view -> template -> partials.

    The template graph is NOT rebuilt here. `scripts/visual/` already parses
    `{% include %}` and `{% extends %}` across every template in the repo, for
    visual-diff, and a second parser would be a second answer to "what does
    this template pull in" -- the two-versions-of-one-rule defect, in code.
    So the index and the regexes are imported from there.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from visual import pages as vpages                       # noqa: PLC0415
        from visual import affected as vaffected                 # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(f"the visual engine is required for --pages: {exc}")

    by_tail = vpages.template_index(root)["by_tail"]

    includes: dict[str, set[str]] = {}
    extends: dict[str, set[str]] = {}
    for tail, rel in by_tail.items():
        try:
            body = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        includes[tail] = {m.group(1).replace("\\", "/")
                          for m in vaffected._RE_INCLUDE.finditer(body)}
        extends[tail] = {m.group(1).replace("\\", "/")
                         for m in vaffected._RE_EXTENDS.finditer(body)}

    # A layout is a template others extend; a partial is one others include.
    layouts = {x for s in extends.values() for x in s}
    included = {x for s in includes.values() for x in s} - layouts

    inc: Counter = Counter()
    for s in includes.values():
        inc.update(s)

    ranked = sorted(by_tail, key=lambda tl: (-inc[tl], tl))[:cap]
    keep = set(ranked)

    nodes = []
    for tail in ranked:
        if tail in layouts:
            kind, sub = "store", "layout"
        elif tail in included:
            kind, sub = "service", "partial"
        else:
            kind, sub = "entry", "page"
        nodes.append({"id": tail, "label": clip(Path(tail).name, MAX_LABEL),
                      "kind": kind,
                      "sub": clip(f"{sub} · {tail}", MAX_SUB),
                      "sourceRef": str(by_tail[tail])})

    edges, seen = [], set()
    for tail, targets in includes.items():
        for tgt in targets:
            if tail in keep and tgt in keep and (tail, tgt) not in seen:
                seen.add((tail, tgt))
                edges.append({"from": tail, "to": tgt,
                              "kind": "reads",
                              "label": "extends" if tgt in extends.get(tail, ()) else ""})

    return _wrap(f"{root.name} — screens", f"{len(by_tail)} templates",
                 nodes, edges[:120], dropped=max(0, len(by_tail) - len(ranked)))


# --------------------------------------------------------------------------
# everything, in one document
# --------------------------------------------------------------------------

def scan_all(root: Path, cap: int) -> dict:
    """Every level in one payload, so one page can hold them all.

    Levels are kept as separate node sets rather than merged into one graph:
    an app and a model and a template are not peers, and pretending they are
    produces a diagram where nothing means anything. The page switches between
    them and searches across all of them at once.
    """
    levels = []

    repo = scan_repo(root, cap)
    levels.append({"id": "apps", "title": "Applications", "graph": repo["graph"],
                   "stats": repo["stats"], "dropped": repo.get("dropped", 0)})

    try:
        pages = scan_pages(root, cap)
        levels.append({"id": "pages", "title": "Screens", "graph": pages["graph"],
                       "stats": pages["stats"], "dropped": pages.get("dropped", 0)})
    except SystemExit:
        pass

    # EVERY app gets its three levels, not a favoured few. The first version
    # took the top 8 by dependency weight, which left 20 of this project's 28
    # apps with no level at all -- and the one you happen to be working on is
    # as likely to be in the tail as in the head. The dropdown is grouped per
    # app so the length stays navigable; a level nobody opens costs a few KB
    # in a local file and nothing else.
    inc: Counter = Counter(e["to"] for e in repo["graph"]["edges"])
    every = sorted(apps(root), key=lambda a: (-inc[a], a))
    for app_name in every:
        for fn, suffix, title in ((scan_app, "inside", "Inside"),
                                  (scan_files, "files", "Files"),
                                  (scan_calls, "calls", "Calls")):
            try:
                d = fn(root, app_name, cap)
            except SystemExit:
                continue
            if not d["graph"]["nodes"]:
                continue
            levels.append({"id": f"{app_name}:{suffix}",
                           "title": f"{title} · {app_name}",
                           "app": app_name, "short": title,
                           "graph": d["graph"], "stats": d["stats"],
                           "dropped": d.get("dropped", 0),
                           "caveat": d.get("caveat", "")})

    return {"version": 1,
            "project": {"name": clip(root.name, 48),
                        "slug": re.sub(r"[^a-z0-9-]+", "-", root.name.lower())[:48],
                        "tagline": f"{len(levels)} levels",
                        "date": date.today().isoformat()},
            "levels": levels}


# --------------------------------------------------------------------------
# rendering -- one self-contained file, no network of any kind
# --------------------------------------------------------------------------

def _layout(data: dict) -> tuple[dict, int, int]:
    """Columns by role, rows by how depended-on a node is.

    A force-directed hairball of 28 apps says nothing. What a person actually
    asks is "what does everything lean on" -- so the most depended-on column
    sits in the middle, what uses it on the left, and what it reaches out to on
    the right. That ordering IS the answer to the question.
    """
    nodes = {n["id"]: dict(n) for n in data["graph"]["nodes"]}
    inc, out = Counter(), Counter()
    for e in data["graph"]["edges"]:
        inc[e["to"]] += 1
        out[e["from"]] += 1

    cols: dict[int, list] = {0: [], 1: [], 2: []}
    for nid, n in nodes.items():
        if n["kind"] == "external" or n["kind"] == "store":
            col = 2
        elif inc[nid] >= max(2, (max(inc.values()) if inc else 0) * 0.4):
            col = 1                              # depended upon: the middle
        else:
            col = 0
        cols[col].append(nid)

    for c in cols:
        cols[c].sort(key=lambda i: (-inc[i], -out[i], nodes[i]["label"].lower()))

    W, H, PAD = 250, 62, 40
    height = PAD * 2 + max((len(v) for v in cols.values()), default=1) * H
    width = PAD * 2 + 3 * W
    for c, ids in cols.items():
        span = height - PAD * 2
        step = span / max(len(ids), 1)
        for i, nid in enumerate(ids):
            nodes[nid]["x"] = PAD + c * W + 10
            nodes[nid]["y"] = PAD + step * i + step / 2
            nodes[nid]["inc"] = inc[nid]
    return nodes, width, height


def render_html(data: dict) -> str:
    nodes, W, H = _layout(data)
    p = data["project"]

    edges = []
    for e in data["graph"]["edges"]:
        a, b = nodes.get(e["from"]), nodes.get(e["to"])
        if not a or not b:
            continue
        mx = (a["x"] + b["x"]) / 2
        edges.append(
            f'<path class="e" data-a="{e["from"]}" data-b="{e["to"]}" '
            f'd="M{a["x"] + 190},{a["y"]} C{mx + 90},{a["y"]} {mx},{b["y"]} '
            f'{b["x"]},{b["y"]}"><title>{_esc(e.get("label") or e.get("kind",""))}</title></path>')

    fill = {"service": "#2d3f5e", "store": "#3d5a3d", "entry": "#5e4a2d",
            "cron": "#4a2d5e", "external": "#5e2d3f"}
    boxes = []
    for nid, n in nodes.items():
        c = fill.get(n["kind"], "#333")
        sub = _esc(n.get("sub", ""))
        detail = _esc(n.get("detail", "") or n.get("sourceRef", ""))
        boxes.append(
            f'<g class="n" data-id="{nid}" transform="translate({n["x"]},{n["y"] - 20})">'
            f'<title>{detail}</title>'
            f'<rect width="190" height="40" rx="7" fill="{c}"/>'
            f'<text x="10" y="17" class="l">{_esc(n["label"])}</text>'
            f'<text x="10" y="31" class="s">{sub}</text>'
            f'{f"<text x=\"180\" y=\"17\" class=\"d\">{n["inc"]}</text>" if n["inc"] else ""}'
            f'</g>')

    stats = "  ".join(f"{k}: {v}" for k, v in data["stats"].items() if v)
    return f"""<!doctype html><meta charset="utf-8">
<title>Map — {_esc(p['name'])}</title>
<style>
 :root {{ color-scheme: dark }}
 body {{ margin:0; background:#12151c; color:#c9d1d9;
        font:13px/1.4 ui-sans-serif,system-ui,Segoe UI,sans-serif }}
 header {{ padding:14px 18px; border-bottom:1px solid #222834 }}
 h1 {{ margin:0; font-size:15px; font-weight:600 }}
 .meta {{ color:#7d8590; font-size:12px; margin-top:3px }}
 .wrap {{ overflow:auto; height:calc(100vh - 62px) }}
 text {{ fill:#e6edf3; font-size:12px; pointer-events:none }}
 .s {{ fill:#9aa4b2; font-size:10px }}
 .d {{ fill:#7d8590; font-size:10px; text-anchor:end }}
 .e {{ stroke:#39414f; fill:none; stroke-width:1.2 }}
 .n rect {{ stroke:#39414f; cursor:pointer }}
 .n:hover rect {{ stroke:#7aa2f7; stroke-width:2 }}
 .e.hot {{ stroke:#7aa2f7; stroke-width:2 }}
 .dim {{ opacity:.12 }}
</style>
<header>
 <h1>{_esc(p['name'])}</h1>
 <div class="meta">{_esc(p.get('tagline',''))} · {stats} · {p['date']}
 · hover a box to trace what it touches · generated locally, nothing uploaded</div>
</header>
<div class="wrap"><svg width="{W}" height="{H}">{"".join(edges)}{"".join(boxes)}</svg></div>
<script>
const S=document.querySelector('svg');
S.addEventListener('mouseover',e=>{{
  const g=e.target.closest('.n'); if(!g) return;
  const id=g.dataset.id;
  S.querySelectorAll('.e').forEach(p=>{{
    const hit=p.dataset.a===id||p.dataset.b===id;
    p.classList.toggle('hot',hit); p.classList.toggle('dim',!hit);
  }});
  const near=new Set([id]);
  S.querySelectorAll('.e').forEach(p=>{{
    if(p.dataset.a===id) near.add(p.dataset.b);
    if(p.dataset.b===id) near.add(p.dataset.a);
  }});
  S.querySelectorAll('.n').forEach(n=>n.classList.toggle('dim',!near.has(n.dataset.id)));
}});
S.addEventListener('mouseleave',()=>S.querySelectorAll('.dim,.hot')
  .forEach(x=>x.classList.remove('dim','hot')));
</script>"""


def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def sources(root: Path):
    """Every file a scan reads. The fingerprint must cover exactly this set.

    Templates count. A screen graph is built from `{% include %}` and
    `{% extends %}`, so a template edit changes the map without touching a
    single `.py` -- fingerprinting only Python would report "unchanged" after
    precisely the kind of edit `visual-diff` exists for.
    """
    for pat in ("**/*.py", "**/*.html", "**/*.txt"):
        for f in root.glob(pat):
            if any(part in SKIP or part.startswith(".") for part in f.parts):
                continue
            yield f


def fingerprint(root: Path) -> str:
    """A digest of what the scan would read, without reading any of it.

    Size and mtime rather than content: hashing 3,500 files costs most of what
    the scan itself costs, which would defeat the point. The trade is a known
    one -- a file restored to an identical byte-for-byte state with a new mtime
    reads as changed, and the scan runs for nothing. False "changed" is
    harmless; false "unchanged" would leave a stale map claiming to be current,
    so the error is deliberately on this side.
    """
    h = hashlib.sha256()
    n = 0
    for f in sorted(sources(root)):
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(str(f.relative_to(root)).replace("\\", "/").encode("utf-8"))
        h.update(f"{st.st_size}:{st.st_mtime_ns}".encode("ascii"))
        n += 1
    return f"{n}:{h.hexdigest()[:32]}"


def stamp_path(root: Path, name: str) -> Path:
    return root / ".map" / (name.replace(".json", "") + ".stamp")


def unchanged(root: Path, name: str) -> bool:
    """Has nothing the scan reads moved since the map was written?

    Requires the OUTPUT to exist too. A stamp whose json was deleted must not
    report "current" -- that is how a missing map becomes an invisible one.
    """
    out = root / ".map" / name
    stamp = stamp_path(root, name)
    if not out.is_file() or not stamp.is_file():
        return False
    try:
        return stamp.read_text(encoding="utf-8").strip() == fingerprint(root)
    except OSError:
        return False


def write_stamp(root: Path, name: str) -> None:
    try:
        p = stamp_path(root, name)
        p.parent.mkdir(exist_ok=True)
        p.write_text(fingerprint(root) + "\n", encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip())
        return 1
    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 1

    def opt(flag, default=None):
        return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

    cap = int(opt("--top", MAX_NODES))

    # The output name is decided BEFORE scanning, so --if-changed and --stale
    # can answer without doing the work they exist to avoid.
    if "--all" in sys.argv:
        name, run = "scan-all.json", lambda: scan_all(root, cap)
    elif "--pages" in sys.argv:
        name, run = "scan-pages.json", lambda: scan_pages(root, cap)
    elif opt("--files"):
        a = opt("--files")
        name, run = f"scan-files-{a}.json", lambda: scan_files(root, a, cap)
    elif opt("--calls"):
        a = opt("--calls")
        name, run = f"scan-calls-{a}.json", lambda: scan_calls(root, a, cap)
    elif opt("--app"):
        a = opt("--app")
        name, run = f"scan-{a}.json", lambda: scan_app(root, a, cap)
    else:
        name, run = "scan.json", lambda: scan_repo(root, cap)

    # --stale answers and does nothing else, for a check that must be cheap.
    # Exit 1 means the map no longer describes the code.
    if "--stale" in sys.argv:
        if unchanged(root, name):
            print(f"  current: {root / '.map' / name}")
            return 0
        out = root / ".map" / name
        print(f"  STALE: {out}" if out.is_file() else f"  MISSING: {out}")
        return 1

    # A full scan is 17-26s on the projects here -- too long to sit in front of
    # a commit, and pure waste when nothing it reads has moved. Fingerprinting
    # costs ~2s, so this is worth doing before deciding.
    if "--if-changed" in sys.argv and unchanged(root, name):
        print(f"  unchanged — {root / '.map' / name} still describes this tree")
        return 0

    data = run()

    outdir = root / ".map"
    outdir.mkdir(exist_ok=True)
    (outdir / name).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    # After the output, never before: a stamp written for a scan that then
    # failed would report a map as current that was never written.
    write_stamp(root, name)

    if "--json" in sys.argv:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if "levels" in data:
        print("=" * 74)
        print(f"MAP — {data['project']['name']}   ({len(data['levels'])} levels)")
        print("=" * 74)
        for L in data["levels"]:
            print(f"  {L['title']:<28} {len(L['graph']['nodes']):>3} nodes "
                  f"{len(L['graph']['edges']):>4} edges"
                  + ("   (approximate)" if L.get("caveat") else ""))
        print()
    else:
        render_text(data)
    print(f"  written: {outdir / name}")

    if "--html" in sys.argv:
        html = outdir / (name.replace(".json", ".html"))
        if "levels" in data:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import render as vrender                            # noqa: PLC0415
            html.write_text(vrender.render(data), encoding="utf-8")
        else:
            html.write_text(render_html(data), encoding="utf-8")
        print(f"  written: {html}")
        if "--open" in sys.argv:
            import webbrowser
            webbrowser.open(html.as_uri())

    # `.map/` is generated output written INTO the scanned repository. Warn
    # rather than edit someone else's .gitignore: a tool that quietly modifies
    # a repo it was only asked to read is a tool you stop trusting.
    gi = root / ".gitignore"
    if (root / ".git").exists():
        ignored = gi.is_file() and ".map" in gi.read_text(encoding="utf-8", errors="replace")
        if not ignored:
            print(f"\n  NOTE: .map/ is generated and is NOT in {gi.name}.")
            print(f"        Add this line yourself -- this tool does not edit it:")
            print(f"        .map/")

    # Keeping it current is one line, and it is PRINTED rather than installed
    # for the same reason the .gitignore note above is: a tool that writes into
    # someone else's repository because it was asked to read it is a tool you
    # stop running. Detached, because a scan takes seconds to tens of seconds
    # depending on how cold the disk is, and nothing should sit in front of a
    # commit.
    if (root / ".git").exists() and "--if-changed" not in sys.argv:
        hook = root / ".git" / "hooks" / "post-commit"
        if not hook.exists():
            print(f"\n  To keep this current, write .git/hooks/post-commit "
                  f"yourself\n  (this tool does not write into your repo):")
            print("        #!/bin/sh")
            print(f"        python {Path(__file__).resolve()} \\")
            print('          "$(git rev-parse --show-toplevel)" --all --if-changed &')
            print("  The `&` matters: --if-changed still fingerprints every "
                  "file, and a\n  commit should never wait on that.")

    print("\n  Nothing was uploaded. This tool has no network code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
