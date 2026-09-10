#!/usr/bin/env python3
"""Offline tests for the visual-diff (before/after) HTTP surface — plan V3.

Three routes, all loopback-only:
    GET  /api/tickets/shots            list the captured works
    GET  /api/tickets/shots/img        one PNG, resolved ONLY through the manifest
    POST /api/tickets/shots/approve    write this work's draft AND POST IT to the ticket

Everything runs against TEMP directories: a temp shots root (BRAIN_SHOTS_DIR) and
a temp ticket store (server.load_config stubbed). The real store and the real
agent_view/.shots are never opened. No network beyond loopback; the server is a
throwaway ThreadingHTTPServer on 127.0.0.1:0.

AND NO HELPDESK. The approve route now POSTS the comment, so `_Env` replaces
`server._helpdesk_adapter` — the one seam through which the HUD builds a client —
with a fake for the whole of every test, before a single request is made. This is
not belt-and-braces: HELPDESK_URL / HELPDESK_TOKEN are set on the operator's
machine, ticket DEMO#08597 in the fixtures is a REAL ticket, and a run of this file
without the stub would put comments on a real customer's ticket. It happened once
already from a subprocess (DEMO#08597, comment id 599, 2026-08-20) — see
`_muzzled_env`, which is the same rule for the CLI tests.

Run:  python test_shots_routes.py
"""
from __future__ import annotations

import base64
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts" / "tickets"))

import server      # noqa: E402  (also puts scripts/tickets on sys.path)
#: The fake helpdesk is NOT written a second time here. `FakeAdapter` enforces the
#: REAL attachment caps and the real per-file check (it holds an AcmeHelpdesk to
#: ask them), so a chunking bug cannot pass in this file and fail on the wire; a
#: hand-rolled stub with generous limits is how that happens.
from test_writeback_sentlog import FakeAdapter      # noqa: E402

_results = []

#: The smallest thing a browser will accept as an image — enough to prove the
#: route hands back exactly the bytes on disk with the right headers.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Harness
# --------------------------------------------------------------------------- #
def _start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _getj(port, path):
    """`(status, parsed json)` — the same GET, for the routes that answer JSON."""
    code, _hdr, body = _get(port, path)
    try:
        return code, json.loads(body or b"{}")
    except ValueError:
        return code, {}


def _post(port, path, obj):
    body = json.dumps(obj).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


