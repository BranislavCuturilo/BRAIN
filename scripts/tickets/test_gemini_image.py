#!/usr/bin/env python3
"""Offline tests for gemini_client.generate_image — no network, no real key. Fakes
urlopen and sleep and points the usage counter at a throwaway file, exactly like
test_gemini_client.py. Run:  python test_gemini_image.py
(ASCII labels only; safe under a cp1252 console.)"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gemini_client as gc  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _img_body(mime, data, text=None):
    """A generateContent response carrying an inlineData image part (camelCase, as
    the REST API returns it), optionally preceded by a text part."""
    parts = []
    if text is not None:
        parts.append({"text": text})
    parts.append({"inlineData": {"mimeType": mime, "data": data}})
    return json.dumps({"candidates": [{"content": {"parts": parts}}]}).encode()


def _text_body(text):
    return json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode()


def _install(seq):
    """seq: list of ('img', mime, data[, text]) | ('text', text) | ('429',) |
    ('500',) consumed per urlopen call. Returns a dict recording urls/keys/bodies."""
    seen = {"urls": [], "keys": [], "bodies": [], "n": 0}
    it = iter(seq)

    def fake_urlopen(req, timeout=None):
        seen["n"] += 1
        seen["urls"].append(req.full_url)
        seen["keys"].append(req.get_header("X-goog-api-key"))
        try:
            seen["bodies"].append(json.loads(req.data.decode("utf-8")))
        except Exception:
            seen["bodies"].append(None)
        try:
            step = next(it)
        except StopIteration:
            step = ("429",)
        if step[0] == "img":
            return _Resp(_img_body(*step[1:]))
        if step[0] == "text":
            return _Resp(_text_body(step[1]))
        code = 429 if step[0] == "429" else 500
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, None)

    gc.urllib.request.urlopen = fake_urlopen
    return seen


def setup():
    tmp = Path(tempfile.mkdtemp(prefix="gcimg_"))
    gc._USAGE_FILE = tmp / ".gemini_usage.json"
    gc.MIN_INTERVAL = 0.0
    gc._last_call.clear()
    gc.time.sleep = lambda *_: None
    for k in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GEMINI_IMAGE_MODEL"):
        os.environ.pop(k, None)


def test_extracts_mime_and_data():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    seen = _install([("img", "image/png", "aW1nYnl0ZXM=")])
    mime, data = gc.generate_image("a red circle on white")
    ck("image: mime extracted", mime == "image/png")
    ck("image: base64 data extracted", data == "aW1nYnl0ZXM=")
    # Hit the IMAGE_MODEL endpoint, not the text default — assert the constant.
    ck("image: hit the IMAGE_MODEL endpoint", gc.IMAGE_MODEL in seen["urls"][0])
    ck("image: key on the header, never the URL",
       seen["keys"][0] == "KEYAAAA1" and "KEYAAAA1" not in seen["urls"][0])
    ck("image: one success counted (metered like every call)", gc.usage()["used"] == 1)


def test_body_asks_for_text_and_image_modalities():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    seen = _install([("img", "image/png", "eA==")])
    gc.generate_image("hello")
    body = seen["bodies"][0] or {}
    gen = body.get("generationConfig") or {}
    ck("image: responseModalities is [TEXT, IMAGE]",
       gen.get("responseModalities") == ["TEXT", "IMAGE"])
    parts = ((body.get("contents") or [{}])[0].get("parts")) or []
    ck("image: prompt is the single text part",
       len(parts) == 1 and parts[0].get("text") == "hello")


def test_image_part_after_text_part_still_found():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    _install([("img", "image/jpeg", "am1n", "here is your image:")])
    mime, data = gc.generate_image("x")
    ck("image: finds the inlineData part even after a leading text part",
       mime == "image/jpeg" and data == "am1n")


def test_text_only_response_raises():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    _install([("text", "I can't create that image.")])
    try:
        gc.generate_image("something")
        ck("image(text-only): raised", False)
    except gc.GeminiError as e:
        ck("image(text-only): raised GeminiError", True)
        ck("image(text-only): message carries no key", "KEYAAAA1" not in str(e))
    # A real API call was made and consumed quota, so it still counts.
    ck("image(text-only): the attempt was still counted", gc.usage()["used"] == 1)


def test_no_key_raises():
    setup()
    try:
        gc.generate_image("x")
        ck("image(no-key): raised", False)
    except gc.GeminiError:
        ck("image(no-key): raised GeminiError", True)


def test_shares_rotation_with_call():
    # generate_image rides the SAME _post_rotating loop as call(): a 429 on key1
    # rotates to key2, and only the success counts.
    setup()
    os.environ["GEMINI_API_KEYS"] = "AAAAkey1, BBBBkey2"
    seen = _install([("429",), ("img", "image/png", "b2s=")])
    mime, data = gc.generate_image("rotate please")
    ck("image(rotate): got the image from the second key",
       mime == "image/png" and data == "b2s=")
    ck("image(rotate): tried both keys in order",
       seen["keys"][0].endswith("key1") and seen["keys"][1].endswith("key2"))
    ck("image(rotate): only the success counted", gc.usage()["used"] == 1)


def test_model_override():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    seen = _install([("img", "image/png", "eA==")])
    gc.generate_image("x", model="some-other-image-model")
    ck("image(model): explicit model overrides IMAGE_MODEL",
       "some-other-image-model" in seen["urls"][0])


def main():
    for t in (test_extracts_mime_and_data,
              test_body_asks_for_text_and_image_modalities,
              test_image_part_after_text_part_still_found,
              test_text_only_response_raises, test_no_key_raises,
              test_shares_rotation_with_call, test_model_override):
        try:
            t()
        except Exception as exc:
            ck(t.__name__ + " (raised)", False)
            print(f"     {type(exc).__name__}: {exc}")
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        return 1
    print("all gemini_client image checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
