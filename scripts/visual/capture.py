#!/usr/bin/env python3
"""Playwright capture: an authenticated browser, one page, and every STATE of it.

The operator's decision that shapes this file: **the first view is not enough.**
"Stranica moze da izgleda isto a da joj se modal promenio." So after the base
shot, the triggers on the page (`data-bs-toggle="modal|dropdown|tab|offcanvas|
collapse"`, `details > summary`, `.modal-trigger`) are found and each is clicked
IN ISOLATION - a fresh BROWSER CONTEXT per state, so state 3 never sees what
state 2 opened.

Two guarantees:

* **Deterministic.** Fixed 1440x900 viewport, reduced motion, animations and
  transitions killed by an injected stylesheet, caret hidden. Two runs of an
  unchanged page produce the same signature - which is why a state gets its own
  CONTEXT and not just its own tab: a new tab on a shared context inherits
  `localStorage`, and a page that renders a dismissed hint on the second visit
  then hashes differently every time it is captured.
* **Read-only, enforced.** Every non-GET/HEAD request is ABORTED by a route
  guard. The dev database is the production one (`run-acme-audit`), so "we
  only take screenshots" cannot be left to discipline - a trigger that turns out
  to submit a form is blocked by the browser, not by good intentions.

`ignore_selectors` are excluded from the SIGNATURE and the GEOMETRY (dynamic
text must not drive a sweep) but never from the PNG - the customer sees the real
page, timestamps and all. The one thing the PNG does NOT show is a DEVELOPER
overlay (`DEV_OVERLAY_SELECTORS`): a debug toolbar is not part of the app, and
it covered the right sixth of every screenshot the customer was sent.

**One page, one picture per LOOK.** A state that opened but produced the same
picture as a state already kept for this page is collapsed (`compare.same_picture`)
and reported in `skipped` with the state it duplicates - a navbar dropdown over
an unchanged page was yielding four near-identical pairs per screen.
"""
from __future__ import annotations

import hashlib
import inspect
import re
import sys
from contextlib import contextmanager
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import compare as vcompare
from . import isolate as visolate
from . import locate as vlocate

VIEWPORT_DEFAULT = {"width": 1440, "height": 900}
NAV_TIMEOUT_MS = 25000
SETTLE_MS = 350
STATE_WAIT_MS = 1500
MAX_TREE_NODES = 6000
MAX_GEOMETRY_BLOCKS = 150

#: `states.max_per_page` caps the PICTURES of a page. This caps the page LOADS
#: spent looking for them, as a multiple of it.
#:
#: The two stopped being the same thing when deduplication arrived, and it cost
#: a real state immediately: on `settings_admin:role_create` two navbar
#: dropdowns opened, collapsed into the base shot, and the budget they had spent
#: was gone - so the run never reached the form's "lokacije" tab, which is one
#: of the four pictures the whole ticket was about. A collapsed state must not
#: take the slot of a state worth keeping; it must still cost something, or a
#: navbar with twenty quiet triggers would load the page twenty times.
STATE_ATTEMPT_FACTOR = 2

TRIGGER_SELECTORS = [
    '[data-bs-toggle="modal"]',
    '[data-bs-toggle="dropdown"]',
    '[data-bs-toggle="tab"]',
    '[data-bs-toggle="offcanvas"]',
    '[data-bs-toggle="collapse"]',
    'details > summary',
    '.modal-trigger',
]

#: THE SITE'S CHROME vs THIS SCREEN. A trigger inside the chrome opens the same
#: menu on every page of the app, so photographing it is a picture of the
#: application, not of the change - `dashboard:location_quality` was shipping six
#: pictures of one screen with a different navbar menu open in each.
#:
#: These are the HTML landmarks, not one project's class names: `nav`, `header`,
#: `aside` and `footer` (and their ARIA roles) are the site's furniture,
#: `main` / `[role=main]` is the screen. A trigger under NEITHER is treated as
#: the screen's own: the rule may only ever remove a state it can positively
#: prove is chrome, so a layout with no landmarks behaves exactly as before.
CHROME_LANDMARKS = ['nav', 'header', 'aside', 'footer', '[role="navigation"]',
                    '[role="banner"]', '[role="complementary"]',
                    '[role="contentinfo"]']
