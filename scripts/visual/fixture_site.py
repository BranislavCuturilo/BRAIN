#!/usr/bin/env python3
"""TEST SUPPORT ONLY - a tiny local site the capture tests drive.

Never the real app: the tests must not need a Django server, must not touch the
production schema behind it, and must not depend on anyone being logged in.

The page reproduces the DOM CONTRACT capture.py relies on rather than loading
Bootstrap: a `[data-bs-toggle="modal"]` trigger that puts `.show` on a
`.modal`, a `[data-bs-toggle="dropdown"]` that puts `.show` on the
`.dropdown-menu` and flips `aria-expanded`, and a `<details><summary>`. That is
exactly what Bootstrap 5 does to the DOM, which is all our discovery reads.

It also fires one POST on load, so a test can prove the route guard refuses it,
and it carries a trigger whose modal does not exist - the state that must be
REPORTED as skipped rather than dropped.

`STORAGE_PAGE` is the second fixture: a page that renders differently on the
second visit because it wrote `localStorage`. Two captures of it in one run must
still produce the same signature, which is only true if each state gets a fresh
browser context.

`QUIET_STATE_PAGE` is the third: one trigger that OPENS something the picture
cannot show (the navbar dropdown case - 50 of 54 states in a real run) and one
that changes the whole screen. Only the second may survive deduplication.

`TOOLBAR_PAGE` is the fourth: a page carrying a django-debug-toolbar the way
version 4 ships it - a host element with a DECLARATIVE SHADOW ROOT, which no
page stylesheet can reach into. Hiding the host is the only thing that works,
and `write_toolbar_page(..., toolbar=False)` renders the identical page without
it, so the two pictures can be compared.

`LANDMARK_PAGE` is the fifth, and it carries all three branches of the chrome
rule at once: a dropdown in the site `<nav>` (chrome), a dropdown inside
`<main>` (this screen), and a tab strip wrapped in a `<nav>` that is ITSELF
inside `<main>` - local navigation, which must keep producing states.

Note that `PAGE` deliberately has NO `<main>`: it is the layout the rule cannot
read, and everything under no landmark at all must keep behaving as it did.
"""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = """<!doctype html>
<html lang="sr"><head><meta charset="utf-8"><title>%(title)s</title>
<style>
 body { font-family: sans-serif; margin: 0; background: #fff; color: #111; }
 .navbar { background: #1b2a4a; color: #fff; padding: 12px 20px; }
 .container { padding: 20px; }
 .card { border: 1px solid #ddd; padding: 16px; margin-bottom: 12px; }
 .card-subtitle { color: #667; font-size: 13px; }
 .modal { display: none; position: fixed; inset: 60px; background: #fff;
          border: 2px solid #1b2a4a; padding: 24px; z-index: 2; }
 .modal.show { display: block; }
 /* Bootstrap dims the page behind an open modal (`.modal-backdrop`, black at half
    opacity). Without it the fixture's modal is white on white, which is a picture
    six phash bits from the base shot - i.e. INSIDE the deduplication
    threshold, on a page where a human sees an obvious dialog. The fixture
    reproduces what Bootstrap renders, not the minimum that opens. */
 .modal-backdrop { display: none; position: fixed; inset: 0; background: #000;
                   opacity: .5; z-index: 1; }
 .modal-backdrop.show { display: block; }
 .dropdown-menu { display: none; border: 1px solid #ccc; padding: 8px; }
 .dropdown-menu.show { display: block; }
</style></head>
<body>
<nav class="navbar"><span id="brand">Kontrola</span>
  <button id="userMenu" data-bs-toggle="dropdown" aria-expanded="false">Korisnik</button>
  <div class="dropdown-menu"><a href="#a">Profil</a><a href="#b">Odjava</a></div>
</nav>
<div class="container">
  <h1 id="page-title">%(title)s</h1>
  <div class="card">
    <h2 class="card-title">Grupe zadataka</h2>
    %(subtitle)s
    <span class="timestamp">%(stamp)s</span>
    <button id="openAdd" data-bs-toggle="modal" data-bs-target="#addModal">Dodaj</button>
    <button id="openGhost" data-bs-toggle="modal" data-bs-target="#nemaModala">Fantom</button>
  </div>
  <details><summary id="moreInfo">Vise informacija</summary><p>Detalji</p></details>
</div>
<div class="modal-backdrop" id="backdrop"></div>
<div class="modal" id="addModal">
  <h3 class="modal-title">Dodavanje grupe</h3>
  %(modal_extra)s
  <label>Naziv <input name="naziv"></label>
</div>
<script>
document.querySelectorAll('[data-bs-toggle="modal"]').forEach(function (t) {
  t.addEventListener('click', function () {
    var el = document.querySelector(t.getAttribute('data-bs-target'));
    if (el) { el.classList.add('show');
              document.getElementById('backdrop').classList.add('show'); }
  });
});
document.querySelectorAll('[data-bs-toggle="dropdown"]').forEach(function (t) {
  t.addEventListener('click', function () {
    var menu = t.parentElement.querySelector('.dropdown-menu');
    if (menu) { menu.classList.add('show'); t.setAttribute('aria-expanded', 'true'); }
  });
});
fetch('/write-attempt', {method: 'POST', body: 'x'}).catch(function () {});
</script>
</body></html>
"""

