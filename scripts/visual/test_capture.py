#!/usr/bin/env python3
"""Capture + locate against a LOCAL fixture site (python http.server on
127.0.0.1:0). Never the real app, never the HUD, never the network.

The claim under test is the operator's: a page can look identical and still have
a changed modal. So the fixture serves two variants that differ ONLY inside the
modal, and the test proves the base shots match while the modal state does not.

Needs playwright + chromium; without them every check prints SKIP and the run
still exits 0 - a missing browser is not a defect in this code.
Run: python test_capture.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # the engine is a PACKAGE: `visual.*`
from visual import capture as vcapture  # noqa: E402
from visual import compare as vcompare  # noqa: E402
from visual import fixture_site  # noqa: E402
from visual import locate as vlocate  # noqa: E402

FAILS = []
SKIPPED = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def skip(label, why):
    print("SKIP " + label + " (" + why + ")")
    SKIPPED.append(label)


def _differs(a, b) -> float:
    """Share of pixels that differ between two shots - TEST SUPPORT ONLY.

    `compare.pixel_changed` used to provide this and was deleted: a pixel ratio
    in the ENGINE was read as evidence that a screen had changed, and reported
    moving database numbers to the customer as UI changes. "Did opening this
    modal repaint anything" is a question about the CAPTURE, so the helper lives
    with the capture test. (An identical copy is in `test_annotate.py`; if a
    third appears, lift it into `fixture_site.py`, which is this package's
    test-support module.)
    """
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 1.0
    diff = ImageChops.difference(ia, ib).convert("L")
    hist = diff.point(lambda v: 255 if v > 12 else 0).histogram()
    return hist[255] / float(ia.width * ia.height)


def _browser_available():
    try:
        import asyncio
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return False, str(exc)
    try:
        pw = sync_playwright().start()
    except Exception as exc:                                    # noqa: BLE001
        return False, str(exc)[:80]
    try:
        b = pw.chromium.launch()
        b.close()
        return True, ""
    except Exception as exc:                                    # noqa: BLE001
        return False, str(exc)[:80]
    finally:
        pw.stop()


def _cfg(base_url, **over):
    cfg = {"base_url": base_url, "tenant": "", "role": "",
           "auth": {"kind": "none", "module": "", "func": "", "args": {},
                    "cookie": {"name": "sessionid", "domain": "", "path": "/"}},
           "server": {"health": base_url + "/", "start": []},
           "pages": {"kind": "list", "pages": []},
           "ignore_selectors": [".timestamp"],
           "states": {"auto": True, "max_per_page": 6, "manual": {}},
           "viewport": {"width": 1440, "height": 900}}
    cfg.update(over)
    return cfg


def _run(root, variant, out, states=None, url_path="/index.html", cfg_over=None):
    """Serve `variant`, capture it, stop the server.
    Returns (shots, skipped_states, blocked_requests)."""
    fixture_site.write_page(root, variant)
    base, stop = fixture_site.serve(root)
    try:
        cfg = _cfg(base, **(cfg_over or {}))
        session = vcapture.open_session(cfg, root)
        try:
            shots, skipped = vcapture.capture_page(session, base + url_path, out,
                                                   page_id="fix:index", states=states)
            return shots, skipped, session.blocked
        finally:
            session.close()
    finally:
        stop()


def test_state_discovery_finds_the_modal_and_reads_the_navbar_as_chrome():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, skipped, _ = _run(d / "site", fixture_site.V1, d / "shots")
        names = [s["state"] for s in shots]
        ck("states: the base shot comes first", names[0] == "base")
        ck("states: the modal trigger became a state", "dodaj" in names)
        # The fixture's dropdown lives in the site `<nav>`: the same menu on
        # every screen of an app, so it is not a state OF this screen.
        ck("states: the navbar dropdown is not a state of this screen",
           "korisnik" not in names)
        ck("states: and it says so, naming the landmark it was read from",
           any(r.get("chrome") and r["label"] == "korisnik"
               and "<nav>" in r["reason"] for r in skipped))
        # `<details>` opens one extra line of small text on a 1440x900 page: it
        # was discovered and captured like the others, and then collapsed,
        # because two bits apart is the same picture twice.
        ck("states: <details><summary> was discovered and opened",
           any(row.get("label") == "vise_informacija" and row.get("collapsed")
               for row in skipped))
        ck("states: and it is not a second picture of the same screen",
           "vise_informacija" not in names)
        ck("states: every state wrote a png",
           all(Path(s["png_path"]).exists() for s in shots))
        ck("states: every state carries a signature",
           all(len(s["dom_signature"]) == 16 for s in shots))
        ck("states: the base shot reports the http status", shots[0]["status"] == 200)
        ck("states: geometry blocks were measured", len(shots[0]["geometry"]) >= 2)
        ck("states: the state is named after the visible text, not the id",
           "openadd" not in names)


def test_a_state_that_does_not_open_is_reported_not_dropped():
    """The fixture carries a trigger whose modal does not exist. Nothing opens,
    so there is no shot - and the state used to vanish without a word, with
    `manifest["skipped"]` staying empty on every run of both passes."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, skipped, _ = _run(d / "site", fixture_site.V1, d / "shots")
        names = [s["state"] for s in shots]
        ck("skipped: the dead trigger produced no state", "fantom" not in names)
        row = next((s for s in skipped if s.get("label") == "fantom"), None)
        ck("skipped: it is REPORTED instead", row is not None)
        ck("skipped: with the selector that was tried",
           row and "data-bs-toggle" in (row.get("selector") or ""))
        ck("skipped: and a reason a human can act on",
           row and "nothing opened" in (row.get("reason") or ""))
        ck("skipped: the states that did open are not in the skip list",
           not [s for s in skipped if s.get("label") == "dodaj"])


