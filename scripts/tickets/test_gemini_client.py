#!/usr/bin/env python3
"""Offline tests for gemini_client — no network, no real key. Fakes urlopen and
sleep, and points the usage counter at a throwaway file. Run: python
test_gemini_client.py"""
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


def _ok_body(text):
    return json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode()


def _install(seq):
    """seq: list of ('ok', text) | ('429',) | ('500',) consumed per urlopen call.
    Returns a dict recording the endpoints and keys seen."""
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
        if step[0] == "ok":
            return _Resp(_ok_body(step[1]))
        code = 429 if step[0] == "429" else 500
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, None)

    gc.urllib.request.urlopen = fake_urlopen
    return seen


def setup():
    tmp = Path(tempfile.mkdtemp(prefix="gcusage_"))
    gc._USAGE_FILE = tmp / ".gemini_usage.json"
    gc.MIN_INTERVAL = 0.0
    gc._last_call.clear()
    gc.time.sleep = lambda *_: None          # no real backoff waits
    for k in ("GEMINI_API_KEY", "GEMINI_API_KEYS"):
        os.environ.pop(k, None)


def test_single_key_success():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    seen = _install([("ok", '{"x": 1}')])
    out = gc.call("hi", json_out=True)
    ck("single: parsed JSON returned", out == {"x": 1})
    # Assert against the module's own default constant, not a re-typed literal —
    # the default model changed to gemini-flash-lite-latest and a hardcoded
    # "gemini-flash-latest" here silently drifted from it.
    ck("single: hit the default-model endpoint", gc.MODEL in seen["urls"][0])
    ck("single: key on the header", seen["keys"][0] == "KEYAAAA1")
    ck("single: one success counted", gc.usage()["used"] == 1)


def test_rotates_on_429():
    setup()
    os.environ["GEMINI_API_KEYS"] = "AAAAkey1, BBBBkey2"
    seen = _install([("429",), ("ok", '{"ok": true}')])   # key1 429 → key2 ok
    out = gc.call("hi")
    ck("rotate: got second key's result", out == {"ok": True})
    ck("rotate: tried both keys in order",
       seen["keys"][0].endswith("key1") and seen["keys"][1].endswith("key2"))
    u = gc.usage()
    ck("rotate: only the success counted (used=1)", u["used"] == 1)
    ck("rotate: cap scales with 2 keys", u["cap"] == gc.DAILY_CAP * 2)
    ck("rotate: per-key credited the winner", u["per_key"].get("key2") == 1)


def test_all_keys_429_raises():
    setup()
    os.environ["GEMINI_API_KEYS"] = "AAAAkey1 BBBBkey2"
    _install([("429",)] * 12)                 # every key, every round → 429
    try:
        gc.call("hi")
        ck("all-429: raised", False)
    except gc.GeminiError as e:
        ck("all-429: raised GeminiError", True)
        ck("all-429: message is the status, not a key", "429" in str(e) and "key" not in str(e))
    ck("all-429: nothing counted", gc.usage()["used"] == 0)


def test_model_override_and_non429_raises():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    seen = _install([("ok", "plain text")])
    out = gc.call("hi", json_out=False, model=gc.MODEL_LITE)
    ck("model: lite endpoint used", gc.MODEL_LITE in seen["urls"][0])
    ck("model: raw text returned when json_out=False", out == "plain text")

    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    _install([("500",)])
    try:
        gc.call("hi")
        ck("non-429: raised", False)
    except gc.GeminiError as e:
        ck("non-429: HTTP 500 surfaced as GeminiError", "500" in str(e))


def test_keys_parsing_and_model_for():
    setup()
    os.environ["GEMINI_API_KEYS"] = "a1,  b2 \n c3"
    ck("keys: split on comma/space/newline", gc.keys() == ["a1", "b2", "c3"])
    ck("keys: key() is the first", gc.key() == "a1")
    ck("model_for: simple -> lite", gc.model_for("S") == gc.MODEL_LITE)
    ck("model_for: hard -> pro", gc.model_for("L") == gc.MODEL_PRO)
    ck("model_for: default -> flash", gc.model_for("M") == gc.MODEL)


def test_no_key_raises():
    setup()
    try:
        gc.call("hi")
        ck("no-key: raised", False)
    except gc.GeminiError:
        ck("no-key: raised GeminiError", True)


def _parts(seen, i=0):
    body = seen["bodies"][i] if seen["bodies"] else {}
    return (((body or {}).get("contents") or [{}])[0].get("parts")) or []


def test_files_none_is_backcompat_text_only():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    seen = _install([("ok", "plain")])
    gc.call("hello", json_out=False)                      # no files arg at all
    parts = _parts(seen)
    ck("files(none): request has exactly one part", len(parts) == 1)
    ck("files(none): that part is the text prompt",
       parts and parts[0].get("text") == "hello" and "inline_data" not in parts[0])


def test_files_are_appended_as_inline_data_parts():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    seen = _install([("ok", "plain")])
    files = [{"mime": "application/pdf", "data": "QkFTRTY0"},
             {"mime": "image/png", "data": "aW1n"}]
    gc.call("read these", json_out=False, files=files)
    parts = _parts(seen)
    ck("files: text part kept first", parts and parts[0].get("text") == "read these")
    ck("files: two inline_data parts appended", len(parts) == 3)
    inl = [p.get("inline_data") for p in parts if "inline_data" in p]
    ck("files: first inline part is the pdf with mime_type+data",
       bool(inl) and inl[0] == {"mime_type": "application/pdf", "data": "QkFTRTY0"})
    ck("files: second inline part is the png",
       len(inl) == 2 and inl[1] == {"mime_type": "image/png", "data": "aW1n"})