class _Env:
    """A temp shots root + a temp ticket store, both torn down on exit. Mirrors
    the `_Root` helper in test_worklog_server.py (load_config stubbed), plus the
    BRAIN_SHOTS_DIR the capture engine and the HUD share."""

    def __init__(self, helpdesk=None):
        #: The ONLY helpdesk any test in this file may reach. Defaults to a fake
        #: that takes everything; a test that wants a refusal hands in its own
        #: (`FakeAdapter(fail=["add_comment"])`, `fail_after=1`, `ambiguous=True`).
        self.helpdesk = helpdesk if helpdesk is not None else FakeAdapter()
        self.tmp = Path(tempfile.mkdtemp(prefix="shots-test-"))
        self.shots = self.tmp / "shots"
        self.store = self.tmp / "store"
        #: where a `promotable=True` work's baseline is written — a real repo
        #: folder, so promotion and its verification run for real
        self.repo = self.tmp / "repo"
        self.outside = self.tmp / "outside"
        for d in (self.shots, self.store, self.outside):
            d.mkdir(parents=True, exist_ok=True)
        (self.outside / "secret.png").write_bytes(PNG_1X1)

    def __enter__(self):
        self._orig_cfg = server.load_config
        self._orig_env = os.environ.get("BRAIN_SHOTS_DIR")
        self._orig_adapter = server._helpdesk_adapter
        server.load_config = lambda: {"tickets_root": str(self.store)}
        # The send door is muzzled for the WHOLE test, not per call: a test that
        # forgot to stub it would post to a real customer's ticket.
        server._helpdesk_adapter = lambda: self.helpdesk
        os.environ["BRAIN_SHOTS_DIR"] = str(self.shots)
        server._tix_cache["data"] = None
        return self

    def __exit__(self, *exc):
        server.load_config = self._orig_cfg
        server._helpdesk_adapter = self._orig_adapter
        if self._orig_env is None:
            os.environ.pop("BRAIN_SHOTS_DIR", None)
        else:
            os.environ["BRAIN_SHOTS_DIR"] = self._orig_env
        server._tix_cache["data"] = None
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    # -- fixtures ---------------------------------------------------------- #
    def ticket(self, outbox=None):
        (self.store / "DEMO.json").write_text(json.dumps({
            "project": {"name": "DEMO", "helpdesk_module": "DEMO", "repo": ""},
            "tickets": {"08597": {"title": "podnaslov", "status": "active",
                                  "helpdesk": {"is_closed": False},
                                  "outbox": outbox if outbox is not None else []}},
            "rev": 3}), encoding="utf-8")

    def work(self, work_id="w1", ticket="DEMO#08597", pairs=None, baseline=None,
             promotable=False):
        """`promotable=True` gives the manifest the `shots.after` section a REAL
        capture writes, pointed at this env's own temp repo.

        Without it — which is every fixture written before 2026-08-28 — the run
        has nothing to promote, so `finalize_run` refuses it and the folder
        stays. That is correct behaviour and it silently meant no test here ever
        saw a run actually leave; the operator found the gap by hand, in the
        gallery, on a card no button could clear."""
        d = self.shots / work_id
        (d / "img").mkdir(parents=True, exist_ok=True)
        for name in ("p1-before.png", "p1-after.png",
                     "p1-r1-before.png", "p1-r1-after.png",
                     "p1-r2-before.png", "p1-r2-after.png",
                     "p2-before.png", "p2-after.png"):
            (d / "img" / name).write_bytes(PNG_1X1)
        if pairs is None:
            pairs = [
                # TWO changed regions on one screen — the case the panel could not
                # show at all, and now the default fixture so every downstream
                # assertion exercises it rather than the one-region special case.
                {"pair_id": "p1", "page_id": "lista-lokacija", "url": "http://x.lvh.me/loc/",
                 "title": "Lista lokacija", "state": "base", "anchor": {"sel": ".card"},
                 "box": {"x": 10, "y": 20, "w": 30, "h": 40}, "caption": "dodat podnaslov ispod naziva",
                 "located": True, "pixel_ratio": 0.04,
                 "before": {"full": "img/p1-before.png"},
                 "after": {"full": "img/p1-after.png"},
                 "regions": [
                     {"before": {"crop": "img/p1-r1-before.png"},
                      "after": {"crop": "img/p1-r1-after.png"},
                      "crop_from": "after", "crop_kind": "form-field"},
                     {"before": {"crop": "img/p1-r2-before.png"},
                      "after": {"crop": "img/p1-r2-after.png"},
                      "crop_from": "after", "crop_kind": "card"}],
                 "regions_found": 2},
                {"pair_id": "p2", "page_id": "detalj", "url": "http://x.lvh.me/loc/1/",
                 "title": "Detalj lokacije", "state": "modal: uredi", "anchor": None,
                 "box": None, "caption": "izmenjen razmak u modalu",
                 "located": False, "pixel_ratio": 0.01,
                 "before": {"full": "img/p2-before.png"},
                 "after": {"full": "img/p2-after.png"},
                 "regions": [], "regions_found": 0},
            ]
        man = {"ticket": ticket, "work_id": work_id, "repo": "C:/projects/acme-audit",
               "captured_at": "2026-08-20T01:30:00", "pairs": pairs,
               "sweep": None, "errors": []}
        if promotable:
            self.repo.mkdir(parents=True, exist_ok=True)
            man["repo"] = str(self.repo)
            man["shots"] = {"before": {}, "after": {
                p["pair_id"]: {
                    "png_path": str(d / "img" / ("%s-after.png" % p["pair_id"])),
                    "page_id": p.get("page_id"), "state": p.get("state"),
                    "url": p.get("url") or "", "title": p.get("title") or "",
                    "status": 200}
                for p in pairs}}
        if baseline is not None:
            man["baseline"] = baseline
        (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        return d

    def outbox(self):
        d = json.loads((self.store / "DEMO.json").read_text(encoding="utf-8"))
        return d["tickets"]["08597"].get("outbox") or []

    def manifest(self, work_id="w1"):
        return json.loads((self.shots / work_id / "manifest.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
#  GET /api/tickets/shots
# --------------------------------------------------------------------------- #
def test_a_work_with_no_baseline_says_so_instead_of_looking_unchanged():
    """`shoot.py after --no-baseline` produces ONE picture per record and marks
    the work `baseline.state == "none"`. In the gallery that must be visible:
    an empty PRE half with no explanation reads as "nothing changed", which is
    the opposite of what happened."""
    with _Env() as env:
        env.ticket()
        env.work(pairs=[
            {"pair_id": "s1", "page_id": "lista-lokacija", "url": "http://x.lvh.me/loc/",
             "title": "Lista lokacija", "state": "base", "baseline": "none",
             "caption": "dodat podnaslov ispod naziva", "located": True,
             "pixel_ratio": None, "box": {"before": None, "after": {"x": 1, "y": 2, "w": 3, "h": 4}},
             "before": {"full": None},
             "after": {"full": "img/p1-after.png"},
             "regions": [{"before": {"crop": None},
                          "after": {"crop": "img/p1-r1-after.png"}}],
             "regions_found": 1}],
            baseline={"state": "none", "reason": "migracija je vec izvrsena",
                      "declared_at": "2026-08-20T02:00:00"})
        httpd, port = _start_server()
        try:
            code, _h, raw = _get(port, "/api/tickets/shots")
            w = (json.loads(raw).get("works") or [{}])[0]
            check("no baseline: the work carries the state and the reason",
                  (w.get("baseline") or {}).get("state") == "none"
                  and "migracija" in (w.get("baseline") or {}).get("reason", ""),
                  json.dumps(w.get("baseline"))[:200])
            pr = (w.get("pairs") or [{}])[0]
            check("no baseline: the record carries it too", pr.get("baseline") == "none",
                  json.dumps(pr)[:200])
            check("no baseline: only the after images are offered",
                  sorted(pr.get("img") or {}) == ["after_full"]
                  and sorted((pr.get("regions") or [{}])[0].get("img") or {}) == ["after_crop"],
                  json.dumps(pr.get("img")) + json.dumps(pr.get("regions")))

            # ... and the customer's draft says it in words, not by an empty half.
            code2, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [{"pair_id": "s1", "caption": "dodat podnaslov"}]})
            body = [d for d in env.outbox() if d.get("kind") == "shots"][0]["body"]
            check("no baseline: the draft never promises a 'pre' picture (%s)" % code2,
                  body.startswith(server.SHOTS_HEAD_NO_BASELINE_SR)
                  and "bez snimka pre izmene" in body, repr(body))
            atts = [d for d in env.outbox() if d.get("kind") == "shots"][0]["attachments"]
            check("no baseline: and attaches only what exists",
                  [a["name"] for a in atts] == ["1-POSLE-dodat-podnaslov.png",
                                                "1-POSLE-detalj-1-dodat-podnaslov.png"],
                  json.dumps([a["name"] for a in atts]))
            check("no baseline: the line names the one picture there is, and says why",
                  "\u2014 slike: POSLE, POSLE detalj 1 - bez snimka pre izmene" in body,
                  repr(body))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_list_route_shape():
    with _Env() as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            code, _h, raw = _get(port, "/api/tickets/shots")
            d = json.loads(raw)
            check("list: 200", code == 200, str(code))
            works = d.get("works") or []
            check("list: one work", len(works) == 1, json.dumps(d)[:200])
            w = works[0] if works else {}
            check("list: work carries work_id/ticket/captured_at",
                  w.get("work_id") == "w1" and w.get("ticket") == "DEMO#08597"
                  and w.get("captured_at") == "2026-08-20T01:30:00", json.dumps(w)[:200])
            check("list: repo is reduced to its folder name (never an absolute path)",
                  w.get("repo") == "acme-audit", str(w.get("repo")))
            check("list: the work carries the ticket GROUPING key, so the gallery never "
                  "re-implements _shots_ticket_key in JS",
                  w.get("ticket_key") == "DEMO#8597", str(w.get("ticket_key")))
            p1 = (w.get("pairs") or [{}])[0]
            check("list: pair metadata is passed through",
                  p1.get("pair_id") == "p1" and p1.get("title") == "Lista lokacija"
                  and p1.get("state") == "base" and p1.get("url") == "http://x.lvh.me/loc/",
                  json.dumps(p1)[:200])
            check("list: pair is not approved until the operator approves it",
                  p1.get("approved") is False, str(p1.get("approved")))
            check("list: the pair's own img is the two FULL pictures — the crops moved "
                  "to the regions",
                  sorted((p1.get("img") or {}).keys()) == ["after_full", "before_full"]
                  and (p1.get("img") or {}).get("before_full", "").startswith(
                      "/api/tickets/shots/img?work_id=w1&pair=p1&which=before_full"),
                  json.dumps(p1.get("img"))[:200])
            regs = p1.get("regions") or []
            check("list: every changed region is its own before/after comparison",
                  len(regs) == 2 and [r.get("n") for r in regs] == [1, 2]
                  and all(sorted(r.get("img") or {}) == ["after_crop", "before_crop"]
                          for r in regs),
                  json.dumps(regs)[:300])
            check("list: a region image is addressed by INDEX, never by path",
                  (regs[1].get("img") or {}).get("before_crop", "").endswith(
                      "&which=before_crop&region=1"),
                  json.dumps(regs[1])[:200])
            p2 = (w.get("pairs") or [{}, {}])[1]
            check("list: a pair with no changed region advertises the two full shots and "
                  "an empty region list",
                  sorted((p2.get("img") or {}).keys()) == ["after_full", "before_full"]
                  and p2.get("regions") == [] and p2.get("regions_found") == 0,
                  json.dumps(p2.get("img"))[:200] + json.dumps(p2.get("regions")))
            body = raw.decode("utf-8")
            check("list: no filesystem path of any kind reaches the client",
                  str(env.shots) not in body and "img/p1-before.png" not in body
                  and str(env.tmp) not in body, body[:200])
            # A pair is PRE + POSLE + the screen name and its URL. Everything the change
            # DETECTION produced is the engine's working note, not the customer's evidence:
            # a box drawn off `location_city` (a material-icon name) is a claim nobody can
            # check, and a "bez okvira" note fires on every card once the engine stops
            # locating at all. Operator's decision, 2026-08-21.
            #
            # The COLLAPSE verdict is not a diff claim and is exempt on purpose — see
            # test_a_collapsed_pair_reaches_the_gallery_marked_and_still_serves_its_pictures.
            # It says which records are screens to review, which the panel cannot work out
            # for itself, and withholding it shipped "32 nepregledano" over 5 cards.
            check("list: no diff claim is shipped to the browser",
                  all(('"%s"' % f) not in body for f in
                      ("anchor", "box", "located", "only_side", "label", "sweep",
                       "pixel_ratio", "crop_from", "crop_kind")),
                  body[:300])
            code2, _h2, raw2 = _get(port, "/api/tickets/shots?ticket=DEMO%2300000")
            check("list: ?ticket= filters (a foreign ticket yields nothing)",
                  code2 == 200 and (json.loads(raw2).get("works") == []), raw2[:120])
            code3, _h3, raw3 = _get(port, "/api/tickets/shots?work_id=nope")
            check("list: ?work_id= filters", code3 == 200
                  and (json.loads(raw3).get("works") == []), raw3[:120])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_the_gallery_is_one_ticket_and_never_a_ticketless_work():
    """Two operator-reported defects, one listing.

    (1) The gallery opened from a ticket showed EVERY work on the machine — two
        works x 84 pairs for a single change. Opened with ?ticket= it shows that
        ticket only, and the reference has to match the way the operator SPELLS
        it on the capture command line ("DEMO05513" as often as "DEMO#05513"), or
        the filter turns a full gallery into an empty one.
    (2) A work captured with --work-id and no --ticket was listed as "bez
        tiketa". It belongs to no comment, so it is never listed — but the
        images stay on disk and stay servable, because the operator wants them
        kept."""
    with _Env() as env:
        env.ticket()
        env.work(work_id="w1", ticket="DEMO#08597")
        env.work(work_id="wnohash", ticket="DEMO08597")     # the same ticket, typed without "#"
        env.work(work_id="wnone", ticket="")               # --work-id, no --ticket
        env.work(work_id="wother", ticket="DEMO#00042")     # a different ticket
        httpd, port = _start_server()
        try:
            ids = lambda raw: sorted(w["work_id"] for w in json.loads(raw).get("works") or [])
            _c, _h, raw = _get(port, "/api/tickets/shots")
            check("gallery: a work with no ticket is never listed, even unfiltered",
                  "wnone" not in ids(raw), json.dumps(ids(raw)))
            check("gallery: the ticketed works still are (browse-everything stays reachable)",
                  ids(raw) == ["w1", "wnohash", "wother"], json.dumps(ids(raw)))
            _c, _h, raw = _get(port, "/api/tickets/shots?ticket=DEMO%2308597")
            check("gallery: ?ticket= shows THAT ticket's works only",
                  ids(raw) == ["w1", "wnohash"], json.dumps(ids(raw)))
            _c, _h, raw = _get(port, "/api/tickets/shots?ticket=DEMO%2308597")
            check("gallery: a manifest ticket typed without '#' still matches its ticket",
                  "wnohash" in ids(raw), json.dumps(ids(raw)))
            _c, _h, raw = _get(port, "/api/tickets/shots?ticket=DEMO%238597")
            check("gallery: a leading zero on either side is not a different ticket",
                  ids(raw) == ["w1", "wnohash"], json.dumps(ids(raw)))
            _c, _h, raw = _get(port, "/api/tickets/shots?ticket=nista")
            check("gallery: a filter naming no ticket yields NOTHING, never everything",
                  ids(raw) == [], json.dumps(ids(raw)))

            # the collapse groups BY TICKET, and two spellings of one ticket are one group
            _c, _h, raw = _get(port, "/api/tickets/shots")
            keys = {w["work_id"]: w.get("ticket_key") for w in json.loads(raw)["works"]}
            check("gallery: two spellings of one ticket share ONE group key",
                  keys.get("w1") and keys["w1"] == keys.get("wnohash"), json.dumps(keys))
            check("gallery: a different ticket is a different group",
                  keys.get("wother") not in (None, keys.get("w1")), json.dumps(keys))
            _c, _h, raw = _get(port, "/api/tickets/shots?work_id=wnone")
            check("gallery: asking for the ticketless work by id does not surface it either",
                  ids(raw) == [], json.dumps(ids(raw)))

            # hidden, NOT destroyed - the operator keeps every before image
            check("gallery: the ticketless work is still on disk",
                  (env.shots / "wnone" / "manifest.json").is_file()
                  and (env.shots / "wnone" / "img" / "p1-before.png").is_file(), "")
            code, _h, body = _get(port, "/api/tickets/shots/img?work_id=wnone&pair=p1&which=before_full")
            check("gallery: and its images are still servable by work_id",
                  code == 200 and body == PNG_1X1, str(code))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_the_panel_can_say_what_the_capture_is_doing():
    """Operator: the panel gave no feedback at all while the engine was shooting.

    THERE IS NO PROGRESS CHANNEL TO SUBSCRIBE TO. `shoot.py` is its own process,
    never talks to this server, and writes `state.json` and `manifest.json` only
    when a pass has ENDED — so neither moves mid-run. The one thing that changes
    on disk during a capture is the PNG of each page as it is taken. This reads
    exactly that and claims nothing more: an unknown plan is `total: 0`, on which
    the panel prints a count and NO bar.
    """
    with _Env() as env:
        env.ticket()
        d = env.work()
        man = env.manifest()
        man["affected"] = {"pages": [{"page_id": "a"}, {"page_id": "b"}, {"page_id": "c"}]}
        (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        man_at = (d / "manifest.json").stat().st_mtime

        # two of the three pages are shot; one of them opened a second state, which is
        # the SAME page and must not be counted as progress twice
        (d / "after").mkdir(parents=True, exist_ok=True)
        for i, name in enumerate(("pg-a.png", "pg-b.png", "pg-b__modal-uredi.png")):
            (d / "after" / name).write_bytes(PNG_1X1)
            os.utime(d / "after" / name, (man_at + 10 + i, man_at + 10 + i))
        httpd, port = _start_server()
        try:
            w = lambda: (json.loads(_get(port, "/api/tickets/shots")[2]).get("works") or [{}])[0]
            pr = w().get("progress") or {}
            check("progress: counts PAGES against the plan, not files",
                  pr.get("side") == "after" and pr.get("done") == 2 and pr.get("total") == 3,
                  json.dumps(pr))
            check("progress: names the page it is on, and when",
                  pr.get("page") == "pg-b" and str(pr.get("at", "")).startswith("20"),
                  json.dumps(pr))
            check("progress: a PNG newer than the manifest means a pass is mid-flight",
                  pr.get("running") is True, json.dumps(pr))
            check("progress: the page is a slug, never a path",
                  "/" not in pr.get("page", "") and "\\" not in pr.get("page", ""),
                  json.dumps(pr))

            # the pass ends: shoot.py writes the manifest, which is now the newest thing
            os.utime(d / "manifest.json", (man_at + 60, man_at + 60))
            pr = w().get("progress") or {}
            check("progress: a finished pass is not still 'running'",
                  pr.get("running") is False and pr.get("done") == 2, json.dumps(pr))

            # a pass that stopped writing long ago is not running either — silence is the
            # only evidence there is, so it must be read as "stopped", never as a spinner
            # that never ends
            old = man_at - (server.SHOTS_RUN_STALE_S + 600)
            os.utime(d / "manifest.json", (old - 10, old - 10))
            for i, name in enumerate(("pg-a.png", "pg-b.png", "pg-b__modal-uredi.png")):
                os.utime(d / "after" / name, (old + i, old + i))
            check("progress: a stale run is reported stopped, not spinning forever",
                  ((w().get("progress") or {}).get("running")) is False, "")

            # no plan on record yet -> a count, and NO denominator invented for a bar
            man.pop("affected")
            (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
            pr = w().get("progress") or {}
            check("progress: an unknown plan is total:0, never a made-up denominator",
                  pr.get("total") == 0 and pr.get("done") == 2, json.dumps(pr))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_work_with_nothing_captured_yet_reports_no_progress_at_all():
    """Not `0/0`, not a zeroed bar: `null`. The panel must be able to tell "the
    engine has not started here" from "the engine is at 0 of 30"."""
    with _Env() as env:
        env.ticket()
        env.work()                                   # manifest + images, no side folders
        httpd, port = _start_server()
        try:
            w = (json.loads(_get(port, "/api/tickets/shots")[2]).get("works") or [{}])[0]
            check("progress: nothing captured -> progress is null",
                  w.get("progress") is None and "progress" in w, json.dumps(w)[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_every_changed_region_is_its_own_comparison():
    """Operator: "postoje slucajevi gde ima vise izmenjenih elemenata - treba da
    ima vise uvecanih poredjenja kod tih."

    One commit adding four fields to a form is four changed places on one screen,
    and the panel could show exactly ONE of them. A pair now carries N regions,
    rendered in the engine's order (reading order down the page).

    The trap this pins: the operator reads a region by POSITION ("3/4") and the
    URL and the attachment name key off the MANIFEST INDEX. Drop an unreadable
    region from the list and those two numberings part company from that point
    on - "3/3" on screen next to a file called `-crop-4.png`. So every region is
    emitted, empty `img` and all.
    """
    with _Env() as env:
        env.ticket()
        d = env.work(pairs=[{
            "pair_id": "p1", "page_id": "forma", "title": "Nova lokacija",
            "state": "base", "caption": "nova polja",
            "before": {"full": "img/p1-before.png"},
            "after": {"full": "img/p1-after.png"},
            "regions": [
                {"before": {"crop": "img/p1-r1-before.png"},
                 "after": {"crop": "img/p1-r1-after.png"}},
                # one side never captured -> the placeholder means something, so it stays
                {"before": {"crop": "img/p1-r2-before.png"}, "after": {"crop": None}},
                # NEITHER side readable -> still emitted, as a numbered empty row
                {"before": {"crop": "img/nema.png"}, "after": {"crop": "img/nema.png"}},
                {"before": {"crop": "img/p2-before.png"},
                 "after": {"crop": "img/p2-after.png"}}],
            # the engine found seven and capped its own list at four
            "regions_found": 7}])
        httpd, port = _start_server()
        try:
            _c, _h, raw = _get(port, "/api/tickets/shots")
            pr = ((json.loads(raw).get("works") or [{}])[0].get("pairs") or [{}])[0]
            regs = pr.get("regions") or []
            check("regions: four comparisons, in the order the engine gave them",
                  len(regs) == 4 and [r.get("n") for r in regs] == [1, 2, 3, 4],
                  json.dumps([r.get("n") for r in regs]))
            check("regions: each carries only the sides that resolve",
                  sorted(regs[0]["img"]) == ["after_crop", "before_crop"]
                  and sorted(regs[1]["img"]) == ["before_crop"]
                  and regs[2]["img"] == {}
                  and sorted(regs[3]["img"]) == ["after_crop", "before_crop"],
                  json.dumps([sorted(r["img"]) for r in regs]))
            check("regions: an unreadable region is KEPT as a numbered empty row, so the "
                  "position the operator reads still matches the attachment name",
                  regs[2].get("n") == 3 and regs[3].get("n") == 4,
                  json.dumps(regs[2]) + json.dumps(regs[3]))
            check("regions: the engine's own count survives, so the panel can say "
                  "'prikazano 4 od 7'",
                  pr.get("regions_found") == 7, str(pr.get("regions_found")))
            check("regions: the pair's own img is the two full pictures only",
                  sorted(pr.get("img") or {}) == ["after_full", "before_full"],
                  json.dumps(pr.get("img")))
            body = raw.decode("utf-8")
            check("regions: the engine's crop diagnostics never reach the browser",
                  '"crop_kind"' not in body and '"crop_from"' not in body, body[:200])

            # every advertised region URL actually serves - the rule is that a src the
            # server advertises is one the server can serve
            served = 0
            for r in regs:
                for url in (r.get("img") or {}).values():
                    c, _hh, rr = _get(port, url)
                    served += 1 if (c == 200 and rr == PNG_1X1) else 0
            check("regions: every advertised region URL serves its PNG", served == 5,
                  str(served))

            # and the attachment names carry the SAME number the panel prints
            _c2, _j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w1", "ticket": "DEMO#08597",
                             "pairs": [{"pair_id": "p1", "caption": "nova polja"}]})
            names = [a["name"] for a in
                     [dd for dd in env.outbox() if dd.get("kind") == "shots"][0]["attachments"]]
            check("regions: the attachment number IS the position on screen",
                  names == ["1-PRE-nova-polja.png", "1-POSLE-nova-polja.png",
                            "1-PRE-detalj-1-nova-polja.png", "1-POSLE-detalj-1-nova-polja.png",
                            "1-PRE-detalj-2-nova-polja.png",
                            "1-PRE-detalj-4-nova-polja.png", "1-POSLE-detalj-4-nova-polja.png"],
                  json.dumps(names))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_collapsed_pair_reaches_the_gallery_marked_and_still_serves_its_pictures():
    """THE DEFECT: the listing stripped the engine's collapse verdict, so every record
    of a work reached the browser looking like a screen to review. A capture whose
    engine had collapsed 27 of 32 pairs listed 32 cards and printed "32 nepregledano"
    over 5 reviewable ones, and the client could not hide what never arrived.

    Two halves, and BOTH are load-bearing:

    * the verdict ships — `shown` false, plus who speaks for this record
      (`represented_by`) and the engine's own reason (`drop_reason`);
    * the record is still LISTED and its pictures still resolve. The engine keeps a
      collapsed pair's manifest row, PNGs and crops deliberately, and the panel hangs
      it under its representative rather than deleting it.

    `represents` is NOT shipped: the representative's caption already carries the
    engine's own count ("+ N druga ekrana"), and a second list of the same fact in the
    other direction is a second thing to keep in step.
    """
    with _Env() as env:
        env.ticket()
        env.work(pairs=[
            {"pair_id": "p1", "page_id": "podesavanja", "url": "http://x.lvh.me/settings/",
             "title": "Podesavanja", "state": "base",
             "caption": "Podesavanja + 2 druga ekrana",
             "represents": ["korisnici", "upitnici"], "drop": False,
             "before": {"full": "img/p1-before.png"},
             "after": {"full": "img/p1-after.png"},
             "regions": [{"before": {"crop": "img/p1-r1-before.png"},
                          "after": {"crop": "img/p1-r1-after.png"}}],
             "regions_found": 1},
            {"pair_id": "p2", "page_id": "korisnici", "url": "http://x.lvh.me/users/",
             "title": "Korisnici", "state": "base", "caption": "Korisnici",
             "drop": True, "drop_reason": "same shared layout as podesavanja",
             "represented_by": "podesavanja",
             "before": {"full": "img/p2-before.png"},
             "after": {"full": "img/p2-after.png"},
             "regions": [], "regions_found": 0},
            {"pair_id": "p3", "page_id": "mobilni", "url": "http://x.lvh.me/m/",
             "title": "Mobilni", "state": "base", "caption": "Mobilni",
             "drop": True, "drop_reason": "same tree, same layout",
             "before": {"full": "img/p1-before.png"},
             "after": {"full": "img/p1-after.png"},
             "regions": [], "regions_found": 0},
        ])
        httpd, port = _start_server()
        try:
            code, _h, raw = _get(port, "/api/tickets/shots")
            body = json.loads(raw)
            pairs = {p["pair_id"]: p for p in body["works"][0]["pairs"]}
            check("collapse: every record is still listed - nothing is deleted from the "
                  "gallery, only from the review",
                  code == 200 and sorted(pairs) == ["p1", "p2", "p3"], json.dumps(sorted(pairs)))
            check("collapse: the engine's verdict reaches the browser",
                  pairs["p1"]["shown"] is True and pairs["p2"]["shown"] is False
                  and pairs["p3"]["shown"] is False,
                  json.dumps([pairs[k]["shown"] for k in ("p1", "p2", "p3")]))
            check("collapse: a collapsed pair names the pair that IS its picture",
                  pairs["p2"]["represented_by"] == "podesavanja"
                  and pairs["p3"]["represented_by"] == "",
                  pairs["p2"]["represented_by"] + "|" + pairs["p3"]["represented_by"])
            check("collapse: and carries the engine's own reason for the tooltip",
                  "same shared layout" in pairs["p2"]["drop_reason"]
                  and pairs["p1"]["drop_reason"] == "", json.dumps(pairs["p2"]["drop_reason"]))
            check("collapse: `represents` is NOT shipped - the caption already states the "
                  "count, and one direction cannot disagree with itself",
                  "represents" not in pairs["p1"], json.dumps(sorted(pairs["p1"])))
            check("collapse: the representative's caption carries the engine's number, "
                  "untouched",
                  pairs["p1"]["caption"] == "Podesavanja + 2 druga ekrana",
                  pairs["p1"]["caption"])
            served = 0
            for which in ("before_full", "after_full"):
                url = pairs["p2"]["img"].get(which)
                if url and _get(port, url)[0] == 200:
                    served += 1
            check("collapse: a collapsed pair's pictures are still served - reachable, "
                  "never deleted", served == 2, str(served))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_manifest_that_predates_the_collapse_is_all_shown():
    """No `drop` key at all — an older engine, or a run before collapsing existed.
    Everything is reviewable, which is exactly what the panel did before. A default
    that hides records would empty the gallery of every capture already on disk."""
    with _Env() as env:
        env.ticket()
        env.work()                                   # the default fixture: no `drop` key
        httpd, port = _start_server()
        try:
            _code, _h, raw = _get(port, "/api/tickets/shots")
            pairs = json.loads(raw)["works"][0]["pairs"]
            check("collapse: a manifest with no verdict means every pair is shown",
                  all(p["shown"] is True for p in pairs) and len(pairs) == 2,
                  json.dumps([p["shown"] for p in pairs]))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_region_index_can_address_nothing_but_a_region():
    """The index is the only new thing a client may send. It is an integer bounded
    by the list; everything else is the same 404 a bad path already was."""
    with _Env() as env:
        env.ticket()
        env.work()                                   # p1 has two regions
        httpd, port = _start_server()
        try:
            base = "/api/tickets/shots/img?work_id=w1&pair=p1&which=before_crop&region="
            for bad, why in (("9", "out of range"), ("-1", "negative"),
                             ("abc", "not a number"), ("1.5", "not an integer"),
                             ("..%2F..%2Foutside%2Fsecret.png", "a path"),
                             ("1e0", "exponent")):
                c, _h, _b = _get(port, base + bad)
                check("region index: %s -> 404" % why, c == 404, "%s %s" % (bad, c))
            c, _h, _b = _get(port, base + "1")
            check("region index: the one in range serves", c == 200, str(c))
            # a FULL picture is not a region, and asking for one with an index is a miss
            c, _h, _b = _get(port,
                             "/api/tickets/shots/img?work_id=w1&pair=p1&which=before_full&region=0")
            check("region index: a full picture asked for by region is a 404, not the "
                  "region's crop", c == 404, str(c))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_an_attachment_name_is_the_customers_label_not_an_identifier():
    """The helpdesk prints the file name beside the thumbnail, so it is the only
    thing telling the customer which picture is which. Serbian captions carry
    diacritics and separators the filesystem should not; the role has to survive
    the front of the name; nothing internal may leak in."""
    taken = set()
    name = server._shots_att_name(2, "Šifarnik — čačak/žito ćirilica", "after_crop", 0, taken)
    check("name: pure ASCII, so no server has to guess the header encoding",
          name.isascii(), name)
    check("name: the role comes first, right after the screen's number",
          name.startswith("2-POSLE-detalj-1-"), name)
    check("name: Serbian letters are transliterated, not dropped",
          "Sifarnik" in name and "cacak" in name and "zito" in name, name)
    check("name: no run of separators survives a ' - ' or ' — ' in the caption",
          "--" not in name and not name.startswith("-"), name)

    long_caption = "Karton kvaliteta - Volcano Podgorica — Kontrola i jos malo teksta"
    long_name = server._shots_att_name(1, long_caption, "before_full", None, taken)
    check("name: a long caption is cut on a separator, never mid-word",
          long_name.startswith("1-PRE-Karton-kvaliteta-Volcano-Podgorica")
          and long_name.endswith(".png") and len(long_name) < 60, long_name)
    check("name: two pictures of one screen never collide",
          server._shots_att_name(1, long_caption, "after_full", None, taken) != long_name)


def test_the_attachment_policy_is_one_line():
    """WHAT AN APPROVED PAIR ATTACHES is the operator's decision and it is not made
    yet (2026-08-21). With N regions the naive answer is 2 + 2N images per pair,
    which floods a comment. `SHOTS_ATT_POLICY` is that decision and nothing else
    encodes it - this test is the proof that flipping the one line is the whole
    change."""
    with _Env() as env:
        env.ticket()
        d = env.work()                               # p1: two regions + two fulls
        pair = env.manifest()["pairs"][0]
        orig = server.SHOTS_ATT_POLICY
        try:
            got = {}
            for policy in ("crops_then_fulls", "crops_only",
                           "fulls_then_crops", "fulls_only"):
                server.SHOTS_ATT_POLICY = policy
                got[policy] = [w for _h, w, _r in server._shots_att_plan(pair)]
            check("policy: crops_then_fulls sends the crops first, the fulls as context",
                  got["crops_then_fulls"] == ["before_crop", "after_crop",
                                              "before_crop", "after_crop",
                                              "before_full", "after_full"],
                  json.dumps(got["crops_then_fulls"]))
            check("policy: crops_only drops the context",
                  got["crops_only"] == ["before_crop", "after_crop",
                                        "before_crop", "after_crop"],
                  json.dumps(got["crops_only"]))
            check("policy: the DEFAULT is the whole picture before its details - the "
                  "order the description line reads in",
                  orig == server.SHOTS_ATT_POLICY_DEFAULT   # the shipped value, saved above
                  and got[server.SHOTS_ATT_POLICY_DEFAULT][:2] == ["before_full", "after_full"]
                  and len(got[server.SHOTS_ATT_POLICY_DEFAULT]) == 6,
                  json.dumps(got[server.SHOTS_ATT_POLICY_DEFAULT]))
            check("policy: fulls_only is two images per pair however many regions there are",
                  got["fulls_only"] == ["before_full", "after_full"],
                  json.dumps(got["fulls_only"]))
            server.SHOTS_ATT_POLICY = "nesto-sto-niko-nije-definisao"
            check("policy: an unknown policy falls back to the DEFAULT (one constant, "
                  "so flipping the knob cannot leave the fallback behind)",
                  [w for _h, w, _r in server._shots_att_plan(pair)]
                  == got[server.SHOTS_ATT_POLICY_DEFAULT], "")
            # and it really is end-to-end: the draft follows the policy
            server.SHOTS_ATT_POLICY = "crops_only"
            draft = server.shots_draft(d, "w1", [(pair, "opis")])
            check("policy: the DRAFT follows it, not just the plan",
                  [a["name"] for a in draft["attachments"]] == [
                      "1-PRE-detalj-1-opis.png", "1-POSLE-detalj-1-opis.png",
                      "1-PRE-detalj-2-opis.png", "1-POSLE-detalj-2-opis.png"],
                  json.dumps([a["name"] for a in draft["attachments"]]))
        finally:
            server.SHOTS_ATT_POLICY = orig


def test_list_route_is_empty_not_broken_without_a_shots_root():
    with _Env() as env:
        env.ticket()
        shutil.rmtree(env.shots, ignore_errors=True)     # the engine never ran here
        httpd, port = _start_server()
        try:
            code, _h, raw = _get(port, "/api/tickets/shots")
            check("list: no shots root at all -> 200 with an empty list (the gallery's empty state)",
                  code == 200 and json.loads(raw) == {"works": []}, f"{code} {raw[:120]}")
        finally:
            httpd.shutdown()
            httpd.server_close()


# --------------------------------------------------------------------------- #
#  GET /api/tickets/shots/img
# --------------------------------------------------------------------------- #
def test_img_route_serves_the_png():
    with _Env() as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            code, h, raw = _get(port, "/api/tickets/shots/img?work_id=w1&pair=p1&which=before_full")
            check("img: 200", code == 200, str(code))
            check("img: Content-Type image/png", h.get("Content-Type") == "image/png",
                  str(h.get("Content-Type")))
            check("img: Cache-Control no-store", h.get("Cache-Control") == "no-store",
                  str(h.get("Cache-Control")))
            check("img: the bytes are the file on disk", raw == PNG_1X1, str(len(raw)))
            c, _hh, rr = _get(port, "/api/tickets/shots/img?work_id=w1&pair=p1&which=after_full")
            check("img: after_full also serves", c == 200 and rr == PNG_1X1, str(c))
            for region in (0, 1):
                for which in ("before_crop", "after_crop"):
                    c, _hh, rr = _get(
                        port, f"/api/tickets/shots/img?work_id=w1&pair=p1"
                              f"&which={which}&region={region}")
                    check(f"img: region {region} {which} serves",
                          c == 200 and rr == PNG_1X1, str(c))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_img_route_refuses_everything_that_is_not_in_the_manifest():
    """The one place a file leaves this machine. A crafted manifest is the attack
    that matters: the value is DATA, and a path that resolves outside the shots
    root is a 404 exactly like a missing pair."""
    with _Env() as env:
        env.ticket()
        env.work()
        # A manifest hand-edited to point out of the jail — both spellings.
        man = env.manifest()
        man["pairs"].append({
            "pair_id": "evil", "page_id": "x", "title": "x", "state": "base",
            "located": True,
            "before": {"full": "../../outside/secret.png"},
            "after": {"full": str(env.outside / "secret.png")},
            # the same escape, one level deeper: a crafted REGION is a crafted path
            "regions": [{"before": {"crop": "../../outside/secret.png"},
                         "after": {"crop": str(env.outside / "secret.png")}}],
            "regions_found": 1})
        (env.shots / "w1" / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        httpd, port = _start_server()
        try:
            code, _h, _b = _get(port, "/api/tickets/shots/img?work_id=w1&pair=evil&which=before_full")
            check("img: relative ../ traversal in the manifest -> 404", code == 404, str(code))
            code, _h, _b = _get(port, "/api/tickets/shots/img?work_id=w1&pair=evil&which=after_full")
            check("img: absolute path outside the shots root -> 404", code == 404, str(code))
            code, _h, _b = _get(port, "/api/tickets/shots/img?work_id=w1&pair=ghost&which=before_full")
            check("img: a pair id that is not in the manifest -> 404", code == 404, str(code))
            code, _h, _b = _get(port, "/api/tickets/shots/img?work_id=w1&pair=p1&which=sideways")
            check("img: an unknown `which` -> 404", code == 404, str(code))
            code, _h, _b = _get(port, "/api/tickets/shots/img?work_id=..&pair=p1&which=before_full")
            check("img: a traversing work_id -> 404", code == 404, str(code))
            code, _h, _b = _get(port, "/api/tickets/shots/img?work_id=w1&pair=&which=before_full")
            check("img: an empty pair id -> 404", code == 404, str(code))
            check("img: the evil pair is still LISTED (only its bytes are refused)",
                  any(p.get("pair_id") == "evil" and p.get("img") == {}
                      for p in (json.loads(_get(port, "/api/tickets/shots")[2])
                                .get("works")[0].get("pairs"))),
                  "listing should carry no img url for an unresolvable pair")
        finally:
            httpd.shutdown()
            httpd.server_close()


# --------------------------------------------------------------------------- #
#  The loopback gate
# --------------------------------------------------------------------------- #
def test_a_non_loopback_peer_is_refused_on_all_three_routes():
    with _Env() as env:
        env.ticket()
        env.work()
        orig = server.Handler._client_is_local
        server.Handler._client_is_local = lambda self: False
        httpd, port = _start_server()
        try:
            code, _h, _b = _get(port, "/api/tickets/shots")
            check("gate: LAN peer cannot LIST the shots", code == 403, str(code))
            code, _h, _b = _get(port, "/api/tickets/shots/img?work_id=w1&pair=p1&which=before_full")
            check("gate: LAN peer cannot fetch a screenshot", code == 403, str(code))
            code, _j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [{"pair_id": "p1", "caption": "x"}]})
            check("gate: LAN peer cannot approve", code == 403, str(code))
            check("gate: the refused approve wrote nothing", env.outbox() == [], str(env.outbox()))
        finally:
            server.Handler._client_is_local = orig
            httpd.shutdown()
            httpd.server_close()


# --------------------------------------------------------------------------- #
#  POST /api/tickets/shots/approve
# --------------------------------------------------------------------------- #
def test_approve_writes_exactly_one_draft_with_attachments():
    with _Env() as env:
        env.ticket(outbox=[{"id": "old1", "body": "raniji nacrt", "attachments": [],
                            "posted": False}])
        env.work()
        httpd, port = _start_server()
        try:
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w1", "ticket": "DEMO#08597",
                             "pairs": [{"pair_id": "p1", "caption": "dodat podnaslov ispod naziva"}],
                             "drop": ["p2"]})
            check("approve: 200", code == 200, f"{code} {j}")
            check("approve: reports the pair count the operator sees", j.get("pairs") == 1, str(j))
            box = env.outbox()
            shots = [d for d in box if d.get("kind") == "shots"]
            check("approve: exactly ONE shots draft", len(shots) == 1, json.dumps(box)[:300])
            check("approve: the pre-existing draft is untouched",
                  any(d.get("id") == "old1" for d in box), json.dumps(box)[:300])
            dr = shots[0] if shots else {}
            check("approve: the draft is tagged kind/work_id so a re-approve can find it",
                  dr.get("kind") == "shots" and dr.get("work_id") == "w1", json.dumps(dr)[:200])
            check("approve: the body is the Serbian lead sentence + one NUMBERED line "
                  "per pair, naming that screen's pictures by role",
                  dr.get("body") == "Slike ekrana pre i posle izmene:\n"
                                    "1. dodat podnaslov ispod naziva (Lista lokacija) "
                                    "\u2014 slike: PRE, POSLE, PRE detalj 1, POSLE detalj 1, "
                                    "PRE detalj 2, POSLE detalj 2",
                  repr(dr.get("body")))
            atts = dr.get("attachments") or []
            check("approve: six attachments for a pair with TWO changed regions (2x2 + 2)",
                  len(atts) == 6, json.dumps(atts)[:300])
            # SHOTS_ATT_POLICY, the flippable line: the whole picture first, its zoomed
            # details after it - the order the description line lists them in. The
            # detalj-<n> suffix is the same number the panel prints as "n/N", so an
            # attachment matches the comparison it came from without opening it.
            check("approve: attachment order follows SHOTS_ATT_POLICY - fulls then details",
                  [a.get("name") for a in atts] == [
                      "1-PRE-dodat-podnaslov-ispod-naziva.png",
                      "1-POSLE-dodat-podnaslov-ispod-naziva.png",
                      "1-PRE-detalj-1-dodat-podnaslov-ispod-naziva.png",
                      "1-POSLE-detalj-1-dodat-podnaslov-ispod-naziva.png",
                      "1-PRE-detalj-2-dodat-podnaslov-ispod-naziva.png",
                      "1-POSLE-detalj-2-dodat-podnaslov-ispod-naziva.png"],
                  json.dumps([a.get("name") for a in atts]))
            check("approve: the role is at the FRONT, where a truncated file list "
                  "cannot cut it off",
                  all(re.match(r"^\d+-(PRE|POSLE)(-detalj-\d+)?-", a.get("name", ""))
                      for a in atts),
                  json.dumps([a.get("name") for a in atts]))
            check("approve: nothing in a customer-facing name is developer vocabulary",
                  not any(w in a.get("name", "").lower() for a in atts
                          for w in ("before", "after", "crop", "base", "dashboard",
                                    "_", "page")),
                  json.dumps([a.get("name") for a in atts]))
            check("approve: every attachment states the PAIR it belongs to, so the "
                  "chunker never has to parse the name",
                  all(a.get("pair") == "p1" for a in atts),
                  json.dumps([a.get("pair") for a in atts]))
            check("approve: every attachment path is absolute and inside the shots root",
                  all(Path(a["path"]).is_absolute()
                      and Path(a["path"]).resolve().is_relative_to(env.shots.resolve())
                      and Path(a["path"]).is_file() for a in atts),
                  json.dumps([a.get("path") for a in atts])[:300])
            check("approve: pair_count rides along for the ticket modal",
                  dr.get("pair_count") == 1, str(dr.get("pair_count")))
            check("approve: THE DRAFT IS POSTED, in this very request — delivery does "
                  "not wait for a close that may never come",
                  dr.get("posted") is True and j.get("send") == "posted",
                  f"{dr.get('posted')} {j.get('send')} {j.get('send_error')}")
            check("approve: six pictures do not fit one comment, so it went as several "
                  "and the reply says how many",
                  j.get("comments") == j.get("comments_total") == len(env.helpdesk.comments)
                  and j.get("comments") >= 2, str(j))
            check("approve: the OTHER draft on the same ticket was not sent along with it",
                  next(d for d in box if d.get("id") == "old1").get("posted") is not True
                  and not any("raniji nacrt" in c[1] for c in env.helpdesk.comments),
                  json.dumps(env.helpdesk.comments)[:200])
            man = env.manifest()
            check("approve: the manifest records approved true/false per pair",
                  man["pairs"][0].get("approved") is True
                  and man["pairs"][1].get("approved") is False, json.dumps(man["pairs"])[:200])
            check("approve: the store rev was bumped",
                  json.loads((env.store / "DEMO.json").read_text(encoding="utf-8"))["rev"] > 3,
                  "the draft write bumps it, and so does every claim/record of the send")
            check("approve: the API never hands the browser the attachment PATHS",
                  all("path" not in a for a in
                      (server.tickets_data()["projects"][0]["tickets"][0]["outbox"][-1]
                       .get("attachments") or [])),
                  "attachments must leave as {name} only")
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_rejection_is_a_state_on_disk_and_not_a_thing_the_browser_remembers():
    """THE DEFECT: the panel had three states and the manifest had two.

    "odbaci" wrote `approved: false` — indistinguishable from a pair nobody has
    looked at — so leaving the gallery and coming back showed the rejected pair
    as never reviewed, and the operator reviewed it again. A UI state that cannot
    round-trip through storage is a bug in the SCHEMA, not in the UI.
    """
    with _Env() as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w1", "ticket": "DEMO#08597",
                             "pairs": [], "reject": ["p1"], "drop": ["p2"]})
            check("reject: 200", code == 200, f"{code} {j}")
            man = env.manifest()
            by = {p["pair_id"]: p for p in man["pairs"]}
            check("reject: the refusal is PERSISTED as its own state",
                  by["p1"].get("decision") == "rejected", json.dumps(by["p1"])[:200])
            check("reject: never-reviewed is persisted as a THIRD value, not as "
                  "'rejected' and not as a missing key",
                  by["p2"].get("decision") == "", json.dumps(by["p2"])[:200])
            check("reject: `approved` still says the same thing it always did",
                  by["p1"].get("approved") is False and by["p2"].get("approved") is False,
                  json.dumps(man["pairs"])[:200])
            _, listing = _getj(port, "/api/tickets/shots")
            pairs = {p["pair_id"]: p for p in listing["works"][0]["pairs"]}
            check("reject: and the gallery gets it back — a reload shows a rejected "
                  "pair as rejected",
                  pairs["p1"]["decision"] == "rejected"
                  and pairs["p2"]["decision"] == "", json.dumps(pairs)[:300])
            # precedence, stated once in shots_approve and checked here
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [{"pair_id": "p1"}], "reject": ["p1", "p2"], "drop": ["p2"]})
            by = {p["pair_id"]: p for p in env.manifest()["pairs"]}
            check("reject: rejecting beats approving, and dropping beats both",
                  by["p1"].get("decision") == "rejected"
                  and by["p2"].get("decision") == "", json.dumps(by)[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_manifest_that_predates_the_third_state_needs_no_migration():
    """`approved: true` IS "approved" and everything else IS "not reviewed" —
    which is exactly what the reader derives. The derivation is the migration, so
    a run captured before this key existed keeps its approvals and never comes
    back as something the operator has to review again."""
    with _Env() as env:
        env.ticket()
        env.work(pairs=[
            {"pair_id": "p1", "page_id": "a", "title": "A", "state": "base",
             "approved": True, "before": {"full": "img/p1-before.png"},
             "after": {"full": "img/p1-after.png"}, "regions": []},
            {"pair_id": "p2", "page_id": "b", "title": "B", "state": "base",
             "before": {"full": "img/p2-before.png"},
             "after": {"full": "img/p2-after.png"}, "regions": []}])
        httpd, port = _start_server()
        try:
            _, listing = _getj(port, "/api/tickets/shots")
            pairs = {p["pair_id"]: p for p in listing["works"][0]["pairs"]}
            check("legacy: an old approval still reads as approved",
                  pairs["p1"]["decision"] == "approved" and pairs["p1"]["approved"] is True,
                  json.dumps(pairs["p1"])[:200])
            check("legacy: everything else reads as never reviewed",
                  pairs["p2"]["decision"] == "", json.dumps(pairs["p2"])[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_new_screen_tells_the_customer_what_it_is_and_how_to_reach_it():
    """A page that did not exist before is one picture and a customer who has
    never seen it. The comment says what it is, where it is and how to get there
    — and when the engine could not derive an honest click path it leaves the
    line BLANK for the operator rather than inventing one."""
    with _Env() as env:
        env.ticket()
        env.work(pairs=[
            {"pair_id": "p1", "page_id": "settings:city_bulk", "state": "base",
             "title": "Grupni unos gradova", "caption": "Grupni unos gradova",
             "baseline": "none", "new_page": True,
             "nav": {"label": "Grupni unos gradova", "path": "/settings/cities/bulk/",
                     "steps": ["Podešavanja", "Gradovi", "dugme „Grupni unos“"]},
             "before": {"full": None}, "after": {"full": "img/p1-after.png"},
             "regions": []},
            {"pair_id": "p2", "page_id": "x:unmapped", "state": "base",
             "title": "Ekran bez mape", "caption": "Ekran bez mape",
             "baseline": "none", "new_page": True,
             "nav": {"label": "Ekran bez mape", "path": "/x/", "steps": []},
             "before": {"full": None}, "after": {"full": "img/p2-after.png"},
             "regions": []}])
        httpd, port = _start_server()
        try:
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [{"pair_id": "p1"}, {"pair_id": "p2"}]})
            dr = [d for d in env.outbox() if d.get("kind") == "shots"][0]
            check("new page: the three lines the operator asked for, in order",
                  dr["body"] == "Slike ekrana posle izmene (snimak pre izmene ne postoji):\n"
                                "1. Nova stranica: Grupni unos gradova — slike: POSLE\n"
                                "Putanja: /settings/cities/bulk/\n"
                                "Do nje: Podešavanja → Gradovi → dugme „Grupni unos“\n"
                                "2. Nova stranica: Ekran bez mape — slike: POSLE\n"
                                "Putanja: /x/\n"
                                "Do nje:",
                  repr(dr["body"]))
            check("new page: no 'bez snimka pre izmene' apology — the first line "
                  "already says the screen is new",
                  "bez snimka pre izmene" not in dr["body"], repr(dr["body"]))
            _, listing = _getj(port, "/api/tickets/shots")
            pairs = {p["pair_id"]: p for p in listing["works"][0]["pairs"]}
            # the card previews the same LINES; the comment adds the screen's
            # number and its picture list to the first of them
            prev = pairs["p1"]["nav"]["text"].split("\n")
            check("new page: the card previews the SAME text the comment carries",
                  all(ln in dr["body"] for ln in prev[1:])
                  and prev[0] in dr["body"]
                  and pairs["p2"]["nav"]["text"].endswith("Do nje:"),
                  json.dumps(pairs["p1"]["nav"])[:300])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_run_is_promoted_and_removed_only_when_it_is_finished_and_delivered():
    """A PROCESSED run leaves the gallery. Decided is decided however it went —
    but a send that FAILED is not "processed", and deleting the folder there
    would take the pictures away while the customer has nothing, which is the one
    outcome with no way back. The draft names the PNGs by absolute path inside
    that folder, so there would be nothing left to retry with."""
    with _Env(FakeAdapter(fail=["add_comment"])) as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            check("finish: an undecided run is not finished",
                  server.shots_finished("w1")[0] is False
                  and "not decided" in server.shots_finished("w1")[1],
                  str(server.shots_finished("w1")))
            _code, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [{"pair_id": "p1"}], "reject": ["p2"]})
            check("finish: the send failed and says so, distinctly",
                  j.get("send") == "failed" and j.get("send_error"), str(j))
            ok, why = server.shots_finished("w1")
            check("finish: every pair decided, but the pictures NEVER REACHED the "
                  "customer — nothing is promoted and nothing is deleted",
                  ok is False and "delivered" in why, why)
            check("finish: the run is still on disk with its pictures",
                  (env.shots / "w1" / "manifest.json").is_file(), "")
            check("finish: and it is still listed, so the operator can send again",
                  [w["work_id"] for w in _getj(port, "/api/tickets/shots")[1]["works"]] == ["w1"],
                  "")
            # The retry gets through: same draft, resumed, no second copy of it.
            env.helpdesk.fail = set()
            _code, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [{"pair_id": "p1"}], "reject": ["p2"]})
            check("finish: the retry posts", j.get("send") == "posted", str(j))
            check("finish: still ONE shots draft — a failed send leaves nothing behind "
                  "to double up on",
                  len([d for d in env.outbox() if d.get("kind") == "shots"]) == 1,
                  json.dumps(env.outbox())[:300])
            ok, why = server.shots_finished("w1")
            check("finish: delivered AND decided is finished", ok is True, why)
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_processed_run_leaves_however_the_operator_decided_it():
    """"Ne bitno da li sam ga poslao ili nisam ili delimicno neke komentare i
    slike jesam neke nisam" (operator, 2026-08-23). Approved-some / approved-none
    are both "processed": the run is finished and goes. Only an undelivered
    comment holds it."""
    for label, body in (("nothing approved", {"pairs": [], "reject": ["p1", "p2"]}),
                        ("partly approved", {"pairs": [{"pair_id": "p1"}],
                                             "reject": ["p2"]})):
        with _Env() as env:
            env.ticket()
            env.work()
            httpd, port = _start_server()
            try:
                payload = {"work_id": "w1", "ticket": "DEMO#08597"}
                payload.update(body)
                _code, j = _post(port, "/api/tickets/shots/approve", payload)
                ok, why = server.shots_finished("w1")
                check(f"processed ({label}): the run is finished and leaves the gallery",
                      ok is True, f"{why} {j}")
            finally:
                httpd.shutdown()
                httpd.server_close()
    with _Env() as env:                       # and the refusal sends NOTHING
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            _code, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [], "reject": ["p1", "p2"]})
            check("processed (nothing approved): not one comment was posted",
                  env.helpdesk.comments == [] and j.get("send") == "nothing",
                  f"{j} {json.dumps(env.helpdesk.comments)[:200]}")
            check("processed (nothing approved): and no draft was left behind",
                  [d for d in env.outbox() if d.get("kind") == "shots"] == [],
                  json.dumps(env.outbox())[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_decided_run_with_real_shots_actually_leaves_the_disk():
    """THE POSITIVE TWIN, and it is new because nothing here had one: every
    fixture before this omitted the manifest's `shots` section, so promotion had
    nothing to promote, `finalize_run` correctly refused, and every "the run is
    finished" assertion measured `shots_finished` alone — never the delete.

    Rejecting everything is a decision like any other: the pictures still become
    the baseline (they are the app's true current state) and the folder still
    goes."""
    with _Env() as env:
        env.ticket()
        d = env.work(promotable=True)
        httpd, port = _start_server()
        try:
            _code, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [], "reject": ["p1", "p2"]})
            fin = j.get("finalized") or []
            check("leaves: the sweep reports the run deleted",
                  any(f.get("work_id") == "w1" and f.get("deleted") for f in fin),
                  json.dumps(fin)[:300])
            check("leaves: the folder is really gone from disk", not d.is_dir(), str(d))
            check("leaves: and the gallery is empty",
                  _getj(port, "/api/tickets/shots")[1]["works"] == [], "")
            check("leaves: the pictures survived as the repo's baseline",
                  (env.repo / ".visual-baseline" / "index.json").is_file()
                  and len(json.loads((env.repo / ".visual-baseline" / "index.json")
                                     .read_text(encoding="utf-8")).get("shots") or {}) == 2,
                  str(env.repo))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_decided_run_that_cannot_promote_says_why_and_can_be_discarded():
    """The defect the operator reported (2026-08-28): "kada kliknem da se ne
    šalje ne obriše se iz galerije". Refusing every pair decided the ticket, the
    rejections persisted, nothing was owed to the customer — and the card stayed,
    looking exactly like an untouched one, with no button that could clear it.

    The run staying is RIGHT (its pictures cannot be proven into the baseline, and
    the engine will not delete what it could not save). What was missing is the
    panel being told, and a way out."""
    with _Env() as env:
        env.ticket()
        d = env.work()                       # no shots section -> nothing to promote
        httpd, port = _start_server()
        try:
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [], "reject": ["p1", "p2"]})
            check("stuck: the run is finished as far as the decisions go",
                  server.shots_finished("w1")[0] is True, str(server.shots_finished("w1")))
            check("stuck: but it is still on disk", d.is_dir(), str(d))
            _code, lst = _getj(port, "/api/tickets/shots")
            stay = (lst["works"][0] or {}).get("stay") or {}
            check("stuck: the listing SAYS it is stuck, and in Serbian",
                  stay.get("blocked") is True and stay.get("finished") is True
                  and "baseline" in stay.get("reason", ""),
                  json.dumps(stay, ensure_ascii=False)[:300])
            check("stuck: the reason survives on the manifest, so a reload still explains it",
                  (env.manifest().get("finalize_error") or {}).get("reason"),
                  json.dumps(env.manifest().get("finalize_error"))[:200])
            # ... and the way out
            code, j = _post(port, "/api/tickets/shots/discard", {"work_id": "w1"})
            check("discard: it removes the run and says what was given up",
                  code == 200 and j.get("deleted") is True and j.get("reason"),
                  f"{code} {json.dumps(j)[:300]}")
            check("discard: the folder is gone", not d.is_dir(), str(d))
            check("discard: and the gallery is empty",
                  _getj(port, "/api/tickets/shots")[1]["works"] == [], "")
            check("discard: the decisions it was about are untouched on the ticket",
                  [x for x in env.outbox() if x.get("kind") == "shots"] == [],
                  json.dumps(env.outbox())[:200])
            code, j = _post(port, "/api/tickets/shots/discard", {"work_id": "w1"})
            check("discard: a second one is a clean 404, never a crash",
                  code == 404, f"{code} {j}")
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_discard_refuses_the_two_states_where_it_would_destroy_something():
    """The escape hatch is not a delete button. It refuses while a pair is still
    undecided (the pictures are the only record of what the operator was about to
    look at) and while ANYTHING is still owed to the customer — a failed or
    ambiguous send has to be retryable, and the draft names the PNGs inside the
    folder being asked for."""
    with _Env() as env:                          # 1. undecided
        env.ticket()
        d = env.work()
        httpd, port = _start_server()
        try:
            code, j = _post(port, "/api/tickets/shots/discard", {"work_id": "w1"})
            check("discard: an undecided run is refused with 409 and a reason",
                  code == 409 and "not decided" in str(j.get("why")), f"{code} {j}")
            check("discard: and nothing was removed", d.is_dir(), str(d))
        finally:
            httpd.shutdown()
            httpd.server_close()
    with _Env(FakeAdapter(fail=["add_comment"])) as env:    # 2. the customer is owed
        env.ticket()
        d = env.work()
        httpd, port = _start_server()
        try:
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [{"pair_id": "p1"}], "reject": ["p2"]})
            code, j = _post(port, "/api/tickets/shots/discard", {"work_id": "w1"})
            check("discard: a failed send holds the folder — 409, not a delete",
                  code == 409 and "delivered" in str(j.get("why")), f"{code} {j}")
            check("discard: the pictures the retry needs are still there",
                  (d / "img" / "p1-after.png").is_file(), str(d))
            check("discard: and the panel is told it is NOT the stuck case",
                  ((j.get("stay") or {}).get("blocked")) is False,
                  json.dumps(j.get("stay"), ensure_ascii=False)[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()
    with _Env() as env:                          # 3. unknown work / bad body
        env.ticket()
        httpd, port = _start_server()
        try:
            code, _j = _post(port, "/api/tickets/shots/discard", {"work_id": "../etc"})
            check("discard: a work id outside the jail is a 404, never a path walk",
                  code == 404, str(code))
            code, _j = _post(port, "/api/tickets/shots/discard", {})
            check("discard: a body with no work_id is a 400", code == 400, str(code))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_the_automatic_sweep_never_forces_and_the_operator_always_can():
    """`force` belongs to the operator's button and to nothing else. The sweep
    runs inside somebody else's request with nobody watching, and there its
    refusal to delete what it could not save is the whole safety property."""
    src = (HERE / "server.py").read_text(encoding="utf-8")
    sweep = src.split("def shots_finalize_all(")[1].split("\ndef ")[0]
    check("sweep: finalize_run is called WITHOUT force",
          "engine.finalize_run(name)" in sweep and "force" not in sweep, "")
    disc = src.split("def shots_discard(")[1].split("\ndef ")[0]
    check("discard: and the operator's door is the one that passes it",
          "force=True" in disc, "")
    # against the CALL, not the docstring — which names force=True while
    # explaining it and put the first match 600 characters too early
    check("discard: which still checks finished + delivered first",
          disc.index("ok, why = shots_finished(")
          < disc.index("engine.finalize_run(work_id, force=True)"), "")
    eng = (HERE.parent / "scripts" / "visual" / "shoot.py").read_text(encoding="utf-8")
    fin = eng.split("def finalize_run(")[1].split("\ndef ")[0]
    check("engine: force never relaxes the folder jail — that one is about WHERE",
          fin.index("does not resolve inside the shots root")
          > fin.index("shutil.rmtree") - 400
          and "if not folder.is_relative_to(root)" in fin, "")


def test_the_finalize_route_takes_no_arguments_and_still_reads_its_body():
    """A mutating route that does not drain its request body leaves it in the
    socket, and the NEXT request on the same keep-alive connection is parsed
    starting at those bytes: the server answered `501 Unsupported method
    ('{}GET')` and the panel never loaded. Caught by driving the HUD, not by
    reading the diff."""
    import http.client
    with _Env() as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/tickets/shots/finalize",
                         body=b'{"anything": 1}',
                         headers={"Content-Type": "application/json",
                                  "Origin": f"http://127.0.0.1:{port}"})
            r1 = conn.getresponse()
            b1 = r1.read()
            # the SAME connection, exactly as the browser reuses it
            conn.request("GET", "/api/tickets/shots")
            r2 = conn.getresponse()
            b2 = r2.read()
            conn.close()
            check("finalize: the route answers 200 and reports what it did",
                  r1.status == 200 and "finalized" in json.loads(b1), f"{r1.status} {b1[:120]}")
            check("finalize: the next request on the same connection is not "
                  "poisoned by an unread body",
                  r2.status == 200 and "works" in json.loads(b2),
                  f"{r2.status} {b2[:160]}")
            check("finalize: an unfinished run is left exactly where it is",
                  (env.shots / "w1" / "manifest.json").is_file(), "")
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_post_to_an_unknown_route_does_not_poison_the_connection():
    """The route above was fixed one route at a time; this pins the CLASS.

    The operator's HUD was an older process than the JS its browser had loaded,
    so the panel POSTed `/api/tickets/shots/finalize` — a route that server did
    not have. The 404 answered without reading the body, the `{}` stayed in the
    socket, and the next request on that connection came back
    `501 Unsupported method ('{}GET')` as an HTML error page. The panel did
    `JSON.parse` on it and showed `Unexpected token '<', "<!DOCTYPE "`, which
    names neither the route that broke nor the one that failed.

    Reproduced against the live HUD 2026-08-23. The drain now lives in `_send`,
    so every reply that did not read its body is covered — 404, 413, 421 and
    whatever guard gets written next — not just the routes noticed so far."""
    import http.client
    with _Env() as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/tickets/shots/no-such-route-here",
                         body=b'{"a": 1}',
                         headers={"Content-Type": "application/json",
                                  "Origin": f"http://127.0.0.1:{port}"})
            r1 = conn.getresponse()
            r1.read()
            conn.request("GET", "/api/tickets/shots")
            r2 = conn.getresponse()
            b2 = r2.read()
            conn.close()
            check("unknown POST: it is a 404, not a crash", r1.status == 404,
                  str(r1.status))
            check("unknown POST: the next request on the same connection still "
                  "gets JSON, not an HTML 501",
                  r2.status == 200 and "works" in json.loads(b2),
                  f"{r2.status} {b2[:160]}")
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_second_approve_replaces_the_draft_instead_of_duplicating_it():
    """Replacement is what happens to a draft the customer has NOT got. The first
    send is refused by the helpdesk, so the draft is still ours to rewrite; the
    second one lands. (A draft that DID reach the customer is a record and is
    never touched — `test_an_already_posted_shots_draft_is_never_rewritten`.)"""
    with _Env(FakeAdapter(fail=["add_comment"])) as env:
        env.ticket(outbox=[{"id": "old1", "body": "raniji nacrt", "posted": False}])
        env.work()
        httpd, port = _start_server()
        try:
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [{"pair_id": "p1", "caption": "prva verzija"}]})
            env.helpdesk.fail = set()                  # the retry gets through
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w1", "ticket": "DEMO#08597",
                             "pairs": [{"pair_id": "p1", "caption": "ispravljen opis"},
                                       {"pair_id": "p2", "caption": "drugi ekran"}]})
            check("re-approve: 200", code == 200, f"{code} {j}")
            box = env.outbox()
            shots = [d for d in box if d.get("kind") == "shots"]
            check("re-approve: still exactly ONE shots draft for this work",
                  len(shots) == 1, json.dumps(box)[:300])
            check("re-approve: it reports having replaced one", j.get("replaced") == 1, str(j))
            check("re-approve: the new captions are the ones in the body",
                  "ispravljen opis" in (shots[0].get("body") or "")
                  and "prva verzija" not in (shots[0].get("body") or ""),
                  repr(shots[0].get("body")))
            check("re-approve: both pairs are in the body now, numbered in order",
                  (shots[0].get("body") or "").count("\n1. ") == 1
                  and (shots[0].get("body") or "").count("\n2. ") == 1,
                  repr(shots[0].get("body")))
            check("re-approve: eight attachments (6 + 2, the second pair has no region)",
                  len(shots[0].get("attachments") or []) == 8,
                  str(len(shots[0].get("attachments") or [])))
            check("re-approve: the unrelated draft survived every rewrite",
                  any(d.get("id") == "old1" for d in box), json.dumps(box)[:300])
            check("re-approve: and it was never POSTED — the send delivers the draft "
                  "the operator confirmed, never everything the ticket happens to hold",
                  next(d for d in box if d.get("id") == "old1").get("posted") is not True
                  and not any("raniji nacrt" in c[1] for c in env.helpdesk.comments),
                  json.dumps(env.helpdesk.comments)[:300])
            check("re-approve: the second attempt actually reached the helpdesk",
                  j.get("send") == "posted" and len(env.helpdesk.comments) >= 1, str(j))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_pair_goes_in_as_images_only_comment_only_or_both():
    """Per pair the operator picks what rides into the comment: the PNGs, the
    description line, or both. "both" is the default AND what a request that
    sends no mode at all means, so nothing that predates the choice changes.

    THE HELPDESK REFUSES throughout, on purpose: this is about what the draft
    SAYS, and it re-approves the same work five times. A draft that reached the
    customer is never rewritten (it cannot be — the comment is live), so a
    succeeding send here would make every later approve a second draft and the
    assertions would be reading the wrong one."""
    with _Env(FakeAdapter(fail=["add_comment"])) as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            def approve(items, drop=None):
                return _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": items, "drop": drop or []})

            def draft():
                got = [d for d in env.outbox() if d.get("kind") == "shots"]
                return got[0] if got else {}

            # -- images only: the pictures attach, the text says nothing about this pair
            approve([{"pair_id": "p1", "caption": "dodat podnaslov", "mode": "images"}], ["p2"])
            dr = draft()
            check("mode images: no description line for the pair",
                  dr.get("body") == server.SHOTS_HEAD_SR, repr(dr.get("body")))
            check("mode images: every PNG of the pair attaches (2 regions + the fulls)",
                  len(dr.get("attachments") or []) == 6, json.dumps(dr.get("attachments"))[:200])

            # -- comment only: the line goes in, nothing is attached, and the lead
            #    sentence must not promise pictures that are not there
            approve([{"pair_id": "p1", "caption": "dodat podnaslov", "mode": "comment"}], ["p2"])
            dr = draft()
            check("mode comment: the description line goes in",
                  dr.get("body") == server.SHOTS_HEAD_TEXT_ONLY_SR
                                    + "\n- dodat podnaslov (Lista lokacija)", repr(dr.get("body")))
            check("mode comment: nothing is attached",
                  (dr.get("attachments") or []) == [], json.dumps(dr.get("attachments"))[:200])
            check("mode comment: the lead sentence never promises a picture it did not attach",
                  server.SHOTS_HEAD_SR not in dr.get("body", ""), repr(dr.get("body")))

            # -- mixed: one pair for its pictures, the other for its words
            approve([{"pair_id": "p1", "caption": "prvi", "mode": "images"},
                     {"pair_id": "p2", "caption": "drugi", "mode": "comment"}])
            dr = draft()
            check("mode mixed: only the comment pair is described",
                  dr.get("body") == server.SHOTS_HEAD_SR + "\n- drugi (Detalj lokacije)",
                  repr(dr.get("body")))
            check("mode mixed: only the images pair is attached",
                  [a.get("name") for a in dr.get("attachments") or []]
                  == ["1-PRE-prvi.png", "1-POSLE-prvi.png",
                      "1-PRE-detalj-1-prvi.png", "1-POSLE-detalj-1-prvi.png",
                      "1-PRE-detalj-2-prvi.png", "1-POSLE-detalj-2-prvi.png"],
                  json.dumps([a.get("name") for a in dr.get("attachments") or []]))
            check("mode mixed: the described pair contributed no picture, so its line "
                  "claims none",
                  "slike:" not in (dr.get("body") or ""), repr(dr.get("body")))
            check("mode mixed: pair_count is what the operator approved, both modes",
                  dr.get("pair_count") == 2, str(dr.get("pair_count")))

            # -- the manifest remembers the choice, and the gallery reads it back
            man = env.manifest()
            check("mode: the manifest records the choice per pair",
                  [p.get("approve_mode") for p in man["pairs"]] == ["images", "comment"],
                  json.dumps([p.get("approve_mode") for p in man["pairs"]]))
            _c, _h, raw = _get(port, "/api/tickets/shots?ticket=DEMO%2308597")
            pairs = (json.loads(raw)["works"][0]).get("pairs") or []
            check("mode: the gallery reopens on the choice the operator made",
                  [p.get("mode") for p in pairs] == ["images", "comment"],
                  json.dumps([p.get("mode") for p in pairs]))

            # -- backward compatibility: no mode, and a mode nobody defined, both mean "both"
            approve([{"pair_id": "p1", "caption": "dodat podnaslov ispod naziva"}], ["p2"])
            dr = draft()
            check("mode absent: an approve that sends no mode still means BOTH",
                  dr.get("body", "").startswith(
                      server.SHOTS_HEAD_SR
                      + "\n1. dodat podnaslov ispod naziva (Lista lokacija) \u2014 slike: ")
                  and len(dr.get("attachments") or []) == 6, repr(dr.get("body")))
            approve([{"pair_id": "p1", "caption": "x", "mode": "izmisljeno"}], ["p2"])
            dr = draft()
            check("mode unknown: a mode nobody defined degrades to BOTH, never to nothing",
                  dr.get("body", "").startswith(
                      server.SHOTS_HEAD_SR + "\n1. x (Lista lokacija) \u2014 slike: ")
                  and len(dr.get("attachments") or []) == 6, repr(dr.get("body")))
            approve(["p1"], ["p2"])                       # the bare-id form
            dr = draft()
            check("mode: the bare pair-id form still means BOTH",
                  len(dr.get("attachments") or []) == 6
                  and dr.get("body", "").startswith(server.SHOTS_HEAD_SR), repr(dr.get("body")))
            check("mode: still exactly ONE draft after every re-approve",
                  len([d for d in env.outbox() if d.get("kind") == "shots"]) == 1,
                  json.dumps(env.outbox())[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_work_whose_ticket_was_typed_without_a_hash_can_still_be_approved():
    """The reference reaches the manifest exactly as the operator typed it on the
    capture command line. "DEMO05513" used to be refused as "not attached to a
    ticket" — the pictures were captured for a ticket nobody could approve them
    onto."""
    with _Env() as env:
        env.ticket()
        env.work(work_id="wnohash", ticket="DEMO08597")
        httpd, port = _start_server()
        try:
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "wnohash", "pairs": [{"pair_id": "p1", "caption": "x"}]})
            check("no-hash ticket: approved onto the ticket it names (%s)" % code,
                  code == 200 and j.get("ticket") == "DEMO#08597" and j.get("pairs") == 1, f"{code} {j}")
            check("no-hash ticket: the draft landed on that ticket",
                  len([d for d in env.outbox() if d.get("kind") == "shots"]) == 1,
                  json.dumps(env.outbox())[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_approving_zero_pairs_removes_the_draft():
    """The first send is refused, so the draft is still undelivered and can be
    dropped. Nothing goes to the customer on the way through."""
    with _Env(FakeAdapter(fail=["add_comment"])) as env:
        env.ticket(outbox=[{"id": "old1", "body": "raniji nacrt", "posted": False}])
        env.work()
        httpd, port = _start_server()
        try:
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [{"pair_id": "p1", "caption": "prva verzija"}]})
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w1", "ticket": "DEMO#08597",
                             "pairs": [], "drop": ["p1", "p2"]})
            check("clear: 200", code == 200, f"{code} {j}")
            check("clear: reports zero pairs and no draft",
                  j.get("pairs") == 0 and j.get("draft") is False, str(j))
            box = env.outbox()
            check("clear: no shots draft is left",
                  [d for d in box if d.get("kind") == "shots"] == [], json.dumps(box)[:300])
            check("clear: the unrelated draft is still there",
                  [d.get("id") for d in box] == ["old1"], json.dumps(box)[:300])
            man = env.manifest()
            check("clear: the manifest agrees — nothing is approved",
                  all(p.get("approved") is False for p in man["pairs"]),
                  json.dumps(man["pairs"])[:200])
            check("clear: with nothing approved there is nothing to send",
                  j.get("send") == "nothing" and env.helpdesk.comments == [],
                  f"{j.get('send')} {json.dumps(env.helpdesk.comments)[:200]}")
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_an_already_posted_shots_draft_is_never_rewritten():
    """A posted draft is the record of what the customer received — a later
    approve adds a new draft next to it rather than editing history."""
    with _Env() as env:
        env.ticket(outbox=[{"id": "sent1", "kind": "shots", "work_id": "w1",
                            "body": "Slike ekrana pre i posle izmene:\n- staro (X)",
                            "attachments": [], "pair_count": 1,
                            "posted": True, "posted_at": "2026-08-19T10:00:00"}])
        env.work()
        httpd, port = _start_server()
        try:
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w1", "ticket": "DEMO#08597",
                             "pairs": [{"pair_id": "p2", "caption": "novi ekran"}]})
            box = env.outbox()
            check("posted: the sent draft is still there, unchanged",
                  any(d.get("id") == "sent1" and d.get("posted") is True
                      and "staro" in d.get("body", "") for d in box), json.dumps(box)[:300])
            check("posted: the new approval is a SECOND draft",
                  len([d for d in box if d.get("kind") == "shots"]) == 2
                  and j.get("replaced") == 0, f"{code} {json.dumps(box)[:300]}")
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_partly_delivered_shots_draft_is_resumed_and_never_doubled():
    """A draft too big for one comment goes out as several. Once ANY of them has
    landed, the draft is the record of what the customer already has.

    THE DEFECT THE IMMEDIATE SEND EXPOSED: the next approve used to write a
    SECOND draft beside it holding every pair again. While delivery waited for a
    ticket close that was survivable (writeback resumed the first, then posted
    the second); now the button sends what it just wrote, so pressing it again
    would post a fresh copy of the pictures the customer already has while the
    remainder of the first delivery sat unsent for ever. A held draft is
    RESUMED: no new draft, no second copy, and the reply says `resumed`."""
    with _Env() as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            # A real half-delivery: the second comment of a two-comment draft fails.
            env.helpdesk.fail_after = 1
            _code, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [{"pair_id": "p1", "caption": "prvi ekran"}],
                              "reject": ["p2"]})
            box = env.outbox()
            half = [d for d in box if d.get("kind") == "shots"]
            check("partly sent: one comment landed, the next did not",
                  j.get("send") == "failed" and j.get("comments") == 1
                  and j.get("comments_total") == 2, str(j))
            check("partly sent: the draft records WHICH comment the customer has",
                  len(half) == 1 and (half[0].get("chunks") or [])[0].get("posted") is True
                  and not (half[0].get("chunks") or [])[1].get("posted"),
                  json.dumps(half)[:400])
            sent_first = len(env.helpdesk.comments)
            # The operator presses send again - even with a DIFFERENT decision.
            env.helpdesk.fail_after = None
            _code, j2 = _post(port, "/api/tickets/shots/approve",
                              {"work_id": "w1", "ticket": "DEMO#08597",
                               "pairs": [{"pair_id": "p2", "caption": "novi ekran"}],
                               "reject": ["p1"]})
            box = env.outbox()
            check("partly sent: NO second draft was written - the held one is resumed",
                  len([d for d in box if d.get("kind") == "shots"]) == 1
                  and j2.get("resumed") is True and j2.get("replaced") == 0,
                  str(j2) + " " + json.dumps(box)[:300])
            check("partly sent: it sent the REMAINDER, not the whole set again",
                  len(env.helpdesk.comments) == sent_first + 1
                  and j2.get("send") == "posted", str(j2))
            names = [n for c in env.helpdesk.sent_files for n in c]
            check("partly sent: no picture reached the customer twice",
                  len(names) == len(set(names)), json.dumps(names)[:300])
        finally:
            httpd.shutdown()
            httpd.server_close()