CONTENT_LANDMARKS = ['main', '[role="main"]']

OPEN_SELECTORS = [
    ".modal.show", ".dropdown-menu.show", ".offcanvas.show", ".collapse.show",
    "details[open]", '[aria-expanded="true"]', ".tab-pane.active.show",
]

GEOMETRY_SELECTORS = [
    "main", "header", "nav", "footer", "aside", "section", "form", "table",
    ".container", ".container-fluid", ".row", ".card", ".navbar", ".modal.show",
    ".offcanvas.show", ".dropdown-menu.show",
]

ANIM_CSS = """
*, *::before, *::after {
  animation-duration: 0s !important; animation-delay: 0s !important;
  transition-duration: 0s !important; transition-delay: 0s !important;
  caret-color: transparent !important; scroll-behavior: auto !important;
}
"""

#: DEVELOPER overlays: not part of the application, and in the picture anyway.
#: The Django debug toolbar covered the right ~15% of every capture of
#: acme-audit, on every page, on both sides of every pair.
#:
#: The APP's own navigation stays - the operator asked for the debug panel only,
#: and a screenshot without the navbar is not a screenshot of the screen.
#:
#: `#djDebugRoot` is the one that matters on django-debug-toolbar 4.x: it renders
#: into a SHADOW ROOT, and no page stylesheet can reach inside one - hiding the
#: HOST is the only thing that works. `#djDebug` and the rest are the pre-4.0
#: layout, where the panel sits in the page itself.
#:
#: A project without any of them loses nothing: a rule that matches no element
#: is a no-op, which is what makes this safe to inject everywhere.
DEV_OVERLAY_SELECTORS = [
    "#djDebugRoot",                     # django-debug-toolbar >= 4.0 (shadow DOM host)
    "#djDebug",                         # django-debug-toolbar < 4.0
    "#djDebugToolbar",
    "#djDebugToolbarHandle",
    "#djDebugWindow",
]

HIDE_CSS = ", ".join(DEV_OVERLAY_SELECTORS) + """ {
  display: none !important;
}
"""

_TRANSLIT = str.maketrans({"c": "c", "č": "c", "ć": "c", "š": "s",
                           "ž": "z", "đ": "d", "Č": "c",
                           "Ć": "c", "Š": "s", "Ž": "z",
                           "Đ": "d"})


class VisualError(RuntimeError):
    """A failure that STOPS the run and must reach the operator as a question.

    Config problems are a return value (`config.load_config`); a mid-run failure
    is this. `shoot.py` turns both into the same one-line JSON on stdout.
    """

    def __init__(self, code, detail="", questions=None):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = str(detail)
        self.questions = list(questions or [])


def slug(text, fallback="state") -> str:
    """Filename-safe ASCII. State names become file names, and a Windows console
    cannot print `c/c/s/z/d` - so they are transliterated, not dropped."""
    s = str(text or "").strip().lower().translate(_TRANSLIT)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return (s[:40] or fallback)


# --------------------------------------------------------------------------- #
#  Server + auth
# --------------------------------------------------------------------------- #
def check_server(cfg) -> None:
    """Raise VisualError('server_down') when the app does not answer. Never
    starts the server: which command, in which shell, with which settings is the
    operator's call - we only say what the config says it should be."""
    url = cfg.get("server", {}).get("health") or cfg["base_url"]
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            status = getattr(r, "status", 200)
    except urllib.error.HTTPError as exc:
        status = exc.code                                  # 302/403 still means "alive"
    except Exception as exc:                               # noqa: BLE001
        start = " ".join(cfg.get("server", {}).get("start") or []) or "(nije zadato)"
        raise VisualError("server_down", "%s: %s" % (url, exc), [{
            "key": "server",
            "question": "Dev server ne odgovara na %s. Da ga pokrenem, ili je vec "
                        "pokrenut na drugom portu?" % url,
            "example": start}])
    if status >= 500:
        raise VisualError("server_down", "%s -> HTTP %s" % (url, status), [{
            "key": "server", "question": "Server vraca %s na health adresi." % status,
            "example": url}])