def test_files_malformed_entries_are_skipped():
    setup()
    os.environ["GEMINI_API_KEY"] = "KEYAAAA1"
    seen = _install([("ok", "plain")])
    # A non-dict, a dict missing data, and a dict missing mime — all dropped; the
    # one well-formed entry survives, so a bad attachment never corrupts the body.
    gc.call("x", json_out=False,
            files=["nope", {"mime": "application/pdf"}, {"data": "abc"},
                   {"mime": "text/plain", "data": "b2s="}])
    parts = _parts(seen)
    inl = [p.get("inline_data") for p in parts if "inline_data" in p]
    ck("files(malformed): only the one valid inline part survives", len(inl) == 1)


def test_count_records_per_model():
    setup()
    gc._count("k1", "model-a")
    gc._count("k1", "model-a")
    gc._count("k1", "model-b")
    d = gc._load_usage()
    ck("count: two calls to model-a recorded", d["keys"]["k1"]["model-a"] == 2)
    ck("count: one call to model-b recorded", d["keys"]["k1"]["model-b"] == 1)
    u = gc.usage()
    ck("usage: per_model used correct for model-a",
       u["per_model"].get("model-a", {}).get("used") == 2)
    ck("usage: per_key sums across models", u["per_key"].get("k1") == 3)
    ck("usage: used is the total across all models/keys", u["used"] == 3)


def test_legacy_shape_tolerated():
    setup()
    # The OLD usage file shape: key_id -> bare int (no model dimension).
    gc._USAGE_FILE.write_text(
        json.dumps({"day": gc._pacific_day(), "keys": {"k1": 5}}), encoding="utf-8")
    d = gc._load_usage()
    ck("legacy: key_id int mapped to a _legacy pseudo-model",
       d["keys"]["k1"] == {"_legacy": 5})
    u = gc.usage()
    ck("legacy: per_key total preserved", u["per_key"].get("k1") == 5)
    ck("legacy: used counts the legacy total", u["used"] == 5)


def test_usage_per_model_defaults_present_at_zero():
    setup()
    u = gc.usage()
    for m in (gc.MODEL, gc.MODEL_LITE, gc.MODEL_PRO):
        ck(f"usage: per_model has an entry for {m}", m in u["per_model"])
    ck("usage: with no calls made, every default entry starts at zero",
       all(u["per_model"][m]["used"] == 0
           for m in (gc.MODEL, gc.MODEL_LITE, gc.MODEL_PRO)))


def test_parse_model_caps_from_string():
    # newline built with chr(10), not a literal escape, to sidestep no shell/
    # heredoc transport risk in how this test file gets written.
    raw = "m1=1, m2=2" + chr(10) + "m3=3"
    parsed = gc._parse_model_caps(raw)
    ck("parse_model_caps: parses comma/space/newline separated pairs",
       parsed == {"m1": 1, "m2": 2, "m3": 3})
    ck("parse_model_caps: a malformed entry is skipped, not fatal",
       gc._parse_model_caps("bad, m1=notanint, m2=5") == {"m2": 5})


def test_model_cap_default_and_override():
    setup()
    ck("model_cap: an unlisted model falls back to DAILY_CAP",
       gc.model_cap("some-unlisted-model") == gc.DAILY_CAP)
    old_caps = gc.MODEL_CAPS
    gc.MODEL_CAPS = gc._parse_model_caps("model-a=5,model-b=10")
    try:
        ck("model_cap: override applies to model-a", gc.model_cap("model-a") == 5)
        ck("model_cap: override applies to model-b", gc.model_cap("model-b") == 10)
        ck("model_cap: a model outside the override still falls back",
           gc.model_cap("model-c") == gc.DAILY_CAP)
    finally:
        gc.MODEL_CAPS = old_caps


def test_reset_at_is_iso_and_in_the_future():
    setup()
    u = gc.usage()
    from datetime import datetime as _dt
    parsed = _dt.fromisoformat(u["reset_at"])   # raises if not valid ISO-8601
    now_local = _dt.now().astimezone()
    ck("reset_at: parses as ISO-8601", True)
    ck("reset_at: is in the future", parsed > now_local)
    ck("min_interval: echoes the module constant", u["min_interval"] == gc.MIN_INTERVAL)


def main():
    for t in (test_single_key_success, test_rotates_on_429,
              test_all_keys_429_raises, test_model_override_and_non429_raises,
              test_keys_parsing_and_model_for, test_no_key_raises,
              test_files_none_is_backcompat_text_only,
              test_files_are_appended_as_inline_data_parts,
              test_files_malformed_entries_are_skipped,
              test_count_records_per_model, test_legacy_shape_tolerated,
              test_usage_per_model_defaults_present_at_zero,
              test_parse_model_caps_from_string,
              test_model_cap_default_and_override,
              test_reset_at_is_iso_and_in_the_future):
        t()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        return 1
    print("all gemini_client checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
