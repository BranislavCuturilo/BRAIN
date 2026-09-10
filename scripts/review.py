#!/usr/bin/env python3
"""Deterministic code review: the rules that can be checked, checked.

**Why this exists next to the review agents rather than instead of them.** The
agents judge; nothing measures. `craft-security` states its rules precisely and
they still get missed -- two principal-grade audits walked past the same
by-pk defect while catching four seeded ones. That is the finding
`require_skill.py` was built on, applied to review: a rule that must be
recalled is a rule that can be skipped, and a rule that runs cannot be.

**Why not alibaba/open-code-review.** Its useful half is a deterministic layer
over a diff; its other half is a ruleset for Java (NPE, thread-safety) and ten
languages. The half worth having has to encode THESE rules, which no
off-the-shelf ruleset contains, so it has to be written either way. This one
also sends nothing anywhere and needs no key, which means it can run in a hook.

**Precision over completeness, always.** A reviewer that cries wolf gets turned
off, and then it catches nothing at all. Every rule here reports only what it
can establish, and every rule reports how many things it could NOT resolve.
Django is dynamic -- `getattr`, `**kwargs`, decorators applied at runtime -- so
silence from this tool is never proof of safety, and it says so in its own
output.

**Adding a rule.** One entry in `RULES`, and it must name the incident it comes
from. A rule written from general knowledge reads identically to an earned one
once it is in the file, and that is how the whole thing rots.

  review.py <app-or-project>            every rule
  review.py <path> --rule sibling-drift only one
  review.py <path> --json
  review.py --rules                     what it knows, and why each exists
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SKIP_DIRS = {"venv", ".venv", "node_modules", "__pycache__", ".git",
             "migrations", "site-packages", "staticfiles"}

#: A name that appears in a view and means "somebody is being authorized here".
#: Deliberately broad on the LEFT (what counts as a guard) and strict on the
#: RIGHT (what counts as a finding): over-counting guards only ever makes this
#: quieter, and a quiet false negative is cheaper than a loud false positive.
GUARD_BASE = re.compile(
    r"(LoginRequired|PermissionRequired|Tenant|Feature|Admin|Manager|Staff|"
    r"Owner|Scope|Access|Auth)\w*Mixin$|Mixin$")
GUARD_DECOR = re.compile(
    # Named guards first, then the shape. `crm_api_auth` matched none of the
    # original list and it is the ONLY thing protecting a money endpoint --
    # every billing view in two projects was reported as unguarded because a
    # custom auth decorator did not end in `_required`. A decorator whose name
    # contains auth, perm, login, access, token or hmac is a guard.
    r"^(login_required|permission_required|user_passes_test|"
    r"staff_member_required|csrf_protect|\w*_required|require_\w+|"
    r"\w*(auth|perm|login|access|token|hmac|apikey|api_key|scope)\w*)$",
    re.I)

#: Decorators that make a GET impossible, so a write inside is not reachable
#: by one. `require_safe` is deliberately absent: it permits GET, which is the
#: whole point of the rule.
METHOD_LOCKED = re.compile(
    r"^(require_POST|require_PUT|require_PATCH|require_DELETE|"
    r"require_http_methods|api_view|action)$")

GUARD_ATTR = {"required_permission", "required_feature", "permission_required",
              "permission_classes", "required_role", "required_features"}
GUARD_CALL = re.compile(
    r"(^|\.)(can_\w+|has_perm|has_perms|has_permission|check_\w*permission|"
    r"is_authenticated|get_accessible\w*|for_user|for_tenant|visible_to|"
    # Role flags read straight off the user. Missing these made the reviewer
    # report "NO guard of any kind" against two exports whose only check was
    # `if not (user.is_superuser or user.is_administrator)`. A false claim that
    # something is unguarded is worse than missing it: it sends the reader to
    # the wrong defect, and the real one there was different (the check itself
    # raises AttributeError on AnonymousUser).
    r"is_superuser|is_administrator|is_admin|is_staff|is_manager|rukovodilac)$")
GUARD_RAISE = {"PermissionDenied", "Http404", "SuspiciousOperation"}

#: Guards are compared as CONCEPTS, never as names.
#:
#: The first run reported "missing LoginRequiredMixin" against four
#: function-based views. A function cannot have a mixin, so that finding could
#: never be actioned and could never be absent -- a systematic false positive,
#: and the fastest way to get a reviewer switched off. `LoginRequiredMixin` and
#: `@login_required` mean the same thing and must compare equal.
CONCEPTS: list[tuple[str, re.Pattern]] = [
    ("auth", re.compile(
        r"^(LoginRequiredMixin|login_required|is_authenticated)$")),
    # Role flags belong here rather than in a concept of their own. A group
    # where five siblings use `can_x()` and one uses `is_superuser` is not
    # drift -- both answer "is this user allowed" -- and splitting them would
    # report every such group twice.
    ("permission", re.compile(
        r"^(PermissionRequired\w*Mixin|permission_required|required_permission|"
        r"permission_classes|required_role|has_perms?|has_permission|"
        r"can_\w+|check_\w*permission|"
        r"is_superuser|is_administrator|is_admin|is_staff|is_manager|rukovodilac)$")),
    ("tenant", re.compile(
        r"^(Tenant\w*Mixin|Scope\w*Mixin|Owner\w*Mixin|get_accessible\w*|"
        r"for_user|for_tenant|visible_to)$")),
    ("feature", re.compile(
        r"^(Feature\w*Mixin|required_features?)$")),
    ("staff", re.compile(
        r"^(Admin\w*Mixin|Manager\w*Mixin|Staff\w*Mixin|staff_member_required|"
        r"user_passes_test)$")),
    ("refusal", re.compile(
        r"^(PermissionDenied|Http404|SuspiciousOperation|HttpResponseForbidden)$")),
]


def concept_of(guard: str) -> str:
    for name, pat in CONCEPTS:
        if pat.match(guard):
            return name
    return ""


@dataclass
class View:
    name: str
    path: str
    line: int
    kind: str                       # "class" | "function"
    guards: set[str] = field(default_factory=set)
    model: str = ""
    unresolved: str = ""            # why we could not read it, if we could not

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}"

    @property
    def unclassified(self) -> set[str]:
        """Guards recognised as guards but not mapped to a concept.

        A view with one of these is protected by something this tool does not
        understand. It is never 'unguarded', and it must not be compared
        against siblings on a concept it may well provide under another name.
        """
        return {g for g in self.guards if not concept_of(g)}

    @property
    def concepts(self) -> dict[str, str]:
        """concept -> the guard name that provided it, for the message."""
        out: dict[str, str] = {}
        for g in sorted(self.guards):
            c = concept_of(g)
            if c and c not in out:
                out[c] = g
        return out


def py_files(root: Path):
    for f in root.rglob("*.py"):
        if any(p in SKIP_DIRS for p in f.parts):
            continue
        yield f


def _name(node) -> str:
    """Dotted name of an expression, or "" for anything that is not a name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _name(node.value)
        return f"{head}.{node.attr}" if head else node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


