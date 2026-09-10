#!/usr/bin/env python3
"""F7 frontend (kalendar - "Predlozi" strip + izvori popover) - a STATIC Playwright
harness, same shape as test_voice_ui.py.

The HUD server on :7666 is never touched: `web/` is copied into a temp dir and EVERY
request the page makes - the document, the scripts, the css, /api/*, /stream - is
fulfilled by a Playwright route handler from that copy or from a canned fixture. No
socket ever leaves the process, no real Gemini, no POST reaches a running server.

What it pins:
  * the strip sits above the calendar grid (before .cal-layout in the DOM), fetches
    GET /api/calendar/suggestions on every range change with the SAME [from,to] window
    load() uses for /api/calendar/events, and shows a count in its header
  * each row shows a source badge, title, a date/time string, and the "why" text in a
    title attribute (never inline, so it never crowds the row)
  * "+ prihvati" POSTs accept {sid,from,to} and triggers BOTH a new events fetch and a
    new suggestions fetch (load()); "x odbaci" POSTs dismiss {sid} and refreshes ONLY
    suggestions (events is not re-fetched)
  * an empty suggestions list renders the designed empty state, not a blank strip
  * collapsed/open state toggles on header click (not on the gear) and survives a
    page reload via localStorage
  * an occurrence with source != "manual" renders with a source badge and a dashed
    accent (computed style), and opens through the SAME edit modal as a manual one
  * a manual occurrence (or one with no source) never gets a badge or the dashed style
  * the gear opens a MODAL (not an anchored popover) with five checkboxes reflecting
    GET /api/calendar/sources; mail/chat rows carry the Gemini-quota note, the other
    three do not; Save POSTs the full 5-key body and refreshes suggestions
  * hostile text in title/why never becomes markup (esc() throughout)

ASCII output only (safe under a cp1252 console). Run:  python test_calendar_ui.py
"""
from __future__ import annotations

import datetime
import json
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"

try:
    from playwright.sync_api import sync_playwright
except Exception as exc:                                    # noqa: BLE001
    print("SKIP  playwright is not installed: " + str(exc))
    sys.exit(0)

_results = []


def _ascii(s):
    """Never let a Serbian diacritic reach the console - the hard rule this whole
    project runs under (cp1252 crashes mid-run on c/c-/s/z/dj)."""
    return str(s).encode("ascii", "backslashreplace").decode("ascii")


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name +
          (("  -- " + _ascii(detail)) if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Fixtures - exactly the contract shapes (calsug.suggestions / calsvc events),
#  dates anchored on "today" so they always land inside the default month view's
#  fetched [from,to] window regardless of which day the suite runs on.
# --------------------------------------------------------------------------- #
XSS = "<img src=x onerror=alert(1)>"
TODAY = datetime.date.today()
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
D3 = (TODAY + datetime.timedelta(days=3)).isoformat()

SUGGESTIONS = {
    "generated_at": "2026-08-19T09:00:00", "ai_used": True,
    "suggestions": [
        {"sid": "s1ticket0001", "title": "Rok: #123 " + XSS, "start": D1, "end": None,
         "allday": True, "source": "ticket", "ref": "Demo#123",
         "why": "Rok tiketa #123 (Demo)" + XSS},
        {"sid": "s2worklog001", "title": "Rad: #123", "start": D2 + "T09:00:00",
         "end": D2 + "T10:30:00", "allday": False, "source": "worklog",
         "ref": "Demo#123#w1", "why": "Radna sesija na tiketu #123 (Demo)"},
        {"sid": "s3mail000001", "title": "Sastanak sa klijentom", "start": D3 + "T14:00:00",
         "end": None, "allday": False, "source": "mail", "ref": None,
         "why": "Pomenuto u mailu"},
    ],
}
EMPTY_SUGGESTIONS = {"generated_at": "2026-08-19T09:00:00", "ai_used": False, "suggestions": []}

EVENTS = [
    {"master_id": "m1", "occ_id": "m1_" + D1, "occ_date": D1, "title": "Sastanak tima",
     "start": D1 + "T10:00:00", "end": D1 + "T11:00:00", "allday": False, "cat": "cy",
     "notes": "", "is_recurring": False, "remind_min": 10, "source": "manual", "ref": None},
    {"master_id": "m2", "occ_id": "m2_" + D2, "occ_date": D2, "title": "Rok: #123",
     "start": D2, "end": None, "allday": True, "cat": "am",
     "notes": "", "is_recurring": False, "remind_min": 0, "source": "ticket", "ref": "Demo#123"},
]

DEFAULT_SOURCES = {"tickets": True, "worklog": True, "deploy": True, "mail": False, "chat": False}

ACCEPT_OK = {"ok": True, "id": 99, "sid": "s1ticket0001",
             "event": {"master_id": "m9", "title": "Rok: #123", "source": "ticket", "ref": "Demo#123"}}

MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".svg": "image/svg+xml", ".json": "application/json"}


