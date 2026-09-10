#!/usr/bin/env python3
"""Find code that nothing anywhere reaches — candidates only, never a verdict.

**THE RULE THIS IS BUILT AROUND.** The scope you may DELETE from and the scope
you must SEARCH are two different things, and confusing them is the defect that
makes a dead-code tool dangerous:

    delete scope   the folder you named            app1/
    search scope   the WHOLE repository, always    everything, every file type

> The operator's own example, and it is the exact failure: app1 has a function
> in a view that does something for app2 and nothing for app1. Ask a tool to
> clean app1 while it looks only at app1, and that function reads as dead. It
> is not dead. It is load-bearing for another app.

So `--scope` narrows only what may be PROPOSED. The search never narrows, and
this script refuses to run if you try to make it.

**It proposes; it does not judge, and it must not delete.** A name can be
referenced without being imported — `{% url 'name' %}`, a dotted path in
settings, a class key stored in a database row, a patch target in a test
(`craft-code`, `references/change-safety.md`). A script cannot see a reference
that lives in a table, and it cannot know that a method is called by the
framework rather than by your code. Both of those are the agent's job.

What it does do is cheap, repeatable and free: list every symbol defined inside
the delete scope, count its textual occurrences across the ENTIRE repository,
and hand over the ones at zero with the evidence attached.

  dead.py --scope apps/warehouse
  dead.py --scope apps/warehouse --repo C:/projects/acme-audit
  dead.py --scope apps/warehouse --json
  dead.py --scope apps/warehouse --duplicates     also report duplicated bodies
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Files worth searching for a reference. A name can hide in any of them, and
#: leaving one out is how a live symbol comes to look dead.
SEARCHABLE = {".py", ".html", ".htm", ".txt", ".md", ".js", ".jsx", ".ts", ".tsx",
              ".css", ".scss", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
              ".po", ".sql", ".xml", ".csv", ".env", ".sh", ".bat", ".cmd", ".ps1"}

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
             "dist", "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
             "staticfiles", "media", ".graphify", "graphify-out",
             # A GENERATED INDEX lists every symbol in the project, so every
             # symbol reads as referenced and this tool silently reports
             # nothing. Measured: `admin_required` -- an authorization
             # decorator with zero call sites -- was counted alive purely
             # because .map/scan-all.json names it. Our own map tool wrote
             # that file, months after this one was written; anything that
             # enumerates the codebase belongs here the day it is added.
             ".map", "razvojna-mapa", ".brain-map",
             # `.claude/findings/` is THIS toolchain's output about the code.
             # Filing a finding names the symbol, so counting it would move
             # the symbol out of the candidate list the moment somebody wrote
             # it down -- the .map failure, repeated by our own hand.
             "findings"}

#: Names the FRAMEWORK calls, never your code. Reporting these is worse than
#: reporting nothing: it trains the reader to skim the list, and the one real
#: finding goes past with them.
FRAMEWORK_CALLED = {
    # python protocol
    "__init__", "__str__", "__repr__", "__eq__", "__hash__", "__len__",
    "__iter__", "__enter__", "__exit__", "__call__", "__getattr__", "__new__",
    # django views
    "get", "post", "put", "patch", "delete", "head", "options", "dispatch",
    "get_queryset", "get_context_data", "get_object", "get_form", "get_form_kwargs",
    "get_form_class", "get_initial", "get_success_url", "get_template_names",
    "form_valid", "form_invalid", "get_absolute_url", "setup", "http_method_not_allowed",
    # django models / forms
    "save", "clean", "full_clean", "delete", "Meta", "ready", "handle",
    "get_prep_value", "from_db_value", "to_python", "validate", "deconstruct",
    "contribute_to_class", "get_internal_type", "db_type", "formfield",
    # commands, apps, migrations
    "add_arguments", "handle_noargs", "Migration", "dependencies", "operations",
    # tests
    "setUp", "tearDown", "setUpClass", "tearDownClass", "setUpTestData", "runTest",
}



#: A class the FRAMEWORK instantiates, recognised by what it inherits. Our own
#: FRAMEWORK_CALLED is a set of METHOD names, so it never saw a class: measured
#: on acme-audit/audits, 20 of 22 "unreferenced" findings were framework-
#: called classes -- 7 admin classes, 10 test classes, 2 signal receivers, 1
#: AppConfig -- and only 2 were real. A list where nine of ten entries are
#: noise is a list nobody reads to the end, and the real finding goes past with
#: them.
#:
#: The names come from Skylos (github.com/duriantaco/skylos, Apache-2.0,
#: skylos/analysis/known_patterns.py). Only the LISTS are taken; the detection
#: here stays ours, because that tool never reads .html and this one must.
FRAMEWORK_BASES = {
    # django
    "Model", "Manager", "QuerySet", "AppConfig", "Migration",
    "ModelAdmin", "InlineModelAdmin", "StackedInline", "TabularInline",
    "Form", "ModelForm", "BaseForm", "BaseModelForm",
    "BaseCommand", "Command",
    "View", "TemplateView", "ListView", "DetailView", "CreateView", "UpdateView",
    "DeleteView", "FormView", "RedirectView", "ArchiveIndexView", "DateDetailView",
    "DayArchiveView", "MonthArchiveView", "YearArchiveView",
    "MiddlewareMixin", "SecurityMiddleware", "CommonMiddleware", "CsrfViewMiddleware",
    "AuthenticationMiddleware", "SessionMiddleware", "MessageMiddleware",
    # django rest framework
    "APIView", "GenericAPIView", "ViewSet", "GenericViewSet", "ModelViewSet",
    "ListAPIView", "CreateAPIView", "ListCreateAPIView", "RetrieveAPIView",
    "UpdateAPIView", "DestroyAPIView", "RetrieveUpdateAPIView",
    "RetrieveDestroyAPIView", "RetrieveUpdateDestroyAPIView",
    "Serializer", "ModelSerializer", "BaseSerializer", "ListSerializer",
    "HyperlinkedModelSerializer", "BasePermission",
    # tests -- the runner discovers these; nothing imports them
    "TestCase", "SimpleTestCase", "TransactionTestCase", "LiveServerTestCase",
    "StaticLiveServerTestCase", "APITestCase", "APISimpleTestCase",
    "TenantTestCase", "unittest.TestCase",
    # elsewhere in this operator's stack
    "Task", "BaseTask", "BaseModel", "BaseSettings", "Base", "DeclarativeBase",
    "Resource", "MethodView", "APIRouter", "Enum", "IntEnum", "TextChoices",
    "IntegerChoices", "Choices", "Exception", "ValueError",
}

#: A decorator that hands the function to something else to call. Matched on
#: the LAST dotted part, so `@admin.register`, `@receiver`, `@app.route` and
#: `@shared_task` all land. Same source and the same reason as above.
FRAMEWORK_DECORATORS = {
    "register", "receiver", "shared_task", "task", "periodic_task",
    "route", "get", "post", "put", "patch", "delete", "head", "options",
    "websocket", "middleware", "on_event", "exception_handler", "errorhandler",
    "before_request", "after_request", "app_template_filter",
    "simple_tag", "inclusion_tag", "filter", "tag", "assignment_tag",
    "fixture", "hookimpl", "listens_for", "validator", "field_validator",
    "model_validator", "root_validator", "command", "callback", "group",
    "admin_display", "display", "override_settings",
}
#: NOT here, deliberately: @property, @cached_property, @staticmethod,
#: @classmethod. Those are language features, not a framework calling you --
#: a property is reached by attribute access (`obj.foo`), which the textual
#: search finds as `foo` like any other name. Listing them would hide a dead
#: property behind a reason that is not true.


#: A method the framework finds BY NAME, and only inside the right class.
#: (prefix, what the owning class or one of its bases must end with, why).
#: Measured: `clean_kind`, `clean_barcode`, `clean_responsible_roles_text` and
#: `clean_trigger_config_text` were all reported as unreferenced on
#: acme-audit -- every one a ModelForm method Django calls through
#: `getattr(self, "clean_%s" % name)`. The owner check is what keeps this from
#: excusing a module-level `clean_whatever` that really is dead.
NAMED_BY_FRAMEWORK = (
    ("clean_", ("Form",), "Django zove clean_<polje> na formi"),
    ("validate_", ("Serializer",), "DRF zove validate_<polje> na serializeru"),
    ("get_", ("Model",), "Django zove get_<polje>_display na modelu"),
    ("perform_", ("ViewSet", "APIView"), "DRF zove perform_<akcija>"),
)


#: Files that can only ever MENTION a name, never call it. A symbol whose
#: every reference outside its own file is in one of these is a `ghost`:
#: catalogued but not wired.
#:
#: Measured: `validate_no_scheduling_conflict` in acme-audit/audits reads
#: as alive because docs/ARCHITECTURE.md:386 cites it as the exemplary
#: tenant-safety pattern -- while nothing in the codebase calls it. That is
#: worse than an undocumented dead function: the documentation actively
#: recommends a check that never runs, and somebody will follow it.
#:
#: The three-state (live | ghost | dead) is lifted from ICM's `universe:` field
#: (github.com/RinDig/icm-architect, MIT) -- the only thing there this tool did
#: not already have. `.po` is excluded from the prose set on purpose: a msgid
#: is generated FROM code and a hit there means the string is really used.
PROSE_ONLY = {".md", ".txt", ".rst", ".adoc", ".csv"}


def framework_reason(d: dict) -> str:
    """Why the framework, not your code, reaches this name -- or "" when
    nothing says so. The REASON is returned rather than a boolean because a
    filtered-out finding with no stated reason is indistinguishable from a
    tool that stopped working."""
    for b in d.get("bases") or []:
        short = str(b).rsplit(".", 1)[-1]
        if short in FRAMEWORK_BASES:
            return f"nasleđuje {short}"
    for dec in d.get("decorated") or []:
        short = str(dec).rsplit(".", 1)[-1]
        if short in FRAMEWORK_DECORATORS:
            return f"dekorator @{dec}"
    n = d.get("name", "")
    own = d.get("owner") or []
    for prefix, suffixes, why in NAMED_BY_FRAMEWORK:
        if not n.startswith(prefix) or len(n) <= len(prefix):
            continue
        if prefix == "get_" and not n.endswith("_display"):
            continue
        if any(o.endswith(s) for o in own for s in suffixes):
            return why
    f = str(d.get("file", "")).replace("\\", "/")
    if "/tests/" in f or "/test/" in f or f.rsplit("/", 1)[-1].startswith("test_") \
            or f.endswith("tests.py") or f.endswith("conftest.py"):
        return "test — pronalazi ga runner, ništa ga ne uvozi"
    if n.startswith("Test") or n.endswith(("Test", "Tests", "TestCase")):
        return "ime testa — pronalazi ga runner"
    return ""


def git_root(start: Path) -> Path:
    try:
        out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        if out:
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def walk(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SEARCHABLE:
            yield path


def definitions(scope: Path) -> list[dict]:
    """Every function/class defined inside the delete scope."""
    found = []
    for path in scope.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        # Which class each method belongs to. `clean_kind` means "Django calls
        # this" inside a ModelForm and nothing at all outside one, so the owner
        # is what separates a convention from a coincidence.
        owner: dict = {}
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            names = [cls.name] + [
                b.id if isinstance(b, ast.Name)
                else (b.attr if isinstance(b, ast.Attribute) else "")
                for b in cls.bases]
            for m in cls.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner[id(m)] = [n for n in names if n]

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            name = node.name
            if name in FRAMEWORK_CALLED or name.startswith("test_"):
                continue
            if name.startswith("__") and name.endswith("__"):
                continue
            found.append({
                "name": name,
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                "file": str(path),
                "line": node.lineno,
                "private": name.startswith("_"),
                "bases": [
                    b.id if isinstance(b, ast.Name)
                    else (f"{getattr(b.value, 'id', '')}.{b.attr}" if isinstance(b, ast.Attribute) else "")
                    for b in getattr(node, "bases", [])],
                "owner": owner.get(id(node), []),
                "decorated": [
                    d.id if isinstance(d, ast.Name)
                    else getattr(d, "attr", getattr(getattr(d, "func", None), "id", ""))
                    for d in getattr(node, "decorator_list", [])],
            })
    return found


def templates(scope: Path) -> list[dict]:
    out = []
    for path in scope.rglob("*.html"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        # Django refers to a template by the path BELOW templates/, so that is
        # the string worth searching for -- the bare filename collides.
        parts = path.parts
        rel = None
        if "templates" in parts:
            i = len(parts) - 1 - parts[::-1].index("templates")
            rel = "/".join(parts[i + 1:])
        out.append({"name": rel or path.name, "kind": "template",
                    "file": str(path), "line": 1, "private": False,
                    "decorated": []})
    return out


def index(repo: Path, names: set[str]) -> dict[str, dict[str, int]]:
    """name -> {file: count}, across the WHOLE repository, in one pass.

    Only the names we are actually asking about are counted. The first version
    indexed every word of three characters or more, which was both slower and
    WRONG: `hn` is two characters, so it was never indexed at all and a
    function called from another module looked completely unreferenced. Any
    length rule is a silent lie about short names; asking for the exact set
    removes the rule.
    """
    hits: dict[str, dict[str, int]] = {n: {} for n in names}
    if not names:
        return hits
    # One alternation over the exact names, longest first so `foo_bar` is not
    # eaten by `foo`. Word boundaries keep `get` out of `widget`.
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])(" +
        "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) +
        r")(?![A-Za-z0-9_])")
    for path in walk(repo):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found: dict[str, int] = {}
        for m in pattern.finditer(text):
            found[m.group(1)] = found.get(m.group(1), 0) + 1
        for name, n in found.items():
            hits[name][str(path)] = n
    return hits


def analyse(scope: Path, repo: Path, want_duplicates: bool) -> dict:
    defs = definitions(scope) + templates(scope)
    hits = index(repo, {d["name"] for d in defs})

    unreferenced, internal, framework, ghosts, alive = [], [], [], [], 0
    for d in defs:
        per_file = hits.get(d["name"], {})
        elsewhere = {f: n for f, n in per_file.items() if f != d["file"]}
        own = per_file.get(d["file"], 0)

        # Prose is not a reference. Split it off FIRST and then apply the
        # ordinary rules to what is left, or a symbol its own file uses would
        # be called alive on the strength of a sentence in a doc.
        prose = {f for f in elsewhere if Path(f).suffix.lower() in PROSE_ONLY}
        code_elsewhere = {f: n for f, n in elsewhere.items() if f not in prose}

        if code_elsewhere:
            alive += 1
            continue
        if prose and own <= 1:
            # Named in documentation, reached by nothing. Worse than an
            # undocumented dead function: the doc promises a behaviour the
            # code does not deliver, and somebody will follow the doc.
            ghosts.append({**d, "self_mentions": own,
                           "mentioned_in": sorted(Path(f).name for f in prose)})
            continue
        # Reached by the framework, not by your code. Its own bucket WITH the
        # reason -- never silently dropped, because a finding that disappears
        # without a stated reason and a tool that broke look the same.
        why = framework_reason(d)
        if why:
            framework.append({**d, "why": why, "self_mentions": own})
            continue
        if own > 1:
            # Used inside its own module and nowhere else. NOT a strong
            # candidate on its own -- but a dead CLUSTER hides exactly here:
            # A calls B, B calls A, and nothing outside calls either. Reported
            # separately so the strong list stays readable.
            internal.append({**d, "self_mentions": own})
            continue
        unreferenced.append({**d, "self_mentions": own})

    return {
        "scope": str(scope), "repo": str(repo),
        "searched_files": sum(1 for _ in walk(repo)),
        "defined_in_scope": len(defs), "referenced_elsewhere": alive,
        "unreferenced": sorted(unreferenced, key=lambda d: (d["file"], d["line"])),
        "internal_only": sorted(internal, key=lambda d: (d["file"], d["line"])),
        "framework_called": sorted(framework, key=lambda d: (d["file"], d["line"])),
        "ghost": sorted(ghosts, key=lambda d: (d["file"], d["line"])),
        "duplicates": duplicates(repo) if want_duplicates else [],
    }


def duplicates(repo: Path, min_lines: int = 6) -> list[dict]:
    """Byte-identical function bodies, normalised for whitespace.

    REPORTED ONLY, never merged. Two copies that have drifted apart -- and they
    usually have, slightly -- are two behaviours, and choosing which survives is
    a design decision, not a mechanical one (`craft-reuse`, the rule of three).
    """
    bodies: dict[str, list[dict]] = defaultdict(list)
    for path in repo.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            end = getattr(node, "end_lineno", node.lineno)
            if end - node.lineno + 1 < min_lines:
                continue
            body = "\n".join(l.strip() for l in lines[node.lineno:end] if l.strip())
            if not body:
                continue
            bodies[body].append({"name": node.name, "file": str(path),
                                 "line": node.lineno, "lines": end - node.lineno + 1})
    return [{"copies": v, "lines": v[0]["lines"]}
            for v in bodies.values() if len(v) > 1]


def main() -> int:
    args = sys.argv[1:]

    def opt(flag: str) -> str | None:
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None

    scope_arg = opt("--scope")
    if not scope_arg:
        print(__doc__.strip())
        return 1
    scope = Path(scope_arg).resolve()
    if not scope.is_dir():
        print(f"not a directory: {scope}")
        return 1

    repo = Path(opt("--repo") or git_root(scope)).resolve()

    # THE GUARD. Narrowing the search to the delete scope is the one thing that
    # turns this tool into a hazard, so it is refused rather than warned about.
    if not str(scope).startswith(str(repo)):
        print(f"REFUSED: --scope {scope} is not inside --repo {repo}.\n"
              f"The search must cover the whole repository or a symbol used by\n"
              f"another app reads as dead. That is the defect this tool exists\n"
              f"to avoid, so it will not run narrowed.")
        return 2

    r = analyse(scope, repo, "--duplicates" in args)

    if "--json" in args:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0

    print("=" * 74)
    print("DEAD CODE — CANDIDATES ONLY, NOT A VERDICT")
    print("=" * 74)
    print(f"  delete scope   {r['scope']}")
    print(f"  SEARCH scope   {r['repo']}   ({r['searched_files']:,} files)")
    print(f"  defined here   {r['defined_in_scope']}   "
          f"referenced elsewhere: {r['referenced_elsewhere']}")
    print()

    if r["unreferenced"]:
        print(f"  {len(r['unreferenced'])} UNREFERENCED — nothing in the whole "
              f"repository mentions these,\n  not even the file they live in:\n")
        for c in r["unreferenced"]:
            dec = f"  @{','.join(d for d in c['decorated'] if d)}" if any(c["decorated"]) else ""
            print(f"    {c['kind']:<9} {c['name']:<32} {c['file']}:{c['line']}{dec}")
    else:
        print("  Nothing in this scope is completely unreferenced.")

    if r["internal_only"]:
        print(f"\n  {len(r['internal_only'])} INTERNAL-ONLY — used inside their own "
              f"file and nowhere else.\n  Usually correct (a private helper). A dead "
              f"CLUSTER hides here: A calls B,\n  B calls A, and nothing outside calls "
              f"either — check the entry point:\n")
        for c in r["internal_only"]:
            print(f"    {c['kind']:<9} {c['name']:<32} {c['file']}:{c['line']}")

    gh = r.get("ghost") or []
    if gh:
        print(f"\n  {len(gh)} GHOST — named in documentation, called by nothing.\n"
              f"  The docs promise a behaviour the code does not deliver, which is worse\n"
              f"  than an undocumented dead function: somebody will follow the doc:\n")
        for c in gh:
            print(f"    {c['kind']:<9} {c['name']:<32} {c['file']}:{c['line']}")
            print(f"        pominje se samo u: {', '.join(c['mentioned_in'])}")

    fw = r.get("framework_called") or []
    if fw:
        # Counted, then folded: the reader needs to know they were considered
        # and why they were set aside, not to read seven admin classes again.
        by = {}
        for c in fw:
            by.setdefault(c["why"], []).append(c["name"])
        print(f"\n  {len(fw)} FRAMEWORK-CALLED — nothing in your code mentions these "
              f"either,\n  but something else calls them. Set aside WITH the reason, "
              f"not hidden:\n")
        for why, names in sorted(by.items(), key=lambda kv: -len(kv[1])):
            head = ", ".join(sorted(names)[:6])
            more = f", +{len(names) - 6}" if len(names) > 6 else ""
            print(f"    {len(names):>3}  {why:<44} {head}{more}")
        print("    (a name here that your framework does NOT call is a finding "
              "this missed —\n     the reason is printed so you can catch that)")

    for dup in r.get("duplicates", []):
        print(f"\n  DUPLICATE ({dup['lines']} lines, identical):")
        for c in dup["copies"]:
            print(f"      {c['name']}  {c['file']}:{c['line']}")

    print("\n" + "-" * 74)
    print("  NOT PROOF. A name can be referenced without being imported:")
    print("  {% url 'x' %}, a dotted path in settings, a class key stored in a")
    print("  database row, a patch target in a test, another repository's API.")
    print("  A script cannot see a reference that lives in a table.")
    print("  Judgement and deletion belong to the `undertaker` agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