def _load_driver(repo_root, rel_module, func_name):
    """The repo's auth function, loaded BY PATH under a namespaced alias.

    Importing it runs the app's own code in THIS process (a Django driver calls
    `django.setup()` at the top of `mint_session`), which is why nothing here may
    leave a bare module name or a brain directory in the import namespace -
    `isolate.load_module` is the one door, see `visual/__init__.py`. A driver
    that blows up while being imported is the operator's question too, not a
    traceback out of the top of the run.
    """
    path = Path(repo_root) / rel_module
    try:
        mod = visolate.load_module("visual_auth_driver", path)
    except Exception as exc:                                # noqa: BLE001
        raise VisualError("auth_failed", "%s: %s: %s"
                          % (path, type(exc).__name__, str(exc)[:200]), [{
                              "key": "auth",
                              "question": "Drajver za prijavu ne moze da se ucita (%s). "
                                          "Da li je putanja u configu tacna?"
                                          % type(exc).__name__,
                              "example": str(path)}])
    fn = getattr(mod, func_name, None)
    if not callable(fn):
        raise VisualError("auth_failed", "%s has no %s()" % (path, func_name))
    return fn


def _auth_cookie(cfg, repo_root):
    """The session cookie, or None when the app needs no login.

    `auth.kind` is a REGISTRY KEY validated by config.py - never a class path
    resolved out of the file. The module path it names is the repo's own driver,
    imported by path exactly like the repo's skill documents it.
    """
    auth = cfg.get("auth") or {}
    kind = auth.get("kind")
    if kind == "none":
        return None
    if kind != "driver":                                    # config.py rejects the rest
        raise VisualError("auth_failed", "unknown auth kind %r" % kind)

    fn = _load_driver(repo_root, auth["module"], auth["func"])
    want = {"slug": cfg.get("tenant"), "tenant": cfg.get("tenant"),
            "role": cfg.get("role") or None}
    want.update(auth.get("args") or {})
    try:
        params = set(inspect.signature(fn).parameters)
        kwargs = {k: v for k, v in want.items() if k in params}
    except (TypeError, ValueError):
        kwargs = {"slug": cfg.get("tenant"), "role": cfg.get("role") or None}
    try:
        got = fn(**kwargs)
    except Exception as exc:                                # noqa: BLE001
        raise VisualError("auth_failed", "%s(%s): %s: %s"
                          % (auth["func"], ",".join(sorted(kwargs)),
                             type(exc).__name__, exc),
                          [{"key": "auth",
                            "question": "Prijava nije uspela (%s). Koji korisnik/rola "
                                        "da se koristi za snimke?" % type(exc).__name__,
                            "example": "tenant=%s role=%s" % (cfg.get("tenant"),
                                                              cfg.get("role"))}])
    value = got[0] if isinstance(got, (tuple, list)) and got else got
    value = str(value or "")
    if not value:
        raise VisualError("auth_failed", "%s returned no session" % auth["func"])
    ck = auth.get("cookie") or {}
    domain = ck.get("domain") or urlparse(cfg["base_url"]).hostname or ""
    return {"name": ck.get("name") or "sessionid", "value": value,
            "domain": domain, "path": ck.get("path") or "/"}


def ensure_subprocess_event_loop_policy() -> None:
    """Make sure asyncio can still spawn a SUBPROCESS on Windows.

    Playwright's sync API runs its driver as a subprocess, which the Selector
    event loop cannot do: `NotImplementedError` out of
    `asyncio.base_events._make_subprocess_transport`, with no hint of where it
    came from. `django.setup()` - which every `auth.kind: "driver"` repo calls
    while minting the session - installs that Selector policy through channels.

    So this is asserted HERE, after all of the app's imports and immediately
    before `sync_playwright().start()`. Setting it earlier and then
    authenticating undoes it, which is exactly the bug this replaced; the repo's
    own driver documents the same ordering. No-op off Windows, and a no-op again
    once the policy API is removed (3.16), where the default already spawns
    subprocesses.
    """
    if sys.platform != "win32":
        return
    import asyncio
    import warnings
    proactor = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if proactor is None:
        return
    with warnings.catch_warnings():         # the policy API is deprecated, not gone
        warnings.simplefilter("ignore", DeprecationWarning)
        if not isinstance(asyncio.get_event_loop_policy(), proactor):
            asyncio.set_event_loop_policy(proactor())