def test_two_captures_of_one_unchanged_page_have_the_same_signature():
    """Reproducibility, and the reason a state gets its own CONTEXT: the fixture
    writes `localStorage` and renders differently on the second visit. With a
    shared context the second capture of an UNCHANGED page hashed differently,
    which is a sweep alarm nobody caused and a diff nobody can explain."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        site = d / "site"
        fixture_site.write_storage_page(site)
        base, stop = fixture_site.serve(site)
        try:
            session = vcapture.open_session(_cfg(base), d)
            try:
                first, _ = vcapture.capture_page(session, base + "/storage.html",
                                                 d / "a", page_id="p", states=[])
                second, _ = vcapture.capture_page(session, base + "/storage.html",
                                                  d / "b", page_id="p", states=[])
            finally:
                session.close()
        finally:
            stop()
        ck("repeat: the same page twice in one run gives the same signature",
           first[0]["dom_signature"] == second[0]["dom_signature"])
        ck("repeat: and the same geometry",
           first[0]["geometry"] == second[0]["geometry"])
        ck("repeat: the second capture still saw the first-visit markup",
           _differs(first[0]["png_path"], second[0]["png_path"]) == 0.0)


def test_an_open_modal_really_is_open_in_its_shot():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, _, _ = _run(d / "site", fixture_site.V1, d / "shots")
        by_state = {s["state"]: s for s in shots}
        base_sig = by_state["base"]["dom_signature"]
        ck("modal: the modal state differs from the base state",
           by_state["dodaj"]["dom_signature"] != base_sig)
        ck("modal: opening it changes the picture too",
           _differs(by_state["base"]["png_path"],
                    by_state["dodaj"]["png_path"]) > 0.01)


def test_a_change_only_inside_the_modal_is_caught():
    """The whole reason state discovery exists."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        before, _, _ = _run(d / "site1", fixture_site.V1, d / "before")
        after, _, _ = _run(d / "site2", fixture_site.V_MODAL_ONLY, d / "after")
        b = {s["state"]: s for s in before}
        a = {s["state"]: s for s in after}
        ck("modal-only: the base page looks identical",
           _differs(b["base"]["png_path"], a["base"]["png_path"]) < 0.001)
        # The tree walk includes hidden subtrees on purpose: the sweep is a
        # warning for me, and markup that only shows in a modal is still a
        # change. The customer-facing signal is the modal STATE below.
        ck("modal-only: the tree signature notices the hidden markup",
           b["base"]["dom_signature"] != a["base"]["dom_signature"])
        ck("modal-only: the MODAL state is different in pixels",
           _differs(b["dodaj"]["png_path"], a["dodaj"]["png_path"]) > 0.001)
        ck("modal-only: and the modal state is structurally different",
           vcompare.sweep_changed(b["dodaj"], a["dodaj"])["structural"] is True)