def _ticket_row(env):
    """The stored ticket WITHOUT the two things a send is allowed to move."""
    d = json.loads((env.store / "DEMO.json").read_text(encoding="utf-8"))
    t = dict(d["tickets"]["08597"])
    t.pop("outbox", None)
    return t


def test_the_send_comments_and_never_touches_the_tickets_status():
    """THE RULE THE WHOLE REDESIGN RESTS ON: "ova akcija, ovo dugme, komentarise.
    moze da bude i zatvoren i otvoren tiket, niti se menja status" (operator,
    2026-08-23).

    Delivery used to happen at `writeback --close --post-outbox`, so a comment
    could only reach the customer by closing their ticket. This asserts on the
    WHOLE stored ticket rather than on named keys, so it fails for `closed`,
    `closing_at`, `status`, `triage.state` and `helpdesk.is_closed` alike - and
    for whatever the next version of "close" writes."""
    with _Env() as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            before = _ticket_row(env)
            _code, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [{"pair_id": "p1", "caption": "prvi ekran"}],
                              "reject": ["p2"]})
            check("status: the comment was posted", j.get("send") == "posted", str(j))
            check("status: NOTHING about the ticket changed except its outbox",
                  _ticket_row(env) == before,
                  json.dumps({"before": before, "after": _ticket_row(env)})[:400])
            check("status: the helpdesk was never asked to close anything",
                  env.helpdesk.closed == [] and env.helpdesk.estimates == [],
                  json.dumps(env.helpdesk.closed)[:200])
            check("status: what it DID ask for is a comment",
                  len(env.helpdesk.comments) >= 1
                  and env.helpdesk.comments[0][0] == "08597",
                  json.dumps(env.helpdesk.comments)[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_a_closed_ticket_still_takes_the_comment():
    """"moze da bude i zatvoren i otvoren tiket" - the gallery is used before a
    sync and after one, and a closed ticket still accepts comments. Nothing in
    the send path reads the ticket's state, which is the only way that stays
    true."""
    with _Env() as env:
        env.ticket()
        d = json.loads((env.store / "DEMO.json").read_text(encoding="utf-8"))
        d["tickets"]["08597"].update({"status": "done", "helpdesk": {"is_closed": True},
                                      "closed": {"at": "2026-08-01T09:00:00",
                                                 "resolution": "zatvoreno ranije"}})
        (env.store / "DEMO.json").write_text(json.dumps(d), encoding="utf-8")
        env.work()
        httpd, port = _start_server()
        try:
            before = _ticket_row(env)
            _code, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [{"pair_id": "p1"}], "reject": ["p2"]})
            check("closed ticket: the comment goes out all the same",
                  j.get("send") == "posted" and len(env.helpdesk.comments) >= 1, str(j))
            check("closed ticket: and it stays closed, with the same resolution",
                  _ticket_row(env) == before, json.dumps(_ticket_row(env))[:300])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_an_ambiguous_send_is_never_reported_as_sent():
    """The request left and the answer never came. The comment may be live, so
    nothing is re-sent on its own and nothing may call it a success - the
    operator is the one who goes and looks. The run stays in the gallery with its
    pictures, because that is the state with no way back."""
    with _Env(FakeAdapter(fail_after=0, ambiguous=True)) as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            _code, j = _post(port, "/api/tickets/shots/approve",
                             {"work_id": "w1", "ticket": "DEMO#08597",
                              "pairs": [{"pair_id": "p1"}], "reject": ["p2"]})
            check("ambiguous: it is its own outcome, not a success and not a failure",
                  j.get("send") == "ambiguous" and j.get("send_error"), str(j))
            shots = [d for d in env.outbox() if d.get("kind") == "shots"]
            check("ambiguous: the draft keeps its claim, so nothing re-sends it",
                  len(shots) == 1 and shots[0].get("posting_at")
                  and not shots[0].get("posted"), json.dumps(shots)[:300])
            ok, why = server.shots_finished("w1")
            check("ambiguous: the run is NOT finished and its pictures stay on disk",
                  ok is False and (env.shots / "w1" / "manifest.json").is_file(), why)
            n = len(env.helpdesk.comments)
            _code, j2 = _post(port, "/api/tickets/shots/approve",
                              {"work_id": "w1", "ticket": "DEMO#08597",
                               "pairs": [{"pair_id": "p1"}], "reject": ["p2"]})
            check("ambiguous: a retry reports it and posts NOTHING",
                  j2.get("send") == "ambiguous" and len(env.helpdesk.comments) == n,
                  str(j2) + " " + str(len(env.helpdesk.comments)) + " vs " + str(n))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_the_listing_says_what_a_work_still_owes_the_customer():
    """A sent comment cannot be edited or withdrawn, so "press send again" is a
    SECOND comment. The panel needs the server's answer to refuse that, and the
    opposite answer to allow the retry after a failure."""
    with _Env(FakeAdapter(fail=["add_comment"])) as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [{"pair_id": "p1"}], "reject": ["p2"]})
            _code, lst = _getj(port, "/api/tickets/shots")
            check("owed: a failed send leaves the work owing one comment",
                  lst["works"][0].get("undelivered") == 1, json.dumps(lst)[:300])
            env.helpdesk.fail = set()
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [{"pair_id": "p1"}], "reject": ["p2"]})
            _code, lst = _getj(port, "/api/tickets/shots")
            check("owed: once it has landed the work owes nothing",
                  lst["works"] and lst["works"][0].get("undelivered") == 0,
                  json.dumps(lst)[:300])
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_the_panel_is_told_how_many_pictures_a_pair_would_attach():
    """The send confirmation names the pictures before an irreversible comment
    goes out, and that number comes from `_shots_att_plan` - the one definition
    of the attachment decision. Counted in JavaScript it would go wrong the day
    SHOTS_ATT_POLICY is flipped, and the dialog would promise what the customer
    does not get."""
    with _Env() as env:
        env.ticket()
        env.work()
        httpd, port = _start_server()
        try:
            _code, lst = _getj(port, "/api/tickets/shots")
            pairs = {p["pair_id"]: p for p in lst["works"][0]["pairs"]}
            check("att_n: two regions plus the two full pictures is six",
                  pairs["p1"].get("att_n") == 6, str(pairs["p1"].get("att_n")))
            check("att_n: a pair with no region is its two pictures",
                  pairs["p2"].get("att_n") == 2, str(pairs["p2"].get("att_n")))
            _post(port, "/api/tickets/shots/approve",
                  {"work_id": "w1", "ticket": "DEMO#08597",
                   "pairs": [{"pair_id": "p1"}, {"pair_id": "p2"}]})
            drafted = len(([d for d in env.outbox()
                            if d.get("kind") == "shots"][0]).get("attachments") or [])
            check("att_n: and it is exactly what the draft then attaches",
                  drafted == pairs["p1"]["att_n"] + pairs["p2"]["att_n"], str(drafted))
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_approve_refuses_what_it_cannot_place():
    with _Env() as env:
        env.ticket()
        env.work(work_id="w2", ticket=None)             # a work with no ticket
        env.work(work_id="w3", ticket="NOPE#1")         # a module that has no store file
        httpd, port = _start_server()
        try:
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "ghost", "pairs": [{"pair_id": "p1"}]})
            check("refuse: unknown work -> 404", code == 404, f"{code} {j}")
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w2", "pairs": [{"pair_id": "p1"}]})
            check("refuse: a work with no ticket -> 400", code == 400, f"{code} {j}")
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w3", "pairs": [{"pair_id": "p1"}]})
            check("refuse: a module with no ticket file -> 404", code == 404, f"{code} {j}")
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w1", "ticket": "DEMO#08597", "pairs": [{"pair_id": "p1"}]})
            check("refuse: an unknown work_id never creates one", code == 404, f"{code} {j}")
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_the_body_ticket_cannot_redirect_a_work_onto_another_ticket():
    with _Env() as env:
        env.ticket()
        (env.store / "OTHER.json").write_text(json.dumps({
            "project": {"name": "OTHER"},
            "tickets": {"99999": {"title": "tudji", "outbox": []}}, "rev": 1}), encoding="utf-8")
        env.work()
        httpd, port = _start_server()
        try:
            code, j = _post(port, "/api/tickets/shots/approve",
                            {"work_id": "w1", "ticket": "OTHER#99999",
                             "pairs": [{"pair_id": "p1", "caption": "x"}]})
            other = json.loads((env.store / "OTHER.json").read_text(encoding="utf-8"))
            check("redirect: the manifest's own ticket wins over the body's",
                  code == 200 and j.get("ticket") == "DEMO#08597", f"{code} {j}")
            check("redirect: the other customer's ticket was not written to",
                  other["tickets"]["99999"]["outbox"] == [], json.dumps(other)[:200])
            check("redirect: the work's real ticket got the draft",
                  len([d for d in env.outbox() if d.get("kind") == "shots"]) == 1,
                  json.dumps(env.outbox())[:200])
        finally:
            httpd.shutdown()
            httpd.server_close()


