#!/usr/bin/env python3
"""One door for every Gemini call — ticket triage AND mail drafts share the free
API keys, so all calls go through here. It:

  * SPACES calls PER MODEL for the free-tier RPM (~15/min for the flash-lite
    workhorse) so a burst doesn't 429 (different models have separate limits, so
    they get separate spacing),
  * ROTATES across several keys: on a 429 it moves to the next key immediately and
    only backs off once EVERY key has been rate-limited in a round,
  * COUNTS successful calls per key per day (reset at midnight US-Pacific) in a
    small local file, and exposes usage() for the UI,
  * WARNS but NEVER BLOCKS on the daily cap — the user chose warn-not-block; the
    cap is a threshold for the indicator, not a gate, and a real 429 is the only
    hard stop.

Free-tier reference (per PROJECT, not per key — so extra keys only add quota when
they come from DIFFERENT Google Cloud projects):
  * gemini-flash-latest / 2.5-flash : the strong free workhorse (drafts, triage)
  * gemini-2.5-flash-lite           : higher limits, cheaper (summaries, distill)
  * gemini-2.5-pro                  : strongest, but a very thin free tier
Each model has its OWN daily bucket, so routing simple→lite / complex→flash draws
on two separate quotas. Override via env:
  GEMINI_API_KEY            one key
  GEMINI_API_KEYS           several keys (comma/space/newline separated) — rotated
  GEMINI_MODEL              default model (default gemini-flash-lite-latest)
  GEMINI_MODEL_LITE         cheap tier (default gemini-flash-lite-latest)
  GEMINI_MODEL_PRO          strong tier (default gemini-flash-latest)
  GEMINI_DAILY_CAP          per-key warn threshold (default 200); also the
                            default per-model cap for any model with no entry
                            in GEMINI_MODEL_CAPS
  GEMINI_MODEL_CAPS         per-model warn cap override, "model=cap,model=cap"
                            (ints); a model not listed falls back to
                            GEMINI_DAILY_CAP
  GEMINI_MIN_INTERVAL       seconds between calls to one model (default 4.5 —
                            the ~15 RPM workhorse ceiling is 4.0s; 4.5 ≈ 13/min
                            with margin)
  GEMINI_ALERT_AT           total daily calls that fire the one-per-day owner
                            alert hook (default 480)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
MODEL_LITE = os.environ.get("GEMINI_MODEL_LITE", "gemini-flash-lite-latest")
MODEL_PRO = os.environ.get("GEMINI_MODEL_PRO", "gemini-flash-latest")
# The free (~500/day) image model for generate_image. Its own daily bucket, like
# every other model here — so an image draw does not eat the text workhorse quota.
IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
DAILY_CAP = int(os.environ.get("GEMINI_DAILY_CAP", "200"))
MIN_INTERVAL = float(os.environ.get("GEMINI_MIN_INTERVAL", "4.5"))
ALERT_AT = int(os.environ.get("GEMINI_ALERT_AT", "480"))
TIMEOUT_S = 60.0


def _parse_model_caps(raw: str) -> dict:
    """Parse "model=cap,model=cap" (ints) into {model: cap}; malformed entries
    are skipped rather than raising, so a typo in the env degrades to the
    default cap instead of crashing every Gemini call."""
    out = {}
    for part in re.split(r"[,\s]+", (raw or "").strip()):
        if not part or "=" not in part:
            continue
        name, _, val = part.partition("=")
        name = name.strip()
        if not name:
            continue
        try:
            cap = int(val.strip())
        except ValueError:
            continue
        if cap <= 0:                       # 0/negative would mark the model over forever
            continue
        out[name] = cap
    return out


#: Per-model daily warn cap override (env GEMINI_MODEL_CAPS); a model with no
#: entry here uses DAILY_CAP (see model_cap()).
MODEL_CAPS = _parse_model_caps(os.environ.get("GEMINI_MODEL_CAPS", ""))


def model_cap(model: str) -> int:
    """The daily warn cap for one model: MODEL_CAPS override, else DAILY_CAP."""
    return MODEL_CAPS.get(model, DAILY_CAP)

_USAGE_FILE = Path(__file__).resolve().parent / ".gemini_usage.json"  # gitignored
_lock = threading.Lock()
_last_call: dict = {}          # model -> last-call timestamp (per-model RPM spacing)

#: An optional hook an embedder (the agent_view server) registers to be told the
#: FIRST time the daily call count crosses ALERT_AT — used to email the owner that
#: a bigger-quota model is due. gemini_client must NOT import the mail stack
#: (layering), so it only ever calls whatever registered here. Fired at most once
#: per Pacific day, guarded by the `alerted` flag persisted in the usage file.
on_threshold_crossed = None    # fn(used: int, cap: int) -> None


class GeminiError(RuntimeError):
    """A Gemini call failed. Message carries a status/kind, never the key."""


def _endpoint(model: str) -> str:
    return ("https://generativelanguage.googleapis.com/v1beta/models/"
            + model + ":generateContent")


def keys() -> list:
    """Every configured key, in rotation order. GEMINI_API_KEYS (several, any of
    comma/space/newline separated) takes precedence; else the single
    GEMINI_API_KEY; else empty. Extra keys only add real quota when they come
    from different Google Cloud projects — same-project keys share one bucket."""
    multi = (os.environ.get("GEMINI_API_KEYS") or "").strip()
    if multi:
        ks = [k.strip() for k in re.split(r"[,\s]+", multi) if k.strip()]
        if ks:
            return ks
    one = (os.environ.get("GEMINI_API_KEY") or "").strip()
    return [one] if one else []


def key() -> str:
    """The first configured key (back-compat for callers that want just one)."""
    ks = keys()
    return ks[0] if ks else ""


def model_for(complexity) -> str:
    """Map a complexity hint to a model: simple → lite (cheap, separate bucket),
    hard → pro, otherwise the strong default. Lets a caller opt into routing
    without hardcoding a model name."""
    c = str(complexity or "").upper()
    if c in ("S", "LITE", "SIMPLE", "LOW", "CHEAP"):
        return MODEL_LITE
    if c in ("L", "PRO", "COMPLEX", "HARD", "HIGH"):
        return MODEL_PRO
    return MODEL


def _key_id(k: str) -> str:
    """A short, non-sensitive id for a key (its last 4 chars) — enough to tell
    keys apart in the usage breakdown, never enough to reconstruct the key."""
    k = (k or "").strip()
    return k[-4:] if len(k) >= 4 else (k or "----")


def _pacific_day() -> str:
    # Google resets the daily quota at midnight US-Pacific. UTC-8 is a good-enough
    # boundary for a warn counter (a DST hour of slack on a threshold is harmless).
    return (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%d")


def _load_usage() -> dict:
    """Load today's usage, per key id per model: {"day", "keys": {key_id:
    {model: n}}, "alerted"}. A key_id whose value is still the OLD shape (a bare
    int total, no model dimension) is folded into a "_legacy" pseudo-model so an
    upgrade never loses today's count."""
    try:
        d = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    if not isinstance(d, dict) or d.get("day") != _pacific_day():
        d = {"day": _pacific_day(), "keys": {}}   # new day / unreadable → reset
    keys_in = d.get("keys")
    keys_out = {}
    for kid, v in (keys_in.items() if isinstance(keys_in, dict) else []):
        if isinstance(v, dict):
            keys_out[kid] = {str(m): int(n) for m, n in v.items()
                             if isinstance(n, (int, float))}
        elif isinstance(v, (int, float)):
            keys_out[kid] = {"_legacy": int(v)}   # old shape: key_id -> int
    d["keys"] = keys_out
    return d


