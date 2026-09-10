#!/usr/bin/env python3
"""The two page-context rules report the shape and stay quiet otherwise.

undeclared-limit: a view whose POST side refuses an action while its
docs/pages file declares no `limits:` -- the declaration that lies by
omission. no-page-context: a rendering view with a route and no file.

Most of this is silence: a project without docs/pages gets nothing (and a
note saying why); a GET-side PermissionDenied is access control, not a limit;
a page that declares the limit is not reported; an API view with no template
is not asked for a page.

  python scripts/test_review_pages.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import review  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


VIEWS = '''
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect
from django.views.generic import ListView, DetailView, View


class ItemList(ListView):
    template_name = "items/list.html"

    def post(self, request, *a, **kw):
        # deletion is refused on purpose: items are posted to the ledger
        raise PermissionDenied("brisanje nije dozvoljeno")


class ItemDetail(DetailView):
    template_name = "items/detail.html"

    def post(self, request, *a, **kw):
        messages.error(request, "izmena posle zaključavanja nije moguća")
        return redirect("items:detail", pk=kw["pk"])


def item_create(request):
    if request.method == "POST":
        return HttpResponseForbidden()
    return render(request, "items/create.html")


def item_report(request):
    if not request.user.is_staff:
        raise PermissionDenied          # GET-side: access control, not a limit
    return render(request, "items/report.html")


class ItemApi(View):
    def post(self, request):
        return HttpResponseForbidden()
'''

URLS = '''
from django.urls import path
from . import views

app_name = "items"
urlpatterns = [
    path("", views.ItemList.as_view(), name="list"),
    path("<int:pk>/", views.ItemDetail.as_view(), name="detail"),
    path("new/", views.item_create, name="create"),
    path("report/", views.item_report, name="report"),
    path("api/", views.ItemApi.as_view(), name="api"),
]
'''

PAGE_WITH_LIMIT = '''---
url_name: items:list
kind: list
limits:
  - what: brisanje stavki
    why: stavke su knjižene
    since: DEMO#1
---
## Prikaz
Lista.
'''
PAGE_WITHOUT_LIMIT = '''---
url_name: items:detail
kind: detail
---
## Prikaz
Detalj.
'''
PAGE_CREATE_NO_LIMIT = '''---
url_name: items:create
kind: create
---
## Prikaz
Nova.
'''


def project(tmp: Path, with_pages: bool) -> Path:
    root = tmp / "proj"
    app = root / "items"
    app.mkdir(parents=True)
    (root / "manage.py").write_text("# django\n", encoding="utf-8")
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "views.py").write_text(VIEWS, encoding="utf-8")
    (app / "urls.py").write_text(URLS, encoding="utf-8")
    (app / "models.py").write_text("from django.db import models\nclass Item(models.Model):\n    name = models.CharField(max_length=5)\n", encoding="utf-8")
    other = root / "other"
    other.mkdir()
    (other / "__init__.py").write_text("", encoding="utf-8")
    (other / "views.py").write_text(
        "from django.shortcuts import render\n"
        "def home(request):\n    return render(request, 'other/home.html')\n", encoding="utf-8")
    (other / "urls.py").write_text(
        "from django.urls import path\nfrom . import views\napp_name = 'other'\n"
        "urlpatterns = [path('', views.home, name='home')]\n", encoding="utf-8")
    if with_pages:
        d = root / "docs" / "pages"
        d.mkdir(parents=True)
        (d / "items-list.md").write_text(PAGE_WITH_LIMIT, encoding="utf-8")
        (d / "items-detail.md").write_text(PAGE_WITHOUT_LIMIT, encoding="utf-8")
        (d / "items-create.md").write_text(PAGE_CREATE_NO_LIMIT, encoding="utf-8")
    return root


def by_rule(findings: list[dict], rule: str) -> dict[str, dict]:
    return {f["view"]: f for f in findings if f["rule"] == rule}


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        print("-- a project with docs/pages")
        root = project(tmp, with_pages=True)
        ctx = review.build(root / "items")            # pointed at the APP, like the nudge does
        ck("project root found from the app dir", ctx.project == root)
        ck("routes read from urls.py", len(ctx.urls) == 6)
        ck("three declarations read", set(ctx.pages) == {"items:list", "items:detail", "items:create"})
        ck("url_of maps a class view", review.url_of(ctx, next(v for v in ctx.views if v.name == "ItemList")) == "items:list")
        ck("url_of maps a function view", review.url_of(ctx, next(v for v in ctx.views if v.name == "item_create")) == "items:create")

        ul = by_rule(review.undeclared_limit(ctx), "undeclared-limit")
        ck("ItemList refuses in post but its page declares the limit -> silent", "ItemList" not in ul)
        ck("ItemDetail: messages.error in post, page without limits -> reported", "ItemDetail" in ul)
        ck("  ...naming the page file", ul.get("ItemDetail", {}).get("page_file") == "items-detail.md")
        ck("item_create: HttpResponseForbidden in the POST branch -> reported", "item_create" in ul)
        ck("item_report: GET-side PermissionDenied is access control -> silent", "item_report" not in ul)
        ck("ItemApi: no page at all -> not this rule's business", "ItemApi" not in ul)
        ck("every finding is a note, never a defect", all(f["severity"] == "note" for f in ul.values()))

        npc = by_rule(review.no_page_context(ctx), "no-page-context")
        ck("item_report renders, has a route, no file -> reported", "item_report" in npc)
        ck("  ...naming the file to write", "items-report.md" in npc.get("item_report", {}).get("message", ""))
        ck("ItemApi: bare View, no template -> not asked for a page", "ItemApi" not in npc)
        ctx_all = review.build(root)                  # the whole project, like the sweep
        npc_all = by_rule(review.no_page_context(ctx_all), "no-page-context")
        ck("an app with no page file at all is left alone (adoption per app)", "home" not in npc_all)
        ck("  ...while the adopted app is still reported", "item_report" in npc_all)
        ck("adopted_apps reads the namespaces of the files", review.adopted_apps(ctx_all) == {"items"})
        ck("views with a page are silent", not ({"ItemList", "ItemDetail", "item_create"} & set(npc)))

        print("-- the same app without docs/pages")
        root2 = project(tmp / "two", with_pages=False)
        ctx2 = review.build(root2 / "items")
        ck("no docs/pages -> both rules silent", not review.undeclared_limit(ctx2) and not review.no_page_context(ctx2))
        ck("  ...and the notes say why", any("page-context rules did not run" in n for n in ctx2.notes))

        print("-- refusals()")
        import ast  # noqa: PLC0415
        tree = ast.parse(VIEWS)
        node = {n.name: n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
        ck("class post with raise PermissionDenied", [k for k, _ in review.refusals(node["ItemList"])] == ["raise PermissionDenied"])
        ck("class post with messages.error", [k for k, _ in review.refusals(node["ItemDetail"])] == ["messages.error"])
        ck("function POST branch with HttpResponseForbidden", [k for k, _ in review.refusals(node["item_create"])] == ["HttpResponseForbidden"])
        ck("function with GET-side PermissionDenied only -> none", review.refusals(node["item_report"]) == [])

        print("-- the registry")
        ck("both rules registered with an incident", all(r in review.RULES and len(review.RULES[r][2]) > 80
                                                          for r in ("undeclared-limit", "no-page-context")))

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