def test_dynamic_text_does_not_move_the_signature():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        before, _, _ = _run(d / "site1", fixture_site.V1, d / "before", states=[])
        after, _, _ = _run(d / "site2", fixture_site.V_COUNTER_ONLY, d / "after",
                           states=[])
        ck("noise: a changed relative time leaves the signature alone",
           before[0]["dom_signature"] == after[0]["dom_signature"])
        ck("noise: and the sweep says nothing changed",
           vcompare.sweep_changed(before[0], after[0])["structural"] is False)
        ck("noise: but the customer PNG still shows the real page",
           Path(before[0]["png_path"]).exists())


def test_a_real_change_does_move_the_signature():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        before, _, _ = _run(d / "site1", fixture_site.V1, d / "before", states=[])
        after, _, _ = _run(d / "site2", fixture_site.V2, d / "after", states=[])
        verdict = vcompare.sweep_changed(before[0], after[0])
        ck("change: a new element is structural", verdict["structural"] is True)


def test_states_empty_takes_the_base_shot_only():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, _, _ = _run(d / "site", fixture_site.V1, d / "shots", states=[])
        ck("sweep mode: exactly one shot", len(shots) == 1 and shots[0]["state"] == "base")


def test_a_manual_state_runs_its_steps():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        steps = [{"kind": "click", "selector": "#openAdd"},
                 {"kind": "fill", "selector": "input[name=naziv]", "value": "Proba"},
                 {"kind": "wait", "value": 100}]
        shots, _, _ = _run(d / "site", fixture_site.V1, d / "shots",
                           states=[{"name": "popunjen_modal", "steps": steps}])
        names = [s["state"] for s in shots]
        ck("manual: the named state was captured", names == ["base", "popunjen_modal"])
        ck("manual: it differs from the base",
           shots[1]["dom_signature"] != shots[0]["dom_signature"])


def test_no_screenshot_run_produces_signatures_without_files():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        fixture_site.write_page(d / "site", fixture_site.V1)
        base, stop = fixture_site.serve(d / "site")
        try:
            session = vcapture.open_session(_cfg(base), d)
            try:
                shots, _ = vcapture.capture_page(session, base + "/index.html",
                                                 d / "shots", page_id="p",
                                                 states=[], screenshot=False)
            finally:
                session.close()
        finally:
            stop()
        ck("sweep mode: no png is written", shots[0]["png_path"] == "")
        ck("sweep mode: the signature is still there",
           len(shots[0]["dom_signature"]) == 16)


def test_a_post_is_refused_by_the_route_guard():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _, _, blocked = _run(d / "site", fixture_site.V1, d / "shots", states=[])
        ck("read-only: the page's POST was aborted", len(blocked) >= 1)
        ck("read-only: and it is recorded with its method",
           blocked and blocked[0]["method"] == "POST")
        ck("read-only: the url is kept for the report",
           blocked and "write-attempt" in blocked[0]["url"])


def test_a_trigger_in_the_site_chrome_is_not_a_state_of_the_screen():
    """The other half of "4 para istih slika": with the debug toolbar out of the
    frame, a navbar dropdown is 8-26 phash bits from its base shot and a real tab
    is 12-26, so no picture threshold can separate them. Where the trigger LIVES
    can - `dashboard:location_quality` was shipping six pictures of one screen
    with a different application menu open in each.

    All three branches of the rule are on this one page:

    * a dropdown in the site `<nav>`               -> chrome, never photographed
    * a collapse inside `<main>`                   -> this screen, a state
    * a tab in a `<nav>` that is INSIDE `<main>`   -> local navigation, a state

    The third is the one that makes the rule safe to have: a tab strip wrapped
    in `<nav>` is this page's own navigation, not the site's.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        site, out = d / "site", d / "shots"
        fixture_site.write_landmark_page(site)
        base, stop = fixture_site.serve(site)
        try:
            session = vcapture.open_session(_cfg(base), d)
            try:
                shots, skipped = vcapture.capture_page(
                    session, base + "/landmarks.html", out, page_id="fix:landmarks")
            finally:
                session.close()
        finally:
            stop()

        names = [sh["state"] for sh in shots]
        chrome = [r for r in skipped if r.get("chrome")]
        ck("chrome: the site menu is not a state", "meni_aplikacije" not in names)
        ck("chrome: it is recorded, with the landmark that decided it",
           len(chrome) == 1 and chrome[0]["label"] == "meni_aplikacije"
           and "<nav>" in chrome[0]["reason"])
        ck("chrome: and with the selector, so a misjudgement can be found",
           "data-bs-toggle" in (chrome[0].get("selector") or ""))
        ck("chrome: a trigger inside <main> is still a state",
           "meni_u_kartici" in names)
        ck("chrome: a <nav> INSIDE <main> is this page's own navigation",
           "kartica" in names)
        ck("chrome: so the page keeps exactly its own states", len(shots) == 3)


def test_a_layout_with_no_landmarks_keeps_every_state():
    """The rule may only ever remove a state it can POSITIVELY prove is chrome.
    `fixture_site.PAGE` has no `<main>` at all: its modal sits under no landmark,
    and it must go on producing a state exactly as before."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, skipped, _ = _run(d / "site", fixture_site.V1, d / "shots")
        names = [sh["state"] for sh in shots]
        ck("no landmark: a trigger under no landmark is still a state",
           "dodaj" in names)
        ck("no landmark: only what IS inside a chrome landmark is dropped",
           [r["label"] for r in skipped if r.get("chrome")] == ["korisnik"])