def _save_usage(d: dict) -> None:
    try:
        tmp = _USAGE_FILE.with_name(_USAGE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, _USAGE_FILE)
    except Exception:
        pass


def _reset_at_iso() -> str:
    """ISO-8601 LOCAL timestamp of the next Google quota reset: midnight
    US-Pacific for the Pacific day AFTER the one _pacific_day() reports now
    (same UTC-8 approximation _pacific_day uses), converted to local time."""
    d = datetime.strptime(_pacific_day(), "%Y-%m-%d")
    next_midnight_utc = datetime(d.year, d.month, d.day, 8, 0,
                                 tzinfo=timezone.utc) + timedelta(days=1)
    return next_midnight_utc.astimezone().isoformat()


def usage() -> dict:
    """Current usage for the UI indicator. `used` is the total of SUCCESSFUL calls
    today across all keys; `cap` scales with the number of keys; `over` is only a
    WARN flag (calls still go through). `per_key` breaks it down by key id (ints,
    summed over models — unchanged shape for existing callers). `per_model` adds
    the per-model breakdown: every model with calls today, PLUS MODEL/MODEL_LITE/
    MODEL_PRO even at zero, so the indicator always has a row for each configured
    tier. `reset_at` / `min_interval` are exposed for the same indicator."""
    d = _load_usage()
    per_key_models = d.get("keys") or {}
    per = {kid: sum(models.values()) for kid, models in per_key_models.items()}
    used = sum(per.values())
    n = max(len(keys()), 1)
    cap = DAILY_CAP * n
    totals = {}
    for models in per_key_models.values():
        for m, c in models.items():
            totals[m] = totals.get(m, 0) + c
    for m in (MODEL, MODEL_LITE, MODEL_PRO):
        totals.setdefault(m, 0)
    # Per-model cap scales with the key count like the total does (used is summed
    # across keys), so the pill and the total can never disagree on "over".
    # Pseudo-models (the "_legacy" bucket from a pre-per-model day) are counted
    # in the totals but not shown as models.
    per_model = {m: {"used": c, "cap": model_cap(m) * n, "over": c >= model_cap(m) * n}
                 for m, c in totals.items() if not m.startswith("_")}
    return {"used": used, "cap": cap, "day": d.get("day"),
            "over": used >= cap, "per_key": per, "keys": n,
            "per_model": per_model, "reset_at": _reset_at_iso(),
            "min_interval": MIN_INTERVAL}