class Harness:
    """Serves the temp copy of web/ and every /api/* through page.route()."""

    def __init__(self, root: Path, suggestions=None, events=None, sources=None):
        self.root = root
        self.suggestions = suggestions if suggestions is not None else SUGGESTIONS
        self.events = events if events is not None else EVENTS
        self.sources = sources if sources is not None else dict(DEFAULT_SOURCES)
        self.accept_result = (dict(ACCEPT_OK), 200)
        self.calls = []            # (method, path, qs_or_body)
        self.posts = []            # (path, parsed body)

    def get_calls(self, path):
        return [q for m, p, q in self.calls if m == "GET" and p == path]

    def handle(self, route, request):
        u = urlsplit(request.url)
        path = u.path
        method = request.method
        if method == "GET":
            qs = parse_qs(u.query)
            self.calls.append(("GET", path, qs))
            if path == "/api/calendar/events":
                return self._json(route, self.events)
            if path == "/api/calendar/suggestions":
                return self._json(route, self.suggestions)
            if path == "/api/calendar/sources":
                return self._json(route, {"sources": self.sources})
            if path == "/stream":
                return route.fulfill(status=200, content_type="text/event-stream", body="")
            if path.startswith("/api/") or path in ("/sound", "/favicon.ico"):
                return self._json(route, {})
            f = self.root / (path.lstrip("/") or "index.html")
            if f.is_file():
                return route.fulfill(status=200, content_type=MIME.get(f.suffix.lower(), "text/plain"),
                                     body=f.read_bytes())
            return route.fulfill(status=404, content_type="text/plain", body="no")
        if method == "POST":
            try:
                body = json.loads(request.post_data or "{}")
            except Exception:                                   # noqa: BLE001
                body = {"__raw": request.post_data}
            self.posts.append((path, body))
            self.calls.append(("POST", path, body))
            if path == "/api/calendar/suggestions/accept":
                res, code = self.accept_result
                res = dict(res)
                res["sid"] = body.get("sid")
                return route.fulfill(status=code, content_type="application/json", body=json.dumps(res))
            if path == "/api/calendar/suggestions/dismiss":
                return self._json(route, {"ok": True, "sid": body.get("sid")})
            if path == "/api/calendar/sources":
                self.sources = body
                return self._json(route, {"sources": body})
            return self._json(route, {})
        return route.fulfill(status=404, content_type="text/plain", body="no")

    @staticmethod
    def _json(route, obj):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(obj))


def open_page(pw, root, harness, width=1440, height=980):
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": height})
    page.route("**/*", harness.handle)
    page.goto("http://hud.test/index.html", wait_until="load")
    page.wait_for_timeout(300)                      # let boot.js finish wiring
    page.evaluate("()=>setMode('calendar')")
    page.wait_for_timeout(300)                       # let load() (events + suggestions) settle
    return browser, page


