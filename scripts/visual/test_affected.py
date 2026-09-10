#!/usr/bin/env python3
"""Offline tests for affected.py - a synthetic repo, a git-less diff handed in
explicitly, and the two things the customer's screenshot depends on: WHICH pages
changed and WHERE on them to look. No git, no server, no network.
Run: python test_affected.py"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # the engine is a PACKAGE: `visual.*`
from visual import affected as vaffected  # noqa: E402
from visual import pages as vpages  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


BASE_HTML = """<!doctype html>
<html><head><link rel="stylesheet" href="{% static 'css/app.css' %}"></head>
<body><nav class="navbar"><span id="brand">App</span></nav>
{% block content %}{% endblock %}</body></html>
"""

LIST_HTML = """{% extends "base.html" %}
{% load i18n %}
{% block content %}
<div class="card stat-card" id="taskGroups">
  <h2 class="card-title">{% trans "Task groups" %}</h2>
  {% include "myapp/_row.html" %}
</div>
{% endblock %}
"""

DETAIL_HTML = """{% extends "base.html" %}
{% block content %}<div class="detail-wrap"><h1>Detalj</h1></div>{% endblock %}
"""

# The real shape of acme-audit: a segment page extends its own segment base,
# which extends the project base. 16 of 17 segments look like this, so a
# one-level walk from base.html reaches exactly one of them.
SEG_BASE_HTML = """{% extends "base.html" %}
{% block content %}{% include "myapp/_seg_nav.html" %}{% block seg %}{% endblock %}{% endblock %}
"""

SEGMENT_HTML = """{% extends "myapp/base_seg.html" %}
{% block seg %}<div class="seg-body">Sadrzaj segmenta</div>{% endblock %}
"""

SEG_NAV_HTML = """<div class="seg-nav"><a class="seg-link" href="#">Pregled</a></div>
"""

ROW_HTML = """<div class="row-item"><span class="row-name">Ime</span></div>
{% include "myapp/_cell.html" %}
"""

# A partial included BY a partial - two levels below the page.
CELL_HTML = """<span class="cell-value">42</span>
{% include "myapp/_loop.html" %}
"""

# Includes itself: a template graph can carry a cycle, and a fixed point that
# does not guard against one hangs the pre-commit hook instead of failing.
LOOP_HTML = """<span class="loop-mark">x</span>{% include "myapp/_loop.html" %}
"""

# Line 3 sits INSIDE the .stat-card rule: `_css_rule_at` reads the file as it is
# now and the hunk gives the line number, which is exactly the pre-commit case.
CSS = """.stat-card {
  border: 1px solid #ddd;
  background: #fafafa;
}
.detail-wrap {
  padding: 8px;
}
.navbar {
  background: #1b2a4a;
}
@media (max-width: 768px) {
  .stat-card {
    padding: 4px;
  }
}
.cell-value {
  color: #333;
}
"""

JS = """document.getElementById('taskGroups').classList.add('ready');
document.querySelectorAll('.row-item').forEach(function (e) { e.dataset.x = 1; });
"""


def _repo():
    root = Path(tempfile.mkdtemp())
    files = {
        "templates/base.html": BASE_HTML,
        "myapp/templates/myapp/list.html": LIST_HTML,
        "myapp/templates/myapp/detail.html": DETAIL_HTML,
        "myapp/templates/myapp/_row.html": ROW_HTML,
        "myapp/templates/myapp/_cell.html": CELL_HTML,
        "myapp/templates/myapp/_loop.html": LOOP_HTML,
        "myapp/templates/myapp/base_seg.html": SEG_BASE_HTML,
        "myapp/templates/myapp/segment.html": SEGMENT_HTML,
        "myapp/templates/myapp/_seg_nav.html": SEG_NAV_HTML,
        "static/css/app.css": CSS,
        "static/js/app.js": JS,
        "myapp/models.py": "class Thing: pass\n",
    }
    for rel, body in files.items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding="utf-8")
    cfg = {
        "base_url": "http://127.0.0.1:1", "auth": {"kind": "none"},
        "template_globs": ["*/templates/**/*.html", "templates/**/*.html"],
        "static_globs": ["static/**/*.css", "static/**/*.js"],
        "max_pages": 20, "sample_ids": {}, "sample_ids_by_page": {},
        "pages": {"kind": "list", "pages": [
            # the template recorded as a DJANGO NAME, like most of the real map
            {"page_id": "myapp:list", "url": "/list/", "title": "Grupe zadataka",
             "template": "myapp/list.html"},
            {"page_id": "myapp:detail", "url": "/detail/", "title": "Detalj",
             "template": "myapp/detail.html"},
            {"page_id": "myapp:segment", "url": "/segment/", "title": "Segment",
             "template": "myapp/segment.html"},
        ]},
    }
    return root, cfg


def _diff(path, added, start=3):
    head = "--- a/%s\n+++ b/%s\n@@ -%d,0 +%d,%d @@\n" % (path, path, start, start, len(added))
    return head + "".join("+%s\n" % line for line in added)


def test_a_changed_template_finds_its_page_and_its_anchors():
    root, cfg = _repo()
    diff = _diff("myapp/templates/myapp/list.html",
                 ['<p class="card-subtitle" id="grp-sub">{% trans "Podnaslov ispod naziva" %}</p>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("template: the page is found through the django template name",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])
    ck("template: why says what happened", r["pages"][0]["why"] == ["template changed"])
    kinds = {(a["kind"], a["value"]) for a in r["anchors"]}
    ck("anchor: the added id is an anchor", ("id", "grp-sub") in kinds)
    ck("anchor: the trans string is an anchor",
       ("text", "Podnaslov ispod naziva") in kinds)
    ck("anchor: the added class is an anchor", ("class", "card-subtitle") in kinds)
    ck("anchor: every anchor carries its page",
       all(a["page_ids"] == ["myapp:list"] for a in r["anchors"]))
    ck("anchor: the id ranks first (it is what gets boxed)",
       r["anchors"][0]["kind"] == "id")
    ck("source: an explicit diff needs no git", r["source"] == "diff_text")
    ck("provenance: a real hunk marks every anchor as diff-derived",
       all(a["from_diff"] is True for a in r["anchors"]))
    ck("provenance: the file is recorded as a hunk",
       r["provenance"] == {"myapp/templates/myapp/list.html": vaffected.HUNK})


def test_anchors_come_from_the_hunk_not_from_the_file():
    root, cfg = _repo()
    diff = _diff("myapp/templates/myapp/list.html", ['<span id="only-new">Novo</span>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    values = {a["value"] for a in r["anchors"]}
    ck("hunk: the added id is there", "only-new" in values)
    ck("hunk: an unchanged id in the same file is NOT an anchor",
       "taskGroups" not in values)
    ck("hunk: an unchanged trans string is NOT an anchor", "Task groups" not in values)


def test_a_changed_partial_reaches_the_page_that_includes_it():
    root, cfg = _repo()
    diff = _diff("myapp/templates/myapp/_row.html", ['<em class="row-note">Napomena</em>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("include: the including page is affected",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])
    ck("include: why names the partial",
       r["pages"][0]["why"] == ["includes myapp/_row.html"])
    ck("include: the page that does not include it is untouched",
       "myapp:detail" not in {p["page_id"] for p in r["pages"]})


def test_a_changed_layout_reaches_every_page_that_extends_it():
    root, cfg = _repo()
    diff = _diff("templates/base.html", ['<div id="global-banner">Obavestenje</div>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("extends: every page that extends it is affected",
       {p["page_id"] for p in r["pages"]} == {"myapp:list", "myapp:detail",
                                              "myapp:segment"})
    ck("extends: why names the layout",
       all(w == ["includes base.html"] for w in [p["why"] for p in r["pages"]]))


def test_a_layout_two_levels_up_still_reaches_the_page():
    """The finding: a segment page extends `<app>/base_<segment>.html`, which
    extends `base.html`. A one-level walk sees only the pages that name
    base.html themselves - 16 of 17 segments map to nothing."""
    root, cfg = _repo()
    diff = _diff("templates/base.html", ['<div id="global-banner">Obavestenje</div>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("extends x2: the page behind a segment base is reached",
       "myapp:segment" in {p["page_id"] for p in r["pages"]})


def test_a_partial_included_by_a_partial_reaches_the_page():
    root, cfg = _repo()
    diff = _diff("myapp/templates/myapp/_cell.html", ['<em class="cell-note">n</em>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("include x2: the page including the including partial is affected",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])
    ck("include x2: why names the partial",
       r["pages"][0]["why"] == ["includes myapp/_cell.html"])


def test_a_partial_included_by_the_segment_base_reaches_its_pages():
    root, cfg = _repo()
    diff = _diff("myapp/templates/myapp/_seg_nav.html", ['<a class="seg-new">Novo</a>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("include via layout: the segment page is affected",
       [p["page_id"] for p in r["pages"]] == ["myapp:segment"])


def test_a_cycle_in_the_template_graph_terminates():
    """A template that includes itself must not hang the fixed point - the
    pre-commit hook would sit there forever with no output."""
    root, cfg = _repo()
    diff = _diff("myapp/templates/myapp/_loop.html", ['<b class="loop-new">x</b>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("cycle: the walk terminates and still finds the page",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])


def test_a_css_change_maps_by_selector_to_the_templates_using_it():
    root, cfg = _repo()
    diff = _diff("static/css/app.css", ["  background: #fafafa;"], start=3)
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("css: the enclosing rule is the anchor",
       [(a["kind"], a["value"]) for a in r["anchors"]] == [("css_selector", ".stat-card")])
    ck("css: the page carrying that class is affected",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])
    ck("css: why names the class", r["pages"][0]["why"] == ["uses .stat-card"])


def test_a_css_class_used_only_in_the_layout_maps_to_every_page():
    """`.navbar` lives in base.html, not in any page's own template. Searching
    each page's own file for the token answers "no screen uses it", which is how
    a change to the global stylesheet silently mapped to nothing."""
    root, cfg = _repo()
    diff = _diff("static/css/app.css", ["  background: #1b2a4a;"], start=9)
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("css in layout: the enclosing rule is the anchor",
       ("css_selector", ".navbar") in {(a["kind"], a["value"]) for a in r["anchors"]})
    ck("css in layout: every page that renders the layout is affected",
       {p["page_id"] for p in r["pages"]} == {"myapp:list", "myapp:detail",
                                              "myapp:segment"})
    ck("css in layout: nothing is left unlocated", r["unlocated"] == [])


def test_a_property_inside_a_media_block_keeps_its_selector():
    """The @media line is not a selector, so the walk must go OUT of it to the
    rule inside - otherwise the anchor is dropped and the file maps nowhere."""
    root, cfg = _repo()
    diff = _diff("static/css/app.css", ["    padding: 4px;"], start=13)
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("media: the inner selector is the anchor, not the @media line",
       [(a["kind"], a["value"]) for a in r["anchors"]] == [("css_selector", ".stat-card")])
    ck("media: the page carrying that class is affected",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])


def test_a_css_class_used_only_in_a_partial_maps_to_the_page():
    root, cfg = _repo()
    diff = _diff("static/css/app.css", ["  color: #333;"], start=17)
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("css in partial: the page that includes the partial is affected",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])
    ck("css in partial: why names the class", r["pages"][0]["why"] == ["uses .cell-value"])


def test_a_js_selector_used_only_in_a_partial_maps_to_the_page():
    root, cfg = _repo()
    diff = _diff("static/js/app.js",
                 ["document.querySelectorAll('.cell-value').forEach(function (e) {});"])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("js in partial: the page that includes the partial is affected",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])


def test_a_css_rule_nobody_uses_is_unlocated_not_guessed():
    root, cfg = _repo()
    diff = _diff("static/css/app.css", [".brand-new-thing { color: red; }"], start=9)
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("css: no page is invented", r["pages"] == [])
    ck("css: the file is reported as unlocated",
       [u["file"] for u in r["unlocated"]] == ["static/css/app.css"])
    ck("css: the unlocated entry still carries the anchor",
       r["unlocated"][0]["anchors"][0]["value"] == ".brand-new-thing")


def test_a_js_change_maps_by_the_selectors_it_names():
    root, cfg = _repo()
    diff = _diff("static/js/app.js",
                 ["document.getElementById('taskGroups').hidden = false;"])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("js: the element it touches decides the page",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])
    ck("js: the id is the anchor",
       ("id", "taskGroups") in {(a["kind"], a["value"]) for a in r["anchors"]})


def test_non_visual_files_are_ignored():
    root, cfg = _repo()
    diff = _diff("myapp/models.py", ["    field = 1"])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("scope: a python file is not a visual change", r["pages"] == [])
    ck("scope: it is not even considered", r["considered"] == [])
    ck("scope: and it is not reported as unlocated", r["unlocated"] == [])


def test_glob_matching_means_what_the_operator_wrote():
    root, cfg = _repo()
    ck("glob: */templates/**/*.html matches a nested app template",
       vaffected.classify("myapp/templates/myapp/list.html", cfg) == "template")
    ck("glob: templates/**/*.html matches the project layout at depth 1",
       vaffected.classify("templates/base.html", cfg) == "template")
    ck("glob: a css file under static is css",
       vaffected.classify("static/css/app.css", cfg) == "css")
    ck("glob: a js file under static is js",
       vaffected.classify("static/js/app.js", cfg) == "js")
    ck("glob: a template outside the globs is not visual",
       vaffected.classify("docs/example.html", cfg) == "")


def test_utility_classes_never_outrank_a_real_anchor():
    root, cfg = _repo()
    diff = _diff("myapp/templates/myapp/detail.html",
                 ['<div class="mb-3 d-flex detail-note" id="note-box">Tekst ovde</div>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ordered = [(a["kind"], a["value"]) for a in r["anchors"]]
    ck("rank: the id is first", ordered[0] == ("id", "note-box"))
    ck("rank: a bootstrap utility class is last",
       ordered[-1][1] in ("mb-3", "d-flex"))
    ck("rank: the meaningful class beats the utility ones",
       ordered.index(("class", "detail-note")) < ordered.index(("class", "mb-3")))


def test_the_page_cap_reports_the_overflow():
    root, cfg = _repo()
    cfg["max_pages"] = 1
    diff = _diff("templates/base.html", ['<div id="global-banner">x</div>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    ck("cap: only max_pages are captured", len(r["pages"]) == 1)
    ck("cap: the rest are named in overflow", len(r["overflow"]) == 2)
    ck("cap: an anchor never points at a page we did not capture",
       all(set(a["page_ids"]) <= {r["pages"][0]["page_id"]} for a in r["anchors"]))


def test_the_unified_diff_parser_is_the_one_parser():
    text = ("diff --git a/x.html b/x.html\n--- a/x.html\n+++ b/x.html\n"
            "@@ -4,2 +4,3 @@\n context\n+added one\n-removed\n+added two\n")
    got = vaffected.parse_unified_diff(text)
    ck("parser: only added lines are returned",
       [t for _, t in got["x.html"]] == ["added one", "added two"])
    ck("parser: line numbers follow the hunk header",
       [n for n, _ in got["x.html"]] == [5, 6])
    ck("parser: a deleted file is not a path", "/dev/null" not in got)


def test_a_file_git_cannot_diff_is_measurable_but_never_boxable():
    """The DEMO#05513 mechanism, at its source.

    With no hunk - the change is already committed, the edit is not made yet,
    the file is untracked, there is no git - `changed_files` returns the WHOLE
    FILE as "added lines". Those anchors name elements nobody touched: the
    longest untouched string then won the ranking and got the customer's box,
    on a screenshot whose two halves were identical.
    """
    root, cfg = _repo()                     # a temp dir, deliberately not a git repo
    rel = "myapp/templates/myapp/list.html"
    added, source, provenance = vaffected.changed_files(root, files=[rel])
    ck("provenance: no hunk means the whole file, said PER FILE",
       provenance == {rel: vaffected.WHOLE_FILE})
    ck("provenance: the summary string still says so", "whole_file" in source)
    ck("provenance: and the whole file did come back as added lines",
       len(added[rel]) >= 5)

    r = vaffected.from_diff(root, files=[rel], config=cfg)
    values = {a["value"] for a in r["anchors"]}
    ck("whole file: the page is still affected",
       [p["page_id"] for p in r["pages"]] == ["myapp:list"])
    ck("whole file: an untouched id is still an anchor (worth measuring)",
       "taskGroups" in values)
    ck("whole file: nothing from it claims to be diff-derived",
       all(a["from_diff"] is False for a in r["anchors"]))
    ck("whole file: and the page it reaches is still recorded on each anchor",
       all(a["page_ids"] == ["myapp:list"] for a in r["anchors"]))


def test_a_translated_string_anchor_carries_what_the_page_renders():
    """THE ROOT CAUSE of every "ikonice i nebuloze" report.

    `{% trans "Postal code" %}` yields the anchor `Postal code` - the MSGID. The
    page renders `Postanski broj`, so that anchor can never match the DOM. What
    was left were the classes on the same row (`hr-info-row`, which every other
    row carries) and material-icon ligatures, which are the only text in a
    translated template gettext does not touch. Hence icons in boxes and six
    untouched rows outlined.

    Every project this brain serves is translated, so this is the normal path,
    not an edge case.
    """
    root, cfg = _repo()
    po = Path(root) / "locale" / "sr_Latn" / "LC_MESSAGES" / "django.po"
    po.parent.mkdir(parents=True, exist_ok=True)
    po.write_text(chr(10).join([
        'msgid ""', 'msgstr ""', '',
        'msgid "Postal code"', 'msgstr "Postanski broj"', '',
        'msgid "Task groups"', 'msgstr "Grupe zadataka"', '']),
        encoding="utf-8")
    vaffected.vcatalog._CACHE.clear()

    diff = _diff("myapp/templates/myapp/list.html",
                 ['<h3 class="row-label">{% trans "Postal code" %}</h3>'])
    r = vaffected.from_diff(root, diff_text=diff, config=cfg)
    text = [a for a in r["anchors"] if a["kind"] == "text"]
    ck("i18n: the msgid is still the anchor value",
       any(a["value"] == "Postal code" for a in text))
    ck("i18n: and it carries what the page will actually say",
       any(a.get("alternates") == ["Postanski broj"] for a in text))
    ck("i18n: a string with no translation carries no alternates",
       all(not a.get("alternates") for a in r["anchors"] if a["kind"] == "class"))
    vaffected.vcatalog._CACHE.clear()


def test_the_catalogue_is_read_from_where_django_puts_it():
    """`.po` when it is newer than the `.mo`: a string translated today and not
    compiled yet is exactly the string somebody is editing templates for, and
    this repo was in that state when the rule was written."""
    from visual import catalog as vcatalog
    root = Path(tempfile.mkdtemp())
    app = root / "myapp" / "locale" / "de" / "LC_MESSAGES"
    app.mkdir(parents=True)
    (app / "django.po").write_text(chr(10).join([
        'msgid "Save"', 'msgstr "Speichern"', '',
        'msgid "Long one"', 'msgstr ""', '"Sehr "', '"lang"', '']),
        encoding="utf-8")
    vcatalog._CACHE.clear()
    ck("catalogue: a per-app locale directory is found too",
       vcatalog.translations_of(root, "Save") == ["Speichern"])
    ck("catalogue: multi-line msgstr is joined",
       vcatalog.translations_of(root, "Long one") == ["Sehr lang"])
    ck("catalogue: an unknown msgid has no translation",
       vcatalog.translations_of(root, "Nothing") == [])
    ck("catalogue: a repo with no catalogue at all is not an error",
       vcatalog.translations_of(Path(tempfile.mkdtemp()), "Save") == [])
    vcatalog._CACHE.clear()


def test_a_django_form_field_is_an_anchor():
    """A form field is rendered by the FORM, so the hunk that adds one carries no
    id, no label and no text - only `form-label` and `form-text`, which every
    other field on that page carries too. `{{ form.<name> }}` is the one thing in
    it that names the element, and Django writes it out as
    `name="<name>" id="id_<name>"`.

    Without this, a change to a form could only be pointed at as "somewhere in
    this form", and the four fields a real commit added were one vague crop.
    """
    rows = [(1, '<label for="{{ form.parent.id_for_label }}" class="form-label">'),
            (2, '{{ form.parent }}'),
            (3, '{% if form.parent.help_text %}<div class="form-text">x</div>{% endif %}'),
            (4, '{% for e in form.errors %}{{ e }}{% endfor %}'),
            (5, '{{ form.media }}')]
    got = vaffected._template_anchors(rows)
    fields = [v for k, v in got if k == "field"]
    ck("form field: the field behind the label is an anchor", "parent" in fields)
    ck("form field: it is not repeated once per mention", fields.count("parent") <= 3)
    ck("form field: attributes of the FORM are not fields",
       "errors" not in fields and "media" not in fields)
    ck("form field: it ranks with the specific kinds, above a class",
       vaffected.ANCHOR_RANK["field"] < vaffected.ANCHOR_RANK["class"])


def test_a_clean_working_tree_is_an_answer_not_a_crash():
    """`changed_files(repo)` with nothing to report used to recurse into itself
    forever: the empty file list is falsy, so it fell past the `if files:` branch
    and asked git the same question again. `shoot.py before --repo X` on a
    committed tree died with a RecursionError raised from inside `subprocess`,
    naming nothing at all - and that is the exact command the gate issues when it
    is given no `--files`."""
    import subprocess
    root = Path(tempfile.mkdtemp())
    (root / "myapp" / "templates" / "myapp").mkdir(parents=True, exist_ok=True)
    (root / "myapp" / "templates" / "myapp" / "list.html").write_text(
        LIST_HTML, encoding="utf-8")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    for args in (["init", "-q"],
                 ["-c", "user.email=t@e.com", "-c", "user.name=T", "add", "-A"],
                 ["-c", "user.email=t@e.com", "-c", "user.name=T",
                  "commit", "-q", "-m", "clean"]):
        subprocess.run(["git"] + args, cwd=str(root), capture_output=True,
                       text=True, env=env, timeout=60)
    added, source, provenance = vaffected.changed_files(root)
    ck("clean tree: it returns instead of recursing", added == {})
    ck("clean tree: and says the answer came from git", source == "git")
    ck("clean tree: with no provenance to report", provenance == {})


def test_only_a_hunk_anchor_may_decide_where_to_crop():
    """The crop rectangle comes from an anchor, and an anchor read off a file git
    could not diff names a line nobody touched - that is how an untouched
    material-icon name once got the rectangle on both halves of an identical
    zoom. `anchors_for_page` therefore defaults to hunk anchors ONLY; asking for
    everything is possible but has to be spelled out."""
    result = {"anchors": [
        {"file": "a.html", "kind": "id", "value": "untouched", "from_diff": False,
         "page_ids": ["p"]},
        {"file": "a.html", "kind": "class", "value": "nov", "from_diff": True,
         "page_ids": ["p"]},
        {"file": "a.html", "kind": "id", "value": "drugi-ekran", "from_diff": True,
         "page_ids": ["q"]}]}
    rows = vaffected.anchors_for_page(result, "p")
    ck("for_page: hunk anchors only, by default",
       [r["value"] for r in rows] == ["nov"])
    ck("for_page: another page does not borrow this one's anchors",
       [r["value"] for r in vaffected.anchors_for_page(result, "q")]
       == ["drugi-ekran"])
    ck("for_page: everything measurable is available when asked for explicitly",
       len(vaffected.anchors_for_page(result, "p", only_from_diff=False)) == 2)
    ck("for_page: an unknown page has none",
       vaffected.anchors_for_page(result, "nope") == [])


def test_a_hunk_anchor_outranks_every_whole_file_anchor():
    """`_rank` decides which anchors survive the 40-per-file cap in `from_diff`,
    and therefore which of them get to say a stylesheet reaches this page. A
    whole-file anchor is a line nobody edited, so it ranks behind every anchor
    that came out of a real hunk however good its KIND is."""
    rows = sorted([
        {"file": "a.html", "kind": "id", "value": "untouched-but-a-perfect-anchor",
         "from_diff": False, "page_ids": ["p"]},
        {"file": "a.html", "kind": "class", "value": "nov", "from_diff": True,
         "page_ids": ["p"]},
        {"file": "a.html", "kind": "text", "value": "precision_manufacturing",
         "from_diff": False, "page_ids": ["p"]}], key=vaffected._rank)
    ck("rank: the hunk anchor is first even against a better KIND",
       rows[0]["value"] == "nov")
    ck("rank: an anchor with no flag at all is treated as whole-file",
       sorted([{"file": "a", "kind": "id", "value": "old", "page_ids": ["p"]},
               {"file": "a", "kind": "class", "value": "n", "from_diff": True,
                "page_ids": ["p"]}], key=vaffected._rank)[0]["value"] == "n")


def test_a_media_query_says_which_widths_a_change_can_reach():
    """The condition used to be computed and thrown away.

    `_selector_at` walks OUT of `@media` to find the element a change draws a
    box around, and the condition it walked past was dropped -- so a rule that
    can only affect a phone looked exactly like a rule that affects every
    width. That difference is 429 of 430 screenshots.
    """
    from visual import responsive as vresponsive

    css = (
        ".card { padding: 8px; }\n"
        "@media (max-width: 768px) {\n"
        "  .card { padding: 40px; }\n"
        "}\n"
        "@media screen and (min-width: 992px) {\n"
        "  .sidebar { width: 300px; }\n"
        "}\n")
    stacks = vaffected._css_line_selectors(css)

    ck("a rule outside any media query has no condition",
       vaffected._media_at(stacks, 1) == "")
    ck("a rule inside one reports it",
       "max-width: 768px" in vaffected._media_at(stacks, 3))
    ck("and the SELECTOR is still the element, not the @media line",
       vaffected._selector_at(stacks, 3) == ".card")
    ck("a compound condition is reported whole",
       "min-width: 992px" in vaffected._media_at(stacks, 6))

    # --- boundaries worth rendering ---------------------------------------
    ck("max-width yields the boundary and one past it",
       vaffected.media_widths(["@media (max-width: 768px)"]) == [768, 769])
    ck("min-width yields the boundary and one below",
       vaffected.media_widths(["@media (min-width: 992px)"]) == [991, 992])
    ck("em is converted at the browser default",
       vaffected.media_widths(["@media (max-width: 48em)"]) == [768, 769])
    ck("a condition with no width bound yields none",
       vaffected.media_widths(["@media print"]) == [])

    # --- is the rule even live at this width ------------------------------
    ck("a max-width rule is live below it",
       vaffected.media_matches("@media (max-width: 768px)", 375))
    ck("and dead above it",
       not vaffected.media_matches("@media (max-width: 768px)", 1440))
    ck("a min-width rule is live above it",
       vaffected.media_matches("@media (min-width: 992px)", 1440))
    ck("a bound this cannot evaluate is treated as live",
       vaffected.media_matches("@media print", 375))

    # --- what that means for how many renders happen ----------------------
    ck("no media query means every default width",
       vresponsive.widths_for([]) == list(vresponsive.DEFAULT_WIDTHS))
    ck("a phone-only rule drops the desktop width",
       1440 not in vresponsive.widths_for(["@media (max-width: 768px)"]))
    ck("and keeps the widths it is actually live at",
       375 in vresponsive.widths_for(["@media (max-width: 768px)"]))
    ck("a desktop-only rule drops the phone width",
       375 not in vresponsive.widths_for(["@media (min-width: 992px)"]))
    ck("an unevaluable condition narrows nothing",
       vresponsive.widths_for(["@media print"]) == list(vresponsive.DEFAULT_WIDTHS))


def main() -> int:
    for fn in (test_a_changed_template_finds_its_page_and_its_anchors,
               test_anchors_come_from_the_hunk_not_from_the_file,
               test_a_changed_partial_reaches_the_page_that_includes_it,
               test_a_changed_layout_reaches_every_page_that_extends_it,
               test_a_layout_two_levels_up_still_reaches_the_page,
               test_a_partial_included_by_a_partial_reaches_the_page,
               test_a_partial_included_by_the_segment_base_reaches_its_pages,
               test_a_cycle_in_the_template_graph_terminates,
               test_a_css_change_maps_by_selector_to_the_templates_using_it,
               test_a_css_class_used_only_in_the_layout_maps_to_every_page,
               test_a_property_inside_a_media_block_keeps_its_selector,
               test_a_css_class_used_only_in_a_partial_maps_to_the_page,
               test_a_js_selector_used_only_in_a_partial_maps_to_the_page,
               test_a_css_rule_nobody_uses_is_unlocated_not_guessed,
               test_a_js_change_maps_by_the_selectors_it_names,
               test_non_visual_files_are_ignored,
               test_glob_matching_means_what_the_operator_wrote,
               test_utility_classes_never_outrank_a_real_anchor,
               test_the_page_cap_reports_the_overflow,
               test_the_unified_diff_parser_is_the_one_parser,
               test_a_file_git_cannot_diff_is_measurable_but_never_boxable,
               test_a_hunk_anchor_outranks_every_whole_file_anchor,
               test_a_translated_string_anchor_carries_what_the_page_renders,
               test_the_catalogue_is_read_from_where_django_puts_it,
               test_a_django_form_field_is_an_anchor,
               test_a_clean_working_tree_is_an_answer_not_a_crash,
               test_only_a_hunk_anchor_may_decide_where_to_crop,
               test_a_media_query_says_which_widths_a_change_can_reach):
        try:
            fn()
        except Exception as exc:                                # noqa: BLE001
            ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