# --------------------------------------------------------------------------- #
#  Session
# --------------------------------------------------------------------------- #
class Session:
    """An open browser with the app's session cookie. One per run - launching
    chromium per page would dominate the 20-40s budget."""

    def __init__(self, cfg, repo_root):
        self.cfg = cfg
        self.repo_root = str(repo_root)
        self.blocked = []                                   # non-GET attempts, refused
        self._pw = self._browser = self.context = None
        self._cookie = None

    def open(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise VisualError("playwright_missing", str(exc), [{
                "key": "playwright",
                "question": "Playwright nije instaliran u ovom Python-u. Instalirati?",
                "example": "pip install playwright && python -m playwright install chromium"}])

        self._cookie = _auth_cookie(self.cfg, self.repo_root)
        # AFTER auth, immediately before the driver subprocess: authenticating
        # runs the app's code, and `django.setup()` installs the Selector policy
        # over whatever we had set. Order is the entire fix - see the function.
        ensure_subprocess_event_loop_policy()
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch()
        except Exception as exc:                            # noqa: BLE001
            self.close()
            raise VisualError("playwright_missing", str(exc), [{
                "key": "chromium",
                "question": "Chromium za Playwright nedostaje. Instalirati?",
                "example": "python -m playwright install chromium"}])
        self.context = self.new_context()
        return self

    def new_context(self):
        """A context with the app's cookie, the read-only route guard and nothing
        else - no storage, no service worker, no leftovers from the state
        captured a second ago."""
        vp = self.cfg.get("viewport") or VIEWPORT_DEFAULT
        ctx = self._browser.new_context(
            viewport={"width": int(vp["width"]), "height": int(vp["height"])},
            device_scale_factor=1, reduced_motion="reduce")
        ctx.set_default_timeout(NAV_TIMEOUT_MS)
        ctx.route("**/*", self._guard)
        if self._cookie:
            ctx.add_cookies([self._cookie])
        return ctx

    @contextmanager
    def fresh_page(self):
        """One page in its OWN context, closed with it. Capturing every state
        through this is what keeps two captures of an unchanged page identical."""
        ctx = self.new_context()
        page = None
        try:
            page = ctx.new_page()
            yield page
        finally:
            for obj in (page, ctx):
                try:
                    if obj is not None:
                        obj.close()
                except Exception:                           # noqa: BLE001
                    pass

    def _guard(self, route, request):
        """No screenshot is worth a write. The dev database is production."""
        try:
            if request.method not in ("GET", "HEAD"):
                # Path only: a query string can carry a token or a csrf value and
                # this record ends up in a manifest the HUD renders.
                self.blocked.append({"method": request.method,
                                     "url": request.url.split("?")[0]})
                route.abort()
                return
            route.continue_()
        except Exception:                                   # noqa: BLE001
            pass                                            # a closed page mid-route

    def close(self):
        for obj, meth in ((self.context, "close"), (self._browser, "close"),
                          (self._pw, "stop")):
            try:
                if obj is not None:
                    getattr(obj, meth)()
            except Exception:                               # noqa: BLE001
                pass
        self.context = self._browser = self._pw = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False


def open_session(cfg, repo_root) -> Session:
    check_server(cfg)
    return Session(cfg, repo_root).open()


# --------------------------------------------------------------------------- #
#  Signature + geometry (the sweep's two inputs)
# --------------------------------------------------------------------------- #
_JS_SIGNATURE = """
(args) => {
  const [ignore, geoSel, maxNodes, maxBlocks] = args;
  const skip = new Set();
  for (const sel of ignore) {
    try { document.querySelectorAll(sel).forEach(e => skip.add(e)); } catch (e) {}
  }
  const skipped = (el) => { let n = el; while (n) { if (skip.has(n)) return true; n = n.parentElement; } return false; };
  const parts = []; let count = 0;
  const walk = (el, depth) => {
    if (count > maxNodes || depth > 30) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'script' || tag === 'style' || tag === 'noscript') return;
    if (skip.has(el)) return;
    const cls = (el.getAttribute('class') || '').trim().split(/\\s+/).filter(Boolean).sort().join('.');
    parts.push(depth + ':' + tag + (cls ? '.' + cls : ''));
    count++;
    for (const c of el.children) walk(c, depth + 1);
  };
  if (document.body) walk(document.body, 0);
  const pathOf = (el) => {
    const out = [];
    let n = el, guard = 0;
    while (n && n.tagName && n.tagName.toLowerCase() !== 'html' && guard++ < 8) {
      const parent = n.parentElement;
      let idx = 1;
      if (parent) { for (const c of parent.children) { if (c === n) break; if (c.tagName === n.tagName) idx++; } }
      out.unshift(n.tagName.toLowerCase() + ':' + idx);
      n = parent;
    }
    return out.join('>');
  };
  const geo = []; const seen = new Set();
  let els = [];
  try { els = Array.from(document.querySelectorAll(geoSel.join(','))); } catch (e) { els = []; }
  for (const el of els) {
    if (geo.length >= maxBlocks) break;
    if (skipped(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const p = pathOf(el);
    if (seen.has(p)) continue;
    seen.add(p);
    geo.push({path: p, x: Math.round(r.left + window.scrollX),
              y: Math.round(r.top + window.scrollY),
              w: Math.round(r.width), h: Math.round(r.height)});
  }
  return {tree: parts.join('|'), nodes: count, geometry: geo};
}
"""

_JS_OPENNESS = """
(sels) => {
  const out = [];
  for (const sel of sels) {
    let els = [];
    try { els = Array.from(document.querySelectorAll(sel)); } catch (e) { continue; }
    els.forEach((el, i) => {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return;
      out.push(sel + '#' + i + '@' + Math.round(r.width) + 'x' + Math.round(r.height));
    });
  }
  return out.sort().join('|');
}
"""

_JS_TRIGGERS = """
(args) => {
  const [sels, chromeSel, contentSel] = args;
  // Which landmark does this trigger belong to? The NEAREST one decides, and a
  // chrome landmark INSIDE the content is the screen's own navigation - a
  // `<nav class="nav-tabs">` inside `<main>` is this page's tab strip, not the
  // site menu, and it must keep producing states.
  const landmarkOf = (el) => {
    const near = el.closest(contentSel + ',' + chromeSel);
    if (!near || near.matches(contentSel)) return {chrome: false, landmark: ''};
    const inside = near.parentElement && near.parentElement.closest(contentSel);
    return {chrome: !inside, landmark: near.tagName.toLowerCase()};
  };
  const out = [];
  sels.forEach((sel) => {
    let els = [];
    try { els = Array.from(document.querySelectorAll(sel)); } catch (e) { return; }
    els.forEach((el, i) => {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return;
      // The VISIBLE text first: the state name reaches the operator in the HUD
      // gallery, and "Dodaj" is a screen they recognise where "openAdd" is not.
      const label = (el.getAttribute('aria-label') ||
                     (el.textContent || '').trim().split(/\\s+/).slice(0, 4).join(' ') ||
                     el.id || el.getAttribute('data-bs-target') || sel);
      const where = landmarkOf(el);
      out.push({selector: sel, index: i, label: label.slice(0, 60),
                toggle: el.getAttribute('data-bs-toggle') || sel,
                chrome: where.chrome, landmark: where.landmark});
    });
  });
  return out;
}
"""


def signature(page, ignore_selectors=None) -> dict:
    """`{"dom_signature": <16 hex>, "geometry": [...], "nodes": n}`.

    Tag + sorted classes only: no text, no ids, no numbers - a counter that ticks
    from 12 to 13 must not read as a change. Hashing happens here rather than in
    the page so the algorithm is versioned with this file.
    """
    try:
        raw = page.evaluate(_JS_SIGNATURE, [list(ignore_selectors or []),
                                            GEOMETRY_SELECTORS, MAX_TREE_NODES,
                                            MAX_GEOMETRY_BLOCKS])
    except Exception as exc:                                # noqa: BLE001
        return {"dom_signature": "", "geometry": [], "nodes": 0,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    tree = str(raw.get("tree") or "")
    return {"dom_signature": hashlib.sha256(tree.encode("utf-8")).hexdigest()[:16],
            "geometry": raw.get("geometry") or [], "nodes": int(raw.get("nodes") or 0)}


# --------------------------------------------------------------------------- #
#  One page, every state
# --------------------------------------------------------------------------- #
def _goto(page, url):
    """`load` then a best-effort `networkidle`. Never plain networkidle: a page
    with a polling fallback (chat) never goes idle and would time out for a
    reason that has nothing to do with the screenshot."""
    resp = page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:                                       # noqa: BLE001
        pass
    try:
        # ONE injection for both: an author `!important` rule outranks the
        # element's own inline style, which is how the toolbar stays hidden
        # after its script runs.
        page.add_style_tag(content=ANIM_CSS + HIDE_CSS)
    except Exception:                                       # noqa: BLE001
        pass
    page.wait_for_timeout(SETTLE_MS)
    return resp


def _shot(page, out_dir, name, screenshot=True):
    if not screenshot:
        return ""
    path = Path(out_dir) / ("%s.png" % name)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=True, animations="disabled", caret="hide")
    return str(path)


def _normalise_step(step):
    if not isinstance(step, dict):
        return None
    kind = str(step.get("kind") or step.get("action") or "").strip()
    selector = str(step.get("selector") or "")
    value = step.get("value")
    if not kind:
        for k in ("click", "fill", "wait"):
            if k in step:
                kind = k
                v = step[k]
                if isinstance(v, str) and not selector:
                    selector = v
                elif value is None:
                    value = v
                break
    return {"kind": kind, "selector": selector, "value": value} if kind else None


def _manual_states(cfg, url):
    """Manual overrides keyed by full URL or by path - a config should not have
    to repeat the base URL on every entry."""
    manual = (cfg.get("states") or {}).get("manual") or {}
    if url in manual:
        return manual[url]
    path = urlparse(url).path
    return manual.get(path) or manual.get(path.rstrip("/")) or manual.get(path + "/") or []


def _run_steps(page, steps) -> bool:
    for raw in steps or []:
        step = _normalise_step(raw)
        if not step:
            continue
        try:
            if step["kind"] == "click":
                page.locator(step["selector"]).first.click(timeout=STATE_WAIT_MS)
            elif step["kind"] == "fill":
                page.locator(step["selector"]).first.fill(str(step["value"] or ""),
                                                          timeout=STATE_WAIT_MS)
            elif step["kind"] == "wait":
                if step["selector"]:
                    page.wait_for_selector(step["selector"], timeout=STATE_WAIT_MS)
                else:
                    page.wait_for_timeout(int(step["value"] or 300))
        except Exception:                                   # noqa: BLE001
            return False
    page.wait_for_timeout(SETTLE_MS)
    return True


def capture_page(session, url, out_dir, page_id="", states=None, screenshot=True,
                 anchors=None):
    """`(shots, skipped)` for one page.

    `shots` is `[{state, png_path, dom_signature, geometry, url, page_id, status,
    title, trigger, regions}]`, base first, ONE PER DISTINCT PICTURE. `skipped` is
    `[{label, selector, reason}]` for every state that is not in `shots`: the
    ones that did not open, and the ones that opened and looked exactly like a
    state already kept (`collapsed: True`, carrying `same_as`). A state that
    quietly disappears is a state nobody knows is missing, and the customer ends
    up with a pair that never mentions the modal.

    `states=None` follows the config (auto-discovery + manual overrides);
    `states=[]` takes the base shot only (what the sweep wants). Each state gets
    its own browser context, so states never contaminate each other.

    `anchors` (hunk anchors from `affected.py`, best first) are measured WHILE
    the page is open and land on the shot as `regions` - the alternative is a
    second load of every page just to find out where to crop. They are measured
    per STATE, because an element inside a modal only has a rectangle while the
    modal is open.
    """
    cfg = session.cfg
    ignore = cfg.get("ignore_selectors") or []
    max_states = int((cfg.get("states") or {}).get("max_per_page") or 6)
    auto = bool((cfg.get("states") or {}).get("auto", True))
    base_name = page_id and slug(page_id) or slug(urlparse(url).path or "home", "home")

    out, skipped = [], []
    with session.fresh_page() as page:
        resp = _goto(page, url)
        status = resp.status if resp else 0
        title = ""
        try:
            title = page.title()
        except Exception:                                   # noqa: BLE001
            pass
        sig = signature(page, ignore)
        out.append({"state": "base", "png_path": _shot(page, out_dir, base_name, screenshot),
                    "url": url, "page_id": page_id, "status": status, "title": title,
                    "trigger": None, "regions": vlocate.resolve_regions(page, anchors),
                    **sig})

        if states == []:
            return out, skipped

        manual = _manual_states(cfg, url)
        discovered = []
        if states is None and auto:
            try:
                discovered = page.evaluate(
                    _JS_TRIGGERS, [TRIGGER_SELECTORS, ",".join(CHROME_LANDMARKS),
                                   ",".join(CONTENT_LANDMARKS)]) or []
            except Exception:                               # noqa: BLE001
                discovered = []
        elif isinstance(states, list):
            manual = states

        base_open = ""
        try:
            base_open = page.evaluate(_JS_OPENNESS, OPEN_SELECTORS)
        except Exception:                                   # noqa: BLE001
            pass

    # The site's menus are not states of this screen. Dropped BEFORE the budget,
    # so a navbar cannot spend the page loads a real state needed, and recorded
    # one by one - the row naming the landmark is the only way anybody finds out
    # when this rule misjudges a trigger.
    discovered = _drop_site_chrome(discovered, skipped)

    budget = max(0, max_states - 1)                 # PICTURES of this page
    attempts = budget * STATE_ATTEMPT_FACTOR        # page loads spent finding them
    used = set()
    # The pictures KEPT for this page so far, base first. A state earns its own
    # pair only by being a different picture from every one of them.
    kept = _kept_hashes(out)
    opened = 0

    for spec in list(manual)[:budget]:
        name = slug((spec or {}).get("name") or "manual", "manual")
        if name in used:
            continue
        steps = (spec or {}).get("steps") or []
        shot, why = _capture_state(session, url, out_dir, page_id, base_name, name,
                                   steps=steps, ignore=ignore,
                                   screenshot=screenshot, base_open=base_open,
                                   anchors=anchors)
        if shot:
            opened += 1
            used.add(name)
            _keep_or_collapse(shot, kept, out, skipped, _steps_selector(steps),
                              always_keep=True)
        else:
            skipped.append({"label": name, "selector": _steps_selector(steps),
                            "reason": why})

    for trig in discovered:
        if len(out) - 1 >= budget or opened >= attempts:
            break
        name = _trigger_name(trig)
        if name in used:
            continue
        shot, why = _capture_state(session, url, out_dir, page_id, base_name, name,
                                   trigger=trig, ignore=ignore, screenshot=screenshot,
                                   base_open=base_open, anchors=anchors)
        selector = _trigger_selector(trig)
        if shot:
            opened += 1
            used.add(name)
            _keep_or_collapse(shot, kept, out, skipped, selector)
        else:
            skipped.append({"label": name, "selector": selector, "reason": why})
    return out, skipped


def _trigger_name(trig) -> str:
    return slug(trig.get("label") or trig.get("toggle"), "state")


def _trigger_selector(trig) -> str:
    return "%s (#%s)" % (trig.get("selector"), trig.get("index"))


def _drop_site_chrome(discovered, skipped) -> list:
    """The discovered triggers that belong to THIS SCREEN; the rest are recorded
    in `skipped` as chrome and never photographed.

    A menu in the navbar is the same menu on all 232 screens: opening it says
    nothing about the change, and it was costing a page load, a picture and a
    pair on every screen. Deduplication cannot reach this - with the debug
    toolbar out of the frame a navbar dropdown is 8-26 phash bits from its base
    shot and a real tab is 12-26, so no threshold separates them. Where the
    trigger LIVES does.

    `states.manual` is the way back in for a trigger this misjudges: an
    explicitly named state is never filtered, because it is the operator's
    decision rather than a guess about a layout.
    """
    out = []
    for trig in discovered:
        if not trig.get("chrome"):
            out.append(trig)
            continue
        skipped.append({"label": _trigger_name(trig),
                        "selector": _trigger_selector(trig), "chrome": True,
                        "reason": "site chrome (inside <%s>), not a state of this screen"
                                  % (trig.get("landmark") or "nav")})
    return out


def _kept_hashes(shots) -> list:
    """`[(state, phash)]` of the shots already kept - the base one, at first."""
    rows = []
    for shot in shots:
        h = vcompare.phash_or_blank(shot.get("png_path"))
        if h:
            rows.append((shot.get("state"), h))
    return rows


def _keep_or_collapse(shot, kept, out, skipped, selector="", always_keep=False) -> bool:
    """Append `shot` to `out`, or collapse it into the state it duplicates.

    A collapsed state leaves a row in `skipped` naming the state it repeats and
    the distance measured, and its PNG is removed: it is a byte-for-byte
    different, picture-for-picture identical copy of a shot already on record,
    and fifty of those per run is what turned the customer's gallery into four
    copies of every screen.

    NOT A CHANGE VERDICT. This compares two shots of the SAME side of the SAME
    page taken seconds apart. Whether the screen changed BETWEEN THE SIDES is
    `compare.sweep_changed`, it is structural, and nothing here touches it.

    `always_keep` is for a state the OPERATOR named in `states.manual`. A phash
    cannot tell "a menu opened over the page" from "nothing happened" - an open
    dropdown with its own backdrop sits a handful of bits from the base shot -
    so the collapse was silently throwing away exactly the state somebody had
    configured because the change is only visible once it is open. A named state
    is a decision, not a guess about a layout, and decisions are not overruled by
    a perceptual hash.
    """
    if always_keep:
        h = vcompare.phash_or_blank(shot.get("png_path"))
        out.append(shot)
        if h:
            kept.append((shot.get("state"), h))
        return True
    h = vcompare.phash_or_blank(shot.get("png_path"))
    twin, twin_hash, dist = "", "", 64
    for state, known in (kept if h else []):
        d = vcompare.hamming(h, known)
        if d < dist:
            twin, twin_hash, dist = state, known, d
    if not (h and twin and vcompare.same_picture(h, twin_hash)):
        out.append(shot)
        if h:
            kept.append((shot.get("state"), h))
        return True
    png = shot.get("png_path")
    if png:
        try:
            Path(png).unlink()
        except OSError as exc:
            print("visual/capture: could not remove the collapsed shot %s (%s)"
                  % (png, str(exc)[:80]), file=sys.stderr)
    skipped.append({"label": shot.get("state"), "selector": selector,
                    "collapsed": True, "same_as": twin,
                    "reason": "same picture as the '%s' state (phash distance %d)"
                              % (twin, dist)})
    return False


def _steps_selector(steps) -> str:
    for raw in steps or []:
        step = _normalise_step(raw)
        if step and step["selector"]:
            return step["selector"]
    return ""


def _capture_state(session, url, out_dir, page_id, base_name, name, trigger=None,
                   steps=None, ignore=(), screenshot=True, base_open="", anchors=None):
    """`(shot, "")` or `(None, reason)` - one state in its own context.

    A state that did not open (a trigger whose target is not on this page, a
    click that navigated away) is skipped, never faked, and the REASON travels
    with it into the manifest.
    """
    with session.fresh_page() as page:
        try:
            _goto(page, url)
            if trigger is not None:
                try:
                    page.locator(trigger["selector"]).nth(int(trigger["index"])).click(
                        timeout=STATE_WAIT_MS)
                except Exception as exc:                    # noqa: BLE001
                    return None, "the trigger could not be clicked (%s)" % type(exc).__name__
                page.wait_for_timeout(SETTLE_MS)
            elif not _run_steps(page, steps):
                return None, "a manual step failed on this page"

            if page.url.split("#")[0] != url.split("#")[0]:
                return None, "the click navigated away to %s" % page.url.split("?")[0]
            try:
                now_open = page.evaluate(_JS_OPENNESS, OPEN_SELECTORS)
            except Exception:                               # noqa: BLE001
                now_open = ""
            if trigger is not None and now_open == base_open:
                return None, "nothing opened: the page looks exactly like the base state"

            sig = signature(page, ignore)
            png = _shot(page, out_dir, "%s__%s" % (base_name, name), screenshot)
            return {"state": name, "png_path": png, "url": url, "page_id": page_id,
                    "status": 200, "title": "", "trigger": (trigger or {}).get("label"),
                    "regions": vlocate.resolve_regions(page, anchors), **sig}, ""
        except Exception as exc:                            # noqa: BLE001
            return None, "%s: %s" % (type(exc).__name__, str(exc)[:160])
