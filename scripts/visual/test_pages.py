#!/usr/bin/env python3
"""Offline tests for pages.py - the inventory over a fixture razvojna-mapa, the
two template shapes the map mixes, and the rule that an uncapturable page is
MARKED, never dropped. Temp dirs only; no server, no network.
Run: python test_pages.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # the engine is a PACKAGE: `visual.*`
from visual import config as vconfig  # noqa: E402
from visual import pages as vpages  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


MAP_APP = {
    "app": "faults",
    "url_prefix": "/faults/",
    "pages": [
        {"id": "faults:hub", "title_sr": "Pregled kvarova", "kind": "dashboard",
         "url": "/faults/", "url_name": "faults:hub",
         "template": "faults/templates/faults/hub.html"},
        {"id": "faults:detail", "title_sr": "Detalj kvara", "kind": "detail",
         "url": "/faults/<int:pk>/", "url_name": "faults:detail",
         "template": "faults/detail.html"},
        {"id": "faults:nearest", "title_sr": "Najbliza lokacija", "kind": "action",
         "url": "/faults/nearest/", "url_name": "faults:nearest",
         "template": "(JSON odgovor - bez templejta)"},
        {"id": "faults:by_loc", "title_sr": "Kvarovi lokacije", "kind": "list",
         "url": "/faults/loc/<location_pk>/list/", "url_name": "faults:by_loc",
         "template": "faults/by_loc.html"},
    ],
}
MAP_NOT_PAGES = {"processes": [{"name": "nesto"}]}


def _repo(sample_ids=None, by_page=None):
    root = Path(tempfile.mkdtemp())
    data = root / "razvojna-mapa" / "_data"
    data.mkdir(parents=True)
    (data / "faults.json").write_text(json.dumps(MAP_APP), encoding="utf-8")
    (data / "processes.json").write_text(json.dumps(MAP_NOT_PAGES), encoding="utf-8")
    (data / "broken.json").write_text("{not json", encoding="utf-8")
    for rel in ("faults/templates/faults/hub.html",
                "faults/templates/faults/detail.html",
                "faults/templates/faults/by_loc.html"):
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text("<h1>x</h1>", encoding="utf-8")
    cfg = {
        "base_url": "http://127.0.0.1:1", "tenant": "t", "role": "r",
        "auth": {"kind": "none"}, "pages": {"kind": "razvojna-mapa",
                                            "path": "razvojna-mapa/_data", "pages": []},
        "sample_ids": sample_ids or {}, "sample_ids_by_page": by_page or {},
    }
    return root, cfg


def test_every_page_is_returned_and_the_unfetchable_ones_are_marked():
    root, cfg = _repo()
    rep = vpages.inventory_report(cfg, root)
    ids = {p["page_id"] for p in rep["pages"]}
    ck("inventory: all four pages are returned", len(rep["pages"]) == 4)
    ck("inventory: nothing is silently dropped",
       ids == {"faults:hub", "faults:detail", "faults:nearest", "faults:by_loc"})
    ck("inventory: only the hub is capturable",
       {p["page_id"] for p in vpages.usable(rep["pages"])} == {"faults:hub"})
    by_id = {p["page_id"]: p for p in rep["pages"]}
    ck("inventory: an unresolved <int:pk> is reported by name",
       by_id["faults:detail"]["skipped_reason"] == "unresolved_params:pk")
    ck("inventory: a named parameter is reported by ITS name",
       by_id["faults:by_loc"]["skipped_reason"] == "unresolved_params:location_pk")
    ck("inventory: prose in the template field is no_template",
       "no_template" in by_id["faults:nearest"]["skipped_reason"])
    ck("inventory: title_sr wins as the title",
       by_id["faults:hub"]["title"] == "Pregled kvarova")


def test_a_map_file_that_is_not_a_page_file_is_reported_not_ignored():
    root, cfg = _repo()
    rep = vpages.inventory_report(cfg, root)
    reasons = {f["file"]: f["reason"] for f in rep["files_skipped"]}
    ck("files: processes.json is reported as not-a-page-file",
       reasons.get("processes.json") == "no_pages_key")
    ck("files: an unreadable map file is reported, not raised",
       "unreadable" in reasons.get("broken.json", ""))
    ck("files: the real page file was read", rep["files_read"] == ["faults.json"])


def test_sample_ids_resolve_the_url_page_first_then_global():
    root, cfg = _repo(sample_ids={"pk": 7, "location_pk": 3},
                      by_page={"faults:detail": {"pk": 42}})
    by_id = {p["page_id"]: p for p in vpages.inventory(cfg, root)}
    ck("sample: the per-page id wins", by_id["faults:detail"]["url"] == "/faults/42/")
    ck("sample: the global id fills the rest",
       by_id["faults:by_loc"]["url"] == "/faults/loc/3/list/")
    ck("sample: a resolved page becomes capturable",
       by_id["faults:detail"]["skipped_reason"] == "")
    ck("sample: the raw url is kept for the report",
       by_id["faults:detail"]["url_raw"] == "/faults/<int:pk>/")


def test_both_template_shapes_resolve_to_the_same_file():
    root, cfg = _repo(sample_ids={"pk": 1, "location_pk": 1})
    by_id = {p["page_id"]: p for p in vpages.inventory(cfg, root)}
    ck("resolve: a repo path stays itself",
       by_id["faults:hub"]["template_path"] == "faults/templates/faults/hub.html")
    ck("resolve: a django template NAME finds its file",
       by_id["faults:detail"]["template_path"] == "faults/templates/faults/detail.html")
    idx = vpages.by_template(vpages.inventory(cfg, root))
    ck("by_template: the changed-file path is a key",
       "faults/templates/faults/detail.html" in idx)
    ck("by_template: the template name is a key too", "faults/detail.html" in idx)
    ck("by_template: both keys reach the same page",
       idx["faults/templates/faults/detail.html"][0]["page_id"] == "faults:detail")


def test_template_tail_is_the_django_name():
    ck("tail: app template dir",
       vpages.template_tail("faults/templates/faults/hub.html") == "faults/hub.html")
    ck("tail: project template dir",
       vpages.template_tail("templates/base.html") == "base.html")
    ck("tail: not a template path", vpages.template_tail("static/css/x.css") == "")


def test_an_explicit_list_works_without_a_map():
    root, cfg = _repo()
    cfg["pages"] = {"kind": "list", "pages": [
        {"page_id": "home", "url": "/", "title": "Pocetna", "template": ""}]}
    rows = vpages.inventory(cfg, root)
    ck("list: the config's own pages are used", len(rows) == 1 and rows[0]["url"] == "/")
    ck("list: a page without a template is still marked",
       rows[0]["skipped_reason"] == "no_template")


def test_the_real_config_module_and_the_inventory_agree():
    root, cfg = _repo()
    (root / ".claude").mkdir()
    (root / ".claude" / "visual-diff.json").write_text(json.dumps({
        "base_url": "http://127.0.0.1:1", "auth": {"kind": "none"},
        "pages": {"kind": "razvojna-mapa", "path": "razvojna-mapa/_data"}}),
        encoding="utf-8")
    loaded = vconfig.load_config(root)
    ck("config: a valid file loads", not vconfig.is_error(loaded))
    ck("config+pages: the loaded config drives the inventory",
       len(vpages.inventory(loaded, root)) == 4)


def test_a_click_path_is_lifted_from_the_map_or_refused_outright():
    """The line a CUSTOMER reads to find a screen that is new.

    Every step must come out of the map. The alternative is not "a slightly
    wrong path" but a customer clicking through a menu that does not exist, so
    everything that does not read as a click path returns [] and the comment
    prints an empty "Do nje:" for the operator to fill in.
    """
    def steps(line):
        return vpages.nav_steps({"reached_from": [line]})

    ck("nav: a plain arrow chain is the path",
       steps("Glavni meni → Eskalacija → Novo pravilo")
       == ["Glavni meni", "Eskalacija", "Novo pravilo"])
    ck("nav: the map's ASCII arrow is the same arrow",
       steps("Glavni meni -> Administracija -> Korisnici")
       == ["Glavni meni", "Administracija", "Korisnici"])
    # `Lista gradova (Podešavanja → Gradovi, deljeni templejt …)`: the label is a
    # SCREEN, the parenthetical is the menu path to it, and the menu path is the
    # part a customer can follow.
    ck("nav: a parenthetical that carries a path replaces the label it qualifies",
       steps("Lista gradova (Podešavanja → Gradovi, deljeni templejt "
             "settings_admin/portal_codelist/list.html) → dugme 'Grupni unos' "
             "(ikona table_view, vidljivo uz can.settings_edit)")
       == ["Podešavanja", "Gradovi", "dugme „Grupni unos“"])
    ck("nav: a developer note in parentheses is dropped, quotes are the customer's",
       steps("Lista korisnika → ikonica 'Izmeni' u redu (samo za admina)")
       == ["Lista korisnika", "ikonica „Izmeni“ u redu"])
    # The refusals. Each of these was a real line in acme-audit's map.
    ck("nav: prose with no arrow is not a path",
       steps("Link iz emaila za reset lozinke") == [])
    ck("nav: a redirect is not something a customer clicks",
       steps("Automatski redirect sa accounts:password_reset posle slanja") == [])
    ck("nav: a technical fragment blanks the WHOLE path, never just its step",
       steps("Glavni meni → /settings/cities/?tab=1") == [])
    ck("nav: a word that merely CONTAINS a technical one is not technical",
       steps("Glavni meni → Faults → Overview")
       == ["Glavni meni", "Faults", "Overview"])
    ck("nav: no map entry is a blank path, not an exception",
       vpages.nav_steps({}) == [] and vpages.nav_steps(None) == [])
    root, cfg = _repo()
    ck("nav: the inventory carries reached_from so the engine can read it",
       all("reached_from" in p for p in vpages.inventory(cfg, root)))


def main() -> int:
    for fn in (test_a_click_path_is_lifted_from_the_map_or_refused_outright,
               test_every_page_is_returned_and_the_unfetchable_ones_are_marked,
               test_a_map_file_that_is_not_a_page_file_is_reported_not_ignored,
               test_sample_ids_resolve_the_url_page_first_then_global,
               test_both_template_shapes_resolve_to_the_same_file,
               test_template_tail_is_the_django_name,
               test_an_explicit_list_works_without_a_map,
               test_the_real_config_module_and_the_inventory_agree):
        try:
            fn()
        except Exception as exc:                                # noqa: BLE001
            ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
