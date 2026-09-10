#!/usr/bin/env python3
"""F6 frontend (glasovna komanda) - a STATIC Playwright harness.

The HUD server on :7666 is never touched: `web/` is copied into a temp dir and EVERY
request the page makes - the document, the scripts, the css, /api/*, /stream - is
fulfilled by a Playwright route handler from that copy or from a canned fixture. No
socket ever leaves the process, no real Gemini, no real shell, and no POST reaches a
running server. Web Speech is faked with an init script (headless Chromium has none),
which is also how the UNSUPPORTED state gets tested: that page simply gets no fake.

What it pins:
  * the mic button sits next to #askclaude and carries the shortcut in its title
  * Ctrl+Shift+Space toggles listening from anywhere, but NOT while typing in another
    textarea (the panel's own transcript box is the exception)
  * interim speech stays in the dim line and never in the textarea; final text is appended
  * Enter never executes; Ctrl+Enter reaches /plan and never /run
  * a high-risk step renders UNTICKED, a refused step renders with NO checkbox, struck
    through, with the server's reason
  * the payload is verbatim (raw backslashes, not JSON-escaped) and not clipped
  * server text is inert: an <img onerror> in a label/reason/payload renders as text
  * /run receives only the ticked approvals, and the plan locks after one run
  * a hud row with client:true is performed BY THE PAGE (open_view, rescan, open_ticket)
  * Escape closes the panel and stops listening; the transcript never lands in localStorage

ASCII output only (safe under a cp1252 console). Run:  python test_voice_ui.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"

try:
    from playwright.sync_api import sync_playwright
except Exception as exc:                                    # noqa: BLE001
    print("SKIP  playwright is not installed: " + str(exc))
    sys.exit(0)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (("  -- " + str(detail)) if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Fixtures - exactly the contract shapes, never a live call.
# --------------------------------------------------------------------------- #
XSS = "<img src=x onerror=alert(1)>"
TOK = {0: "a" * 64, 1: "b" * 64, 2: "c" * 64}
WIN_CWD = "C:\\Users\\you\\projects\\demo"          # backslashes must survive VERBATIM

PLAN = {
    "plan_id": "a1b2c3d4e5f60718293a4b5c",
    "note": "Tri koraka, jedan odbijen.",
    "error": "",
    "steps": [
        {"idx": 0, "kind": "shell",
         "payload": {"cmd": "git status --porcelain", "cwd": WIN_CWD},
         "label": "Provera stanja repozitorijuma", "approve_token": TOK[0],
         "expires": int(time.time()) + 600, "risk": "high", "refused": None},
        {"idx": 1, "kind": "write_file",
         "payload": {"path": "C:\\Users\\you\\Desktop\\beleska.txt",
                     "content": "prva linija\ndruga linija", "overwrite": False},
         "label": "Upis beleske na Desktop", "approve_token": TOK[1],
         "expires": int(time.time()) + 600, "risk": "low", "refused": None},
        {"idx": 2, "kind": "hud",
         "payload": {"action": "open_view", "arg": "tiketi"},
         "label": "Otvori pregled tiketa", "approve_token": TOK[2],
         "expires": int(time.time()) + 600, "risk": "none", "refused": None},
        # refused + hostile text in the label, the reason AND the payload
        {"idx": 3, "kind": "shell",
         "payload": {"cmd": "rm -rf / " + XSS, "cwd": None},
         "label": XSS + " brisanje svega", "approve_token": "",
         "expires": int(time.time()) + 600, "risk": "high",
         "refused": "crna lista: rekurzivno brisanje " + XSS},
    ],
}
RUN = {
    "plan_id": PLAN["plan_id"],
    "results": [
        {"idx": 0, "kind": "shell", "status": "skipped", "detail": "nije odobreno"},
        {"idx": 1, "kind": "write_file", "status": "ok", "detail": "upisano"},
        {"idx": 2, "kind": "hud", "status": "ok", "detail": "izvrsava HUD", "client": True},
        {"idx": 3, "kind": "shell", "status": "skipped", "detail": "nije odobreno"},
    ],
}
TICKETS = {"projects": [{"module": "Demo", "dir": "demo", "rev": "1", "tickets": [
    {"id": "8597", "title": "Test tiket", "desc": "opis", "customer": "Kupac",
     "priority": "major", "status": "active", "category": "bug", "comments": []}]}]}

FAKE_SPEECH = """
window.__vcRec={starts:0,stops:0,lang:"",continuous:null,interim:null,inst:null};
window.SpeechRecognition=function(){var self=this;
  this.lang="";this.continuous=false;this.interimResults=false;
  this.onresult=null;this.onend=null;this.onerror=null;
  this.start=function(){var s=window.__vcRec;s.starts++;s.lang=self.lang;
    s.continuous=self.continuous;s.interim=self.interimResults;};
  this.stop=function(){window.__vcRec.stops++;};
  window.__vcRec.inst=this;};