#: The page that renders differently the SECOND time it is opened in the same
#: browser profile. Nothing about it changed on the server - only the storage the
#: previous capture left behind.
STORAGE_PAGE = """<!doctype html>
<html lang="sr"><head><meta charset="utf-8"><title>Zapamceno stanje</title></head>
<body>
<div class="container">
  <h1 id="page-title">Zapamceno stanje</h1>
  <p id="hint" class="first-visit-hint">Prvi dolazak</p>
</div>
<script>
try {
  if (localStorage.getItem('seen')) {
    document.body.classList.add('returning');
    var h = document.getElementById('hint');
    if (h) { h.parentNode.removeChild(h); }
  }
  localStorage.setItem('seen', '1');
} catch (e) {}
</script>
</body></html>
"""

#: A page with two states: one that opens something INVISIBLE (an 8x8 white
#: block on a white page - it changes the openness string, so the capture is
#: real, and changes the picture by nothing at all) and one that covers the
#: screen. This is the shape the operator measured: "4 para istih slika".
#:
#: The QUIET one is discovered FIRST, deliberately: discovery walks
#: `capture.TRIGGER_SELECTORS` in order, `modal` before `collapse`, so a rule
#: that lets a collapsed state spend the page's picture budget never reaches the
#: loud one - which is the defect this ordering exists to catch.
QUIET_STATE_PAGE = """<!doctype html>
<html lang="sr"><head><meta charset="utf-8"><title>Tiha stanja</title>
<style>
 body { font-family: sans-serif; margin: 0; background: #fff; color: #111; }
 .navbar { background: #1b2a4a; color: #fff; padding: 12px 20px; }
 .container { padding: 20px; }
 button:focus { outline: none; }
 .modal { display: none; }
 .modal.show { display: block; width: 8px; height: 8px; background: #fff; }
 .collapse { display: none; }
 .collapse.show { display: block; position: fixed; inset: 0; background: #1b2a4a;
                  color: #fff; padding: 80px; font-size: 40px; }
</style></head>
<body>
<nav class="navbar"><span id="brand">Kontrola</span></nav>
<div class="container">
  <h1 id="page-title">Tiha stanja</h1>
  <p>Sadrzaj stranice koji se ne menja.</p>
  <button id="openQuiet" data-bs-toggle="modal" data-bs-target="#quietBox">Tiho</button>
  <button id="openLoud" data-bs-toggle="collapse">Glasno</button>
  <div class="modal" id="quietBox"></div>
</div>
<div class="collapse" id="loudBox">Ceo ekran je drugaciji</div>
<script>
document.getElementById('openQuiet').addEventListener('click', function () {
  document.getElementById('quietBox').classList.add('show');
});
document.getElementById('openLoud').addEventListener('click', function () {
  document.getElementById('loudBox').classList.add('show');
});
</script>
</body></html>
"""