# --------------------------------------------------------------------------- #
#  writeback: the close routine mentions the pictures
# --------------------------------------------------------------------------- #
def test_writeback_counts_unposted_shots_pairs():
    sys.path.insert(0, str(HERE.parent / "scripts" / "tickets"))
    import writeback                                       # noqa: PLC0415
    with _Env() as env:
        env.ticket(outbox=[
            {"id": "a", "body": "obican nacrt", "posted": False},
            {"id": "b", "kind": "shots", "work_id": "w1", "pair_count": 2,
             "body": "Slike ekrana pre i posle izmene:", "posted": False},
            {"id": "c", "kind": "shots", "work_id": "w0", "pair_count": 5,
             "body": "vec poslato", "posted": True}])
        fp = env.store / "DEMO.json"
        check("writeback: counts only the UNPOSTED shots pairs",
              writeback.unposted_shots_pairs(fp, "08597") == 2,
              str(writeback.unposted_shots_pairs(fp, "08597")))
        check("writeback: unposted_drafts still returns (id, body) pairs",
              writeback.unposted_drafts(fp, "08597") == [
                  ("a", "obican nacrt"), ("b", "Slike ekrana pre i posle izmene:")],
              str(writeback.unposted_drafts(fp, "08597")))
    src = (HERE.parent / "scripts" / "tickets" / "writeback.py").read_text(encoding="utf-8")
    check("writeback: --close warns when the resolution never mentions the comment",
          'unposted_shots_pairs(fp, args.ticket_id)' in src
          and '"komentar" not in resolution.lower()' in src, "")
    check("writeback: it only REFUSES under --strict-shots",
          "--strict-shots" in src and "if args.strict_shots:" in src, "")


