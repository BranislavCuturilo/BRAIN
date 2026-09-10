#!/usr/bin/env python3
"""CLI smoke for shoot.py - the four commands against a LOCAL fixture site, and
the failure modes that must reach the operator as a question instead of a
traceback. The real app, the real HUD and the real shots directory are never
touched: BRAIN_SHOTS_DIR is redirected into a temp dir for every run.
Run: python test_shoot.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # the engine is a PACKAGE: `visual.*`
from visual import annotate as vannotate  # noqa: E402
from visual import shoot as vshoot  # noqa: E402
from visual import fixture_site  # noqa: E402

FAILS = []
SKIPPED = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def skip(label, why):
    print("SKIP " + label + " (" + why + ")")
    SKIPPED.append(label)


TEMPLATE_V1 = """<div class="card" id="taskGroups">
  <h2 class="card-title">Grupe zadataka</h2>
</div>
"""
TEMPLATE_V2 = """<div class="card" id="taskGroups">
  <h2 class="card-title">Grupe zadataka</h2>
  <p class="card-subtitle" id="grp-sub">Podnaslov ispod naziva</p>
</div>
"""


def _run(args, shots_dir):
    env = dict(os.environ)
    env["BRAIN_SHOTS_DIR"] = str(shots_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(HERE / "shoot.py")] + args,
                       capture_output=True, text=True, env=env, timeout=300)
    return r.returncode, r.stdout, r.stderr


def _json_line(out):
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _commit(root, message="wip"):
    """Commit everything in the fixture repo.

    The engine's whole premise is `git diff`: with no repository every changed
    file falls back to WHOLE-FILE anchors, and a suite that never committed
    would test the fallback while believing it tested the diff. That is the
    shape of the defect these tests exist for, so the fixture is a real repo and
    a test says explicitly which state it is in.
    """
    root = str(root)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    for args in (["init", "-q"],
                 ["-c", "user.email=t@example.com", "-c", "user.name=T",
                  "add", "-A"],
                 ["-c", "user.email=t@example.com", "-c", "user.name=T",
                  "commit", "-q", "-m", message]):
        subprocess.run(["git"] + args, cwd=root, capture_output=True, text=True,
                       env=env, timeout=60)


def _repo(root, base_url, auth=None, states=None, template=TEMPLATE_V1, pages=None):
    """A minimal app repo: one page, one template, a config pointing at the
    fixture server, and a git repository with it all committed. `auth` overrides
    the auth block for the host-trap test; `states` turns state discovery off
    for tests that only care about the base shot."""
    root = Path(root)
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    tpl = root / "myapp" / "templates" / "myapp" / "index.html"
    tpl.parent.mkdir(parents=True, exist_ok=True)
    tpl.write_text(template, encoding="utf-8")
    (root / ".claude" / "visual-diff.json").write_text(json.dumps({
        "base_url": base_url,
        "auth": auth or {"kind": "none"},
        "server": {"health": base_url + "/", "start": ["python", "-m", "http.server"]},
        "pages": {"kind": "list", "pages": pages or [
            {"page_id": "fix:index", "url": "/index.html",
             "title": "Grupe zadataka", "template": "myapp/index.html"}]},
        "template_globs": ["*/templates/**/*.html"],
        "static_globs": ["static/**/*.css"],
        "ignore_selectors": [".timestamp"],
        "states": states or {"auto": True, "max_per_page": 3, "manual": {}},
    }), encoding="utf-8")
    _commit(root, "initial")
    return root, "myapp/templates/myapp/index.html"


#: A repo's auth driver, doing exactly what a Django one does: put the repo root
#: on sys.path, import the app's OWN top-level `config` PACKAGE, and leave the
#: Selector event-loop policy behind (django/channels installs it at setup()).
#: Both used to kill the run - the engine's `visual/config.py` had already taken
#: the name `config`, and the Proactor policy was asserted before auth instead of
#: before playwright. Neither is visible in a repo that has no `config/`.
HOSTILE_DRIVER = """import sys
from pathlib import Path