def test_an_anchor_matches_the_TRANSLATION_of_its_string():
    """The matcher tries the msgid and every rendered form of it. The fixture
    page is Serbian, like every page this engine photographs; the anchor is the
    English msgid the template carried."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        anchors = [{"kind": "text", "value": "Subtitle below the title",
                    "alternates": ["Podnaslov ispod naziva"]},
                   {"kind": "text", "value": "Never rendered anywhere"}]
        fixture_site.write_page(d / "site", fixture_site.V2)
        base, stop = fixture_site.serve(d / "site")
        try:
            session = vcapture.open_session(_cfg(base), d)
            try:
                shots, _ = vcapture.capture_page(session, base + "/index.html",
                                                 d / "shots", page_id="fix:index",
                                                 states=[], anchors=anchors)
            finally:
                session.close()
        finally:
            stop()
        regions = (shots[0] or {}).get("regions") or {}
        hit = regions.get("text:Subtitle below the title") or []
        ck("i18n: the msgid resolved through its translation", len(hit) == 1)
        ck("i18n: and it found the element that renders it",
           bool(hit) and hit[0]["element"]["w"] > 10)
        ck("i18n: a string that is nowhere still resolves to nothing",
           regions.get("text:Never rendered anywhere") == [])


def test_a_state_that_shows_nothing_new_is_collapsed_not_paired():
    """THE 4-COPIES DEFECT. `states.auto` clicks every trigger on the page, and
    on most screens the trigger opens a navbar dropdown that the full-page shot
    can barely show: a real run produced 84 shots of 30 pages, 50 of them within
    6 phash bits of their own base shot, and the customer was handed four
    near-identical pairs per screen.

    The fixture has one of each: a trigger that opens an 8x8 white block on a
    white page (it really opens - the openness string changes, so this is not
    the "nothing opened" path) and one that covers the screen.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        site, out = d / "site", d / "shots"
        fixture_site.write_quiet_state_page(site)
        base, stop = fixture_site.serve(site)
        try:
            session = vcapture.open_session(_cfg(base), d)
            try:
                shots, skipped = vcapture.capture_page(
                    session, base + "/quiet.html", out, page_id="fix:quiet")
            finally:
                session.close()
        finally:
            stop()

        names = [sh["state"] for sh in shots]
        ck("dedup: the base state is always kept", names[0] == "base")
        ck("dedup: the state that changed the screen is kept", "glasno" in names)
        ck("dedup: the state that showed nothing is not a second picture",
           "tiho" not in names)
        collapsed = [row for row in skipped if row.get("collapsed")]
        ck("dedup: it is RECORDED, not silently dropped",
           len(collapsed) == 1 and collapsed[0]["label"] == "tiho")
        ck("dedup: the record names the state it repeats",
           collapsed[0].get("same_as") == "base")
        ck("dedup: and the distance that was measured",
           "phash distance" in collapsed[0].get("reason", ""))
        ck("dedup: the duplicate picture is not left on disk",
           sorted(f.name for f in out.iterdir())
           == ["fix_quiet.png", "fix_quiet__glasno.png"])
        ck("dedup: every kept state still has its picture",
           all(Path(sh["png_path"]).is_file() for sh in shots))