def _muzzled_env():
    """The subprocess environment with the helpdesk credentials REMOVED.

    A test that shells out inherits the operator's shell, and HELPDESK_URL /
    HELPDESK_TOKEN are set on this machine. Without this, the branch of the CLI
    that does NOT refuse walks straight on into `run()`, builds a real adapter
    and posts a real comment on a real customer ticket — which is exactly what
    happened once while this file was being written (DEMO#08597, comment id 599,
    2026-08-20). Stripping the two variables makes the adapter constructor raise
    HelpdeskError before any socket is opened, and the test ASSERTS that it got
    that far and no further."""
    env = dict(os.environ)
    for k in ("HELPDESK_URL", "HELPDESK_TOKEN"):
        env.pop(k, None)
    return env


def test_the_close_cli_refuses_only_under_strict_shots():
    """Driven through the real CLI with the helpdesk credentials stripped from
    the child's environment, so no branch of it can reach the network."""
    wb = HERE.parent / "scripts" / "tickets" / "writeback.py"
    penv = _muzzled_env()
    with _Env() as env:
        env.ticket(outbox=[{"id": "s1", "kind": "shots", "work_id": "w1", "pair_count": 2,
                            "body": "Slike ekrana pre i posle izmene:\n- dodat podnaslov (Lista)",
                            "attachments": [], "posted": False}])
        base = [sys.executable, str(wb), "--root", str(env.store), "--module", "DEMO",
                "--id", "08597", "--close", "--post-outbox"]
        r = subprocess.run(base + ["--strict-shots"], input="Dodat je podnaslov na listi.",
                           capture_output=True, text=True, timeout=90, env=penv)
        check("close CLI: --strict-shots refuses a resolution that ignores the pictures",
              r.returncode == 5, f"rc={r.returncode} {r.stderr[:200]}")
        check("close CLI: it suggests the exact sentence to add",
              "Slike pre i posle su u komentaru." in r.stderr, r.stderr[:300])
        check("close CLI: it says how many pairs are riding along",
              "2 before/after pair(s)" in r.stderr, r.stderr[:300])
        check("close CLI: the refusal happens BEFORE the adapter exists",
              "helpdesk not reachable" not in r.stderr, r.stderr[:200])
        r2 = subprocess.run(base + ["--strict-shots"],
                            input="Ispravljeno; slike su u komentaru ispod.",
                            capture_output=True, text=True, timeout=90, env=penv)
        check("close CLI: a resolution that mentions the comment is not refused",
              r2.returncode != 5 and "Slike pre i posle su u komentaru." not in r2.stderr,
              f"rc={r2.returncode} {r2.stderr[:200]}")
        check("close CLI: and it stops at the credential-less adapter, never on the wire",
              r2.returncode == 2 and "helpdesk not reachable" in r2.stderr,
              f"rc={r2.returncode} {r2.stderr[:200]}")
        check("close CLI: nothing was marked posted in the temp mirror",
              all(not d.get("posted") for d in env.outbox()), json.dumps(env.outbox())[:200])


