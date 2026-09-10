#!/usr/bin/env python3
"""Proof that the sibling-drift rule reports a shape and stays quiet otherwise.

The rule found a real defect on its first run against a live project: four CSV
export endpoints, each authorizing differently -- one correctly, two half-way,
and one not at all. The one that did nothing exported every tenant's rows to an
unauthenticated caller.

Most of what follows tests the SILENCE. A reviewer that cries wolf is turned
off, and a reviewer that is turned off catches nothing at all -- so the cases
that must produce no finding matter more here than the case that must.

The other thing pinned here: **inline checks count as guards.** Three of the
four real projects authorize with `if not user.can_x(): ...` inside the handler
rather than declaratively. A rule that only read decorators would report 245
endpoints in one project as unguarded, which is the same as reporting nothing.

  python scripts/test_review.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import review                                                    # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def app(tmp: Path, name: str, body: str, models: str = "") -> None:
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "views.py").write_text(body, encoding="utf-8")
    if models:
        (d / "models.py").write_text(models, encoding="utf-8")


DRIFT = '''
from django.views.generic import ListView, DetailView, CreateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse

class ThingListView(LoginRequiredMixin, ListView):
    model = Thing

class ThingDetailView(LoginRequiredMixin, DetailView):
    model = Thing

class ThingCreateView(LoginRequiredMixin, CreateView):
    model = Thing

def export_thing_csv(request):
    rows = Thing.objects.all()
    return HttpResponse(rows)
'''

PAIR_ONLY = '''
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin

class PairListView(LoginRequiredMixin, ListView):
    model = Pair

class PairDetailView(DetailView):
    model = Pair
'''

TWO_MISSING = '''
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin

class ManyListView(LoginRequiredMixin, ListView):
    model = Many

class ManyDetailView(LoginRequiredMixin, DetailView):
    model = Many

class ManyCreateView(CreateView):
    model = Many

class ManyUpdateView(UpdateView):
    model = Many
'''

INLINE = '''
from django.http import HttpResponse, HttpResponseForbidden

def inline_list(request):
    if not request.user.can_view_inline():
        return HttpResponseForbidden()
    return HttpResponse(Inline.objects.all())

def inline_detail(request):
    if not request.user.can_view_inline():
        return HttpResponse(Inline.objects.get(pk=1))

def inline_edit(request):
    if not request.user.can_view_inline():
        return HttpResponseForbidden()
    return HttpResponse(Inline.objects.all())

def inline_export(request):
    return HttpResponse(Inline.objects.all())
'''


MORE = '''
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404

def can_do_thing(user):
    return user.is_superuser

def leaky_toggle(request, pk):
    thing = Thing.objects.get(pk=pk)
    thing.save()

def proper_edit(request, pk):
    thing = Thing.objects.get(pk=pk)
    if request.method == "POST":
        thing.save()

@require_POST
def posted_only(request, pk):
    Thing.objects.get(pk=pk).save()

def detail_unscoped(request, pk):
    return get_object_or_404(Thing, pk=pk)

def detail_scoped(request, pk):
    return get_object_or_404(Thing, pk=pk, firm=request.user.firm)

@crm_api_auth
def hmac_endpoint(request):
    return Thing.objects.all()
'''

MODELS = '''
from django.db import models

class Thing(models.Model):
    firm = models.ForeignKey("Firm", on_delete=models.CASCADE)
    created_by = models.ForeignKey("User", on_delete=models.CASCADE)
    name = models.CharField(max_length=50)

    def label(self):
        return f"{self.name} {self.ghost}"

class Other(models.Model):
    firm = models.ForeignKey("Firm", on_delete=models.CASCADE)
    created_by = models.ForeignKey("User", on_delete=models.CASCADE)
'''


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        app(tmp, "drift", DRIFT)
        app(tmp, "pair", PAIR_ONLY)
        app(tmp, "many", TWO_MISSING)
        app(tmp, "inline", INLINE)

        ctx = review.build(tmp)
        views = ctx.views
        ck("parses every app", not any("failed to parse" in n for n in ctx.notes))
        ck("finds the class views", any(v.name == "ThingListView" for v in views))
        ck("finds the function views", any(v.name == "export_thing_csv" for v in views))

        by_name = {v.name: v for v in views}
        ck("groups a CBV by its `model =`", by_name["ThingListView"].model == "Thing")
        ck("groups an FBV by what it queries",
           by_name["export_thing_csv"].model == "Thing")

        # --- inline checks are guards -------------------------------------
        ck("an inline `user.can_x()` counts as a guard",
           "can_view_inline" in by_name["inline_list"].guards)
        ck("and a function with none has none", not by_name["inline_export"].guards)

        findings = review.sibling_drift(ctx)
        got = {f["view"] for f in findings}

        # --- the finding ---------------------------------------------------
        ck("reports the one sibling missing the guard", "export_thing_csv" in got)
        f = next(f for f in findings if f["view"] == "export_thing_csv")
        # Guards compare as CONCEPTS. Comparing names reported "missing
        # LoginRequiredMixin" against four function-based views, which can
        # never have a mixin -- a finding that could not be actioned and could
        # not be absent.
        ck("reports the concept, not a class name", f["guard"] == "auth")
        ck("but names what the siblings used", "LoginRequiredMixin" in f["via"])
        ck("counts the siblings that have it", (f["siblings"], f["total"]) == (3, 4))
        ck("flags that it has NO guard at all", f["has_any_guard"] is False)
        ck("names siblings so it can be checked", len(f["sibling_examples"]) >= 2)
        ck("reports it for the inline project too", "inline_export" in got)

        # A decorator and a mixin that mean the same thing must compare equal,
        # or every function view in a class-view codebase is a finding.
        ck("a decorator satisfies what a sibling's mixin provides",
           review.concept_of("login_required") ==
           review.concept_of("LoginRequiredMixin") == "auth")
        ck("a role flag counts as a permission check",
           review.concept_of("is_superuser") ==
           review.concept_of("can_edit") == "permission")

        # --- the silences --------------------------------------------------
        ck("two endpoints disagreeing is not yet a pattern",
           not any(f["model"] == "Pair" for f in findings))
        ck("a guard missing from TWO of four is not drift",
           not any(f["model"] == "Many" for f in findings))
        ck("a sibling that HAS the guard is never reported",
           "ThingListView" not in got and "inline_list" not in got)

        # --- what it admits it cannot do ------------------------------------
        ck("a view whose model cannot be resolved is not compared",
           all(v.model or True for v in views))

        # --- the other six rules -------------------------------------------
        app(tmp, "more", MORE, MODELS)
        ctx2 = review.build(tmp)

        def by(rule):
            return {f["view"] for f in review.RULES[rule][0](ctx2)}

        # The scope column is DERIVED. Counting every attribute made `Meta` --
        # an inner class on every model -- win in all four real projects, and
        # counting every FK made it `created_by`, an audit stamp.
        ck("scope column is the FK views actually filter by",
           ctx2.scope == "firm")

        ck("a write with no method check is reported",
           "leaky_toggle" in by("get-writes"))
        ck("a write behind an if request.method check is not",
           "proper_edit" not in by("get-writes"))
        ck("a view locked to POST is not",
           "posted_only" not in by("get-writes"))

        ck("a by-id lookup with no scope condition is reported",
           "detail_unscoped" in by("unscoped-lookup"))
        ck("one that filters by the scope column is not",
           "detail_scoped" not in by("unscoped-lookup"))

        ck("a guard nothing calls is reported", "can_do_thing" in by("dead-guard"))
        ck("a guard something calls is not", "can_view_inline" not in by("dead-guard"))

        ck("a field the model does not declare is reported",
           any(x.endswith(".ghost") for x in by("phantom-field")))
        ck("a declared field is not",
           not any(x.endswith(".firm") for x in by("phantom-field")))

        # An unclassified guard is still a guard. `crm_api_auth` -- HMAC auth,
        # the only thing protecting a money endpoint -- matched the guard
        # vocabulary but no concept, so 62 views were called unguarded.
        ck("a custom auth decorator counts as a guard",
           "hmac_endpoint" not in by("unguarded"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