def mint_session(slug=None, role=None):
    repo = str(Path(__file__).resolve().parents[1])
    if repo not in sys.path:
        sys.path.append(repo)          # APPENDED: the engine must not shadow it
    import config.settings as st       # the app's config package, not the engine's
    if not getattr(st, "APP_CONFIG_LOADED", False):
        raise RuntimeError("wrong config module: %r" % (st,))
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return "fixture-session", "driver@example.com"
"""


def _hostile_repo(root, base_url):
    """`_repo` plus a `config/` package of its own and the driver above."""
    root, tpl = _repo(root, base_url, auth={
        "kind": "driver", "module": ".claude/driver.py", "func": "mint_session",
        "cookie": {"name": "sessionid", "domain": "127.0.0.1", "path": "/"}})
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text("APP_CONFIG_LOADED = True",
                                                 encoding="utf-8")
    (root / ".claude" / "driver.py").write_text(HOSTILE_DRIVER, encoding="utf-8")
    return root, tpl


def test_the_engine_does_not_squat_the_app_s_config_or_its_event_loop():
    """The host traps, end to end: the engine loads the repo's driver IN THIS
    PROCESS, so its own module names and its own asyncio policy are the app's.
    A run against a repo with a `config/` package and a channels-like driver
    must still capture a page."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        site = d / "site"
        fixture_site.write_page(site, fixture_site.V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _hostile_repo(d / "repo", base)
            code, out, err = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#host"], d / "shots")
        finally:
            stop()
        tail = (out + err).strip()[-200:]
        ck("host traps: the run exits 0 (%s)" % tail, code == 0)
        ck("host traps: the app's own config package won, not the engine's",
           "not a package" not in out and "auth_failed" not in out)
        ck("host traps: playwright still got its subprocess loop",
           "NotImplementedError" not in out and "NotImplementedError" not in err)
        shot = d / "shots" / "demo_host" / "before"
        ck("host traps: a page was captured",
           shot.is_dir() and any(p.suffix == ".png" for p in shot.iterdir()))


def _browser_available():
    try:
        import asyncio
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        from playwright.sync_api import sync_playwright
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


def test_a_run_with_no_ticket_refuses_and_writes_nothing():
    """The operator, 2026-08-21: "treba da bude samo grupa slika koja je vezana
    za tiket; kada nema tiketa, nema ni slika."

    A `--work-id` pass with no ticket used to capture the whole affected set,
    write a manifest the HUD lists as "bez tiketa" and that the approve path
    then refuses - pictures nobody could ever send, produced at full cost. The
    refusal comes before the config is read and before the server is touched, so
    a refused run leaves NOTHING in the shots store.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots = d / "shots"
        repo, tpl = _repo(d / "repo", "http://127.0.0.1:9")   # never contacted
        for args in (["before", "--repo", str(repo), "--files", tpl,
                      "--work-id", "wnoticket"],
                     ["after", "--repo", str(repo), "--files", tpl,
                      "--work-id", "wnoticket"],
                     ["before", "--repo", str(repo), "--files", tpl]):
            code, out, _ = _run(args, shots)
            payload = _json_line(out) or {}
            ck("no ticket: `%s` without one refuses" % args[0], code != 0)
            ck("no ticket: with the engine's error contract",
               payload.get("error") == "no_ticket" and payload.get("exit") == code)
            ck("no ticket: and a question an operator can answer",
               len(payload.get("questions") or []) == 1
               and "tiket" in payload["questions"][0]["question"].lower())
            ck("no ticket: whose example shows how to answer it",
               "--ticket" in payload["questions"][0]["example"])
        ck("no ticket: nothing at all was written to the shots store",
           not shots.exists())

        # The work id ALONE is not the ticket, but a pass that named one wrote
        # it into this run's state.json - and that is a fact, not a guess.
        (shots / "wnoticket").mkdir(parents=True)
        (shots / "wnoticket" / "state.json").write_text(
            json.dumps({"work_id": "wnoticket", "ticket": "DEMO#08597",
                        "repo": str(repo)}), encoding="utf-8")
        code, out, _ = _run(["before", "--repo", str(repo), "--files", tpl,
                             "--work-id", "wnoticket"], shots)
        payload = _json_line(out) or {}
        ck("no ticket: a ticket on the run's state file is enough",
           payload.get("error") != "no_ticket")
        ck("no ticket: (that run then fails on the dead server, as it should)",
           payload.get("error") == "server_down")

        # A repo-wide command belongs to no ticket and never asked for one.
        code2, out2, _ = _run(["sweep", "--repo", str(repo)], shots)
        ck("no ticket: sweep is not a customer picture and is not gated",
           (_json_line(out2) or {}).get("error") != "no_ticket")


def test_a_missing_config_is_a_question_not_a_crash():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        code, out, err = _run(["before", "--repo", str(d), "--ticket", "DEMO#cfg"],
                              d / "shots")
        payload = _json_line(out)
        ck("no config: exit code 2", code == 2)
        ck("no config: one json line on stdout", payload is not None)
        ck("no config: the error names the cause", payload["error"] == "needs_config")
        ck("no config: it says WHAT is missing", payload["missing"] == ["file"])
        ck("no config: it carries questions for the operator",
           len(payload["questions"]) >= 3)
        ck("no config: every question has a key and an example",
           all({"key", "question", "example"} <= set(q) for q in payload["questions"]))
        ck("no config: nothing was written", not (d / "shots").exists())


def test_an_unusable_config_names_the_broken_key():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / ".claude").mkdir()
        (d / ".claude" / "visual-diff.json").write_text(json.dumps({
            "base_url": "", "auth": {"kind": "magic"},
            "pages": {"kind": "razvojna-mapa", "path": "nope/"}}), encoding="utf-8")
        code, out, _ = _run(["before", "--repo", str(d), "--ticket", "DEMO#cfg2"],
                            d / "shots")
        payload = _json_line(out)
        ck("bad config: exit code 2", code == 2)
        ck("bad config: every broken key is listed",
           set(payload["missing"]) == {"base_url", "auth.kind", "pages.path"})
        ck("bad config: an unknown auth kind is refused, not imported",
           any("driver" in q["question"] or "none" in q["question"]
               for q in payload["questions"]))


def test_a_dead_server_is_a_question():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        repo, tpl = _repo(d / "repo", "http://127.0.0.1:9")
        code, out, _ = _run(["before", "--repo", str(repo), "--files", tpl,
                             "--ticket", "DEMO#down"],
                            d / "shots")
        payload = _json_line(out)
        ck("server down: exit code 3", code == 3)
        ck("server down: the error is server_down", payload["error"] == "server_down")
        ck("server down: the question offers the start command",
           payload["questions"] and "http.server" in payload["questions"][0]["example"])


def test_a_bad_repo_path_is_a_usage_error():
    with tempfile.TemporaryDirectory() as d:
        code, out, _ = _run(["before", "--repo", str(Path(d) / "nope")], Path(d) / "s")
        ck("usage: exit code 7", code == 7)
        ck("usage: the error says which path", _json_line(out)["error"] == "usage")


def test_after_without_before_asks_instead_of_guessing():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        site = d / "site"
        fixture_site.write_page(site, fixture_site.V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _repo(d / "repo", base)
            code, out, _ = _run(["after", "--repo", str(repo), "--files", tpl,
                                 "--ticket", "DEMO#1"], d / "shots")
        finally:
            stop()
        payload = _json_line(out)
        ck("no before: it refuses rather than inventing a baseline", code == 6)
        ck("no before: the error is no_before", payload["error"] == "no_before")
        q = payload["questions"][0]
        ck("no before: it asks whether the 'pre' state can still be captured",
           "pre" in q["question"])
        # The question used to offer "shall I shoot the current look AS the
        # before" - the one thing the engine must never do. It now offers the
        # two honest answers instead.
        ck("no before: it offers --no-baseline, not a faked before",
           "--no-baseline" in q["example"])


def test_after_can_stand_alone_when_the_before_is_unobtainable():
    """`--no-baseline`: the artefact exists, is marked, and is NOT a pair.

    The case it is for: the before state is gone (a migration already ran, or the
    engine was broken when the baseline was due). Reading the gallery, "no
    baseline captured" must be distinguishable from "nothing changed" - so both
    the manifest and every record say which one it is.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots = d / "shots"
        site = d / "site"
        fixture_site.write_page(site, fixture_site.V2)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _repo(d / "repo", base)
            (repo / tpl).write_text(TEMPLATE_V2, encoding="utf-8")
            code, out, err = _run(["after", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#nb", "--no-baseline",
                                   "--baseline-reason", "migracija je vec izvrsena"],
                                  shots)
            ck("no baseline: the run succeeds (%s)" % (err.strip()[-120:] or "no stderr"),
               code == 0)
            ck("no baseline: the console says there is nothing to compare against",
               "NO BASELINE" in out)
            man = json.loads((shots / "demo_nb" / "manifest.json").read_text(encoding="utf-8"))
            ck("no baseline: the manifest states it once, with the reason",
               man["baseline"]["state"] == "none"
               and "migracija" in man["baseline"]["reason"])
            ck("no baseline: nothing was recorded as a before shot",
               not man["shots"]["before"])
            ck("no baseline: something was actually captured", bool(man["pairs"]))
            rec = man["pairs"][0]
            ck("no baseline: every record carries the marker",
               all(r.get("baseline") == "none" for r in man["pairs"]))
            ck("no baseline: the before half is empty, not a copy of the after",
               rec["before"]["full"] is None)
            ck("no baseline: the after picture is there",
               bool(rec["after"]["full"]) and Path(rec["after"]["full"]).is_file())
            ck("no baseline: no comparison is claimed",
               rec["sweep"] is None and rec["drop"] is False
               and "pixel_ratio" not in rec)
            ck("no baseline: no state is reported as unpaired", man["unpaired"] == [])

            # The gate's contract: this pass covered the file on the AFTER side
            # only. Recording a before here is what would turn a post-change
            # capture into a pre-change one.
            st = json.loads((shots / "demo_nb" / "state.json").read_text(encoding="utf-8"))
            ck("no baseline: the after side is recorded", st["after"]["files"] == [tpl])
            ck("no baseline: the before side stays empty", "before" not in st)

            # And a run that HAS a baseline ignores the flag rather than
            # throwing the real before shots away.
            code2, out2, _ = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#nb2"], shots)
            code3, out3, _ = _run(["after", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#nb2", "--no-baseline"], shots)
            man2 = json.loads((shots / "demo_nb2" / "manifest.json").read_text(encoding="utf-8"))
            ck("no baseline: the flag is ignored when a before exists (%d/%d)"
               % (code2, code3), "ignored" in out3)
            ck("no baseline: and the run stays a real pair",
               man2["baseline"]["state"] == "captured"
               and all(r.get("baseline") == "captured" for r in man2["pairs"]))
        finally:
            stop()


def test_the_happy_path_produces_a_customer_pair():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots = d / "shots"
        site = d / "site"
        fixture_site.write_page(site, fixture_site.V1)
        base, stop = fixture_site.serve(site)
        try:
            # Room for three states: the modal, the dropdown, and the <details>
            # that opens one line of text - which is the one that gets collapsed,
            # and the reason this run is worth four pictures instead of five.
            repo, tpl = _repo(d / "repo", base,
                              states={"auto": True, "max_per_page": 4, "manual": {}})

            code, out, err = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#08597"], shots)
            ck("before: exit 0 (%s)" % (err.strip()[-160:] or "no stderr"), code == 0)
            mf = shots / "demo_08597" / "manifest.json"
            ck("before: the manifest is written where the ticket says", mf.exists())
            man = json.loads(mf.read_text(encoding="utf-8"))
            ck("before: the run is stamped with the ticket", man["ticket"] == "DEMO#08597")
            ck("before: the affected page was found",
               [p["page_id"] for p in man["affected"]["pages"]] == ["fix:index"])
            ck("before: shots were recorded", len(man["shots"]["before"]) >= 1)
            ck("before: the base shot exists on disk",
               Path(man["shots"]["before"]["fix_index__base"]["png_path"]).exists())
            ck("before: an anchor came out of the template (it is what maps a "
               "stylesheet to a page)",
               any(a["kind"] == "id" for a in man["affected"]["anchors"]))
            ck("before: there are no pairs yet", man["pairs"] == [])
            first_png = man["shots"]["before"]["fix_index__base"]["png_path"]
            first_bytes = Path(first_png).read_bytes()

            # A second `before` for the same ticket must NOT overwrite the
            # baseline: it is the state from BEFORE the first edit.
            (repo / tpl).write_text(TEMPLATE_V2, encoding="utf-8")
            fixture_site.write_page(site, fixture_site.V2)
            code2, out2, _ = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#08597"], shots)
            ck("before twice: exit 0", code2 == 0)
            ck("before twice: it says the page is already captured",
               "already captured" in out2)
            ck("before twice: the original pre-edit shot is untouched",
               Path(first_png).read_bytes() == first_bytes)

            code3, out3, err3 = _run(["after", "--repo", str(repo), "--files", tpl,
                                      "--ticket", "DEMO#08597"], shots)
            ck("after: exit 0 (%s)" % (err3.strip()[-160:] or "no stderr"), code3 == 0)
            man = json.loads((shots / "demo_08597" / "manifest.json").read_text(encoding="utf-8"))
            pairs = {p["pair_id"]: p for p in man["pairs"]}
            ck("after: the base state is paired", "fix_index__base" in pairs)
            base_pair = pairs.get("fix_index__base") or {}
            for side in ("before", "after"):
                ck("after: the %s full image exists" % side,
                   Path(base_pair[side]["full"]).exists())
            ck("after: the pictures are the captures themselves, not copies",
               base_pair["before"]["full"]
               == man["shots"]["before"]["fix_index__base"]["png_path"])
            ck("after: the caption is at most ten words",
               1 <= len(base_pair["caption"].split()) <= 10)
            ck("after: the caption names the screen",
               "Grupe" in base_pair["caption"])
            ck("after: a structurally changed page is kept",
               base_pair["drop"] is False)
            ck("after: the sweep verdict rides along",
               base_pair["sweep"]["structural"] is True)
            ck("after: why explains the mapping",
               base_pair["why"] == ["template changed"])
            ck("after: modal/dropdown states were paired too", len(pairs) >= 2)
            # `<details>` opens one line of text: a second picture of the same
            # screen, and the trace of that decision has to REACH the manifest.
            collapsed = [r for r in man["skipped"] if r.get("collapsed")]
            ck("after: a collapsed state is on record with the run",
               bool(collapsed) and collapsed[0].get("stage") in ("before", "after"))
            ck("after: naming the state it repeats and the page it was on",
               bool(collapsed[0].get("same_as")) and bool(collapsed[0].get("page_id")))
            ck("after: a state with no structural change is marked, not deleted",
               all(("drop" in p and "drop_reason" in p) for p in man["pairs"])
               and all(p["drop"] is False or p["drop_reason"]
                       for p in man["pairs"]))
        finally:
            stop()


#: A menu template shaped like the one that produced the defect: dozens of long,
#: distinctive, UNTOUCHED strings, plus a material-icon name. Anchors are capped
#: at 40 per page for measurement and ranked by kind then length, so a short
#: added string sits behind all of these - and the box fell to whatever WAS
#: measured, which was never the change.
NAV_ITEMS = ["Stavka %02d" % i for i in range(45)]
NAV_MARKUP = "\n".join('  <span class="nav-text">%s</span>' % t for t in NAV_ITEMS)
NAV_ICON = '  <i class="material-icons">precision_manufacturing</i>'
NAV_TPL_V1 = '<div class="menu" >\n%s\n%s\n</div>\n' % (NAV_MARKUP, NAV_ICON)
NAV_TPL_V2 = ('<div class="menu" >\n%s\n%s\n  <span class="nav-text">Gradovi</span>\n'
              '</div>\n' % (NAV_MARKUP, NAV_ICON))
#: The one-line change that renders on NO screen - a block behind a permission,
#: a template only used by another view. The engine must say so, not point at
#: something else.
NAV_TPL_INVISIBLE = ('<div class="menu" >\n%s\n%s\n'
                     '  <span id="nikad-prikazano">Nikad prikazano</span>\n'
                     '</div>\n' % (NAV_MARKUP, NAV_ICON))

NAV_PAGE_V1 = {"title": "Administracija", "subtitle": NAV_MARKUP + "\n" + NAV_ICON,
               "stamp": "pre 2 minuta", "modal_extra": ""}
NAV_PAGE_V2 = dict(NAV_PAGE_V1,
                   subtitle=NAV_PAGE_V1["subtitle"]
                   + '\n<span class="nav-text">Gradovi</span>')
#: Same tree, same layout, different NUMBERS - the dev database is the
#: production one, so this is what a page does between two runs on its own.
NAV_PAGE_DATA = dict(NAV_PAGE_V1,
                     subtitle=NAV_PAGE_V1["subtitle"]
                     + '\n<span class="nav-count">1.234.567.890 8.901.234.567</span>')
NAV_PAGE_DATA2 = dict(NAV_PAGE_V1,
                      subtitle=NAV_PAGE_V1["subtitle"]
                      + '\n<span class="nav-count">9.876.543.210 1.098.765.432</span>')

NAV_STATES = {"auto": False, "max_per_page": 1, "manual": {}}
NAV_PAGES = [{"page_id": "fix:index", "url": "/index.html",
              "title": "Administracija", "template": "myapp/index.html"}]


def _nav_repo(root, base_url, template=NAV_TPL_V1):
    return _repo(root, base_url, states=NAV_STATES, template=template,
                 pages=NAV_PAGES)


def test_the_pair_is_two_pictures_and_a_screen_name():
    """The operator, 2026-08-21: "odbaci poredjenja koja su napravljena jer ne
    valjaju - ne prikazuju poredjenje sta je stvarno uradjeno vec ikonice i
    nebuloze."

    What the customer received before this: `novo: Novi korisnik: analytics
    analitika: "location_city"` on a boxed rectangle. Every part of that after
    the screen name is the engine's evidence about itself - `location_city` is a
    material-icon name that really did come out of the diff and really does mean
    nothing to the person reading the ticket.

    This fixture is the one that produced it: 45 untouched strings, a material
    icon, and a one-line change that really does render. The pair may name the
    screen and nothing else, whatever the engine believes it found.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots = d / "shots"
        site = d / "site"
        fixture_site.write_page(site, NAV_PAGE_V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _nav_repo(d / "repo", base)
            code, out, err = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#pair"], shots)
            ck("pair: before exits 0 (%s)" % (err.strip()[-160:] or "no stderr"),
               code == 0)

            # ONE line added to the template, and the same one line on the page.
            (repo / tpl).write_text(NAV_TPL_V2, encoding="utf-8")
            fixture_site.write_page(site, NAV_PAGE_V2)
            code2, out2, err2 = _run(["after", "--repo", str(repo), "--files", tpl,
                                      "--ticket", "DEMO#pair"], shots)
            ck("pair: after exits 0 (%s)" % (err2.strip()[-160:] or "no stderr"),
               code2 == 0)
            man = json.loads((shots / "demo_pair" / "manifest.json").read_text(encoding="utf-8"))
            pair = (man["pairs"] or [{}])[0]

            ck("pair: the before picture is there",
               Path(pair["before"]["full"]).is_file())
            ck("pair: the after picture is there",
               Path(pair["after"]["full"]).is_file())
            ck("pair: the caption is the screen name",
               pair.get("caption") == "Administracija")
            ck("pair: it quotes no token at all - not even the right one",
               not any(tok in pair.get("caption", "")
                       for tok in ["Gradovi", "precision_manufacturing", '"']
                       + NAV_ITEMS))
            ck("pair: and no side is claimed to be new",
               "nov" not in pair.get("caption", "").lower())
            for key in ("anchor", "box", "located", "only_side", "label",
                        "pixel_ratio"):
                ck("pair: %r is not published" % key, key not in pair)
            ck("pair: no derived image was made at all",
               not (shots / "demo_pair" / "pairs").exists())
            ck("pair: the screen it is a picture of is still named",
               pair.get("page_id") == "fix:index"
               and str(pair.get("url") or "").endswith("/index.html"))

            # The one thing that still decides anything: this page really did
            # change, so the pair is kept.
            ck("pair: the structural sweep still runs",
               (pair.get("sweep") or {}).get("structural") is True)
            ck("pair: and it is what keeps the pair", pair.get("drop") is False)
        finally:
            stop()


#: A shared layout and three screens that extend it. Two are in the change ONLY
#: because the layout is; the third has its own template in the diff as well,
#: which is the case that must never be collapsed into somebody else.
#:
#: The urls are chosen so that "shortest path" and "alphabetically first" give
#: DIFFERENT answers: a test that cannot tell the rule from the discovery order
#: does not test the rule.
LAY_BASE_V1 = """<!doctype html>
<html><body><nav class="sidebar"><a href="/x/" class="list-group-item">Lokacije</a></nav>
{% block content %}{% endblock %}</body></html>
"""
LAY_BASE_V2 = """<!doctype html>
<html><body><nav class="sidebar"><a href="/x/" class="list-group-item">Lokacije</a>
<a href="/y/" class="list-group-item" id="gradovi-tab">Gradovi</a></nav>
{% block content %}{% endblock %}</body></html>
"""
LAY_PAGE = """{% extends "myapp/base_lay.html" %}
{% block content %}<div class="card" id="__NAME__-card">Sadrzaj</div>{% endblock %}
"""
LAY_OWN_V2 = """{% extends "myapp/base_lay.html" %}
{% block content %}<div class="card" id="own-card">Sadrzaj
<p class="card-subtitle" id="own-subtitle">Podnaslov ispod naziva</p></div>{% endblock %}
"""
LAY_PAGES = [
    {"page_id": "app:aaa", "url": "/aaa-dugacka-putanja.html", "title": "Ekran A",
     "template": "myapp/aaa.html"},
    {"page_id": "app:zz", "url": "/z.html", "title": "Ekran Z",
     "template": "myapp/zz.html"},
    {"page_id": "app:own", "url": "/own.html", "title": "Ekran sa svojom izmenom",
     "template": "myapp/own.html"},
]


def _layout_repo(root, base_url):
    """A repo whose three pages all extend one layout."""
    root = Path(root)
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    tdir = root / "myapp" / "templates" / "myapp"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "base_lay.html").write_text(LAY_BASE_V1, encoding="utf-8")
    for name in ("aaa", "zz", "own"):
        (tdir / ("%s.html" % name)).write_text(
            LAY_PAGE.replace("__NAME__", name), encoding="utf-8")
    (root / ".claude" / "visual-diff.json").write_text(json.dumps({
        "base_url": base_url,
        "auth": {"kind": "none"},
        "server": {"health": base_url + "/", "start": []},
        "pages": {"kind": "list", "pages": LAY_PAGES},
        "template_globs": ["*/templates/**/*.html"],
        "static_globs": ["static/**/*.css"],
        "ignore_selectors": [".timestamp"],
        "states": {"auto": False, "max_per_page": 1, "manual": {}},
    }), encoding="utf-8")
    _commit(root, "initial")
    return root


def test_a_shared_layout_is_photographed_once_not_once_per_screen():
    """The operator, looking at thirty pairs: "prikazuju se jer je na side bar
    dodat tab, a ne jer postoji izmena."

    A menu item added to a layout reaches every screen that extends it, and the
    layout renders the SAME markup on all of them. One picture is the story; the
    rest are copies of it. A screen whose OWN template is in the same diff is a
    different matter and keeps its pair.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, site = d / "shots", d / "site"
        for name in ("aaa-dugacka-putanja", "z", "own"):
            fixture_site.write_page(site, fixture_site.V1, "%s.html" % name)
        base, stop = fixture_site.serve(site)
        try:
            repo = _layout_repo(d / "repo", base)
            # The gate names the file about to be edited, which is what puts
            # the layout in the change set before the edit exists.
            lay = "myapp/templates/myapp/base_lay.html"
            own = "myapp/templates/myapp/own.html"
            code, out, err = _run(["before", "--repo", str(repo), "--files",
                                   lay, own, "--ticket", "DEMO#lay"], shots)
            ck("layout: before exits 0 (%s)" % (err.strip()[-160:] or "no stderr"),
               code == 0)

            tdir = repo / "myapp" / "templates" / "myapp"
            (tdir / "base_lay.html").write_text(LAY_BASE_V2, encoding="utf-8")
            (tdir / "own.html").write_text(LAY_OWN_V2, encoding="utf-8")
            for name in ("aaa-dugacka-putanja", "z", "own"):
                fixture_site.write_page(site, fixture_site.V2, "%s.html" % name)
            code2, out2, err2 = _run(["after", "--repo", str(repo),
                                      "--ticket", "DEMO#lay"], shots)
            ck("layout: after exits 0 (%s)" % (err2.strip()[-160:] or "no stderr"),
               code2 == 0)
            man = json.loads((shots / "demo_lay" / "manifest.json").read_text(encoding="utf-8"))
            pairs = {p["page_id"]: p for p in man["pairs"]}
            ck("layout: all three screens were captured", len(pairs) == 3)

            own = pairs.get("app:own") or {}
            ck("layout: the screen with its own change is never collapsed",
               not own.get("represented_by"))
            ck("layout: and its own template is why it is here",
               "template changed" in (own.get("why") or []))

            rep = [p for p in man["pairs"] if p.get("represents")]
            ck("layout: exactly one representative for the layout", len(rep) == 1)
            rep = rep[0] if rep else {}
            # /z.html is the SHORTEST path; app:aaa is alphabetically first. The
            # rule is the data, not the order.
            ck("layout: the representative is the shortest path, not the first id",
               rep.get("page_id") == "app:zz")
            ck("layout: it names every screen it stands for",
               rep.get("represents") == ["app:aaa"])
            ck("layout: and says so in the caption",
               rep.get("caption", "").endswith("+ 1 drugi ekran"))

            other = pairs.get("app:aaa") or {}
            ck("layout: the other one is hidden, not deleted",
               other.get("represented_by") == "app:zz" and other.get("drop") is True)
            ck("layout: with a reason that names the representative",
               "app:zz" in (other.get("drop_reason") or ""))
            ck("layout: its pictures are still on record",
               Path(other["before"]["full"]).is_file()
               and Path(other["after"]["full"]).is_file())
        finally:
            stop()


def test_the_pair_carries_every_changed_region_enlarged():
    """The other half of what a customer gets: the changed regions of the screen,
    enlarged, one entry per region. Not a zoom on the element - the block it sits
    in ("ne bukvalno full zoom na element vec na div u kom se nalazi") - and not
    one per page either, because a change usually touches several places.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, site = d / "shots", d / "site"
        fixture_site.write_page(site, fixture_site.V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _repo(d / "repo", base,
                              states={"auto": False, "max_per_page": 1, "manual": {}})
            _run(["before", "--repo", str(repo), "--files", tpl,
                  "--ticket", "DEMO#crop"], shots)
            (repo / tpl).write_text(TEMPLATE_V2, encoding="utf-8")
            fixture_site.write_page(site, fixture_site.V2)
            code, out, err = _run(["after", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#crop"], shots)
            ck("crop: after exits 0 (%s)" % (err.strip()[-160:] or "no stderr"),
               code == 0)
            man = json.loads((shots / "demo_crop" / "manifest.json").read_text(encoding="utf-8"))
            pair = (man["pairs"] or [{}])[0]
            regions = pair.get("regions") or []
            ck("crop: the pair carries a LIST of regions", bool(regions))
            ck("crop: and says how many there were before the cap",
               pair.get("regions_found", 0) >= len(regions))
            ck("crop: never more than the cap", len(regions) <= vshoot.MAX_REGIONS)
            ck("crop: the single-crop keys are gone, one truth per pair",
               "crop" not in pair["after"] and "crop_from" not in pair)
            first = regions[0] if regions else {}
            ck("crop: each region names the side its rectangle came from",
               first.get("crop_from") in ("before", "after"))
            ck("crop: and what kind of block it is",
               isinstance(first.get("crop_kind"), str) and first.get("crop_kind"))
            ck("crop: the after half is on disk",
               Path(first["after"]["crop"]).is_file())
            from PIL import Image
            if first.get("before", {}).get("crop"):
                with Image.open(first["before"]["crop"]) as b, \
                        Image.open(first["after"]["crop"]) as a:
                    ck("crop: the two halves are the same size, so they compare",
                       b.size == a.size)
                    ck("crop: and smaller than the page they came from",
                       b.width <= 1440 * vannotate.CROP_MAX_SCALE)
            else:
                ck("crop: a region with no honest before says so", True)
            ck("crop: the full pictures are still the plain captures",
               pair["after"]["full"] == man["shots"]["after"]["fix_index__base"]["png_path"])
            ck("crop: and the caption still claims nothing",
               '"' not in pair["caption"] and "nov" not in pair["caption"].lower())
        finally:
            stop()


ALIGN_TPL_V1 = """<div class="container">
  <div class="card" id="target">Stara vrednost</div>
</div>
"""
ALIGN_TPL_V2 = """<div class="container">
  <div class="spacer" id="ubaceno">Ubaceno iznad</div>
  <div class="card" id="target">Nova vrednost</div>
</div>
"""


def test_regions_are_offered_in_reading_order():
    """Top to bottom in PAGE coordinates, then left to right - the order the
    customer scrolls through them. Anchor rank decides what is worth showing; it
    has nothing to do with where the things are on the screen."""
    def shot(boxes, side="after"):
        return {"regions": {"id:a%d" % i: [{"element": b, "block": b, "kind": "div"}]
                            for i, b in enumerate(boxes)}}

    boxes = [{"x": 40, "y": 900, "w": 300, "h": 60},
             {"x": 900, "y": 200, "w": 300, "h": 60},
             {"x": 40, "y": 200, "w": 300, "h": 60}]
    anchors = [{"kind": "id", "value": "a%d" % i, "from_diff": True}
               for i in range(len(boxes))]
    specs = vshoot._region_specs(shot(boxes), {"regions": {}}, anchors)
    tops = [sp["rect"][1] for sp in specs]
    ck("order: sorted top to bottom", tops == sorted(tops))
    ck("order: and left to right within a row",
       specs[0]["rect"][0] < specs[1]["rect"][0])
    ck("order: nothing was lost on the way", len(specs) == 3)


CLASS_ONLY_V1 = """<div class="card" id="taskGroups">
  <h2 class="card-title">Grupe zadataka</h2>
</div>
"""
CLASS_ONLY_V2 = """<div class="card" id="taskGroups">
  <h2 class="card-title">Grupe zadataka</h2>
  <div class="hr-info-row"><span class="hr-info-row__value"></span></div>
</div>
"""


def test_a_class_is_not_a_region_no_matter_how_much_it_wants_to_be():
    """The operator, twice: a crop of an untouched element is worse than no crop.

    A hunk whose only readable anchors are CLASSES gives the engine nothing that
    names one element - `hr-info-row` is on every row of the card. Cropping to
    "the first few elements carrying it" outlined six untouched rows and missed
    the one that was new. There is no fallback: the pair ships as two full
    pictures and says nothing.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, site = d / "shots", d / "site"
        fixture_site.write_page(site, fixture_site.V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _repo(d / "repo", base, template=CLASS_ONLY_V1,
                              states={"auto": False, "max_per_page": 1, "manual": {}})
            _run(["before", "--repo", str(repo), "--files", tpl,
                  "--ticket", "DEMO#cls"], shots)
            (repo / tpl).write_text(CLASS_ONLY_V2, encoding="utf-8")
            fixture_site.write_page(site, fixture_site.V2)
            code, out, err = _run(["after", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#cls"], shots)
            ck("class-only: after exits 0 (%s)" % (err.strip()[-160:] or "no stderr"),
               code == 0)
            man = json.loads((shots / "demo_cls" / "manifest.json").read_text(encoding="utf-8"))
            pair = (man["pairs"] or [{}])[0]
            kinds = {a["kind"] for a in man["affected"]["anchors"] if a["from_diff"]}
            ck("class-only: the change really did yield class anchors only",
               kinds == {"class"})
            ck("class-only: and NOT ONE region was invented", pair.get("regions") == [])
            ck("class-only: nor counted as found", pair.get("regions_found") == 0)
            ck("class-only: the pair still ships, as two full pictures",
               Path(pair["before"]["full"]).is_file()
               and Path(pair["after"]["full"]).is_file())
            ck("class-only: and still claims nothing",
               '"' not in pair["caption"])
        finally:
            stop()


def test_a_region_is_cut_where_its_content_is_not_where_its_pixels_were():
    """THE INSERTION DEFECT. The change adds 400px above a block, so on the after
    page that block sits 400px lower. Cutting both sides at the same page
    coordinates gave a pair whose halves showed two different parts of one form -
    "Nivo" on one side, "Sifra"/"Region" on the other.

    The fixture makes it unmissable: the block is solid green, and everything
    else is white. The before half MUST contain green, which the same-coordinate
    cut cannot.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, site = d / "shots", d / "site"
        fixture_site.write_align_page(site, fixture_site.ALIGN_V1, "index.html")
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _repo(d / "repo", base, template=ALIGN_TPL_V1,
                              states={"auto": False, "max_per_page": 1, "manual": {}})
            code, out, err = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#align"], shots)
            ck("align: before exits 0 (%s)" % (err.strip()[-160:] or "no stderr"),
               code == 0)

            (repo / tpl).write_text(ALIGN_TPL_V2, encoding="utf-8")
            fixture_site.write_align_page(site, fixture_site.ALIGN_V2, "index.html")
            code2, out2, err2 = _run(["after", "--repo", str(repo), "--files", tpl,
                                      "--ticket", "DEMO#align"], shots)
            ck("align: after exits 0 (%s)" % (err2.strip()[-160:] or "no stderr"),
               code2 == 0)
            man = json.loads((shots / "demo_align" / "manifest.json").read_text(encoding="utf-8"))
            pair = (man["pairs"] or [{}])[0]
            regions = pair.get("regions") or []
            target = None
            for r in regions:
                if r.get("before", {}).get("crop"):
                    target = r
                    break
            ck("align: the moved block produced a region with both halves",
               target is not None)
            if not target:
                return
            from PIL import Image

            def greenish(path):
                with Image.open(path).convert("RGB") as img:
                    px = list(img.getdata())
                return sum(1 for r, g, b in px if g > 100 and g > r + 40
                           and g > b + 40) / float(len(px))

            ck("align: the after half shows the block",
               greenish(target["after"]["crop"]) > 0.05)
            ck("align: THE BEFORE HALF SHOWS THE SAME BLOCK, 400px higher up",
               greenish(target["before"]["crop"]) > 0.05)
            with Image.open(target["before"]["crop"]) as b, \
                    Image.open(target["after"]["crop"]) as a:
                ck("align: and the two are still the same size", b.size == a.size)
        finally:
            stop()


def test_a_change_that_renders_nowhere_is_still_a_plain_pair():
    """The half that used to carry a note: when nothing from the diff could be
    found on the screen the caption said "izmena nije lokalizovana". That is the
    engine talking about itself on a card meant for a customer - and on a pair
    that never had a box, it is noise on every card. The pair is the two
    pictures and the screen name, in this case as in every other."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots = d / "shots"
        site = d / "site"
        fixture_site.write_page(site, NAV_PAGE_V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _nav_repo(d / "repo", base)
            _run(["before", "--repo", str(repo), "--files", tpl,
                  "--ticket", "DEMO#nobox"], shots)
            # The template gains an element the page never renders.
            (repo / tpl).write_text(NAV_TPL_INVISIBLE, encoding="utf-8")
            fixture_site.write_page(site, NAV_PAGE_DATA)
            code, out, err = _run(["after", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#nobox"], shots)
            ck("no box: after exits 0 (%s)" % (err.strip()[-160:] or "no stderr"),
               code == 0)
            man = json.loads((shots / "demo_nobox" / "manifest.json").read_text(encoding="utf-8"))
            pair = (man["pairs"] or [{}])[0]
            ck("no box: the pair is still offered",
               bool(pair) and Path(pair["before"]["full"]).is_file()
               and Path(pair["after"]["full"]).is_file())
            ck("no box: the caption is the screen name, with no note on it",
               pair.get("caption") == "Administracija")
            ck("no box: the caption invents no token",
               not any(tok in pair.get("caption", "")
                       for tok in ["precision_manufacturing", "Nikad"] + NAV_ITEMS))
        finally:
            stop()


def test_a_page_whose_numbers_moved_is_not_reported_as_a_change():
    """The operator: "kod pixel diff vide se i svi grafikoni i inputi u bazi koji
    su se promenili kao izmene a zapravo su samo promenjeni podaci."

    The dev database IS production. Same DOM tree, same block geometry,
    different digits: the PICTURES differ, and that must not be a change."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots = d / "shots"
        site = d / "site"
        fixture_site.write_page(site, NAV_PAGE_DATA)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _nav_repo(d / "repo", base)
            _run(["before", "--repo", str(repo), "--files", tpl,
                  "--ticket", "DEMO#data"], shots)
            fixture_site.write_page(site, NAV_PAGE_DATA2)      # only the digits move
            code, out, err = _run(["after", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#data"], shots)
            ck("data: after exits 0 (%s)" % (err.strip()[-160:] or "no stderr"),
               code == 0)
            man = json.loads((shots / "demo_data" / "manifest.json").read_text(encoding="utf-8"))
            pair = (man["pairs"] or [{}])[0]
            ck("data: the two screenshots really do differ",
               Path(pair["before"]["full"]).read_bytes()
               != Path(pair["after"]["full"]).read_bytes())
            ck("data: the structural verdict says no change",
               (pair.get("sweep") or {}).get("structural") is False)
            # ...BUT THIS PAGE'S OWN TEMPLATE IS IN THE CHANGE SET, and the
            # comparison is blind to TEXT. "Same tree, same layout" therefore
            # cannot tell an edit structure cannot see (a corrected label, a
            # reworded button) from a counter that moved by itself — and
            # dropping is much the more expensive mistake, because it hides the
            # ONLY pair a wording ticket has. DEMO#53464 ("Zapremnica" ->
            # "Primka") was exactly that and the operator was shown nothing.
            # Same principle the `comparable: false` branch already applies: a
            # page we cannot judge is the one somebody has to look at. A page
            # pulled in only by a SHARED file keeps the drop — its `why`
            # carries no `template changed`.
            ck("data: the pair is KEPT, because this page's own template changed",
               pair.get("drop") is False)
            ck("data: the structural verdict is still recorded for the reader",
               "same tree" in ((pair.get("sweep") or {}).get("why") or ""))
            ck("data: no pixel ratio is published at all", "pixel_ratio" not in pair)
        finally:
            stop()


def test_the_baseline_command_seeds_the_rolling_store():
    """`baseline` seeds `<repo>/.visual-baseline/` over the inventory, so the
    first ticket to touch any screen already has a "before".

    It keeps the phash and the thumb (the cheap identity check and the
    recognition aid) and now also keeps the FULL picture - which is the whole
    point: a hash cannot be shown to a customer as the previous state.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        site = d / "site"
        fixture_site.write_page(site, NAV_PAGE_V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, _ = _nav_repo(d / "repo", base)
            code, out, err = _run(["baseline", "--repo", str(repo)], d / "shots")
        finally:
            stop()
        ck("baseline: exit 0 (%s)" % (err.strip()[-160:] or "no stderr"), code == 0)
        root = repo / ".visual-baseline"
        data = json.loads((root / "index.json").read_text(encoding="utf-8"))
        ck("baseline: the index is in the app repo", (root / "index.json").exists())
        ck("baseline: it is keyed by page AND state",
           list(data["shots"]) == ["fix_index__base"])
        row = data["shots"]["fix_index__base"]
        ck("baseline: the entry names its page and state",
           row["page_id"] == "fix:index" and row["state"] == "base")
        ck("baseline: the hash is 16 hex chars", len(row["phash"]) == 16)
        ck("baseline: the full picture is stored", (root / row["png"]).is_file())
        ck("baseline: with the capture record beside it",
           (root / row["meta"]).is_file()
           and json.loads((root / row["meta"]).read_text(encoding="utf-8"))["dom_signature"])
        thumb = root / row["thumb"]
        ck("baseline: the thumb is still there and still small",
           thumb.exists() and thumb.stat().st_size < 30 * 1024)
        ck("baseline: the console says it is not for git",
           "gitignore" in out)


def test_the_rolling_baseline_is_the_before_and_promotion_is_explicit():
    """The design change: `before` READS the stored baseline instead of
    capturing, and `promote` makes an approved `after` the new one.

    What it removes: a real session had to stash the work, restart the server,
    shoot, unstash and restart again - twice - to get a pre-edit picture, which
    is why that ticket ended up with no baseline at all.

    What it must not break: the picture handed to a customer as "before" is the
    one from before THEIR change, the old one is archived rather than dropped,
    and promoting twice does neither twice.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, site = d / "shots", d / "site"
        fixture_site.write_page(site, NAV_PAGE_V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _nav_repo(d / "repo", base)
            root = repo / ".visual-baseline"
            _run(["baseline", "--repo", str(repo)], shots)
            v1_bytes = (root / "shots" / "fix_index__base.png").read_bytes()

            # The edit is ALREADY made and already live - the case that used to
            # force the stash dance, and the case the operator asked for.
            (repo / tpl).write_text(NAV_TPL_V2, encoding="utf-8")
            fixture_site.write_page(site, NAV_PAGE_V2)

            code, out, err = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#roll"], shots)
            ck("rolling: before exits 0 (%s)" % (err.strip()[-160:] or "no stderr"),
               code == 0)
            ck("rolling: it says the picture came from the baseline",
               "from the stored baseline" in out)
            man = json.loads((shots / "demo_roll" / "manifest.json").read_text(encoding="utf-8"))
            bshot = man["shots"]["before"]["fix_index__base"]
            ck("rolling: the before shot is marked as baseline-sourced",
               bshot.get("source") == "baseline")
            ck("rolling: and it IS the stored picture, not a fresh capture of "
               "the changed page",
               Path(bshot["png_path"]).read_bytes() == v1_bytes)
            ck("rolling: the run keeps its own copy of it",
               Path(bshot["png_path"]).parent.name == "before")
            ck("rolling: the file counts as covered on the before side",
               json.loads((shots / "demo_roll" / "state.json").read_text(
                   encoding="utf-8"))["before"]["files"] == [tpl])

            code2, out2, err2 = _run(["after", "--repo", str(repo), "--files", tpl,
                                      "--ticket", "DEMO#roll"], shots)
            ck("rolling: after exits 0 (%s)" % (err2.strip()[-160:] or "no stderr"),
               code2 == 0)
            man = json.loads((shots / "demo_roll" / "manifest.json").read_text(encoding="utf-8"))
            pair = (man["pairs"] or [{}])[0]
            ck("rolling: the pair is a real pair", pair.get("baseline") == "captured")
            # A picture adopted from the baseline was taken for whichever
            # ticket promoted it, so anything this run might "notice" about it
            # is about that ticket's change. The pair says what it always says.
            ck("rolling: an adopted before is shown, and nothing is claimed of it",
               pair.get("caption") == "Administracija"
               and Path(pair["before"]["full"]).is_file())

            # --- promotion ------------------------------------------------
            code3, out3, _ = _run(["promote", "--repo", str(repo),
                                   "--ticket", "DEMO#roll"], shots)
            ck("promote: exit 0", code3 == 0)
            after_png = Path(man["shots"]["after"]["fix_index__base"]["png_path"])
            ck("promote: the after picture became the baseline",
               (root / "shots" / "fix_index__base.png").read_bytes()
               == after_png.read_bytes())
            archived = list((root / "archive").rglob("fix_index__base.png"))
            ck("promote: the picture it replaced was archived, not deleted",
               len(archived) == 1 and archived[0].read_bytes() == v1_bytes)
            ck("promote: the run records that it happened",
               bool(json.loads((shots / "demo_roll" / "manifest.json").read_text(
                   encoding="utf-8")).get("promoted")))
            ck("promote: the ticket's OWN before image is untouched by promotion",
               Path(bshot["png_path"]).read_bytes() == v1_bytes)

            code4, out4, _ = _run(["promote", "--repo", str(repo),
                                   "--ticket", "DEMO#roll", "--json"], shots)
            again = _json_line(out4) or {}
            ck("promote: running it twice promotes nothing (idempotent)",
               code4 == 0 and again.get("promoted") == []
               and again.get("unchanged") == ["fix_index__base"])
            ck("promote: and archives nothing the second time",
               len(list((root / "archive").rglob("fix_index__base.png"))) == 1)

            # The next ticket's "before" is the picture we just promoted.
            code5, out5, _ = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--ticket", "DEMO#next"], shots)
            man2 = json.loads((shots / "demo_next" / "manifest.json").read_text(encoding="utf-8"))
            nxt = man2["shots"]["before"]["fix_index__base"]
            ck("rolling: the next ticket starts from the promoted picture",
               code5 == 0 and Path(nxt["png_path"]).read_bytes() == after_png.read_bytes())
        finally:
            stop()


def test_promotion_refuses_a_shot_that_is_not_a_good_picture_of_the_page():
    """Offline, no browser: the rules that keep a broken screen out of the
    baseline. Whatever is promoted becomes what the NEXT ticket shows a customer
    as "before", so an error page, a page that did not load, or a shot whose
    file is gone must be refused with a reason - never promoted quietly."""
    from PIL import Image
    from visual import shoot

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, repo = d / "shots", d / "repo"
        (repo / ".claude").mkdir(parents=True)
        old = os.environ.get("BRAIN_SHOTS_DIR")
        os.environ["BRAIN_SHOTS_DIR"] = str(shots)
        try:
            def png(name, colour):
                p = shots / "k" / "after" / name
                p.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (120, 90), colour).save(p)
                return str(p)

            good = png("good.png", (240, 240, 240))
            broken = png("broken.png", (10, 10, 10))
            shoot.update_json(shots / "k" / "manifest.json", lambda cur: {
                "repo": str(repo), "shots": {"before": {}, "after": {
                    "p_ok__base": {"page_id": "p:ok", "state": "base", "status": 200,
                                   "url": "/ok/", "title": "Ok", "png_path": good,
                                   "dom_signature": "abc", "geometry": []},
                    "p_500__base": {"page_id": "p:500", "state": "base", "status": 500,
                                    "url": "/500/", "png_path": broken},
                    "p_gone__base": {"page_id": "p:gone", "state": "base", "status": 200,
                                     "url": "/gone/", "png_path": str(d / "nope.png")},
                    "p_err__base": {"page_id": "p:err", "state": "base", "status": 200,
                                    "url": "/err/", "png_path": broken}}},
                "errors": [{"stage": "after", "page_id": "p:err", "error": "timeout"}]})

            res = shoot.promote_baseline("k", repo=repo)
            reasons = {r["pair_id"]: r["reason"] for r in res["skipped"]}
            ck("promote: only the good shot is promoted",
               res["promoted"] == ["p_ok__base"])
            ck("promote: an http error page is refused, with the status",
               "500" in reasons.get("p_500__base", ""))
            ck("promote: a shot whose file is gone is refused",
               "no picture on disk" in reasons.get("p_gone__base", ""))
            ck("promote: a page whose capture errored is refused",
               "error" in reasons.get("p_err__base", ""))
            ck("promote: nothing was archived on a first write", res["archived"] == [])

            idx = shoot.load_baseline(repo)
            ck("promote: the index holds one page+state",
               list(idx["shots"]) == ["p_ok__base"])
            ck("promote: the picture is in the store",
               (shoot.baseline_dir(repo) / idx["shots"]["p_ok__base"]["png"]).is_file())

            same = shoot.promote_baseline("k", repo=repo)
            ck("promote: the same bytes promote again as unchanged",
               same["promoted"] == [] and same["unchanged"] == ["p_ok__base"])

            Image.new("RGB", (120, 90), (12, 200, 12)).save(good)
            moved = shoot.promote_baseline("k", repo=repo)
            ck("promote: a new picture replaces it", moved["promoted"] == ["p_ok__base"])
            ck("promote: and the previous one is archived, not deleted",
               len(moved["archived"]) == 1 and Path(moved["archived"][0]).is_file())

            # Adoption: the run that promoted a picture must never get it back
            # as its own "before" - that is the after shot standing in for the
            # before, which is the one thing the engine may never do.
            cfg = {"base_url": ""}
            rows = [{"page_id": "p:ok", "title": "Ok", "why": []}]
            mine, pages, notes = shoot.adopt_baseline_shots(
                repo, cfg, d / "dest_mine", rows, run_key="k")
            ck("adopt: a run does not adopt its own promotion",
               mine == {} and pages == set() and any("promoted by this run" in n
                                                     for n in notes))
            theirs, pages2, _ = shoot.adopt_baseline_shots(
                repo, cfg, d / "dest_other", rows, run_key="other")
            ck("adopt: another run does adopt it",
               list(theirs) == ["p_ok__base"] and pages2 == {"p:ok"})
            shot = theirs["p_ok__base"]
            ck("adopt: the shot is marked baseline-sourced",
               shot["source"] == "baseline")
            ck("adopt: the picture is copied into the run's own folder",
               Path(shot["png_path"]).parent == d / "dest_other")
            ck("adopt: the capture record travels with it",
               shot.get("dom_signature") == "abc")
        finally:
            if old is None:
                os.environ.pop("BRAIN_SHOTS_DIR", None)
            else:
                os.environ["BRAIN_SHOTS_DIR"] = old


def _new_page_map(repo):
    """A fixture razvojna-mapa with ONE new screen in it, reachable by a path the
    mapper wrote down."""
    data = repo / "razvojna-mapa" / "_data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "settings.json").write_text(json.dumps({
        "app": "settings", "url_prefix": "/settings/", "pages": [
            {"id": "settings:city_bulk", "title_sr": "Grupni unos gradova",
             "url": "/settings/cities/bulk/", "url_name": "city_bulk",
             "template": "settings/templates/settings/bulk.html",
             "reached_from_sr": ["Lista gradova (Podesavanja → Gradovi) → "
                                 "dugme 'Grupni unos'"]}]}), encoding="utf-8")
    tpl = repo / "settings" / "templates" / "settings" / "bulk.html"
    tpl.parent.mkdir(parents=True, exist_ok=True)
    tpl.write_text("<h1>x</h1>", encoding="utf-8")
    return {"pages": {"kind": "razvojna-mapa", "path": "razvojna-mapa/_data"}}


def test_a_screen_that_did_not_exist_before_is_a_pair_not_a_footnote():
    """A page whose own screen is NEW has no before shot, and used to end up in
    `unpaired` - a diagnostic row the HUD never rendered, so the customer was
    never told the screen exists.

    It is a pair now: one picture, no comparison. What it must NOT do is claim
    novelty for every unpaired record - a page whose BEFORE capture failed looks
    identical on disk and is the one an operator most needs to see as a failure.
    """
    from PIL import Image
    from visual import shoot

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        repo = d / "repo"
        cfg = _new_page_map(repo)

        def png(name):
            p = d / "after" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (60, 40), (9, 9, 9)).save(p)
            return str(p)

        after = {
            "settings_city_bulk__base": {
                "page_id": "settings:city_bulk", "state": "base", "status": 200,
                # the browser title names the LIST screen, and the url carries the
                # operator's dev host: neither may reach a customer
                "title": "Gradovi | Kontrola - Kontrola",
                "url": "http://acme.lvh.me:8020/settings/cities/bulk/",
                "png_path": png("bulk.png")},
            "settings_broken__base": {
                "page_id": "settings:broken", "state": "base", "status": 200,
                "title": "Ekran", "url": "http://acme.lvh.me:8020/settings/broken/",
                "png_path": png("broken.png")},
            "settings_nofile__base": {
                "page_id": "settings:city_bulk", "state": "modal", "status": 200,
                "title": "Ekran", "url": "http://acme.lvh.me:8020/settings/x/",
                "png_path": str(d / "gone.png")},
        }
        record = {"errors": [{"stage": "before", "page_id": "settings:broken",
                              "error": "timeout"}], "skipped": []}
        unpaired = shoot.unpaired_rows({}, after, record)
        whys = {r["pair_id"]: r["why"] for r in unpaired}
        ck("new page: the appeared reason is the ONE constant both sides use",
           whys["settings_city_bulk__base"] == shoot.WHY_STATE_APPEARED)
        ck("new page: a failed BEFORE capture keeps its real reason",
           "timeout" in whys["settings_broken__base"])

        pairs, consumed = shoot.new_page_pairs(unpaired, after, cfg, repo)
        ids = [p["pair_id"] for p in pairs]
        ck("new page: only the screen that really appeared becomes a pair",
           ids == ["settings_city_bulk__base"], )
        ck("new page: the row it came from is consumed, never listed twice",
           consumed == ["settings_city_bulk__base"])
        pair = pairs[0]
        ck("new page: it is one picture and says so",
           pair["baseline"] == "none" and pair["new_page"] is True
           and pair["before"]["full"] is None and pair["after"]["full"])
        ck("new page: the label is the map's name, not the browser's title",
           pair["nav"]["label"] == "Grupni unos gradova"
           and pair["caption"] == "Grupni unos gradova")
        ck("new page: the customer is given a PATH, never the operator's dev host",
           pair["nav"]["path"] == "/settings/cities/bulk/")
        ck("new page: the click path is lifted from the map",
           pair["nav"]["steps"] == ["Podesavanja", "Gradovi", "dugme „Grupni unos“"])
        ck("new page: it is a normal pair for everything downstream",
           pair["regions"] == [] and pair["drop"] is False)

        # No map at all: the pair still exists, and the STEPS are blank rather
        # than guessed - a wrong click path is worse than a missing one.
        blind, _ = shoot.new_page_pairs(unpaired, after, {"pages": {"kind": "list",
                                                                   "pages": []}}, repo)
        ck("new page: a screen the map does not know gets a blank path, not a guess",
           len(blind) == 1 and blind[0]["nav"]["steps"] == []
           and blind[0]["nav"]["path"] == "/settings/cities/bulk/")


def test_a_finished_run_is_promoted_verified_and_only_then_deleted():
    """The order that is not negotiable. The run folder holds the only copy of
    the pictures, so a delete that runs before a promotion that failed loses
    them permanently: promote, prove the promotion landed by reading the
    baseline back off disk, and only then remove the folder.

    A REJECTED pair is promoted like an approved one. Rejecting says "do not
    send this to the customer" - it is a decision about the message, never a
    claim that the screenshot is wrong, and the picture is still the app's true
    current state (operator, 2026-08-23).
    """
    from PIL import Image
    from visual import shoot

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots, repo = d / "shots", d / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        old = os.environ.get("BRAIN_SHOTS_DIR")
        os.environ["BRAIN_SHOTS_DIR"] = str(shots)
        try:
            def run_with(key, extra_shot=None):
                folder = shots / key / "after"
                folder.mkdir(parents=True, exist_ok=True)
                # a different look per run, or the second promotion would report
                # "unchanged" (identical bytes) and prove nothing about promotion
                tint = len(key)
                ok = folder / "ok.png"
                Image.new("RGB", (100, 70), (30, 90 + tint, 60)).save(ok)
                rej = folder / "rejected.png"
                Image.new("RGB", (100, 70), (90 + tint, 30, 30)).save(rej)
                after = {
                    "p_ok__base": {"page_id": "p:ok", "state": "base", "status": 200,
                                   "url": "/ok/", "title": "Ok", "png_path": str(ok)},
                    "p_rej__base": {"page_id": "p:rej", "state": "base", "status": 200,
                                    "url": "/rej/", "title": "Odbijen",
                                    "png_path": str(rej)}}
                if extra_shot:
                    after.update(extra_shot)
                shoot.update_json(shots / key / "manifest.json", lambda cur: {
                    "repo": str(repo), "ticket": "DEMO#1",
                    "shots": {"before": {}, "after": after}, "errors": [],
                    "pairs": [{"pair_id": "p_ok__base", "decision": "approved"},
                              {"pair_id": "p_rej__base", "decision": "rejected"}]})
                return folder

            # --- the failure branch FIRST: one page cannot be promoted --------
            run_with("bad", {"p_gone__base": {"page_id": "p:gone", "state": "base",
                                              "status": 200, "url": "/gone/",
                                              "png_path": str(d / "nowhere.png")}})
            res = shoot.finalize_run("bad")
            ck("finalize: a page that cannot be promoted stops the whole thing",
               res["deleted"] is False and "promotion refused" in res["reason"])
            ck("finalize: and NOTHING is deleted - the pictures are still there",
               (shots / "bad" / "after" / "ok.png").is_file())

            # --- the happy path ----------------------------------------------
            run_with("good")
            res = shoot.finalize_run("good")
            ck("finalize: both pages are promoted",
               sorted(res["promoted"]) == ["p_ok__base", "p_rej__base"], )
            ck("finalize: the promotion is verified before the delete",
               sorted(res["verified"]) == ["p_ok__base", "p_rej__base"])
            ck("finalize: only then is the run folder removed",
               res["deleted"] is True and not (shots / "good").exists())
            idx = shoot.load_baseline(repo)
            ck("finalize: the baseline names both screens",
               sorted(idx["shots"]) == ["p_ok__base", "p_rej__base"])
            ck("finalize: A REJECTED PAIR IS IN THE BASELINE - it is the app's "
               "current look, and rejecting was about the message",
               (shoot.baseline_dir(repo) / idx["shots"]["p_rej__base"]["png"]).is_file())

            # --- verification is not a formality ------------------------------
            run_with("tamper")
            shoot.promote_baseline("tamper", repo=repo)
            png = shoot.baseline_dir(repo) / idx["shots"]["p_ok__base"]["png"]
            Image.new("RGB", (100, 70), (1, 1, 1)).save(png)     # something else wrote it
            ver = shoot.verify_promotion("tamper", repo=repo)
            ck("verify: a baseline picture that is not what the index says fails",
               ver["ok"] is False
               and any("index entry" in m["reason"] for m in ver["missing"]))
            res = shoot.finalize_run("tamper")
            ck("verify: and a run whose promotion cannot be proven is never deleted",
               res["deleted"] is False and (shots / "tamper").is_dir())
        finally:
            if old is None:
                os.environ.pop("BRAIN_SHOTS_DIR", None)
            else:
                os.environ["BRAIN_SHOTS_DIR"] = old


def test_sweep_compares_against_its_own_previous_run():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots = d / "shots"
        site = d / "site"
        fixture_site.write_page(site, fixture_site.V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, _ = _repo(d / "repo", base)
            code, out, err = _run(["sweep", "--repo", str(repo)], shots)
            ck("sweep: exit 0 (%s)" % (err.strip()[-160:] or "no stderr"), code == 0)
            store_files = list((shots / "_sweep").glob("*.json"))
            ck("sweep: the signature store is written", len(store_files) == 1)
            first = json.loads(store_files[0].read_text(encoding="utf-8"))
            ck("sweep: the first run has nothing to compare against",
               first["compared_against"] == "" and first["needs_review"] == [])
            ck("sweep: the page signature was stored",
               len(first["pages"]["fix:index"]["dom_signature"]) == 16)
            ck("sweep: no png was written for a sweep",
               not list(shots.rglob("*.png")))

            fixture_site.write_page(site, fixture_site.V2)
            code2, out2, _ = _run(["sweep", "--repo", str(repo)], shots)
            second = json.loads(store_files[0].read_text(encoding="utf-8"))
            ck("sweep: the second run exits 0 too", code2 == 0)
            ck("sweep: the structural change is flagged",
               [r["page_id"] for r in second["needs_review"]] == ["fix:index"])
            ck("sweep: it says why", "tree" in second["needs_review"][0]["why"]
               or "block" in second["needs_review"][0]["why"])
            ck("sweep: the console line is ascii only", out2.isascii())
        finally:
            stop()


def test_two_page_ids_that_slug_alike_get_separate_shots():
    from visual import shoot
    taken = {}
    a = shoot.pair_id("audits:audit_list", "base", taken)
    b = shoot.pair_id("audits:audit-list", "base", taken)
    again = shoot.pair_id("audits:audit_list", "base", taken)
    ck("pair_id: the first page keeps the plain id", a == "audits_audit_list__base")
    ck("pair_id: a colliding page id gets its own key", b != a)
    ck("pair_id: the same page asks twice and gets the same key", again == a)
    ck("pair_id: the state is part of the key",
       shoot.pair_id("p", "dodaj", {}) == "p__dodaj")


def test_the_manifest_write_re_reads_under_the_lock():
    """A concurrent writer's rows must survive: `update_json` loads INSIDE the
    lock, so a command that started with a stale copy still merges."""
    from visual import shoot
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "manifest.json"
        shoot.update_json(fp, lambda cur: {"pairs": [], "shots": {"a": 1}})
        seen = {}

        def merge(current):
            seen.update(current)
            out = dict(current)
            out["shots"] = dict(out.get("shots") or {}, b=2)
            return out

        got = shoot.update_json(fp, merge)
        ck("update_json: the mutate saw what was on disk", seen.get("shots") == {"a": 1})
        ck("update_json: both writers' rows survive", got["shots"] == {"a": 1, "b": 2})
        ck("update_json: the file matches what was returned",
           json.loads(fp.read_text(encoding="utf-8")) == got)
        ck("update_json: no lock file is left behind",
           not list(Path(d).glob("*.lock")))


def test_the_gate_state_file_records_what_each_pass_covered():
    """`state.json` is the V2 PreToolUse gate's contract (its own module
    documents it). Only the SHAPE is asserted here - importing that module would
    make this suite fail whenever the hook is edited."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shots = d / "shots"
        site = d / "site"
        fixture_site.write_page(site, fixture_site.V1)
        base, stop = fixture_site.serve(site)
        try:
            repo, tpl = _repo(d / "repo", base)
            code, out, err = _run(["before", "--repo", str(repo), "--files", tpl,
                                   "--work-id", "w123", "--ticket", "DEMO#gate"], shots)
            ck("state: before exits 0 (%s)" % (err.strip()[-120:] or "no stderr"),
               code == 0)
            fp = shots / "w123" / "state.json"
            ck("state: it is written next to the manifest", fp.exists())
            st = json.loads(fp.read_text(encoding="utf-8"))
            ck("state: the contract keys are there",
               {"work_id", "repo", "ticket", "before"} <= set(st))
            ck("state: the work id names the folder and the record",
               st["work_id"] == "w123")
            ck("state: and the ticket it belongs to is recorded with it",
               st["ticket"] == "DEMO#gate")
            ck("state: naming a ticket does not move the run out from under "
               "the gate, which only knows the work id",
               (shots / "w123" / "manifest.json").exists()
               and not (shots / "demo_gate").exists())
            ck("state: the edited file is listed as covered",
               st["before"]["files"] == [tpl])
            ck("state: the pass is stamped", bool(st["before"]["at"]))
            ck("state: there is no after side yet", "after" not in st)

            # A visual file that maps to NO screen is a QUESTION, not a quiet
            # success: exit non-zero, do NOT mark it covered (a "0 pages, exit 0"
            # run satisfied the gate with no baseline at all). The operator
            # answers with --accept-unlocated, and only then is it covered.
            css = repo / "static" / "css" / "app.css"
            css.parent.mkdir(parents=True, exist_ok=True)
            css.write_text(".nothing-uses-this { color: red; }",
                           encoding="utf-8")
            code2, out2, _ = _run(["before", "--repo", str(repo), "--files",
                                   "static/css/app.css", "--work-id", "w123"], shots)
            st = json.loads(fp.read_text(encoding="utf-8"))
            ck("state: a file that maps to no screen does NOT exit 0", code2 != 0)
            ck("state: and it asks the operator where that change is seen",
               "no_pages" in out2 and "ekran" in out2)
            ck("state: it is NOT recorded as covered", st["before"]["files"] == [tpl])
            code2b, _, _ = _run(["before", "--repo", str(repo), "--files",
                                 "static/css/app.css", "--work-id", "w123",
                                 "--accept-unlocated"], shots)
            st = json.loads(fp.read_text(encoding="utf-8"))
            ck("state: with the operator's answer it passes", code2b == 0)
            ck("state: and only then is the file covered",
               st["before"]["files"] == sorted([tpl, "static/css/app.css"]))

            (repo / tpl).write_text(TEMPLATE_V2, encoding="utf-8")
            fixture_site.write_page(site, fixture_site.V2)
            code3, _, err3 = _run(["after", "--repo", str(repo), "--files", tpl,
                                   "--work-id", "w123"], shots)
            st = json.loads(fp.read_text(encoding="utf-8"))
            ck("state: after exits 0 (%s)" % (err3.strip()[-120:] or "no stderr"),
               code3 == 0)
            ck("state: the after side is recorded", st["after"]["files"] == [tpl])
            ck("state: the before side is untouched by the after pass",
               st["before"]["files"] == sorted([tpl, "static/css/app.css"]))
        finally:
            stop()


def main() -> int:
    offline = (test_regions_are_offered_in_reading_order,
               test_a_screen_that_did_not_exist_before_is_a_pair_not_a_footnote,
               test_a_finished_run_is_promoted_verified_and_only_then_deleted,
               test_a_run_with_no_ticket_refuses_and_writes_nothing,
               test_a_missing_config_is_a_question_not_a_crash,
               test_two_page_ids_that_slug_alike_get_separate_shots,
               test_promotion_refuses_a_shot_that_is_not_a_good_picture_of_the_page,
               test_the_manifest_write_re_reads_under_the_lock,
               test_an_unusable_config_names_the_broken_key,
               test_a_dead_server_is_a_question,
               test_a_bad_repo_path_is_a_usage_error)
    online = (test_the_engine_does_not_squat_the_app_s_config_or_its_event_loop,
              test_after_without_before_asks_instead_of_guessing,
              test_after_can_stand_alone_when_the_before_is_unobtainable,
              test_the_happy_path_produces_a_customer_pair,
              test_the_pair_is_two_pictures_and_a_screen_name,
              test_a_change_that_renders_nowhere_is_still_a_plain_pair,
              test_a_shared_layout_is_photographed_once_not_once_per_screen,
              test_the_pair_carries_every_changed_region_enlarged,
              test_a_class_is_not_a_region_no_matter_how_much_it_wants_to_be,
              test_a_region_is_cut_where_its_content_is_not_where_its_pixels_were,
              test_a_page_whose_numbers_moved_is_not_reported_as_a_change,
              test_the_baseline_command_seeds_the_rolling_store,
              test_the_rolling_baseline_is_the_before_and_promotion_is_explicit,
              test_sweep_compares_against_its_own_previous_run,
              test_the_gate_state_file_records_what_each_pass_covered)
    for fn in offline:
        try:
            fn()
        except Exception as exc:                                # noqa: BLE001
            ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    ok, why = _browser_available()
    for fn in online:
        if not ok:
            skip(fn.__name__, "no browser: " + why)
            continue
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