# --------------------------------------------------------------------------- #
#  The client
# --------------------------------------------------------------------------- #
def test_the_gallery_controls_are_reachable_and_uniquely_named():
    """Three defects the operator hit in one screenshot.

    `.tksh-bar` named TWO unrelated things — the 5px capture-progress meter in a
    group header and the sticky bottom strip. The later rule won, so every
    progress meter became a strip glued to the bottom of the dialog: a clipped
    blue slab with no readable text, and the symptom appeared on the element
    that was NOT renamed.

    A collapsed group carried no controls at all, so deciding a ticket meant
    expanding it first. And there was no way to say "nothing from this ticket
    goes to the customer" in one action — only rejecting each pair by hand,
    which is a different statement from an untouched ticket.
    """
    js = (HERE / "web" / "tickets.js").read_text(encoding="utf-8")
    css = (HERE / "web" / "app.css").read_text(encoding="utf-8")
    check("css: .tksh-bar is the progress meter and nothing else — never sticky",
          "position:sticky" not in
          "".join(l for l in css.splitlines() if ".tksh-bar{" in l), "")
    check("css: the sticky strip has its own name",
          ".tksh-foot{position:sticky" in css, "")
    check("js: the strip markup uses that name too",
          '<div class="tksh-foot">' in js and '<div class="tksh-bar">' not in js, "")
    # Anchored to the GROUP's own summary, not to the first "</summary>" in the
    # file — tickets.js has several <details> and the earlier assertion compared
    # against one of the others.
    _head = js.split('<summary title="\'+esc(g.label)+\'')[-1].split("</summary>")[0]
    check("js: both ticket actions sit on the HEADER, so a collapsed group has them",
          'class="tksh-hact"' in _head
          and "tksh-go" in _head and "tksh-nogo" in _head, _head[-160:])
    check("js: 'nothing to the comment' exists and posts, so the refusal persists",
          "tksh-nogo" in js and "tkShSend(key);" in js, "")
    check("js: it asks BEFORE it marks anything — a cancel changes nothing",
          js.index("window.confirm(\"Nijedna slika")
          < js.index("tkShSel[tkShKey(w.work_id,p.pair_id)]=false;});});"), "")
    check("js: a header button does not also toggle its <details>",
          "ev.preventDefault();ev.stopPropagation();" in js
          # BOTH ticket actions, and the count is the check: the send handler grew a
          # confirmation between noToggle and tkShSend, so an assertion on the two
          # calls being adjacent would pass only for whichever one had not changed.
          and js.count("b.onclick=function(ev){noToggle(ev);") >= 2, "")