#: django-debug-toolbar 4.x: the panel lives behind a DECLARATIVE SHADOW ROOT,
#: so `#djDebug` (which is inside it) cannot be reached from a page stylesheet
#: at all - only the host `#djDebugRoot` can. `%(toolbar)s` is the whole block
#: or nothing, so the same page can be rendered with and without it.
TOOLBAR_PAGE = """<!doctype html>
<html lang="sr"><head><meta charset="utf-8"><title>Ekran sa alatkom</title>
<style>
 body { font-family: sans-serif; margin: 0; background: #fff; color: #111; }
 .navbar { background: #1b2a4a; color: #fff; padding: 12px 20px; }
 .container { padding: 20px; }
</style></head>
<body>
<nav class="navbar"><span id="brand">Kontrola</span></nav>
<div class="container">
  <h1 id="page-title">Ekran sa alatkom</h1>
  <p>Sadrzaj koji kupac treba da vidi.</p>
</div>
%(toolbar)s
</body></html>
"""

TOOLBAR_BLOCK = """<div id="djDebugRoot">
  <template shadowrootmode="open">
    <style>
      #djDebug { position: fixed; top: 0; right: 0; width: 15vw; height: 100vh;
                 background: #111; color: #fff; font-size: 12px; padding: 8px; }
    </style>
    <div id="djDebug" dir="ltr">
      <div id="djDebugToolbar">
        <ul id="djDebugPanelList"><li>SQL 42 queries</li><li>Time 310ms</li></ul>
      </div>
    </div>
  </template>
</div>"""

#: The chrome rule in one page. Every trigger here opens something visible, so
#: nothing is lost to deduplication and the only thing under test is WHERE the
#: trigger lives.
LANDMARK_PAGE = """<!doctype html>
<html lang="sr"><head><meta charset="utf-8"><title>Orijentiri</title>
<style>
 body { font-family: sans-serif; margin: 0; background: #fff; color: #111; }
 .navbar { background: #1b2a4a; color: #fff; padding: 12px 20px; }
 main { padding: 20px; }
 .dropdown-menu { display: none; }
 .dropdown-menu.show { display: block; position: absolute; background: #1b2a4a;
                       color: #fff; width: 60vw; height: 40vh; padding: 20px; }
 .tab-pane { display: none; }
 .tab-pane.active.show { display: block; background: #444; color: #fff;
                         width: 70vw; height: 50vh; padding: 20px; }
 .collapse { display: none; }
 .collapse.show { display: block; background: #777; color: #fff;
                  width: 65vw; height: 45vh; padding: 20px; }
</style></head>
<body>
<nav class="navbar">
  <span id="brand">Kontrola</span>
  <button id="siteMenu" data-bs-toggle="dropdown">Meni aplikacije</button>
  <div class="dropdown-menu" id="siteMenuBox">Isti meni na svakom ekranu</div>
</nav>
<main>
  <h1 id="page-title">Orijentiri</h1>
  <nav class="tabs">
    <button id="localTab" data-bs-toggle="tab" data-bs-target="#tabPane">Kartica</button>
  </nav>
  <div class="tab-pane" id="tabPane">Sadrzaj kartice</div>
  <button id="cardMenu" data-bs-toggle="collapse">Meni u kartici</button>
  <div class="collapse" id="cardMenuBox">Sadrzaj menija u kartici</div>
</main>
<script>
document.getElementById('siteMenu').addEventListener('click', function () {
  document.getElementById('siteMenuBox').classList.add('show');
});
document.getElementById('localTab').addEventListener('click', function () {
  document.getElementById('tabPane').classList.add('active');
  document.getElementById('tabPane').classList.add('show');
});
document.getElementById('cardMenu').addEventListener('click', function () {
  document.getElementById('cardMenuBox').classList.add('show');
});
</script>
</body></html>
"""