window.__vcSay=function(text,isFinal){var r=window.__vcRec.inst;
  if(!r||!r.onresult)return false;
  r.onresult({resultIndex:0,results:[{isFinal:!!isFinal,0:{transcript:text},length:1}]});
  return true;};
"""

# Chromium DEFINES webkitSpeechRecognition even headless, so "unsupported" (Firefox, Safari)
# has to be simulated by removing both names before any script runs.
NO_SPEECH = """
try{delete window.SpeechRecognition;}catch(e){}
try{delete window.webkitSpeechRecognition;}catch(e){}
try{Object.defineProperty(window,'SpeechRecognition',{value:undefined,configurable:true});}catch(e){}
try{Object.defineProperty(window,'webkitSpeechRecognition',{value:undefined,configurable:true});}catch(e){}
"""

MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".svg": "image/svg+xml", ".json": "application/json"}


class Harness:
    """Serves the temp copy of web/ and every /api/* through page.route()."""

    def __init__(self, root: Path, plan=None, run=None, tickets=None):
        self.root = root
        self.plan = plan if plan is not None else PLAN
        self.run = run if run is not None else RUN
        self.tickets = tickets if tickets is not None else {"projects": []}
        self.posts = []           # (path, parsed body) - every POST the page attempted

    def handle(self, route, request):
        url = request.url
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path.split("?", 1)[0].split("#", 1)[0]
        if path in ("/", ""):
            path = "/index.html"
        if request.method == "POST":
            body = None
            try:
                body = json.loads(request.post_data or "{}")
            except Exception:                                   # noqa: BLE001
                body = {"__raw": request.post_data}
            self.posts.append((path, body))
            if path == "/api/voice/plan":
                return self._json(route, self.plan)
            if path == "/api/voice/run":
                return self._json(route, self.run)
            return self._json(route, {})
        if path == "/stream":                                   # SSE: end it immediately
            return route.fulfill(status=200, content_type="text/event-stream", body="")
        if path == "/api/tickets":
            return self._json(route, self.tickets)
        if path.startswith("/api/") or path in ("/sound", "/favicon.ico"):
            return self._json(route, {})
        f = self.root / path.lstrip("/")
        if f.is_file():
            ext = f.suffix.lower()
            return route.fulfill(status=200, content_type=MIME.get(ext, "text/plain"),
                                 body=f.read_bytes())
        return route.fulfill(status=404, content_type="text/plain", body="no")

    @staticmethod
    def _json(route, obj):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(obj))

    def voice_posts(self, which):
        return [b for p, b in self.posts if p == "/api/voice/" + which]


def open_page(pw, root, harness, speech=True):
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 980})
    page.route("**/*", harness.handle)
    page.add_init_script(FAKE_SPEECH if speech else NO_SPEECH)
    page.goto("http://hud.test/index.html", wait_until="load")
    page.wait_for_timeout(400)                      # let boot.js finish wiring
    return browser, page