def test_the_panel_explains_a_stuck_run_instead_of_just_keeping_it():
    """What the operator saw: every pair rejected, the counts saying so, and the
    card still there. The only sentence the panel had for that path was "nema
    izmena od poslednjeg slanja — komentar je već otišao", which is false when
    nothing was ever sent, and it hid the real reason."""
    js = (HERE / "web" / "tickets.js").read_text(encoding="utf-8")
    css = (HERE / "web" / "app.css").read_text(encoding="utf-8")
    check("js: the strip is rendered ONLY for a work the server marked blocked",
          "w.stay&&w.stay.blocked" in js and "tkShStuckHtml(g)" in js, "")
    check("js: it prints the server's reason rather than inventing one",
          "(w.stay&&w.stay.reason)" in js, "")
    check("js: the way out exists and posts to the discard route",
          'class="btn tksh-drop"' in js and '"/api/tickets/shots/discard"' in js, "")
    check("js: and it confirms first, naming what is lost",
          js.index('window.confirm("Ukloniti ') < js.index('"/api/tickets/shots/discard"')
          and "Slike se brišu sa diska" in js, "")
    check("js: an all-rejected group is no longer told a comment went out",
          "else if(okN)" in js
          and 'note.set("err","nema izmena od poslednjeg slanja' in js
          and "sve je odbačeno — nijedan komentar nije ni poslat" in js, "")
    check("css: the strip has its own class and is not styled as an error",
          "#tkanmodal .tksh-stuck{" in css and "#tkanmodal .tksh-drop{" in css
          and "var(--warn)" in "".join(l for l in css.splitlines()
                                       if ".tksh-stuck{" in l), "")


def test_the_send_button_confirms_and_reports_what_the_helpdesk_said():
    """The button is not a draft button any more: one click puts a comment on a
    real customer's ticket and there is no unsend. So it CONFIRMS first, naming
    the ticket, the pairs and the pictures - and afterwards it reports what the
    helpdesk actually said, in three distinct outcomes."""
    js = (HERE / "web" / "tickets.js").read_text(encoding="utf-8")
    css = (HERE / "web" / "app.css").read_text(encoding="utf-8")
    check("js: the send confirms before anything leaves, and names the ticket",
          'window.confirm("Komentar ide na tiket "+grp.label' in js
          and "ODMAH" in js, "")
    check("js: the dialog counts the pictures from the SERVER's att_n, never its own",
          "p.att_n" in js and "regions.length*2" not in js, "")
    check("js: the dialog says the status is not touched - the operator's own question",
          "Status tiketa se ne menja." in js, "")
    check("js: a send with nothing approved asks nothing - the refusal has its own dialog",
          "if(n&&!window.confirm(" in js, "")
    # "pri zatvaranju tiketa" itself is not the tell - an unrelated Gemini tooltip
    # says it, and so does the comment explaining this very defect. The tell is the
    # DRAFT vocabulary: the gallery no longer produces anything the operator could
    # go and edit later, so a sentence pointing at "the draft" is now a false promise.
    check("js: the old lie is gone - nothing the gallery writes waits to be edited",
          "u nacrtu komentara" not in js, "")
    check("js: three outcomes, read off the server's answer",
          'd.send==="posted"' in js and 'd.send==="ambiguous"' in js
          and 'd.send==="failed"' in js, "")
    check("js: an AMBIGUOUS send is painted as neither sent nor failed",
          'cls="warn"' in js and "#tkanmodal .tkan-note.warn{" in css, "")
    check("js: and it tells the operator to go and look, because nothing retries itself",
          "ništa se ne šalje ponovo" in js, "")
    check("js: the answer SURVIVES the repaint the send itself triggers",
          "var tkShMsg={}" in js and "function tkShNotesPaint()" in js
          and "tkShNotesPaint();\n    tkShSyncTop();}" in js, "")
    check("js: the note keeps its tksh-gnote marker, or the next write cannot find it",
          js.count('"tkan-note tksh-gnote"+(') >= 1
          and "n.className=\"tkan-note tksh-gnote\"" in js, "")
    # Owing nothing = decisions match the server, no pending draft, AND a draft
    # has been written at some point. `pairs.length` must never be a trigger on
    # its own: that is what would put a SECOND copy of a delivered comment on
    # the customer's ticket. It only counts together with `!w.ever_sent`.
    check("js: a work that owes the customer nothing is NOT sent again",
          "if(changed||(w.undelivered||0)||(pairs.length&&!w.ever_sent))" in js
          and "if(changed||pairs.length)jobs.push" not in js, "")


