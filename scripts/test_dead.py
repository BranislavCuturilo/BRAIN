#!/usr/bin/env python3
"""Proof that the finder spares code that is alive somewhere ELSE.

The dangerous failure has a name and the operator gave it: app1 has a function
that serves app2 and nothing in app1. Narrow the search to app1 and it reads as
dead — and deleting it breaks app2. Everything here exists to pin that the
search scope never narrows with the delete scope.

The second half matters just as much: a finder that reports nothing is safe and
useless. So it must also actually FIND the one function nothing reaches.

  python scripts/test_dead.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dead                                                      # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def build(root: Path) -> None:
    (root / "app1").mkdir(parents=True)
    (root / "app2").mkdir(parents=True)
    (root / "templates" / "app1").mkdir(parents=True)
    (root / "app1" / "views.py").write_text(
        "def helper_for_app2(x):\n"
        "    return x * 2\n"
        "\n"
        "def truly_dead(y):\n"
        "    return y\n"
        "\n"
        "def reached_by_template():\n"
        "    return 'hi'\n"
        "\n"
        "def _local_helper():\n"
        "    return 1\n"
        "\n"
        "def uses_local():\n"
        "    return _local_helper()\n"
        "\n"
        "def get_queryset(self):\n"          # framework-called, must be filtered
        "    return None\n",
        encoding="utf-8")
    (root / "app2" / "services.py").write_text(
        "from app1.views import helper_for_app2\n"
        "def run(v):\n"
        "    return helper_for_app2(v)\n", encoding="utf-8")
    (root / "templates" / "app1" / "page.html").write_text(
        "<a href=\"{% url 'reached_by_template' %}\">go</a>\n", encoding="utf-8")
    (root / "app1" / "urls.py").write_text(
        "urlpatterns = []\n", encoding="utf-8")


    (root / "app1" / "admin.py").write_text(
        "from django.contrib import admin" + chr(10)
        + "from django.db import models" + chr(10) + chr(10)
        + "class ThingAdmin(admin.ModelAdmin):" + chr(10)
        + "    list_display = ('id',)" + chr(10) + chr(10)
        + "class ThingInline(admin.TabularInline):" + chr(10)
        + "    model = None" + chr(10), encoding="utf-8")
    (root / "app1" / "apps.py").write_text(
        "from django.apps import AppConfig" + chr(10) + chr(10)
        + "class App1Config(AppConfig):" + chr(10)
        + "    name = 'app1'" + chr(10), encoding="utf-8")
    (root / "app1" / "signals.py").write_text(
        "from django.dispatch import receiver" + chr(10) + chr(10)
        + "@receiver(None)" + chr(10)
        + "def on_thing_saved(sender, **kwargs):" + chr(10)
        + "    return None" + chr(10), encoding="utf-8")
    (root / "app1" / "forms.py").write_text(
        "from django import forms" + chr(10) + chr(10)
        + "class ThingForm(forms.ModelForm):" + chr(10)
        + "    def clean_barcode(self):" + chr(10)
        + "        return self.cleaned_data['barcode']" + chr(10) + chr(10)
        + "def clean_barcode_loose(value):" + chr(10)
        + "    return value" + chr(10), encoding="utf-8")
    (root / "app1" / "props.py").write_text(
        "class Holder:" + chr(10)
        + "    @property" + chr(10)
        + "    def forgotten_property(self):" + chr(10)
        + "        return 1" + chr(10), encoding="utf-8")
    (root / "app1" / "tests").mkdir(parents=True, exist_ok=True)
    (root / "app1" / "tests" / "test_thing.py").write_text(
        "class ThingTests:" + chr(10)
        + "    def helper_inside_a_test(self):" + chr(10)
        + "        return 1" + chr(10), encoding="utf-8")


    # A symbol the DOCS name and the code never calls, plus the three shapes
    # that must NOT be mistaken for it.
    (root / "app1" / "ghosts.py").write_text(
        "def documented_but_uncalled():" + chr(10)
        + "    return 1" + chr(10) + chr(10)
        + "def documented_and_called():" + chr(10)
        + "    return 2" + chr(10) + chr(10)
        + "def used_at_home_and_documented():" + chr(10)
        + "    return 3" + chr(10) + chr(10)
        + "def _caller():" + chr(10)
        + "    return used_at_home_and_documented()" + chr(10) + chr(10)
        + "def only_in_our_findings():" + chr(10)
        + "    return 4" + chr(10), encoding="utf-8")
    (root / "app2" / "consumer.py").write_text(
        "from app1.ghosts import documented_and_called" + chr(10)
        + "x = documented_and_called()" + chr(10), encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "ARCHITECTURE.md").write_text(
        "Use documented_but_uncalled for this." + chr(10)
        + "See documented_and_called too." + chr(10)
        + "And used_at_home_and_documented." + chr(10), encoding="utf-8")
    (root / ".claude" / "findings").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "findings" / "2026-01-01-x.md").write_text(
        "only_in_our_findings izgleda mrtvo." + chr(10), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        build(root)
        subprocess.run(["git", "init", "-q", str(root)], capture_output=True)

        r = dead.analyse(root / "app1", root, want_duplicates=False)
        unref = {c["name"] for c in r["unreferenced"]}
        internal = {c["name"] for c in r["internal_only"]}

        # --- THE rule: alive elsewhere is alive ---------------------------
        ck("a function used only by ANOTHER app is not reported",
           "helper_for_app2" not in unref and "helper_for_app2" not in internal)

        # --- and a reference that is only a STRING still counts ------------
        ck("a view reached only from a template by name is not reported",
           "reached_by_template" not in unref)

        # --- but it does find the real thing ------------------------------
        # A finder that reports nothing is safe and worthless; this is the
        # assertion that would go red if the whole thing stopped working.
        ck("the genuinely unreachable function IS reported", "truly_dead" in unref)
        # --- framework-called, set aside WITH a reason -----------------------
        # Measured on acme-audit/audits: 20 of 22 "unreferenced" findings
        # were framework-called classes. A list that is 91% noise is a list
        # nobody reads to the end.
        fw = {c["name"]: c["why"] for c in r["framework_called"]}
        ck("an admin class is framework-called, not unreferenced",
           "ThingAdmin" in fw and "ThingAdmin" not in unref)
        ck("  ...and the inline beside it", "ThingInline" in fw)
        ck("an AppConfig is framework-called", "App1Config" in fw)
        ck("a @receiver is framework-called", "on_thing_saved" in fw)
        ck("a test class is framework-called (the runner finds it)",
           "ThingTests" in fw and "ThingTests" not in unref)
        ck("a helper INSIDE a test file goes with it",
           "helper_inside_a_test" in fw)
        ck("every one carries a stated reason",
           all(v.strip() for v in fw.values()), )
        ck("the reason names the base class it matched",
           "ModelAdmin" in fw.get("ThingAdmin", ""))

        # --- the naming convention, and its limit ---------------------------
        # clean_<field> is called by Django through getattr, but ONLY on a
        # form. The same name at module level is not excused by anything.
        ck("clean_<field> on a ModelForm is framework-called",
           "clean_barcode" in fw)
        ck("  ...and says why", "clean_" in fw.get("clean_barcode", ""))
        ck("a module-level clean_* is NOT excused -- it stays a candidate",
           "clean_barcode_loose" in unref)

        # --- what must NOT be excused ---------------------------------------
        # @property is a language feature, not a framework calling you: a
        # property nothing reaches is exactly the finding this tool is for.
        ck("an unreached @property is still reported",
           "forgotten_property" in unref and "forgotten_property" not in fw)
        ck("the real dead function survived every filter", "truly_dead" in unref)
        # --- ghost: named in prose, called by nothing -------------------------
        # Measured: validate_no_scheduling_conflict in acme-audit reads as
        # alive because ARCHITECTURE.md cites it as the exemplary pattern,
        # while nothing calls it. Documentation that promises a check the code
        # does not run is worse than no documentation -- somebody follows it.
        gh = {c["name"]: c for c in r["ghost"]}
        ck("a symbol only the docs name is a GHOST", "documented_but_uncalled" in gh)
        ck("  ...and it is not in UNREFERENCED", "documented_but_uncalled" not in unref)
        ck("  ...and the doc that names it is quoted",
           gh.get("documented_but_uncalled", {}).get("mentioned_in") == ["ARCHITECTURE.md"])
        ck("a symbol the docs name AND code calls is alive, not a ghost",
           "documented_and_called" not in gh and "documented_and_called" not in unref)
        ck("a symbol used inside its own file is INTERNAL-ONLY, never a ghost",
           "used_at_home_and_documented" not in gh
           and "used_at_home_and_documented" in {c["name"] for c in r["internal_only"]})

        # Our OWN output must not move a symbol out of the candidate list. The
        # .map incident, repeated by our own hand: writing a finding names the
        # symbol, and counting that mention would hide it the moment it was
        # written down.
        ck("a mention in .claude/findings/ does not count as a reference",
           "only_in_our_findings" in unref and "only_in_our_findings" not in gh)



        # --- classification -----------------------------------------------
        ck("a helper used inside its own file is INTERNAL-ONLY, not unreferenced",
           "_local_helper" in internal and "_local_helper" not in unref)
        ck("its caller, which nothing outside reaches, is unreferenced",
           "uses_local" in unref)

        # --- framework hooks are never candidates -------------------------
        # Reporting these trains the reader to skim, and the one real finding
        # goes past with them.
        ck("a framework-called method is filtered out",
           "get_queryset" not in unref and "get_queryset" not in internal)

        # --- the search really did cover the whole repo --------------------
        ck("the search covered files outside the delete scope",
           r["searched_files"] >= 4)
        ck("the report names both scopes so a reader can check them",
           str(root) in r["repo"] and "app1" in r["scope"])

        # --- the guard: a narrowed search is REFUSED -----------------------
        proc = subprocess.run(
            [sys.executable, str(HERE / "dead.py"),
             "--scope", str(root / "app1"), "--repo", str(root / "app1")],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        # scope == repo is legal (a one-app repo); what must be refused is a
        # scope that sits OUTSIDE the search root.
        outside = subprocess.run(
            [sys.executable, str(HERE / "dead.py"),
             "--scope", str(root / "app1"), "--repo", str(root / "app2")],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        ck("a scope outside the search root is REFUSED, not run",
           outside.returncode == 2 and "REFUSED" in outside.stdout)
        ck("the refusal explains the defect it is preventing",
           "another app" in outside.stdout or "whole repository" in outside.stdout)
        ck("a legitimate single-app repo still runs", proc.returncode == 0)

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s)")
        return 1
    print("ok - spares what another app uses, finds what nothing reaches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