def test_a_collapsed_state_does_not_spend_the_pictures_budget():
    """`states.max_per_page` caps the PICTURES of a page. Counting collapsed
    states against it cost a real one the first time it ran: on
    `settings_admin:role_create` two navbar dropdowns opened, collapsed into the
    base shot, and the run then stopped before the form's "lokacije" tab - one
    of the four pictures that ticket was about.

    The fixture reproduces it exactly: the quiet trigger comes FIRST in the DOM,
    and there is room for one picture besides the base shot.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        site, out = d / "site", d / "shots"
        fixture_site.write_quiet_state_page(site)
        base, stop = fixture_site.serve(site)
        cfg = _cfg(base, states={"auto": True, "max_per_page": 2, "manual": {}})
        try:
            session = vcapture.open_session(cfg, d)
            try:
                shots, skipped = vcapture.capture_page(
                    session, base + "/quiet.html", out, page_id="fix:quiet")
            finally:
                session.close()
        finally:
            stop()
        names = [sh["state"] for sh in shots]
        ck("budget: the quiet state still opened and still collapsed",
           any(r.get("collapsed") and r["label"] == "tiho" for r in skipped))
        ck("budget: and the state behind it was still reached", names == ["base", "glasno"])
        ck("budget: while the cap on PICTURES is still honoured", len(shots) == 2)


def test_the_developer_overlay_is_out_of_the_picture():
    """The Django debug toolbar covered the right ~15% of every screenshot the
    customer was sent. Version 4 renders it behind a DECLARATIVE SHADOW ROOT, so
    a rule for `#djDebug` (which lives inside it) reaches nothing at all - the
    host is what has to be hidden.

    Both halves matter: the overlay must really be there and really be visible
    without the engine's stylesheet, or the test proves nothing.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        site = d / "site"
        fixture_site.write_toolbar_page(site, "toolbar.html", toolbar=True)
        fixture_site.write_toolbar_page(site, "plain.html", toolbar=False)
        base, stop = fixture_site.serve(site)
        probe = """() => {
            const host = document.getElementById('djDebugRoot');
            if (!host) return ['absent', -1];
            const inner = host.shadowRoot && host.shadowRoot.getElementById('djDebug');
            return [getComputedStyle(host).display,
                    inner ? Math.round(inner.getBoundingClientRect().width) : -1];
        }"""
        try:
            session = vcapture.open_session(_cfg(base), d)
            try:
                with_tb, _ = vcapture.capture_page(session, base + "/toolbar.html",
                                                   d / "tb", page_id="fix:tb", states=[])
                without, _ = vcapture.capture_page(session, base + "/plain.html",
                                                   d / "pl", page_id="fix:pl", states=[])
                page = session.context.new_page()
                page.goto(base + "/toolbar.html", wait_until="load")
                raw = page.evaluate(probe)              # control: no injection
                vcapture._goto(page, base + "/toolbar.html")
                hidden = page.evaluate(probe)           # treatment: the engine's
                page.close()
            finally:
                session.close()
        finally:
            stop()

        ck("overlay: the fixture really carries one", raw[0] != "absent")
        ck("overlay: and it really covers the page without the engine's css",
           raw[0] == "block" and raw[1] > 100)
        ck("overlay: the engine hides the shadow-root HOST", hidden[0] == "none")
        ck("overlay: so nothing inside it has a box any more", hidden[1] == 0)
        ck("overlay: the picture is the one the page would take without it",
           vcompare.hamming(vcompare.phash(with_tb[0]["png_path"]),
                            vcompare.phash(without[0]["png_path"])) == 0)
        ck("overlay: the app's own navbar is untouched by the rule",
           "#djDebugRoot" in vcapture.HIDE_CSS and "nav" not in vcapture.HIDE_CSS)