def test_tickets_js_parses():
    js = (HERE / "web" / "tickets.js").read_text(encoding="utf-8")
    check("js: the gallery button is injected next to Procene",
          'b.id="tk-shots"' in js and 'rb.parentNode.insertBefore(b,rb)' in js, "")
    check("js: the ticket modal shows the shots line under the poslato strip",
          "tkShotsKv(t)+" in js and "u komentaru" in js, "")
    check("js: images are loaded from the route, never as a data: URL",
          "data:image" not in js, "")
    check("js: the ticket modal's shots row opens the gallery for THAT ticket",
          'tkShOpen({ticket:f.dir+"#"+String(t.id)})' in js, "")
    check("js: the gallery passes the ticket through to the route",
          '"?ticket="+encodeURIComponent(tkShFilter)' in js, "")
    check("js: the toolbar button still browses everything (no filter)",
          'b.onclick=function(){tkShOpen();}' in js, "")
    check("js: each pair carries the three-way choice, and it goes on the wire",
          '["both","slike + opis"' in js and '["images","samo slike"' in js
          and '["comment","samo opis"' in js and "mode:md" in js, "")
    check("js: the choice control reuses the HUD pill, not a new framework",
          '"tk-pill tksh-md"'.strip('"') in js and "bootstrap" not in js.lower(), "")
    check("js: a pixel percentage is never rendered as if it were the size of the change",
          "p.pixel_ratio" not in js and "pixel_ratio}" not in js, "")
    # ---- ask 1: one collapsible group per ticket ------------------------------------ #
    check("js: the gallery is one <details> group per ticket, not a flat run of pairs",
          '<details class="tksh-grp"' in js and "function tkShGroups()" in js, "")
    check("js: grouping uses the SERVER's ticket_key — no second normaliser in JS",
          "w.ticket_key" in js and "toUpperCase()" not in js.split("tkShGroups")[-1][:600], "")
    check("js: the open/closed choice survives a repaint",
          "tkShOpenG[el.getAttribute(\"data-g\")]=el.open" in js
          and "(g.key in tkShOpenG)" in js, "")
    # ---- ask 2.1: whose pictures are these ------------------------------------------ #
    check("js: the ticket is on the group header AND on every pair card, from one label",
          js.count('class="tksh-tkt"') >= 2 and "tkShPairHtml(w,p,g.label," in js, "")
    # ---- the collapsed-pair defect ---------------------------------------------------- #
    tally = js.split("function tkShTally")[1].split("function tkShCountHtml")[0]
    check("js: the counts count what is ON SCREEN - the collapsed records are not cards",
          "function tkShIsShown" in js and "function tkShPairsOf" in js
          and "tkShPairsOf(w).forEach" in tally and "w.pairs" not in tally, tally[:160])
    check("js: a server that does not send the verdict means everything is shown",
          "p.shown===false" in js.replace(" ", "") or "shown===false" in js.replace(" ", ""), "")
    check("js: an ALREADY APPROVED record stays reviewable however the engine collapsed it",
          "p.approved===true" in js.replace(" ", ""), "")
    check("js: a collapsed record hangs under the pair that is its picture, by page_id",
          "p.represented_by" in js and "s.page_id===rep" in js.replace(" ", ""), "")
    check("js: whatever has no representative among the shown pairs still gets a home",
          "orphans" in js and "else orphans.push(p);" in js, "")
    check("js: the collapsed disclosure states NO number - the caption already carries it",
          "ekrani sa istim izgledom" in js
          and "ks.length+" not in js and "ks.length+' " not in js, "")
    check("js: a collapsed record is read-only - no caption box, no odobri/odbaci",
          "tksh-ro" in js and 'ro?"":' in js and "tksh-roflag" in js
          and "ne ide u komentar" in js, "")
    check("js: a work with nothing left to review says so instead of going blank",
          "Nema šta da se pregleda" in js, "")
    # ---- ask: back to the top of THIS ticket ------------------------------------------ #
    check("js: the jump-back control names the ticket it returns to",
          'id="tksh-top"' in js and 'id="tksh-toptkt"' in js
          and "function tkShSyncTop" in js, "")
    check("js: it targets the GROUP under the top edge, not the top of the panel",
          "tkShTopTarget" in js and "querySelectorAll(\".tksh-grp\")" in js
          and "scrollTop+=" in js.replace(" ", ""), "")
    check("js: it hides itself when there is nowhere to go, via .on and not [hidden]",
          'btn.classList.toggle("on"' in js and 'id="tksh-top" hidden' not in js, "")
    check("js: the scroll hook is assigned, never stacked - it is rewired on every render",
          "host.onscroll=tkShSyncTop" in js
          and 'addEventListener("scroll"' not in js, "")
    # ---- ask 2.2: progress ----------------------------------------------------------- #
    check("js: progress is rendered from the server's progress block, never faked",
          "function tkShProgHtml" in js and "w.progress" in js, "")
    check("js: an unknown plan prints a count and NO bar",
          'tot?\'<span class="tksh-bar"' in js and 'plan još nije poznat' in js, "")
    check("js: the poll stops the moment the shared modal is not ours any more",
          "function tkShAlive" in js and 'getElementById("tksh-root")' in js
          and "clearInterval(tkShTimer)" in js, "")
    check("js: a poll never re-renders under a caption the operator is typing",
          "/^(INPUT|TEXTAREA)$/.test(ae.tagName" in js, "")
    # ---- ask 2.3: approved / rejected / UNTOUCHED ------------------------------------ #
    check("js: review state is three-valued, and 'never reviewed' is one of the three",
          "function tkShState" in js and 'v===true?"ok":(v===false?"no":"un")' in js
          and "nije pregledano" in js, "")
    check("js: the counts are on every group header and on the sticky strip",
          "function tkShCountHtml" in js and "nepregledano" in js
          and "function tkShPaintCounts" in js, "")
    check("js: the three states ROUND-TRIP — the server's decision is what the "
          "panel starts from, with `approved` as the older shape's fallback",
          'p.decision==="rejected"' in js.replace(" ", "")
          and 'p.decision==="approved"||p.approved' in js.replace(" ", ""), "")
    check("js: all three go on the wire — rejected is its own list, never merged "
          "into drop (which is what lost every rejection)",
          "reject:reject" in js.replace(" ", "") and "reject.push(p.pair_id)" in js
          and "else drop.push(p.pair_id);" in js, "")
    # ---- per-ticket send ------------------------------------------------------------- #
    check("js: EACH TICKET sends itself, from the bottom of its own group",
          'class="tksh-gsend"' in js and "function tkShSend(gkey)" in js
          and 'id="tksh-go-' in js, "")
    check("js: there is no global send button and no global send bar left",
          'id="tksh-go"' not in js and "tksh-send" not in js
          and 'class="tksh-bar"' in js, "")
    check("js: the send button posts THAT group's works and nobody else's",
          "g.works.forEach(function(w){" in js and "tkShWorks.forEach(function(w){\n      var pairs" not in js, "")
    # ---- a finished run leaves the gallery -------------------------------------------- #
    check("js: opening the gallery asks the server to retire what is finished",
          '"/api/tickets/shots/finalize"' in js and "function tkShLoad" in js, "")
    check("js: and a run that leaves is SAID, never just gone",
          "d.finalized" in js and "baseline" in js, "")
    # ---- a screen that did not exist before ------------------------------------------- #
    check("js: a new page explains its missing PRE half instead of looking broken",
          "ova stranica nije postojala pre izmene" in js and "p.new_page===true" in js, "")
    check("js: the card previews the SERVER's text — the panel never words the "
          "customer's sentence itself",
          "nav.text" in js and "Nova stranica:" not in js, "")
    check("js: a path the engine could not derive is flagged for the operator, "
          "never filled in by the panel",
          "tksh-fill" in js and "nav.steps&&nav.steps.length" in js.replace(" ", ""), "")
    check("js: a mode pill is lit only on an APPROVED pair, never on an untouched one",
          '(st==="ok"&&md===m[0])' in js and 'st==="ok"&&el.getAttribute("data-m")' in js, "")
    # ---- N zoomed comparisons per pair ---------------------------------------------- #
    check("js: every region is rendered, in the order given",
          "function tkShRegionsHtml" in js and "p.regions" in js
          and "regs.map(function(r,i)" in js and ".sort(" not in js.split("tkShRegionsHtml")[-1][:800], "")
    check("js: a region is labelled by POSITION and by nothing else",
          'pos=String(r&&r.n?r.n:(i+1))+"/"+n' in js
          and "crop_kind" not in js and "crop_from" not in js, "")
    check("js: a truncated region list says so",
          "prikazano " in js and "izmenjenih mesta" in js and "found>n" in js, "")
    # The whole screen is never hidden by default. It used to fold away as soon
    # as a pair had regions, and since every shown pair has them, the operator's
    # before/after view vanished exactly when the zoom started working:
    # "problem je sto nema celog screenshota pre i posle".
    check("js: the whole PRE/POSLE screen is always visible, and comes before the zooms",
          '<details class="tksh-full" open>' in js
          and '<details class="tksh-full">' not in js
          # compare positions INSIDE the card markup: the bare name also matches
          # the function definition, which sits far earlier in the file
          and (lambda card: card.index("img.before_full") < card.index("tkShRegionsHtml(p,shot)+"))(
              js[js.index('<div class="tkan-h"><span class="tksh-tkt">'):]), "")
    check("js: the pair-level crop keys are gone (regions replaced them)",
          "img.before_crop" not in js and "img.after_crop" not in js, "")
    # ---- the pair shape shrank under us --------------------------------------------- #
    check("js: nothing from the change detection is read any more",
          "p.located" not in js and "p.box" not in js and "p.anchor" not in js
          and "bez okvira" not in js, "")
    check("js: a caption typed into a COLLAPSED group is still sent (held in JS, not read "
          "back out of the DOM)",
          "tkShCapV[tkShKey(w,p)]" in js and 'getElementById("tksh-cap-"' not in js, "")
    node = shutil.which("node")
    # A decision reaches disk when it is MADE. It was browser-local until the
    # send button, so deciding a batch and leaving threw all of it away.
    check("js: every decision click saves to the server",
          js.count("tkShSave(") >= 3 and '"/api/tickets/shots/decide"' in js, "")
    # aiPost RESOLVES on an HTTP error instead of rejecting, so a handler using
    # only .catch() counts a 404 as a save — which then made the send button
    # claim the comment had already gone.
    check("js: a failed save is read from the resolved body, not only .catch",
          (lambda s: "d&&d.error" in s and "NIJE sa" in s)(
              js[js.index('aiPost("/api/tickets/shots/decide"'):][:1400]), "")
    # With decisions persisted, `changed` is false right after a save and
    # `undelivered` is 0 when no draft was ever written — approved-and-never-
    # sent therefore needs its own reason or the send is refused outright.
    check("js: approved-but-never-sent still offers to send",
          "pairs.length&&!w.ever_sent" in js, "")
    # "Already sent" is a claim about the CUSTOMER'S ticket; only made when a
    # draft for the group actually exists.
    check("js: 'komentar je vec otisao' is gated on ever_sent",
          (lambda s: "w.ever_sent" in s and "nikada poslat" in s)(
              js[max(0, js.index("nema izmena od poslednjeg slanja") - 700):][:1600]), "")

    if not node:
        check("js: parse check (node not installed - skipped)", True, "")
        return
    out = subprocess.run(
        [node, "-e", "const fs=require('fs');new Function(fs.readFileSync(process.argv[1],'utf8'));"
                     "console.log('PARSED')", str(HERE / "web" / "tickets.js")],
        capture_output=True, text=True, timeout=60)
    check("js: tickets.js parses as a function body",
          out.returncode == 0 and "PARSED" in out.stdout,
          (out.stderr or out.stdout)[:300])


def main():
    for fn in (test_list_route_shape,
               test_a_work_with_no_baseline_says_so_instead_of_looking_unchanged,
               test_the_gallery_is_one_ticket_and_never_a_ticketless_work,
               test_every_changed_region_is_its_own_comparison,
               test_a_collapsed_pair_reaches_the_gallery_marked_and_still_serves_its_pictures,
               test_a_manifest_that_predates_the_collapse_is_all_shown,
               test_a_region_index_can_address_nothing_but_a_region,
               test_an_attachment_name_is_the_customers_label_not_an_identifier,
               test_the_attachment_policy_is_one_line,
               test_the_panel_can_say_what_the_capture_is_doing,
               test_a_work_with_nothing_captured_yet_reports_no_progress_at_all,
               test_a_pair_goes_in_as_images_only_comment_only_or_both,
               test_a_work_whose_ticket_was_typed_without_a_hash_can_still_be_approved,
               test_list_route_is_empty_not_broken_without_a_shots_root,
               test_img_route_serves_the_png,
               test_img_route_refuses_everything_that_is_not_in_the_manifest,
               test_a_non_loopback_peer_is_refused_on_all_three_routes,
               test_approve_writes_exactly_one_draft_with_attachments,
               test_a_rejection_is_a_state_on_disk_and_not_a_thing_the_browser_remembers,
               test_a_manifest_that_predates_the_third_state_needs_no_migration,
               test_a_new_screen_tells_the_customer_what_it_is_and_how_to_reach_it,
               test_a_run_is_promoted_and_removed_only_when_it_is_finished_and_delivered,
               test_the_finalize_route_takes_no_arguments_and_still_reads_its_body,
               test_a_post_to_an_unknown_route_does_not_poison_the_connection,
               test_a_second_approve_replaces_the_draft_instead_of_duplicating_it,
               test_approving_zero_pairs_removes_the_draft,
               test_an_already_posted_shots_draft_is_never_rewritten,
               test_a_partly_delivered_shots_draft_is_resumed_and_never_doubled,
               test_a_processed_run_leaves_however_the_operator_decided_it,
               test_a_decided_run_with_real_shots_actually_leaves_the_disk,
               test_a_decided_run_that_cannot_promote_says_why_and_can_be_discarded,
               test_discard_refuses_the_two_states_where_it_would_destroy_something,
               test_the_automatic_sweep_never_forces_and_the_operator_always_can,
               test_the_send_comments_and_never_touches_the_tickets_status,
               test_a_closed_ticket_still_takes_the_comment,
               test_an_ambiguous_send_is_never_reported_as_sent,
               test_the_listing_says_what_a_work_still_owes_the_customer,
               test_the_panel_is_told_how_many_pictures_a_pair_would_attach,
               test_approve_refuses_what_it_cannot_place,
               test_the_body_ticket_cannot_redirect_a_work_onto_another_ticket,
               test_writeback_counts_unposted_shots_pairs,
               test_the_close_cli_refuses_only_under_strict_shots,
               test_the_gallery_controls_are_reachable_and_uniquely_named,
               test_the_panel_explains_a_stuck_run_instead_of_just_keeping_it,
               test_the_send_button_confirms_and_reports_what_the_helpdesk_said,
               test_tickets_js_parses):
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