# --------------------------------------------------------------------------- #
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="calui_"))
    root = tmp / "web"
    shutil.copytree(WEB, root)
    shots = tmp / "shots"
    shots.mkdir()

    with sync_playwright() as pw:
        # ==================== main flow ====================
        h = Harness(root)
        browser, page = open_page(pw, root, h)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # --- the strip exists, above the grid -------------------------------
        check("cal-sug strip exists", page.query_selector("#cal-sug") is not None)
        order = page.evaluate(
            "()=>{var s=document.getElementById('cal-sug'),l=document.querySelector('#view-calendar .cal-layout');"
            "if(!s||!l)return 'missing';var p=s.compareDocumentPosition(l);"
            "return (p&Node.DOCUMENT_POSITION_FOLLOWING)?'sug-before-layout':'other';}")
        check("strip sits ABOVE .cal-layout (the grid)", order == "sug-before-layout", order)

        # --- it fetched suggestions with the SAME window load() used for events ----
        ev_calls = h.get_calls("/api/calendar/events")
        sug_calls = h.get_calls("/api/calendar/suggestions")
        check("events were fetched on tab open", len(ev_calls) >= 1, len(ev_calls))
        check("suggestions were fetched on tab open", len(sug_calls) >= 1, len(sug_calls))
        check("suggestions used the SAME [from,to] as events",
              sug_calls and ev_calls and sug_calls[-1].get("from") == ev_calls[-1].get("from")
              and sug_calls[-1].get("to") == ev_calls[-1].get("to"),
              str(sug_calls[-1:]) + " vs " + str(ev_calls[-1:]))

        # --- count + rows ------------------------------------------------------
        check("header count matches the fixture", page.eval_on_selector("#cal-sug-count", "e=>e.textContent") == "3")
        rows = page.query_selector_all("#cal-sug-body .cal-sug-row")
        check("one row per suggestion", len(rows) == 3, len(rows))
        b0 = page.eval_on_selector(".cal-sug-row:nth-child(1) .cal-sug-badge", "e=>e.textContent")
        check("ticket badge label is 'tiket'", "tiket" in b0, b0)
        b1 = page.eval_on_selector(".cal-sug-row:nth-child(2) .cal-sug-badge", "e=>e.textContent")
        check("worklog badge label is 'rad'", "rad" in b1, b1)
        b2 = page.eval_on_selector(".cal-sug-row:nth-child(3) .cal-sug-badge", "e=>e.textContent")
        check("mail badge label is 'mail'", "mail" in b2, b2)
        why0 = page.eval_on_selector(".cal-sug-row:nth-child(1) .cal-sug-title", "e=>e.getAttribute('title')")
        check("why reaches the title attribute", "Rok tiketa #123" in (why0 or ""), why0)
        check("hostile why/title never becomes markup",
              page.eval_on_selector_all("#cal-sug-body img,#cal-sug-body script", "els=>els.length") == 0)
        title0 = page.eval_on_selector(".cal-sug-row:nth-child(1) .cal-sug-title", "e=>e.textContent")
        check("hostile title renders as literal text", XSS in (title0 or ""), title0)

        # --- collapse toggle (not triggered by the gear) -----------------------
        check("strip starts open (default)",
              not page.eval_on_selector("#cal-sug", "e=>e.classList.contains('collapsed')"))
        page.click("#cal-sug-toggle")
        page.wait_for_timeout(60)
        check("clicking the header collapses the strip",
              page.eval_on_selector("#cal-sug", "e=>e.classList.contains('collapsed')"))
        check("collapsed state persisted to localStorage",
              page.evaluate("()=>localStorage.getItem('av_cal_sug_open')") == "0")
        page.reload()
        page.wait_for_timeout(250)
        check("collapsed state survives a reload",
              page.eval_on_selector("#cal-sug", "e=>e.classList.contains('collapsed')"))
        page.click("#cal-sug-toggle")                          # re-open for the rest of the flow
        page.wait_for_timeout(60)
        page.evaluate("()=>setMode('calendar')")
        page.wait_for_timeout(250)

        # --- range change re-fetches suggestions with a NEW window -------------
        before = len(h.get_calls("/api/calendar/suggestions"))
        last_qs = h.get_calls("/api/calendar/suggestions")[-1]
        page.click("#cal-next")
        page.wait_for_timeout(250)
        after_calls = h.get_calls("/api/calendar/suggestions")
        check("changing the visible range re-fetches suggestions", len(after_calls) > before, len(after_calls))
        check("the new fetch uses a DIFFERENT window",
              after_calls[-1].get("from") != last_qs.get("from"), (after_calls[-1], last_qs))
        page.click("#cal-prev")                                 # back to the month the fixtures target
        page.wait_for_timeout(250)

        # --- auto vs manual event rendering -------------------------------------
        auto_cell = page.query_selector(".cal-ev.auto")
        manual_cell = None
        for el in page.query_selector_all(".cal-ev"):
            if "auto" not in (el.get_attribute("class") or ""):
                manual_cell = el
                break
        check("an auto-created occurrence renders with the 'auto' class", auto_cell is not None)
        check("a manual occurrence exists too (control)", manual_cell is not None)
        if auto_cell is not None:
            badge = auto_cell.query_selector(".cal-src-badge")
            check("the auto occurrence shows a source badge", badge is not None)
            auto_style = auto_cell.evaluate("e=>getComputedStyle(e).borderLeftStyle")
            check("the auto occurrence's accent is dashed", auto_style == "dashed", auto_style)
        if manual_cell is not None:
            check("a manual occurrence carries NO source badge",
                  manual_cell.query_selector(".cal-src-badge") is None)
            manual_style = manual_cell.evaluate("e=>getComputedStyle(e).borderLeftStyle")
            check("a manual occurrence's accent stays solid", manual_style == "solid", manual_style)

        # clicking an auto occurrence opens the SAME edit modal as a manual one
        if auto_cell is not None:
            auto_cell.click()
            page.wait_for_timeout(120)
            check("clicking the auto occurrence opens the edit modal",
                  page.eval_on_selector("#cal-ov", "e=>e.classList.contains('on')"))
            check("the modal is pre-filled from the clicked occurrence",
                  "Rok: #123" in page.eval_on_selector("#cal-f-title", "e=>e.value"))
            page.click("#cal-dlgx")
            page.wait_for_timeout(60)

        # --- accept: POSTs {sid,from,to}, then reloads BOTH events and suggestions --
        ev_before = len(h.get_calls("/api/calendar/events"))
        sug_before = len(h.get_calls("/api/calendar/suggestions"))
        page.click('.cal-sug-row:nth-child(1) .cal-sug-acc')
        page.wait_for_timeout(300)
        acc_posts = [b for p, b in h.posts if p == "/api/calendar/suggestions/accept"]
        check("accept posted exactly once", len(acc_posts) == 1, len(acc_posts))
        check("accept carries sid + the CURRENT window (not client-guessed fields)",
              acc_posts and set(acc_posts[0].keys()) == {"sid", "from", "to"}
              and acc_posts[0]["sid"] == "s1ticket0001", acc_posts)
        check("accept re-fetched events", len(h.get_calls("/api/calendar/events")) > ev_before)
        check("accept re-fetched suggestions", len(h.get_calls("/api/calendar/suggestions")) > sug_before)

        # --- dismiss: POSTs {sid} only, refreshes suggestions ONLY -----------------
        h2 = Harness(root)
        browser2, page2 = open_page(pw, root, h2)
        ev_before2 = len(h2.get_calls("/api/calendar/events"))
        sug_before2 = len(h2.get_calls("/api/calendar/suggestions"))
        page2.click('.cal-sug-row:nth-child(2) .cal-sug-dis')
        page2.wait_for_timeout(250)
        dis_posts = [b for p, b in h2.posts if p == "/api/calendar/suggestions/dismiss"]
        check("dismiss posted exactly once with only sid", len(dis_posts) == 1
              and set(dis_posts[0].keys()) == {"sid"} and dis_posts[0]["sid"] == "s2worklog001", dis_posts)
        check("dismiss re-fetched suggestions", len(h2.get_calls("/api/calendar/suggestions")) > sug_before2)
        check("dismiss did NOT re-fetch events (only the suggestion list changed)",
              len(h2.get_calls("/api/calendar/events")) == ev_before2)
        browser2.close()

        # --- empty state -------------------------------------------------------
        h3 = Harness(root, suggestions=EMPTY_SUGGESTIONS)
        browser3, page3 = open_page(pw, root, h3)
        check("empty suggestions show the designed empty state",
              "Nema predloga za ovaj period" in page3.eval_on_selector("#cal-sug-body", "e=>e.textContent"))
        check("count reads 0 for an empty list", page3.eval_on_selector("#cal-sug-count", "e=>e.textContent") == "0")
        browser3.close()

        # ==================== izvori (sources) modal ====================
        check("sources modal starts closed",
              not page.eval_on_selector("#cal-src-ov", "e=>e.classList.contains('on')"))
        page.click("#cal-sug-gear")
        page.wait_for_timeout(200)
        check("gear opens the modal (never just a toggle)",
              page.eval_on_selector("#cal-src-ov", "e=>e.classList.contains('on')"))
        check("collapsing the strip is NOT triggered by the gear click",
              not page.eval_on_selector("#cal-sug", "e=>e.classList.contains('collapsed')"))
        boxes = page.evaluate(
            "()=>({tickets:document.getElementById('cal-src-tickets').checked,"
            "worklog:document.getElementById('cal-src-worklog').checked,"
            "deploy:true,"
            "mail:document.getElementById('cal-src-mail').checked,"
            "chat:document.getElementById('cal-src-chat').checked})")
        check("checkboxes reflect GET /api/calendar/sources", boxes == DEFAULT_SOURCES, boxes)
        def _note(page_, cb_id):
            return page_.evaluate(
                "()=>{var n=document.getElementById('" + cb_id + "').closest('label').nextElementSibling;"
                "return (n&&n.classList.contains('cal-src-note'))?n.textContent:'';}")
        mail_note = _note(page, "cal-src-mail")
        chat_note = _note(page, "cal-src-chat")
        tix_note = _note(page, "cal-src-tickets")
        check("mail row carries the Gemini-quota note", "Gemini poziv dnevno" in (mail_note or ""), mail_note)
        check("chat row carries the Gemini-quota note", "Gemini poziv dnevno" in (chat_note or ""), chat_note)
        check("tickets row carries NO Gemini-quota note", not tix_note, tix_note)

        # toggle mail on, save -> full 5-key body, modal closes, suggestions reload
        sug_before3 = len(h.get_calls("/api/calendar/suggestions"))
        page.click("#cal-src-mail")
        page.click("#cal-src-save")
        page.wait_for_timeout(250)
        src_posts = [b for p, b in h.posts if p == "/api/calendar/sources"]
        check("save posted the FULL 5-key body", src_posts and set(src_posts[-1].keys()) == set(DEFAULT_SOURCES),
              src_posts)
        check("the toggled key changed, the rest did not",
              src_posts and src_posts[-1]["mail"] is True and src_posts[-1]["tickets"] is True,
              src_posts[-1] if src_posts else None)
        check("modal closes after save",
              not page.eval_on_selector("#cal-src-ov", "e=>e.classList.contains('on')"))
        check("saving reloads suggestions", len(h.get_calls("/api/calendar/suggestions")) > sug_before3)

        # Escape closes it too
        page.click("#cal-sug-gear")
        page.wait_for_timeout(120)
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)
        check("Escape closes the sources modal",
              not page.eval_on_selector("#cal-src-ov", "e=>e.classList.contains('on')"))

        page.screenshot(path=str(shots / "01-suggestions.png"))
        check("no page errors during the whole flow", not errors, "; ".join(_ascii(x) for x in errors[:3]))
        browser.close()

    print("\nscreenshots: " + str(shots))
    bad = [n for n, ok, _ in _results if not ok]
    print("%d checks, %d failed" % (len(_results), len(bad)))
    for n in bad:
        print("  FAILED: " + n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