def test_the_region_of_a_change_is_measured_while_the_page_is_open():
    """What a crop is cut to: the element the diff named, and THE BLOCK it sits
    in - resolved in the same page load as the screenshot, because a second load
    of every page just to find a rectangle would double the commit-time budget.

    The fixture is the real shape: an added `<p class="card-subtitle">` inside a
    `.card` that has a border of its own. The block must be the card, and it must
    CONTAIN the element - a block that does not is a rectangle of somewhere else.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        anchors = [{"kind": "id", "value": "podnaslov-ispod-naziva"},
                   {"kind": "id", "value": "nikad-nije-postojao"}]
        fixture_site.write_page(d / "site", fixture_site.V2)
        base, stop = fixture_site.serve(d / "site")
        try:
            session = vcapture.open_session(_cfg(base), d)
            try:
                shots, _ = vcapture.capture_page(session, base + "/index.html",
                                                 d / "shots", page_id="fix:index",
                                                 states=[], anchors=anchors)
            finally:
                session.close()
        finally:
            stop()

        regions = (shots[0] or {}).get("regions") or {}
        ck("regions: every anchor OFFERED is in the map",
           set(regions) == {"id:podnaslov-ispod-naziva", "id:nikad-nije-postojao"})
        ck("regions: one that is not on the page is empty, not missing",
           regions["id:nikad-nije-postojao"] == [])
        ck("regions: an anchor answers with a LIST - one change touches several "
           "places, and a class is on every one of them",
           isinstance(regions["id:podnaslov-ispod-naziva"], list))
        got = (regions["id:podnaslov-ispod-naziva"] or [None])[0]
        ck("regions: the element was measured", bool(got and got["element"]["w"] > 10))
        ck("regions: and so was the block around it", bool(got and got["block"]))
        el, block = got["element"], got["block"]
        ck("regions: the block CONTAINS the element",
           block["x"] <= el["x"] and block["y"] <= el["y"]
           and block["x"] + block["w"] >= el["x"] + el["w"]
           and block["y"] + block["h"] >= el["y"] + el["h"])
        ck("regions: the block is bigger than the element, or it is not context",
           block["w"] * block["h"] > el["w"] * el["h"])
        ck("regions: and it says what kind of block it found",
           "card" in (got.get("kind") or ""))
        ck("regions: a shot taken without anchors carries an empty map",
           vlocate.regions_of({"regions": {}}) == {}
           and vlocate.regions_of({}) == {})
        every = vlocate.all_regions(shots[0], anchors)
        ck("regions: every resolved anchor is offered to the caller, best first",
           every and every[0] == got)


def test_a_missing_server_is_a_question_not_a_traceback():
    cfg = _cfg("http://127.0.0.1:9")
    try:
        vcapture.check_server(cfg)
        ck("down: a dead server raises VisualError", False)
    except vcapture.VisualError as exc:
        ck("down: the code says server_down", exc.code == "server_down")
        ck("down: it carries a question for the operator", len(exc.questions) == 1)
        ck("down: the question names the health url",
           "127.0.0.1:9" in exc.questions[0]["question"])


def main() -> int:
    ok, why = _browser_available()
    tests = (test_state_discovery_finds_the_modal_and_reads_the_navbar_as_chrome,
             test_a_state_that_does_not_open_is_reported_not_dropped,
             test_two_captures_of_one_unchanged_page_have_the_same_signature,
             test_an_open_modal_really_is_open_in_its_shot,
             test_a_change_only_inside_the_modal_is_caught,
             test_dynamic_text_does_not_move_the_signature,
             test_a_real_change_does_move_the_signature,
             test_states_empty_takes_the_base_shot_only,
             test_a_manual_state_runs_its_steps,
             test_no_screenshot_run_produces_signatures_without_files,
             test_a_post_is_refused_by_the_route_guard,
             test_a_trigger_in_the_site_chrome_is_not_a_state_of_the_screen,
             test_a_layout_with_no_landmarks_keeps_every_state,
             test_the_region_of_a_change_is_measured_while_the_page_is_open,
             test_an_anchor_matches_the_TRANSLATION_of_its_string,
             test_a_state_that_shows_nothing_new_is_collapsed_not_paired,
             test_a_collapsed_state_does_not_spend_the_pictures_budget,
             test_the_developer_overlay_is_out_of_the_picture)
    if not ok:
        for fn in tests:
            skip(fn.__name__, "no browser: " + why)
    else:
        for fn in tests:
            try:
                fn()
            except Exception as exc:                            # noqa: BLE001
                ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    for fn in (test_a_missing_server_is_a_question_not_a_traceback,):
        try:
            fn()
        except Exception as exc:                                # noqa: BLE001
            ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    if SKIPPED:
        print("\n%d test(s) skipped - install chromium to run them" % len(SKIPPED))
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