def guards_in_body(node) -> set[str]:
    """Authorization signals anywhere inside this view.

    Inline checks count. Three of the four real projects here authorize with
    `if not user.can_x(): ...` in the handler body rather than declaratively,
    and a reviewer that only reads decorators would report every one of those
    245 endpoints as unguarded -- which is the loud-false-positive failure this
    file exists to avoid.
    """
    found: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            nm = _name(n.func)
            if nm and GUARD_CALL.search(nm):
                found.add(nm.split(".")[-1])
        elif isinstance(n, ast.Attribute):
            nm = _name(n)
            if nm and GUARD_CALL.search(nm):
                found.add(nm.split(".")[-1])
        elif isinstance(n, ast.Raise):
            exc = _name(n.exc)
            if exc.split(".")[-1] in GUARD_RAISE:
                found.add(exc.split(".")[-1])
        elif isinstance(n, ast.Name) and n.id in GUARD_RAISE:
            found.add(n.id)
    return found


def model_of(node, source_names: set[str]) -> str:
    """The model this view is about, or "".

    `model = X` wins outright. Otherwise the CamelCase name most often used as
    `X.objects` / `get_object_or_404(X` inside it. A view that touches three
    models is grouped by the one it touches most, which is the one its siblings
    will also touch.
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in ("model",):
                    got = _name(n.value)
                    if got:
                        return got.split(".")[-1]

    counts: dict[str, int] = {}
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in ("objects", "_default_manager"):
            nm = _name(n.value).split(".")[-1]
            if nm and nm[:1].isupper():
                counts[nm] = counts.get(nm, 0) + 2
        elif isinstance(n, ast.Call):
            fn = _name(n.func).split(".")[-1]
            if fn in ("get_object_or_404", "get_list_or_404") and n.args:
                nm = _name(n.args[0]).split(".")[-1]
                if nm and nm[:1].isupper():
                    counts[nm] = counts.get(nm, 0) + 3
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "queryset":
                    nm = _name(n.value).split(".")[0]
                    if nm and nm[:1].isupper():
                        counts[nm] = counts.get(nm, 0) + 3
    if not counts:
        return ""
    best = max(counts, key=lambda k: counts[k])
    return best if best in source_names or True else ""


def inherit_guards(views: list[View], bases: dict[str, set[str]],
                   guards: dict[str, set[str]]) -> None:
    """Push a base class's guards down to everything that inherits it.

    Without this, a project that puts `LoginRequiredMixin` on one base class
    and inherits it everywhere looks entirely unguarded: acme-audit
    produced 140 "no authorization of any kind" findings, every one of them
    wrong, which is precisely how a reviewer gets switched off on day one.
    """
    def resolve(name: str, seen: set[str]) -> set[str]:
        if name in seen:
            return set()
        seen.add(name)
        out = set(guards.get(name, ()))
        for b in bases.get(name, ()):
            out |= resolve(b, seen)
        return out

    for v in views:
        if v.kind == "class":
            v.guards |= resolve(v.name, set())


def collect(root: Path) -> tuple[list[View], list[str]]:
    """Every view in the tree, plus the files that could not be parsed."""
    views: list[View] = []
    broken: list[str] = []
    class_bases: dict[str, set[str]] = {}
    class_guards: dict[str, set[str]] = {}
    for f in py_files(root):
        if f.name not in ("views.py",) and "views" not in f.parts and \
                not f.name.startswith("views"):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError) as exc:
            broken.append(f"{f}: {exc}")
            continue
        rel = str(f).replace("\\", "/")
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.ClassDef, ast.FunctionDef))}

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = {_name(b).split(".")[-1] for b in node.bases}
                if not any(b.endswith(("View", "Mixin")) or b in
                           ("APIView", "ViewSet", "ModelViewSet") for b in bases):
                    continue
                v = View(node.name, rel, node.lineno, "class")
                class_bases[node.name] = bases
                v.guards |= {b for b in bases if GUARD_BASE.search(b)}
                for st in node.body:
                    if isinstance(st, ast.Assign):
                        for t in st.targets:
                            if isinstance(t, ast.Name) and t.id in GUARD_ATTR:
                                v.guards.add(t.id)
                v.guards |= guards_in_body(node)
                v.model = model_of(node, names)
                class_guards[node.name] = set(v.guards)
                views.append(v)

            elif isinstance(node, ast.FunctionDef):
                decs = {_name(d).split(".")[-1] for d in node.decorator_list}
                args = [a.arg for a in node.args.args]
                if not args or args[0] not in ("request",):
                    continue
                v = View(node.name, rel, node.lineno, "function")
                v.guards |= {d for d in decs if GUARD_DECOR.match(d)}
                v.guards |= guards_in_body(node)
                v.model = model_of(node, names)
                views.append(v)

    # Every base class in the tree, guard-bearing or not, so a chain of three
    # project classes still carries the mixin down to the leaf.
    for f in py_files(root):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                b = {_name(x).split(".")[-1] for x in node.bases}
                class_bases.setdefault(node.name, set())
                class_bases[node.name] |= b
                g = {x for x in b if GUARD_BASE.search(x)}
                for st in node.body:
                    if isinstance(st, ast.Assign):
                        for tg in st.targets:
                            if isinstance(tg, ast.Name) and tg.id in GUARD_ATTR:
                                g.add(tg.id)
                if g:
                    class_guards.setdefault(node.name, set())
                    class_guards[node.name] |= g

    inherit_guards(views, class_bases, class_guards)
    return views, broken


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

#: Views that are public on purpose. Reporting these is how a reviewer teaches
#: people to ignore it: a login page with no login check is not a finding, and
#: the reader who dismisses one dismisses the next.
PUBLIC_BY_DESIGN = re.compile(
    r"^(login|logout|signup|register|landing|home|index|robots|sitemap|health|"
    r"ping|status|set_language|manifest|favicon|handler\d+|password_reset\w*|"
    r"password_change\w*|custom_login\w*|\w*login_view)$", re.I)

#: `add`, `remove`, `clear` and `set_password` were here and had to go: they
#: match messages.add(), a list's .remove() and a set's .clear(), which turned
#: 71 of popis's findings into noise. Only calls that write a ROW stay.
WRITE_CALLS = {"save", "delete", "create", "update", "bulk_create",
               "bulk_update", "get_or_create", "update_or_create"}

#: Receivers whose .save()/.update() is not a database write.
NOT_A_ROW = ("response.", "request.", "self.request", "messages.", "session.",
             "request.session", "cache.", "logger.", "os.", "settings.")

#: A name shaped like an authorization helper. Used only by the dead-guard
#: rule, so a false match costs one line of output and never a wrong edit.
GUARD_SHAPE = re.compile(
    r"^(can_\w+|may_\w+|has_\w*perm\w*|check_\w*(perm|access|scope)\w*|"
    r"\w*_required|ensure_\w+|assert_\w+|authorize\w*|is_allowed\w*)$")

#: Attributes every Django model has whether or not the class body names them.
MODEL_BUILTINS = {
    "id", "pk", "objects", "save", "delete", "full_clean", "clean",
    "clean_fields", "validate_unique", "refresh_from_db", "serializable_value",
    "get_deferred_fields", "DoesNotExist", "MultipleObjectsReturned",
    "_state", "_meta", "date_error_message", "unique_error_message",
    "get_absolute_url", "__class__", "__dict__",
}


@dataclass
class Ctx:
    """Everything the rules read. Built once; no rule touches the disk."""
    root: Path
    views: list[View]
    models: dict[str, set[str]] = field(default_factory=dict)
    model_files: dict[str, str] = field(default_factory=dict)
    scope: str = ""
    sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    project: Path | None = None
    pages_dir: Path | None = None
    urls: dict = field(default_factory=dict)      # (urls.py dir, view symbol) -> url_name
    pages: dict = field(default_factory=dict)     # url_name -> docs/pages declaration


def model_fields(root: Path) -> tuple[dict[str, set[str]], dict[str, set[str]],
                                      dict[str, str],
                                      list[tuple[str, str, int, str]]]:
    """{Model: {attribute names}}, where each is defined, and self.X uses.

    Only classes whose bases are visibly `models.Model` are recorded. A model
    inheriting an abstract base declared elsewhere has fields this cannot see,
    and reporting those as missing would be the loud false positive that gets a
    reviewer switched off.
    """
    fields_by: dict[str, set[str]] = {}
    fks_by: dict[str, set[str]] = {}
    where: dict[str, str] = {}
    uses: list[tuple[str, str, int, str]] = []

    for f in py_files(root):
        if f.name != "models.py" and "models" not in f.parts:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        rel = str(f).replace("\\", "/")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {_name(b) for b in node.bases}
            if not any(b in ("models.Model", "Model") for b in bases):
                continue
            names: set[str] = set()
            fks: set[str] = set()
            for st in node.body:
                if isinstance(st, ast.Assign):
                    got = {t.id for t in st.targets if isinstance(t, ast.Name)}
                    names |= got
                    # Only a ForeignKey can partition data. Counting every
                    # attribute made `Meta` -- an inner class on every model --
                    # win the vote for scope column in all four projects, which
                    # silently turned two rules into noise generators.
                    call = _name(st.value).split(".")[-1] if isinstance(st.value, ast.Call) else ""
                    if call in ("ForeignKey", "OneToOneField"):
                        fks |= got
                elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    names.add(st.target.id)
                elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(st.name)
                elif isinstance(st, ast.ClassDef):
                    names.add(st.name)
            # A model that assigns attributes dynamically has fields nothing
            # static can enumerate; leave it out entirely rather than guess.
            body_src = ast.dump(node)
            dynamic = "setattr" in body_src or "kwargs" in body_src
            fields_by[node.name] = names
            fks_by[node.name] = fks
            where[node.name] = f"{rel}:{node.lineno}"
            if dynamic:
                fields_by[node.name] = names | {"*"}
            for st in node.body:
                if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for n in ast.walk(st):
                    if isinstance(n, ast.Attribute) and \
                            isinstance(n.value, ast.Name) and n.value.id == "self":
                        uses.append((node.name, n.attr, n.lineno, rel))
    return fields_by, fks_by, where, uses


def scope_column(fks: dict[str, set[str]], sources: dict[str, str]) -> tuple[str, int]:
    """The column that partitions the data, and how many models carry it.

    Derived, never configured -- it is `firmaid`, `firm`, `firma` and
    `organization` across the four projects here, so a hardcoded name would be
    wrong for three of them.

    Prevalence alone is not enough. Counting the most common foreign key gave
    `created_by` in one project: an audit stamp sits on as many models as the
    tenant does. The deciding signal is BEHAVIOURAL -- the scope column is the
    one views actually filter querysets by, and nobody filters a list by who
    created the row.

    Returning "" is a real answer. The rules that need a scope column then do
    not run, and the report says so; guessing would turn both of them into
    noise generators, which is what `Meta` did before foreign keys were the
    only candidates.
    """
    AUDIT = re.compile(r"^(created|updated|modified|deleted|changed)_by$")
    counts: dict[str, int] = {}
    for names in fks.values():
        for n in names:
            if n.startswith("_") or AUDIT.match(n):
                continue
            counts[n] = counts.get(n, 0) + 1
    if not counts:
        return "", 0

    floor = max(2, len(fks) // 5)
    candidates = {k: v for k, v in counts.items() if v >= floor} or counts

    view_src = "\n".join(s for path, s in sources.items() if "views" in path)
    used = {k: len(re.findall(r"\b" + re.escape(k) + r"(_id)?\s*=", view_src))
            for k in candidates}

    # Prevalence breaks ties, but a column no view ever filters by is not the
    # partition however many models carry it.
    best = max(candidates, key=lambda k: (used.get(k, 0), candidates[k]))
    if used.get(best, 0) == 0:
        return "", 0
    return best, candidates[best]


def source_text(root: Path) -> dict[str, str]:
    out = {}
    for f in py_files(root):
        try:
            out[str(f).replace("\\", "/")] = f.read_text(encoding="utf-8",
                                                         errors="replace")
        except OSError:
            continue
    return out


def build(root: Path) -> Ctx:
    views, broken = collect(root)
    ctx = Ctx(root=root, views=views)
    ctx.models, fks, ctx.model_files, ctx._uses = model_fields(root)   # type: ignore[attr-defined]
    ctx.sources = source_text(root)
    ctx.scope, n = scope_column(fks, ctx.sources)
    ctx.project = project_root(root)
    if ctx.project and (ctx.project / PAGES_DIR).is_dir():
        ctx.pages_dir = ctx.project / PAGES_DIR
        ctx.pages = docs_pages(ctx.project)
    ctx.urls = url_names(ctx.project or root)
    if ctx.pages_dir is not None:
        ctx.notes.append(f"{len(ctx.pages)} page declaration(s) in {PAGES_DIR.as_posix()}; "
                         f"{len(ctx.urls)} route(s) read from urls.py")
    else:
        ctx.notes.append("no docs/pages/ in this project; the page-context rules did not run")
    if broken:
        ctx.notes.append(f"{len(broken)} file(s) failed to parse")
    if ctx.scope:
        ctx.notes.append(f"scope column read as `{ctx.scope}` ({n} models)")
    else:
        ctx.notes.append("no scope column could be derived; the rules that "
                         "need one did not run")
    return ctx


def _finding(rule: str, v_name: str, where: str, model: str, message: str,
             severity: str = "note", **extra) -> dict:
    d = {"rule": rule, "view": v_name, "where": where, "model": model,
         "message": message, "severity": severity}
    d.update(extra)
    return d


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def sibling_drift(ctx: Ctx) -> list[dict]:
    """One endpoint on a model is missing a guard every sibling has.

    THE incident, stated in craft-security: "When the list narrows by
    own-records, sub-tree, archive or draft status, every sibling endpoint on
    that model must apply the same narrowing -- and the POST/action ones, which
    have no template and nobody looks at, are the ones that get forgotten."

    This does not judge whether a guard is correct. It reports a SHAPE: n
    endpoints on one model agree, one does not. That shape is either a defect
    or a deliberate exception, and the author knows instantly which -- which is
    what makes it worth reporting and cheap to dismiss.
    """
    by_model: dict[str, list[View]] = {}
    for v in ctx.views:
        if v.model:
            by_model.setdefault(v.model, []).append(v)

    out = []
    for model, group in sorted(by_model.items()):
        if len(group) < 3:
            continue          # two endpoints disagreeing is not yet a pattern
        counts: dict[str, int] = {}
        for v in group:
            for c in v.concepts:
                counts[c] = counts.get(c, 0) + 1
        for concept, n in counts.items():
            # Present in ALL BUT ONE. Looser than that and every optional
            # mixin in the codebase becomes a finding.
            if n != len(group) - 1:
                continue
            odd = [v for v in group if concept not in v.concepts]
            if len(odd) != 1:
                continue
            # It may provide the same thing under a name this cannot read.
            if odd[0].unclassified:
                continue
            v = odd[0]
            via = sorted({s.concepts[concept] for s in group
                          if concept in s.concepts})
            examples = [s.name for s in group if concept in s.concepts][:3]
            out.append(_finding(
                "sibling-drift", v.name, v.where, model,
                f"no {concept.upper()} check, which {n} of {len(group)} "
                f"siblings have (via {', '.join(via[:3])}; e.g. "
                f"{', '.join(examples[:2])})",
                "high" if not v.guards else "note",
                guard=concept, via=via[:3], siblings=n, total=len(group),
                has_any_guard=bool(v.guards), sibling_examples=examples))
    out.sort(key=lambda f: (f["severity"] != "high", -f["siblings"]))
    return out


def unguarded(ctx: Ctx) -> list[dict]:
    """A view with no authorization of any kind, in an app that has some.

    Not every unguarded view is a defect -- a login page cannot require a
    login. So this fires only where the surrounding code establishes an
    expectation: the view names a model, and most of its neighbours are
    guarded. A codebase with no guards at all produces nothing here, because
    there is no expectation to violate.
    """
    guarded = [v for v in ctx.views if v.guards]
    if len(guarded) < 3 or len(guarded) < len(ctx.views) * 0.4:
        return []
    out = []
    for v in ctx.views:
        if v.guards or not v.model:
            continue
        if PUBLIC_BY_DESIGN.match(v.name):
            continue
        out.append(_finding(
            "unguarded", v.name, v.where, v.model,
            f"no authorization of any kind, in an app where "
            f"{len(guarded)} of {len(ctx.views)} views have some, and it "
            f"queries {v.model}",
            "high"))
    return out


def _writes_on_get(node) -> list[tuple[str, int]]:
    """[(call, line)] for writes reachable without a POST check.

    A write guarded by `if request.method == "POST"` is fine. The walk carries
    that state down rather than looking for it afterwards, because a function
    can check the method for one branch and write in another.
    """
    found: list[tuple[str, int]] = []

    def walk(n, protected: bool) -> None:
        if isinstance(n, ast.If):
            src = ast.dump(n.test)
            guards = ("POST" in src or "method" in src or "is_valid" in src
                      or "DELETE" in src or "PUT" in src or "PATCH" in src)
            for c in n.body:
                walk(c, protected or guards)
            for c in n.orelse:
                walk(c, protected)
            return
        if isinstance(n, ast.Call) and not protected:
            fn = _name(n.func).split(".")[-1]
            if fn in WRITE_CALLS:
                base = _name(n.func)
                # `form.save()` and `.objects.create()` are the real ones;
                # `response.set_cookie` and friends are not writes to data.
                if not base.startswith(NOT_A_ROW):
                    found.append((base, n.lineno))
        for c in ast.iter_child_nodes(n):
            walk(c, protected)

    for c in ast.iter_child_nodes(node):
        walk(c, False)
    return found


def get_writes(ctx: Ctx) -> list[dict]:
    """A state change reachable by GET.

    craft-security rule 3: "GET must never write. CSRF protection does not
    cover safe methods, so a write behind ?archive=1 executes as whichever
    victim loads an image tag pointing at it. Split the verb; do not add a
    flag."
    """
    out = []
    for path, src in ctx.sources.items():
        if "views" not in path:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            target, label = None, ""
            if isinstance(node, ast.FunctionDef) and node.name == "get":
                target, label = node, "the get() handler"
            elif isinstance(node, ast.FunctionDef):
                a = [x.arg for x in node.args.args]
                if a and a[0] == "request":
                    target, label = node, "a view with no method check"
            if target is None:
                continue
            # A view locked to POST cannot be reached by GET at all, whatever
            # its body does. Missing this reported every @require_POST billing
            # endpoint in two projects.
            decs = {_name(d).split(".")[-1] for d in node.decorator_list}
            if any(METHOD_LOCKED.match(d) for d in decs):
                continue
            hits = _writes_on_get(target)
            if not hits:
                continue
            call, line = hits[0]
            out.append(_finding(
                "get-writes", node.name, f"{path}:{line}", "",
                f"`{call}()` runs in {label} with nothing testing the request "
                f"method — a GET that writes executes as whoever loads an "
                f"<img> pointing at it, and CSRF does not cover safe methods",
                "high"))
    return out


def scope_drift(ctx: Ctx) -> list[dict]:
    """A view that never narrows by the scope column while its siblings do."""
    if not ctx.scope:
        return []
    by_model: dict[str, list[View]] = {}
    for v in ctx.views:
        if v.model:
            by_model.setdefault(v.model, []).append(v)

    out = []
    for model, group in sorted(by_model.items()):
        if len(group) < 3 or ctx.scope not in ctx.models.get(model, set()):
            continue
        def scoped(v: View) -> bool:
            src = ctx.sources.get(v.path, "")
            body = src.splitlines()[v.line - 1:v.line + 120]
            return any(ctx.scope in l for l in body)
        with_scope = [v for v in group if scoped(v)]
        if len(with_scope) < len(group) - 1 or len(with_scope) == len(group):
            continue
        for v in group:
            if v in with_scope:
                continue
            out.append(_finding(
                "scope-drift", v.name, v.where, model,
                f"never mentions `{ctx.scope}`, while {len(with_scope)} of "
                f"{len(group)} views on {model} narrow by it "
                f"(e.g. {with_scope[0].name})",
                "high"))
    return out


def unscoped_lookup(ctx: Ctx) -> list[dict]:
    """A by-id lookup with no scope condition, in a view that never scopes.

    craft-security rule 2 again, at the query rather than the guard: "a detail,
    edit or action endpoint addressed by id authorizes through the SAME helper
    the list view uses -- never a bare scope filter plus a scope-wide
    permission." A `get_object_or_404(Model, pk=pk)` in a view that mentions
    the scope column nowhere is that defect in one line.
    """
    if not ctx.scope:
        return []
    tenant_scoped = {v.name for v in ctx.views
                     if "tenant" in v.concepts or v.unclassified}
    out = []
    for path, src in ctx.sources.items():
        if "views" not in path:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                continue
            body = ast.dump(node)
            if ctx.scope in body:
                continue                    # it scopes somewhere; not this rule
            # A class that carries a tenant mixin narrows in the mixin, where
            # the column name never appears in this file. acme-audit
            # scopes 723 views through TenantQuerysetMixin; without this the
            # rule reported 41 of them and every one was wrong.
            if node.name in tenant_scoped:
                continue
            for n in ast.walk(node):
                if not isinstance(n, ast.Call):
                    continue
                fn = _name(n.func).split(".")[-1]
                kw = {k.arg for k in n.keywords if k.arg}
                if fn == "get_object_or_404" and kw and kw <= {"pk", "id", "slug", "uuid"}:
                    model = _name(n.args[0]).split(".")[-1] if n.args else ""
                    out.append(_finding(
                        "unscoped-lookup", node.name, f"{path}:{n.lineno}", model,
                        f"`get_object_or_404({model}, {'/'.join(sorted(kw))}=…)` "
                        f"with no `{ctx.scope}` condition, and nothing else in "
                        f"this view mentions `{ctx.scope}` — any id from any "
                        f"tenant resolves",
                        "high"))
                    break
    return out


def dead_guard(ctx: Ctx) -> list[dict]:
    """An authorization helper that nothing calls.

    craft-security: "A guard with zero call sites is worse than no guard,
    because the codebase reads as protected. When you find an authorization
    helper, grep for its callers before believing it: the correct helper
    existed and was called from nowhere in the repository."
    """
    defined: dict[str, tuple[str, int]] = {}
    for path, src in ctx.sources.items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    GUARD_SHAPE.match(node.name):
                defined.setdefault(node.name, (path, node.lineno))

    out = []
    for name, (path, line) in sorted(defined.items()):
        uses = sum(src.count(name) for src in ctx.sources.values())
        # One occurrence is the definition itself. Decorators and templates can
        # reference a name this does not scan, so a single hit is reported as a
        # question, not a verdict.
        if uses <= 1:
            out.append(_finding(
                "dead-guard", name, f"{path}:{line}", "",
                f"`{name}` is defined and its name appears nowhere else in "
                f"this tree — if nothing calls it, everything it was meant to "
                f"protect is unprotected while reading as guarded",
                "high"))
    return out


def phantom_field(ctx: Ctx) -> list[dict]:
    """A model method reading `self.X` where the model has no X.

    The incident: "A price-tolerance check was written against `order.lines` --
    a relation that did not exist on that model -- so it silently returned
    nothing on every call. Two tenant-tunable thresholds sat in the settings
    screen guarding code that could never run, and the presence of the knobs
    made the feature look MORE real, not less."

    Restricted to `self.` inside the model's own class, where the receiver's
    type is certain. A service reading `order.lines` is the same defect and is
    not covered here -- inferring that `order` is an Order needs type
    information Python does not carry.
    """
    uses = getattr(ctx, "_uses", [])
    out = []
    seen: set[tuple[str, str]] = set()
    for model, attr, line, path in uses:
        known = ctx.models.get(model)
        if not known or "*" in known:
            continue
        if attr in known or attr in MODEL_BUILTINS or attr.startswith("_"):
            continue
        if attr.endswith(("_set", "_id")) or f"{attr}_set" in known:
            continue          # reverse relations and FK id shortcuts
        if (model, attr) in seen:
            continue
        seen.add((model, attr))
        out.append(_finding(
            "phantom-field", f"{model}.{attr}", f"{path}:{line}", model,
            f"`self.{attr}` is read but `{model}` declares no `{attr}` — if it "
            f"is not inherited, this evaluates to nothing every time and the "
            f"code around it has never run",
            "note"))
    return out



# --------------------------------------------------------------------------
# Page context: what a screen DECLARES about itself vs what its view refuses
# --------------------------------------------------------------------------
#
# The incident: "ne mogu da obrišem na Excel pregledu" was filed as a bug
# against a screen that refuses deletion on purpose. The refusal was in the
# view (a PermissionDenied in the POST branch); the declaration was nowhere,
# so the reporter, the extension's model and the brain's triage all read it as
# a defect. docs/pages/<url_name>.md is where the declaration now lives, and
# these two rules keep it honest: a view that refuses something its page does
# not declare is a declaration that lies by omission, and a screen with no
# file at all is a screen nobody can be told the truth about.

PAGES_DIR = Path("docs") / "pages"
URL_FUNCS = {"path", "re_path", "url"}
POST_METHODS = {"post", "put", "patch", "delete", "form_valid", "perform_create",
                "perform_update", "perform_destroy"}
RENDER_BASES = ("TemplateView", "ListView", "DetailView", "CreateView", "UpdateView",
                "DeleteView", "FormView", "View")


def project_root(root: Path) -> Path | None:
    """The Django project this app belongs to: the nearest ancestor (root
    included) carrying manage.py or docs/pages. None when there is none."""
    for p in [root, *root.parents][:6]:
        if (p / "manage.py").is_file() or (p / PAGES_DIR).is_dir():
            return p
    return None


def _view_symbol(expr) -> str:
    """`views.Foo.as_view()` -> Foo; `views.foo` -> foo; `Foo.as_view()` -> Foo."""
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) \
            and expr.func.attr == "as_view":
        expr = expr.func.value
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Name):
        return expr.id
    return ""


def url_names(project: Path) -> dict[tuple[str, str], str]:
    """{(urls.py directory, view symbol): "app_name:name"} from every urls.py.

    Keyed by the directory too, because `index` exists in half the apps of any
    project and a bare name would map one app's view to another's route."""
    out: dict[tuple[str, str], str] = {}
    for f in py_files(project):
        if f.name != "urls.py":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        app_name = ""
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "app_name" for t in node.targets) \
                    and isinstance(node.value, ast.Constant):
                app_name = str(node.value.value)
        here = str(f.parent).replace("\\", "/")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _name(node.func).split(".")[-1] not in URL_FUNCS:
                continue
            if len(node.args) < 2:
                continue
            name = next((k.value.value for k in node.keywords
                         if k.arg == "name" and isinstance(k.value, ast.Constant)), "")
            sym = _view_symbol(node.args[1])
            if name and sym:
                out.setdefault((here, sym), f"{app_name}:{name}" if app_name else str(name))
    return out


def docs_pages(project: Path) -> dict[str, dict]:
    """{url_name: declaration} from docs/pages/*.md -- the same parser the
    ticket pipeline uses (scripts/tickets/project_context.pages_catalog), so
    the reviewer and the model read one format."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tickets"))
    try:
        import project_context                                      # noqa: PLC0415
        return {p["url_name"]: p for p in project_context.pages_catalog(str(project))}
    except Exception:                                               # noqa: BLE001
        return {}


def url_of(ctx: "Ctx", v: View) -> str:
    d = Path(v.path)
    for base in (d.parent, d.parent.parent):
        u = ctx.urls.get((str(base).replace("\\", "/"), v.name))
        if u:
            return u
    return ""


def _node_of(ctx: "Ctx", v: View):
    src = ctx.sources.get(v.path)
    if not src:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == v.name \
                and node.lineno == v.line:
            return node
    return None


def _is_post_test(test) -> bool:
    """`request.method == "POST"` / `in ("POST", ...)` / `.method != "GET"`-ish."""
    src = ast.unparse(test) if hasattr(ast, "unparse") else ""
    return ".method" in src and ("POST" in src or "PUT" in src or "DELETE" in src)


def _refusals_in(body) -> list[tuple[str, int]]:
    out = []
    for node in body:
        for n in ast.walk(node):
            if isinstance(n, ast.Raise) and n.exc is not None:
                nm = _name(n.exc.func if isinstance(n.exc, ast.Call) else n.exc).split(".")[-1]
                if nm == "PermissionDenied":
                    out.append(("raise PermissionDenied", n.lineno))
            elif isinstance(n, ast.Call):
                fn = _name(n.func)
                if fn.split(".")[-1] == "HttpResponseForbidden":
                    out.append(("HttpResponseForbidden", n.lineno))
                elif fn.endswith("messages.error"):
                    out.append(("messages.error", n.lineno))
                elif any(k.arg == "status" and isinstance(k.value, ast.Constant)
                         and k.value.value == 403 for k in n.keywords):
                    out.append(("status=403", n.lineno))
    return out


def refusals(node) -> list[tuple[str, int]]:
    """Where a view says NO to an action: in its POST-side code only, because a
    GET-side PermissionDenied is access control (craft-security's business),
    while a refusal of an action the screen offers is what a page must declare."""
    if isinstance(node, ast.ClassDef):
        bodies = [m.body for m in node.body
                  if isinstance(m, ast.FunctionDef) and m.name in POST_METHODS]
        return [r for b in bodies for r in _refusals_in(b)]
    if isinstance(node, ast.FunctionDef):
        decs = {_name(d).split(".")[-1] for d in node.decorator_list}
        if "require_POST" in decs or "require_http_methods" in decs:
            return _refusals_in(node.body)
        out = []
        for n in ast.walk(node):
            if isinstance(n, ast.If) and _is_post_test(n.test):
                out += _refusals_in(n.body)
        return out
    return []


def _renders(node, src: str) -> bool:
    if isinstance(node, ast.ClassDef):
        bases = {_name(b).split(".")[-1] for b in node.bases}
        if any(st for st in node.body if isinstance(st, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "template_name" for t in st.targets)):
            return True
        return any(b.endswith(RENDER_BASES) and b != "View" for b in bases)
    seg = ast.get_source_segment(src, node) or ""
    return "render(" in seg


def undeclared_limit(ctx: "Ctx") -> list[dict]:
    """A view refuses an action its docs/pages file does not declare."""
    if not ctx.pages:
        return []
    out = []
    for v in ctx.views:
        url = url_of(ctx, v)
        page = ctx.pages.get(url) if url else None
        if not page:
            continue
        node = _node_of(ctx, v)
        refs = refusals(node) if node is not None else []
        if refs and not page["limits"]:
            kinds = ", ".join(sorted({f"{k} (line {ln})" for k, ln in refs}))
            out.append(_finding(
                "undeclared-limit", v.name, v.where, v.model,
                f"refuses an action -- {kinds} -- but {PAGES_DIR.as_posix()}/{page['file']} "
                f"declares no `limits:` entry, so the reporter, the extension and the "
                f"triage will all read that refusal as a bug. Add `limits: [{{what, why, "
                f"since}}]` for what this screen refuses on purpose, or remove the refusal",
                "note", url_name=url, page_file=page["file"]))
    return out


def adopted_apps(ctx: "Ctx") -> set[str]:
    """URL namespaces that have at least one docs/pages file. The first sweep
    of a 729-view project reported 322 screens without a file, most of them in
    apps nobody had started documenting -- a flood that teaches the reader to
    skip the report. An app that has written one page has set the expectation
    for its others; an app that has written none has not, and is left alone."""
    return {u.split(":")[0] for u in ctx.pages if ":" in u}


def no_page_context(ctx: "Ctx") -> list[dict]:
    """A rendering view with a url_name and no docs/pages file, in an app that
    has adopted docs/pages (see adopted_apps)."""
    if ctx.pages_dir is None:
        return []
    apps = adopted_apps(ctx)
    out = []
    for v in ctx.views:
        url = url_of(ctx, v)
        if not url or url in ctx.pages or url.split(":")[0] not in apps:
            continue
        node = _node_of(ctx, v)
        if node is None or not _renders(node, ctx.sources.get(v.path, "")):
            continue
        out.append(_finding(
            "no-page-context", v.name, v.where, v.model,
            f"renders `{url}` and {PAGES_DIR.as_posix()}/{url.replace(':', '-')}.md does not "
            f"exist -- nothing tells the reporter or the extension what this screen "
            f"allows, refuses, requires or depends on. Write it (frontmatter: url_name, "
            f"kind, requires, feature, limits) while the answers are in your head",
            "note", url_name=url))
    return out


RULES = {
    "sibling-drift": (
        sibling_drift,
        "one endpoint on a model lacks a guard every sibling has",
        "craft-security rule 2. Two principal-grade audits walked past exactly "
        "this while catching four seeded defects. The POST/action endpoints, "
        "which have no template and nobody looks at, are the ones forgotten. "
        "Found an unauthenticated cross-tenant CSV export on its first run."),
    "unguarded": (
        unguarded,
        "a view with no authorization at all, in an app that guards its others",
        "The same incident, at its coarsest. Reported only where the "
        "surrounding code establishes the expectation, so a login page is "
        "never a finding."),
    "get-writes": (
        get_writes,
        "a write reachable by GET",
        "craft-security rule 3. CSRF does not cover safe methods, so a write "
        "behind ?archive=1 executes as whichever victim loads an image tag "
        "pointing at it."),
    "scope-drift": (
        scope_drift,
        "a view that never narrows by the scope column while its siblings do",
        "craft-security rule 1: filter by scope FIRST, in every query. The "
        "scope column is derived from the models, not configured -- it is "
        "firmaid, firm, firma and organization across four projects here."),
    "unscoped-lookup": (
        unscoped_lookup,
        "get_object_or_404 by id with no scope condition anywhere in the view",
        "craft-security rule 2 at the query rather than the guard. Every "
        "scoped id the request names is an authorization surface, and only "
        "the first one looks like one."),
    "dead-guard": (
        dead_guard,
        "an authorization helper nothing calls",
        "craft-security: a guard with zero call sites is worse than no guard, "
        "because the codebase reads as protected. The correct helper existed "
        "and was called from nowhere in the repository."),
    "phantom-field": (
        phantom_field,
        "a model method reads a field the model does not declare",
        "A price check written against `order.lines`, a relation that did not "
        "exist, returned nothing on every call -- and two tenant-tunable "
        "thresholds in the settings screen made the dead feature look more "
        "real, not less."),
    "undeclared-limit": (
        undeclared_limit,
        "a view refuses an action its docs/pages file does not declare",
        "'Ne mogu da obrišem na Excel pregledu' was filed as a bug against a "
        "screen that refuses deletion on purpose. The refusal was in the view's "
        "POST branch; the declaration was nowhere, so the reporter, the "
        "extension's model and the triage all read it as a defect. A page "
        "file without the limit is a declaration that lies by omission."),
    "no-page-context": (
        no_page_context,
        "a rendering view with a route and no docs/pages file",
        "Same incident, one step earlier: a screen with no declaration is a "
        "screen nobody can be told the truth about -- the extension reports "
        "it as NEMA KONTEKST FAJL and the brain counts it. Only where the "
        "project has adopted docs/pages/; a note, never a defect."),
}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if "--rules" in sys.argv:
        for name, (_fn, what, why) in RULES.items():
            print(f"\n  {name}\n    {what}\n    WHY: {why}")
        return 0
    if not args:
        print(__doc__.strip())
        return 1

    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"  not a directory: {root}")
        return 1

    only = sys.argv[sys.argv.index("--rule") + 1] \
        if "--rule" in sys.argv and sys.argv.index("--rule") + 1 < len(sys.argv) else None
    if only and only not in RULES:
        print(f"  no such rule: {only}\n  known: {', '.join(RULES)}")
        return 1

    ctx = build(root)
    findings: list[dict] = []
    for name, (fn, _what, _why) in RULES.items():
        if only and name != only:
            continue
        try:
            findings += fn(ctx)
        except Exception as exc:                                # noqa: BLE001
            ctx.notes.append(f"rule {name} failed: {type(exc).__name__}: {exc}")

    no_model = [v for v in ctx.views if not v.model]
    findings.sort(key=lambda f: (f["severity"] != "high", f["rule"], f["where"]))

    if "--json" in sys.argv:
        print(json.dumps({"root": str(root), "views": len(ctx.views),
                          "models": len(ctx.models), "scope": ctx.scope,
                          "unresolved_model": len(no_model),
                          "notes": ctx.notes,
                          "findings": findings}, indent=2, ensure_ascii=False))
        return 0

    print("=" * 74)
    print(f"REVIEW — {root.name}")
    print("=" * 74)
    print(f"  {len(ctx.views)} view(s), {len(ctx.models)} model(s), "
          f"{len(findings)} finding(s)\n")

    # no-page-context is one fact per FILE ("these screens have no page"),
    # not one per view; printed per view it drowns the defects above it.
    npc: dict[str, list[dict]] = {}
    for f in findings:
        if f["rule"] == "no-page-context":
            npc.setdefault(f["where"].rsplit(":", 1)[0], []).append(f)
    for path, fs in sorted(npc.items()):
        names = [f.get("url_name", f["view"]) for f in fs]
        print(f"   ! [no-page-context] {len(fs)} screen(s) with no docs/pages file")
        print(f"        {path}")
        for line in _wrap(", ".join(names[:8]) + (f", +{len(names) - 8} more" if len(names) > 8 else ""), 66):
            print(f"        {line}")
        print()
    for f in findings:
        if f["rule"] == "no-page-context":
            continue
        flag = "!!" if f["severity"] == "high" else " !"
        head = f"{f['view']}" + (f"  ({f['model']})" if f["model"] else "")
        print(f"  {flag} [{f['rule']}] {head}")
        print(f"        {f['where']}")
        for line in _wrap(f["message"], 66):
            print(f"        {line}")
        print()

    print("-" * 74)
    for n in ctx.notes:
        print(f"  {n}")
    print(f"  Could not determine the model for {len(no_model)} of "
          f"{len(ctx.views)} views; those were not compared to anything.")
    print("  Silence here is NOT proof of safety. Django resolves guards at "
          "runtime\n  through getattr, **kwargs and dynamically applied "
          "decorators, none of\n  which this can see. It reports shapes it can "
          "establish and nothing else.")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