# --------------------------------------------------------------------------- #
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="voiceui_"))
    root = tmp / "web"
    shutil.copytree(WEB, root)
    shots = tmp / "shots"
    shots.mkdir()

    with sync_playwright() as pw:
        # ---------------- main flow (Web Speech present) ----------------
        h = Harness(root, tickets=TICKETS)
        browser, page = open_page(pw, root, h)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # --- the entry point -------------------------------------------------
        mic = page.query_selector("#voice-mic")
        check("mic button exists", mic is not None)
        check("mic title carries the shortcut",
              mic and "Ctrl+Shift+Space" in (mic.get_attribute("title") or ""),
              mic and mic.get_attribute("title"))
        sib = page.evaluate("()=>{var m=document.getElementById('voice-mic');"
                            "return m&&m.previousElementSibling?m.previousElementSibling.id:'';}")
        check("mic sits next to #askclaude", sib == "askclaude", sib)
        check("panel starts closed",
              page.evaluate("()=>!document.getElementById('voice-panel').classList.contains('open')"))

        # --- shortcut from anywhere -----------------------------------------
        page.keyboard.press("Control+Shift+Space")
        page.wait_for_timeout(120)
        check("shortcut opens the panel",
              page.evaluate("()=>document.getElementById('voice-panel').classList.contains('open')"))
        check("shortcut starts listening", page.evaluate("()=>window.__vcRec.starts") == 1)
        check("recognition config: sr-RS + continuous + interim",
              page.evaluate("()=>window.__vcRec.lang==='sr-RS'&&window.__vcRec.continuous===true"
                            "&&window.__vcRec.interim===true"))
        check("mic shows the recording state",
              page.evaluate("()=>document.getElementById('voice-mic').classList.contains('rec')"))
        check("status line says slusam",
              page.evaluate("()=>document.getElementById('voice-status').textContent") == "slusam...")

        # --- interim vs final -------------------------------------------------
        page.evaluate("()=>window.__vcSay('otvori tikete',false)")
        page.wait_for_timeout(60)
        check("interim goes to the dim line, NOT the textarea",
              page.evaluate("()=>document.getElementById('voice-interim').textContent")
              == "otvori tikete"
              and page.evaluate("()=>document.getElementById('voice-text').value") == "")
        grey = page.evaluate("()=>getComputedStyle(document.getElementById('voice-interim')).color")
        fg = page.evaluate("()=>getComputedStyle(document.getElementById('voice-text')).color")
        check("interim is rendered dimmer than the transcript", grey != fg, grey + " vs " + fg)
        page.evaluate("()=>window.__vcSay('otvori tikete',true)")
        page.wait_for_timeout(60)
        check("final text is appended to the editable transcript",
              page.evaluate("()=>document.getElementById('voice-text').value") == "otvori tikete")
        page.evaluate("()=>window.__vcSay('i uradi rescan',true)")
        page.wait_for_timeout(60)
        check("a second final chunk appends (never replaces)",
              page.evaluate("()=>document.getElementById('voice-text').value")
              == "otvori tikete i uradi rescan")

        # --- the transcript box is editable, and Enter never executes ---------
        page.fill("#voice-text", "napravi belesku na desktopu")
        page.click("#voice-text")
        page.keyboard.press("Enter")
        page.wait_for_timeout(120)
        check("Enter in the transcript sends nothing", len(h.posts) == 0, str(h.posts))
        check("Enter inserts a newline instead",
              "\n" in page.evaluate("()=>document.getElementById('voice-text').value"))

        # --- shortcut is ignored while typing elsewhere ----------------------
        starts_before = page.evaluate("()=>window.__vcRec.starts")
        page.evaluate("()=>{document.getElementById('claude-panel').classList.add('open');"
                      "document.getElementById('claude-in').focus();}")
        page.keyboard.press("Control+Shift+Space")
        page.wait_for_timeout(100)
        check("shortcut ignored while typing in another textarea",
              page.evaluate("()=>window.__vcRec.starts") == starts_before)
        page.evaluate("()=>document.getElementById('claude-panel').classList.remove('open')")

        # --- Ctrl+Enter reaches /plan and ONLY /plan -------------------------
        page.fill("#voice-text", "napravi belesku na desktopu")
        page.click("#voice-text")
        page.keyboard.press("Control+Enter")
        page.wait_for_selector(".vc-step", timeout=4000)
        check("Ctrl+Enter posts to /api/voice/plan", len(h.voice_posts("plan")) == 1)
        check("Ctrl+Enter never posts to /api/voice/run", len(h.voice_posts("run")) == 0)
        sent = h.voice_posts("plan")[0]
        check("the plan request carries the edited transcript",
              sent.get("text") == "napravi belesku na desktopu", json.dumps(sent))

        # --- the step list ----------------------------------------------------
        n = page.eval_on_selector_all(".vc-step", "els=>els.length")
        check("one row per step", n == 4, str(n))
        state = page.evaluate("""()=>Array.prototype.map.call(
            document.querySelectorAll('.vc-step'),function(s){
              var c=s.querySelector('.vc-cbx');
              return {idx:s.getAttribute('data-idx'),has:!!c,checked:c?c.checked:null};});""")
        check("high-risk step renders UNTICKED",
              state[0]["has"] and state[0]["checked"] is False, json.dumps(state[0]))
        check("low-risk step renders ticked",
              state[1]["has"] and state[1]["checked"] is True, json.dumps(state[1]))
        check("no-risk step renders ticked",
              state[2]["has"] and state[2]["checked"] is True, json.dumps(state[2]))
        check("refused step has NO checkbox", state[3]["has"] is False, json.dumps(state[3]))
        deco = page.eval_on_selector('.vc-step[data-idx="3"] .vc-label',
                                     "e=>getComputedStyle(e).textDecorationLine")
        check("refused step is struck through", "line-through" in deco, deco)
        reason = page.eval_on_selector('.vc-step[data-idx="3"] .vc-reason', "e=>e.textContent")
        check("refused reason is shown", "crna lista" in reason, reason)

        # --- payload verbatim + inert ----------------------------------------
        pre0 = page.eval_on_selector('.vc-step[data-idx="0"] .vc-pre', "e=>e.textContent")
        check("shell command is shown verbatim", "cmd: git status --porcelain" in pre0, pre0)
        check("windows path keeps single backslashes (not JSON-escaped)",
              ("cwd: " + WIN_CWD) in pre0, pre0)
        clipped = page.eval_on_selector('.vc-step[data-idx="0"] .vc-pre',
                                        "e=>e.scrollHeight>e.clientHeight+1")
        check("the shell command is fully visible (not clipped)", clipped is False)
        pre1 = page.eval_on_selector('.vc-step[data-idx="1"] .vc-pre', "e=>e.textContent")
        check("write_file shows path, overwrite and the content last",
              "path: C:\\Users\\you\\Desktop\\beleska.txt" in pre1
              and "overwrite: false" in pre1
              and pre1.index("--- content ---") > pre1.index("overwrite:"), pre1)
        check("no element was injected from server text",
              page.eval_on_selector_all("#voice-steps img,#voice-steps script", "e=>e.length") == 0)
        lab3 = page.eval_on_selector('.vc-step[data-idx="3"] .vc-label', "e=>e.textContent")
        check("hostile label renders as literal text", XSS in lab3, lab3)
        check("hostile payload renders as literal text",
              XSS in page.eval_on_selector('.vc-step[data-idx="3"] .vc-pre', "e=>e.textContent"))
        check("plan note is rendered",
              "Tri koraka" in page.eval_on_selector(".vc-note", "e=>e.textContent"))

        # --- run button gating ------------------------------------------------
        page.screenshot(path=str(shots / "01-plan.png"))
        check("run button is enabled while rows are ticked",
              page.eval_on_selector("#voice-run", "e=>!e.disabled"))
        page.evaluate("""()=>{Array.prototype.forEach.call(
            document.querySelectorAll('.vc-cbx'),function(c){
              if(c.checked){c.checked=false;c.onchange();}});}""")
        check("run button disables when nothing is ticked",
              page.eval_on_selector("#voice-run", "e=>e.disabled"))
        page.click('#vc-cbx-0')                              # the operator CAN approve a high step
        check("a high-risk step can still be approved by hand",
              page.eval_on_selector("#voice-run", "e=>!e.disabled"))
        page.click('#vc-cbx-0')                              # ... and untick it again
        page.click('#vc-cbx-1')
        page.click('#vc-cbx-2')

        # --- run --------------------------------------------------------------
        page.click("#voice-run")
        page.wait_for_timeout(400)
        runs = h.voice_posts("run")
        check("run posts once", len(runs) == 1, str(len(runs)))
        body = runs[0] if runs else {}
        idxs = sorted(a.get("idx") for a in body.get("approvals", []))
        check("only the TICKED steps are approved", idxs == [1, 2], json.dumps(body))
        check("the refused step's idx never reaches /run", 3 not in idxs)
        check("approvals carry the server's tokens",
              all(a["approve_token"] == TOK[a["idx"]] for a in body.get("approvals", [])),
              json.dumps(body))
        check("plan_id is echoed back", body.get("plan_id") == PLAN["plan_id"])
        res1 = page.eval_on_selector("#vc-res-1", "e=>e.textContent")
        res0 = page.eval_on_selector("#vc-res-0", "e=>e.textContent")
        check("per-row result is rendered", "ok" in res1 and "upisano" in res1, res1)
        check("a skipped row says so", "skipped" in res0, res0)
        check("hud row with client:true switched the view (open_view)",
              page.evaluate("()=>document.body.className") == "v-tickets",
              page.evaluate("()=>document.body.className"))
        check("the plan locks after one run",
              page.eval_on_selector("#voice-run", "e=>e.disabled")
              and page.eval_on_selector_all(".vc-cbx", "els=>els.every(function(c){return c.disabled;})"))
        page.screenshot(path=str(shots / "02-results.png"))

        # --- the transcript is never persisted --------------------------------
        dump = page.evaluate("()=>JSON.stringify(window.localStorage)")
        check("transcript never lands in localStorage",
              "belesku" not in dump and "tikete" not in dump, dump[:300])
        check("only the language CODE is stored",
              page.evaluate("()=>localStorage.getItem('av_voice_lang')") in (None, "sr-RS", "en-US"))

        # --- language toggle ---------------------------------------------------
        page.click("#voice-lang")
        check("language toggles to en-US",
              page.eval_on_selector("#voice-lang", "e=>e.textContent") == "en-US")
        page.click("#voice-lang")
        check("language toggles back to sr-RS",
              page.eval_on_selector("#voice-lang", "e=>e.textContent") == "sr-RS")

        # --- the panel button is a toggle, in step with the mic ---------------
        check("listen button reads Stani while listening",
              page.eval_on_selector("#voice-listen", "e=>e.textContent") == "Stani")
        page.click("#voice-listen")
        page.wait_for_timeout(80)
        check("panel button stops listening",
              page.evaluate("()=>!document.getElementById('voice-mic').classList.contains('rec')")
              and page.eval_on_selector("#voice-listen", "e=>e.textContent") == "Slusaj")
        page.click("#voice-listen")
        page.wait_for_timeout(80)
        check("panel button starts listening again",
              page.evaluate("()=>document.getElementById('voice-mic').classList.contains('rec')"))

        # --- Escape closes AND stops listening ---------------------------------
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)
        check("Escape closes the panel",
              page.evaluate("()=>!document.getElementById('voice-panel').classList.contains('open')"))
        check("Escape stops listening",
              page.evaluate("()=>!document.getElementById('voice-mic').classList.contains('rec')")
              and page.evaluate("()=>window.__vcRec.stops") >= 1)

        # --- the two panels are never open together ---------------------------
        page.click("#voice-mic")
        page.click("#askclaude")
        page.wait_for_timeout(100)
        check("opening Ask Claude closes the voice panel",
              page.evaluate("()=>document.getElementById('claude-panel').classList.contains('open')"
                            "&&!document.getElementById('voice-panel').classList.contains('open')"))
        page.click("#voice-mic")
        page.wait_for_timeout(100)
        check("opening the voice panel closes Ask Claude",
              page.evaluate("()=>document.getElementById('voice-panel').classList.contains('open')"
                            "&&!document.getElementById('claude-panel').classList.contains('open')"))
        box = page.eval_on_selector("#voice-panel", "e=>{var r=e.getBoundingClientRect();"
                                    "return {t:r.top,l:r.left,w:r.width,h:r.height};}")
        check("panel is on screen and sized",
              box["t"] >= 0 and box["l"] >= 0 and box["w"] > 300 and box["h"] > 200, json.dumps(box))
        check("no page errors during the whole flow", not errors, "; ".join(errors[:3]))
        browser.close()

        # ---------------- hud rows the PAGE performs: rescan + open_ticket ----
        plan2 = {"plan_id": "b" * 24, "note": "", "error": "", "steps": [
            {"idx": 0, "kind": "hud", "payload": {"action": "rescan", "arg": ""},
             "label": "Rescan tiketa", "approve_token": TOK[0],
             "expires": int(time.time()) + 600, "risk": "none", "refused": None},
            {"idx": 1, "kind": "hud", "payload": {"action": "open_ticket", "arg": "#8597"},
             "label": "Otvori tiket 8597", "approve_token": TOK[1],
             "expires": int(time.time()) + 600, "risk": "none", "refused": None}]}
        run2 = {"plan_id": "b" * 24, "results": [
            {"idx": 0, "kind": "hud", "status": "ok", "detail": "izvrsava HUD", "client": True},
            {"idx": 1, "kind": "hud", "status": "ok", "detail": "izvrsava HUD", "client": True}]}
        h2 = Harness(root, plan=plan2, run=run2, tickets=TICKETS)
        browser, page = open_page(pw, root, h2)
        errors2 = []
        page.on("pageerror", lambda e: errors2.append(str(e)))
        page.evaluate("()=>setMode('hud')")                  # start OUTSIDE the tickets view
        page.click("#voice-mic")
        page.fill("#voice-text", "uradi rescan i otvori tiket 8597")
        page.click("#voice-plan")
        page.wait_for_selector(".vc-step", timeout=4000)
        page.click("#voice-run")
        page.wait_for_timeout(1200)
        check("hud rescan switched to the tickets view first",
              page.evaluate("()=>document.body.className") == "v-tickets",
              page.evaluate("()=>document.body.className"))
        check("hud rescan posted /api/tickets/rescan (mocked, never a live server)",
              any(p == "/api/tickets/rescan" for p, _ in h2.posts),
              str([p for p, _ in h2.posts]))
        check("hud open_ticket opened the ticket modal",
              page.evaluate("()=>document.getElementById('tixmodal').classList.contains('open')"))
        check("hud rows report what the page did",
              "otvoren #8597" in page.eval_on_selector("#vc-res-1", "e=>e.textContent"),
              page.eval_on_selector("#vc-res-1", "e=>e.textContent"))
        check("no page errors in the hud-action flow", not errors2, "; ".join(errors2[:3]))
        browser.close()

        # ---------------- unsupported browser (no Web Speech) ----------------
        h3 = Harness(root)
        browser, page = open_page(pw, root, h3, speech=False)
        page.click("#voice-mic")
        page.wait_for_timeout(150)
        check("panel still opens without Web Speech",
              page.evaluate("()=>document.getElementById('voice-panel').classList.contains('open')"))
        check("the listen button is disabled, not dead-looking",
              page.eval_on_selector("#voice-listen", "e=>e.disabled"))
        hint = page.eval_on_selector("#voice-hint", "e=>e.textContent")
        check("the hint says Chromium only", "Chromium" in hint, hint)
        check("the plain text box is still offered",
              page.eval_on_selector("#voice-text", "e=>!e.disabled&&!e.readOnly"))
        check("the language picker is disabled too (it only steers recognition)",
              page.eval_on_selector("#voice-lang", "e=>e.disabled"))
        page.keyboard.press("Control+Shift+Space")
        page.wait_for_timeout(120)
        check("the shortcut reports the missing API instead of failing silently",
              "greska" in page.eval_on_selector("#voice-status", "e=>e.textContent"),
              page.eval_on_selector("#voice-status", "e=>e.textContent"))
        page.fill("#voice-text", "napravi belesku")
        page.click("#voice-plan")
        page.wait_for_selector(".vc-step", timeout=4000)
        check("the typed flow is identical (plan renders without a mic)",
              page.eval_on_selector_all(".vc-step", "els=>els.length") == 4)
        page.screenshot(path=str(shots / "03-no-speech.png"))
        browser.close()

        # ---------------- top bar: is the new entry point actually ON SCREEN? --
        # .top is one line with overflow-x:auto (wrapping it once overlapped the mail
        # toolbar), so a button added to it can end up past the right edge and reachable
        # only by scrolling the bar. Measured, not assumed - and the second check pins that
        # the overflow at 1440 is PRE-EXISTING, so the mic does not get blamed for it.
        h5 = Harness(root)
        browser = pw.chromium.launch()
        for w, want_visible in ((1920, True), (1600, True)):
            pg = browser.new_page(viewport={"width": w, "height": 900})
            pg.route("**/*", h5.handle)
            pg.add_init_script(FAKE_SPEECH)
            pg.goto("http://hud.test/index.html", wait_until="load")
            pg.wait_for_timeout(250)
            vis = pg.evaluate("()=>{var t=document.querySelector('.top'),"
                              "r=document.getElementById('voice-mic').getBoundingClientRect();"
                              "return r.right<=t.clientWidth+1&&r.left>=0;}")
            check("mic is fully visible in the top bar at %dpx" % w, vis == want_visible)
            pg.close()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        pg.route("**/*", h5.handle)
        pg.add_init_script(FAKE_SPEECH)
        pg.goto("http://hud.test/index.html", wait_until="load")
        pg.wait_for_timeout(250)
        over_with = pg.evaluate("()=>{var t=document.querySelector('.top');return t.scrollWidth-t.clientWidth;}")
        pg.evaluate("()=>{var m=document.getElementById('voice-mic');m.parentNode.removeChild(m);}")
        pg.wait_for_timeout(60)
        over_without = pg.evaluate("()=>{var t=document.querySelector('.top');return t.scrollWidth-t.clientWidth;}")
        check("the 1440px top-bar overflow is pre-existing, not caused by the mic",
              over_without > 0, "with mic %+d / without %+d" % (over_with, over_without))
        browser.close()

        # ---------------- empty / error states -------------------------------
        h4 = Harness(root, plan={"plan_id": "", "steps": [], "note": "",
                                 "error": "gemini nije dostupan"})
        browser, page = open_page(pw, root, h4)
        page.click("#voice-mic")
        page.fill("#voice-text", "bilo sta")
        page.click("#voice-plan")
        page.wait_for_timeout(400)
        st = page.eval_on_selector("#voice-status", "e=>e.textContent")
        check("a 503 from /plan is shown as an error, not as an empty plan",
              "greska" in st and "gemini nije dostupan" in st, st)
        check("no step rows are rendered on error",
              page.eval_on_selector_all(".vc-step", "els=>els.length") == 0)
        check("run stays disabled after a failed plan",
              page.eval_on_selector("#voice-run", "e=>e.disabled"))
        page.screenshot(path=str(shots / "04-plan-error.png"))
        # a model that returns no step at all
        h4.plan = {"plan_id": "c" * 24, "steps": [], "note": "nista za uraditi", "error": ""}
        page.fill("#voice-text", "bilo sta drugo")
        page.click("#voice-plan")
        page.wait_for_selector(".vc-empty", timeout=4000)
        check("an empty plan has a designed empty state",
              "nijedan korak" in page.eval_on_selector(".vc-empty", "e=>e.textContent"))
        browser.close()

    print("\nscreenshots: " + str(shots))
    bad = [n for n, ok, _ in _results if not ok]
    print("%d checks, %d failed" % (len(_results), len(bad)))
    for n in bad:
        print("  FAILED: " + n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