def _space(model: str) -> None:
    """RPM spacing, per model — different models have independent rate limits, so
    a flash call never has to wait on a lite call."""
    with _lock:
        wait = MIN_INTERVAL - (time.time() - _last_call.get(model, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[model] = time.time()


def _count(key_id: str, model: str = "") -> None:
    """Record one successful call for (key_id, model). `model` defaults to the
    module MODEL constant (read at call time, not bind time) so a legacy caller
    passing only key_id keeps working."""
    mdl = model or MODEL
    fire = None
    with _lock:
        d = _load_usage()
        per_key = d["keys"].setdefault(key_id, {})
        per_key[mdl] = int(per_key.get(mdl, 0)) + 1
        # Claim the day's ONE threshold alert atomically, under the same lock and
        # in the same file as the count — so a burst of concurrent calls can never
        # fire it twice, and a failed send never re-arms it (at-most-once). The
        # `alerted` flag rides in the day's usage dict; a new Pacific day resets it
        # along with the counts (see _load_usage). Fired on the TOTAL across every
        # model — the alert is about overall daily volume, not one model's bucket.
        used = sum(sum(models.values()) for models in d["keys"].values())
        cap = DAILY_CAP * max(len(keys()), 1)
        if used >= ALERT_AT and not d.get("alerted"):
            d["alerted"] = True
            fire = (used, cap)
        _save_usage(d)                           # count is advisory (warn-only)
    # Fire OUTSIDE the lock (the hook may do slow I/O — an SMTP send). Swallow
    # everything: an alert must never break the AI call that triggered it.
    if fire is not None and on_threshold_crossed is not None:
        try:
            on_threshold_crossed(*fire)
        except Exception:
            pass


def _extract(resp, json_out: bool):
    cand = (resp.get("candidates") or [{}])[0] if isinstance(resp, dict) else {}
    parts = ((cand.get("content") or {}).get("parts") or [{}])
    text = (parts[0].get("text") if parts else "") or ""
    if not json_out:
        return text
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        raise GeminiError("model did not return JSON") from None


def _extract_image(resp):
    """Find the first inline image part in a generateContent response and return
    (mime_type, base64_data). Google returns the image as an `inlineData` part
    (camelCase in the REST response); snake_case is accepted too, defensively.
    Raises GeminiError when there is no image part — a text-only reply, a refusal,
    or a quota message all land here rather than returning junk."""
    cand = (resp.get("candidates") or [{}])[0] if isinstance(resp, dict) else {}
    parts = (cand.get("content") or {}).get("parts") or []
    for p in parts:
        if not isinstance(p, dict):
            continue
        inline = p.get("inlineData") or p.get("inline_data")
        if isinstance(inline, dict) and inline.get("data"):
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            return (str(mime), str(inline["data"]))
    raise GeminiError("no image in response")


def _post_rotating(model: str, data: bytes, extract, *, api_key: str = ""):
    """The ONE budget/rate gate, shared by call() and generate_image(): POST the
    JSON body `data` to `model`'s generateContent endpoint, rotate across keys on
    429 (sleeping only once every key has 429'd in a round), space per model for
    the RPM ceiling, count a real success, and return extract(resp). Raises
    GeminiError on failure. Both callers ride this single loop so the rotation /
    counting / back-off logic can never fork into a second, drifting copy."""
    ks = [api_key] if api_key else keys()
    ks = [k for k in ks if k]
    if not ks:
        raise GeminiError("no GEMINI_API_KEY")
    ep = _endpoint(model)
    last_err = None
    for rnd in range(3):
        for k in ks:
            _space(model)
            req = urllib.request.Request(ep, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("X-goog-api-key", k)   # key on the header, never the URL
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                _count(_key_id(k), model)         # count only a real success
                return extract(resp)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    last_err = GeminiError("HTTP 429")
                    continue                      # this key is throttled → next key
                raise GeminiError(f"HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                raise GeminiError(exc.__class__.__name__) from None
        time.sleep(5 * (2 ** rnd))                # every key 429'd → back off, retry
    raise last_err or GeminiError("rate-limited")


def call(prompt: str, *, json_out: bool = True, temperature: float = 0.2,
         api_key: str = "", model: str = None, files=None) -> object:
    """One Gemini call through the budget/rate gate. Returns the parsed JSON
    object when json_out, else the raw text. Raises GeminiError on failure.

    Rotates across every configured key: on a 429 it tries the next key at once,
    and only sleeps (backing off) once every key has 429'd in a round. Spaces
    calls per model for RPM. Never blocks on the daily cap — only counts.

    `api_key` pins a single key (bypasses rotation); `model` overrides the model
    for this one call (e.g. gemini_client.MODEL_LITE for a cheap summary).

    `files` is an optional list of {"mime": <str>, "data": <base64 str>}: each is
    appended as an inline_data part alongside the text part, so the model reads
    an attachment (a PDF, image, or text file) inline. `files=None` (the default)
    produces the exact same request body as before — every existing caller is
    unchanged."""
    mdl = model or MODEL
    gen = {"temperature": temperature}
    if json_out:
        gen["responseMimeType"] = "application/json"
    parts = [{"text": prompt}]
    for f in files or []:                          # files=None → parts unchanged
        if not isinstance(f, dict):
            continue
        mime, b64 = f.get("mime"), f.get("data")
        if not mime or not b64:
            continue
        parts.append({"inline_data": {"mime_type": str(mime), "data": str(b64)}})
    data = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": gen}).encode("utf-8")
    return _post_rotating(mdl, data, lambda resp: _extract(resp, json_out),
                          api_key=api_key)


def generate_image(prompt: str, *, api_key: str = "", model: str = None):
    """Generate an image from a text prompt, through the SAME budget/rate gate as
    call() (one door): it rotates across keys, spaces per model, and COUNTS toward
    the day's usage — the image model is the ~500/day free tier, so this is metered
    like every other call. Returns (mime_type, base64_data) of the first inline
    image part; raises GeminiError when the model returns no image part (quota,
    refusal, or a text-only reply).

    Model defaults to IMAGE_MODEL (env GEMINI_IMAGE_MODEL). The request asks for
    both modalities — this model requires TEXT alongside IMAGE."""
    mdl = model or IMAGE_MODEL
    data = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }).encode("utf-8")
    return _post_rotating(mdl, data, _extract_image, api_key=api_key)