#: The page that MOVES. `%(spacer)s` inserts 400px above the target block, which
#: is what any real insertion does to everything below it: the same page
#: coordinates then land somewhere else entirely. The target is a solid colour so
#: a test can say "the crop contains it" without reading text out of pixels.
ALIGN_PAGE = """<!doctype html>
<html lang="sr"><head><meta charset="utf-8"><title>Pomereni blok</title>
<style>
 body { font-family: sans-serif; margin: 0; background: #fff; color: #111; }
 .container { padding: 20px; }
 .spacer { height: 400px; background: #eef; border: 1px solid #ccd; }
 .card { border: 2px solid #093; background: #0a3; color: #fff; padding: 30px;
         font-size: 28px; }
 .filler { height: 700px; }
</style></head>
<body>
<div class="container">
  %(spacer)s
  <div class="card" id="target">%(value)s</div>
  <div class="filler"></div>
</div>
</body></html>
"""

ALIGN_V1 = {"spacer": "", "value": "Stara vrednost"}
ALIGN_V2 = {"spacer": '<div class="spacer" id="ubaceno">Ubaceno iznad</div>',
            "value": "Nova vrednost"}

V1 = {"title": "Grupe zadataka", "subtitle": "", "stamp": "pre 2 minuta",
      "modal_extra": ""}
#: The id is deliberately the LONGEST on the page: anchors are ranked by kind and
#: then by length, so the added element wins the box - which is the case where
#: resolving the two sides independently boxed two different things.
V2 = {"title": "Grupe zadataka",
      "subtitle": '<p class="card-subtitle" id="podnaslov-ispod-naziva">'
                  'Podnaslov ispod naziva</p>',
      "stamp": "pre 9 minuta", "modal_extra": '<p class="modal-hint">Obavezno polje</p>'}
V_COUNTER_ONLY = {"title": "Grupe zadataka", "subtitle": "", "stamp": "pre 51 minuta",
                  "modal_extra": ""}
#: Identical to V1 until the modal is OPEN - the case the operator insisted on:
#: "stranica moze da izgleda isto a da joj se modal promenio".
V_MODAL_ONLY = {"title": "Grupe zadataka", "subtitle": "", "stamp": "pre 2 minuta",
                "modal_extra": '<p class="modal-hint">Obavezno polje</p>'}


def write_page(root, variant, name="index.html") -> Path:
    """Render a variant into `<root>/<name>` and return the path."""
    return _write(root, name, PAGE % variant)


def write_storage_page(root, name="storage.html") -> Path:
    """The localStorage fixture - same bytes on every request, by design."""
    return _write(root, name, STORAGE_PAGE)


def write_quiet_state_page(root, name="quiet.html") -> Path:
    """The deduplication fixture: one loud state, one that shows nothing."""
    return _write(root, name, QUIET_STATE_PAGE)


def write_landmark_page(root, name="landmarks.html") -> Path:
    """The chrome fixture: one trigger in the site nav, one inside `<main>`, and
    one in a `<nav>` that is itself inside `<main>`."""
    return _write(root, name, LANDMARK_PAGE)


def write_align_page(root, variant, name="align.html") -> Path:
    """The content-alignment fixture: a block that moves 400px down."""
    return _write(root, name, ALIGN_PAGE % variant)


def write_toolbar_page(root, name="toolbar.html", toolbar=True) -> Path:
    """The dev-overlay fixture. `toolbar=False` is the SAME page without it -
    the two pictures must come out identical once the overlay is hidden."""
    return _write(root, name, TOOLBAR_PAGE % {"toolbar": TOOLBAR_BLOCK if toolbar else ""})


def _write(root, name, body) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fp = root / name
    fp.write_text(body, encoding="utf-8")
    return fp


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):                                  # no test noise
        pass

    def do_POST(self):                                          # noqa: N802
        self.send_response(204)
        self.end_headers()


def serve(root):
    """(base_url, stop) - a threaded server on 127.0.0.1 on a free port."""
    handler = partial(_Quiet, directory=str(root))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    port = httpd.server_address[1]

    def stop():
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)

    return "http://127.0.0.1:%d" % port, stop
