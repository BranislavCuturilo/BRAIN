#!/usr/bin/env python3
"""How much of the Claude plan's limit is burned -- the real number, not an estimate.

**Where the number comes from.** The same GET Claude Code itself makes for
`/usage`: `https://api.anthropic.com/api/oauth/usage` with the OAuth token
Claude Code stores in `~/.claude/.credentials.json`. It returns the 5-hour and
7-day windows as a percentage with a reset time. Nothing here is computed from
transcripts; `ccusage`-style cost estimates are a different thing.

**What this is not.** Not a published API (Anthropic documents an Admin API
that does not carry this). The response shape is pinned by `test_usage_limit.py`
on a real captured payload, so a change upstream goes red here first. Using the
token outside Claude Code is tolerated (30+ tools do it), not sanctioned -- the
owner's call, recorded in the journal.

**Rules it lives by.**
- The token is READ, never refreshed (Claude Code owns it), never printed,
  never sent anywhere but api.anthropic.com. An expired token means "run
  claude"; it is not this script's job to mint one.
- Named windows first, `limits[]` second. A window drops out of `limits[]` the
  moment its `resets_at` passes, so a reader of the array alone loses the
  session at exactly the reset (Codenotch's recorded lesson).
- 429 is a normal state. Poll floor 60 s; on 429 the floor doubles per
  consecutive hit up to 15 min; `Retry-After` may only RAISE the floor,
  because the endpoint answers `Retry-After: 0`.
- Never block a render. `--statusline` first reads the `rate_limits` object
  Claude Code 2.1.x itself puts in the status line's stdin JSON (the same
  numbers, no token, no network); only without it does it fall back to the
  cache, fetching when that is older than the floor, with a short timeout.

  usage_limit.py               human-readable
  usage_limit.py --json        for health.py / the dashboard
  usage_limit.py --statusline  one line, reads Claude Code's stdin JSON if given
  usage_limit.py --force       ignore the poll floor (not the backoff)
  usage_limit.py --no-fetch    cache only
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"
CACHE = ROOT / "journal" / "usage-limit.json"       # gitignored: derived, per machine
POLL_FLOOR = 60
BACKOFF_CAP = 900
STALE_AFTER = 900
#: Friendly labels for the windows worth naming. Everything else the endpoint
#: returns is DISCOVERED, not listed -- see `parse`. A hardcoded tuple silently
#: dropped `nimbus_quill`, a live window with a real utilisation, for as long as
#: this file existed; the endpoint invents codename windows (tangelo,
#: iguana_necktie, cinder_cove, copper_kite, amber_ladder) and a list can only
#: ever be behind.
WINDOWS = (("five_hour", "session", "5h"), ("seven_day", "weekly_all", "7d"),
           ("seven_day_opus", "weekly_opus", "7d opus"), ("seven_day_sonnet", "weekly_sonnet", "7d sonnet"))
KNOWN = {n: l for n, _k, l in WINDOWS}

#: NOT a window, and NOT visible here at all: the `/low-priority` ("slow")
#: budget. It is carried only on message-response headers
#: (anthropic-ratelimit-unified-slow-budget-utilization / -reset), so nothing
#: reading this endpoint can show it. Said out loud because an absent number
#: looks the same as a zero one.
#:
#: `juniper_tide` IS here, and it is the `/limit-reset` signal: non-null means
#: the once-a-week session reset is in play for this account.
LIMIT_RESET_KEY = "juniper_tide"

#: What Claude Code hands the status line, kept per session for the HUD: the
#: model, the context used, the cost and lines. `rate_limits` is dropped (the
#: usage cache holds it) and nothing here is a secret. Gitignored, per machine.
STATUS_DIR = ROOT / "journal" / "statusline"
STATUS_KEEP = ("session_id", "cwd", "model", "version", "cost", "context_window",
               "exceeds_200k_tokens", "output_style", "workspace")
STATUS_TTL = 86400          # a file older than a day is a session long gone
SESSION_MAX_AGE = 3600      # what the HUD lists: sessions that rendered in the last hour
_SID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def credentials_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / ".credentials.json"


def read_token(path: Path | None = None, now: float | None = None) -> tuple[str, str]:
    """(token, problem). The token is returned to the caller only; nothing
    here logs it. `problem` is a sentence when there is no usable token."""
    p = path or credentials_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", f"no credentials at {p} -- run `claude` and log in"
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return "", "credentials carry no claudeAiOauth.accessToken"
    exp = oauth.get("expiresAt")
    if isinstance(exp, (int, float)) and exp / 1000 < (now if now is not None else time.time()):
        return "", "the access token has expired -- run `claude` once and it refreshes it"
    return str(oauth["accessToken"]), ""


def parse(payload: dict) -> dict:
    """Every window the endpoint reports, plus what `/limit-reset` needs.

    DISCOVERED, not listed: any top-level object carrying a non-null
    `utilization` is a window. The named ones get a friendly label and the rest
    keep their key. The previous version walked a fixed tuple and therefore
    never saw `nimbus_quill` -- a live window with a limit and a reset time --
    which is the same failure shape as reading the wrong binary: silence that
    looks like a zero."""
    out: dict = {"windows": {}}
    by_kind = {}
    for lim in payload.get("limits") or []:
        if isinstance(lim, dict) and lim.get("kind"):
            by_kind[lim["kind"]] = lim

    for key, obj in (payload or {}).items():
        if key == LIMIT_RESET_KEY or not isinstance(obj, dict):
            continue
        if obj.get("utilization") is None:
            continue
        out["windows"][key] = {"label": KNOWN.get(key, key),
                               "percent": round(float(obj["utilization"])),
                               "resets_at": obj.get("resets_at")}
    # A named window the endpoint left null can still be in limits[]: a window
    # drops out of that array at its reset, so the two fill each other's gaps.
    for named, kind, label in WINDOWS:
        if named in out["windows"] or kind not in by_kind:
            continue
        lim = by_kind[kind]
        if lim.get("percent") is not None:
            out["windows"][named] = {"label": label, "percent": round(float(lim["percent"])),
                                     "resets_at": lim.get("resets_at")}

    # `/limit-reset` — undocumented, hidden, server-gated. Its availability is
    # the only thing this endpoint says about it.
    jt = (payload or {}).get(LIMIT_RESET_KEY)
    out["limit_reset"] = {"offered": jt is not None,
                          "detail": jt if isinstance(jt, dict) else None}
    return out


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def load_cache(path: Path = CACHE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(data: dict, path: Path = CACHE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def next_floor(consecutive_429: int, retry_after: str | None) -> int:
    """Seconds to wait after the Nth consecutive 429. Retry-After may only
    raise it: the endpoint answers `Retry-After: 0`, and obeying that keeps
    you rate-limited forever."""
    floor = min(POLL_FLOOR * (2 ** max(consecutive_429 - 1, 0)), BACKOFF_CAP)
    try:
        ra = int(float(retry_after)) if retry_after not in (None, "") else 0
    except ValueError:
        ra = 0
    return max(floor, min(ra, BACKOFF_CAP))


def http_get(token: str, timeout: float) -> tuple[int, dict, str | None]:
    """(status, json, retry_after). Network errors surface as status 0."""
    req = urllib.request.Request(ENDPOINT, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": BETA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}"), r.headers.get("Retry-After")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:                                       # noqa: BLE001
            body = {}
        return e.code, body, e.headers.get("Retry-After") if e.headers else None
    except Exception:                                           # noqa: BLE001
        return 0, {}, None


def refresh(*, force: bool = False, fetch=http_get, timeout: float = 10.0,
            cache_path: Path = CACHE, creds: Path | None = None, now: float | None = None) -> dict:
    """The cache, refreshed when the floor allows. Always returns something
    printable: `state` is ok | stale | backoff | needs_auth | error | empty."""
    t = now if now is not None else time.time()
    c = load_cache(cache_path)
    floor_until = float(c.get("floor_until") or 0)
    fresh = c.get("fetched_at") and t - float(c["fetched_at"]) < POLL_FLOOR
    if (fresh and not force) or t < floor_until:
        c["state"] = "backoff" if t < floor_until and not c.get("windows") else c.get("state", "stale")
        if c.get("windows") and t - float(c.get("fetched_at") or 0) >= STALE_AFTER:
            c["state"] = "stale"
        return c

    token, problem = read_token(creds, now=t)
    if not token:
        c.update({"state": "needs_auth", "problem": problem, "checked_at": t})
        save_cache(c, cache_path)
        return c

    status, body, retry_after = fetch(token, timeout)
    if status == 200 and isinstance(body, dict):
        parsed = parse(body)
        # No floor after a success: freshness (POLL_FLOOR) throttles the normal
        # path and --force may override it; the floor is for 429 and errors.
        c = {"state": "ok", "fetched_at": t, "fetched_iso": _iso(t), "windows": parsed["windows"],
             "limit_reset": parsed.get("limit_reset") or {"offered": False},
             "consecutive_429": 0, "floor_until": 0}
        if not parsed["windows"]:
            c["state"] = "error"
            c["problem"] = "200 but no window this understands -- the response shape moved; see test_usage_limit.py"
    elif status == 429:
        n = int(c.get("consecutive_429") or 0) + 1
        wait = next_floor(n, retry_after)
        c.update({"state": "backoff", "consecutive_429": n, "floor_until": t + wait, "checked_at": t,
                  "problem": f"rate-limited (429 x{n}); next try in {wait}s"})
    elif status in (401, 403):
        c.update({"state": "needs_auth", "checked_at": t, "floor_until": t + POLL_FLOOR,
                  "problem": f"HTTP {status}: the token was refused -- run `claude` once"})
    else:
        c.update({"state": "stale" if c.get("windows") else "error", "checked_at": t,
                  "floor_until": t + POLL_FLOOR,
                  "problem": f"HTTP {status or 'network error'}"})
    save_cache(c, cache_path)
    return c


def from_statusline(sl: dict, now: float | None = None) -> dict | None:
    """The numbers Claude Code hands the status line itself (2.1.x: `rate_limits`
    with five_hour / seven_day, used_percentage 0-100, resets_at). None when
    the payload has none -- older Claude Code, API-key auth, or no subscription."""
    rl = (sl or {}).get("rate_limits") if isinstance(sl, dict) else None
    if not isinstance(rl, dict):
        return None
    windows = {}
    for named, _kind, label in WINDOWS:
        x = rl.get(named)
        if isinstance(x, dict) and x.get("used_percentage") is not None:
            windows[named] = {"label": label, "percent": round(float(x["used_percentage"])),
                              "resets_at": x.get("resets_at")}
    if not windows:
        return None
    return {"state": "ok", "source": "statusline", "fetched_at": now if now is not None else time.time(),
            "windows": windows}


def record_statusline(sl: dict, path: Path = STATUS_DIR, now: float | None = None) -> Path | None:
    """One file per session with the status-line payload's useful part. The
    session id is the file name, so it is validated as a bare token first --
    a payload is input, and "../x" is not a session."""
    sid = str((sl or {}).get("session_id") or "").strip()
    if not sid or not _SID.fullmatch(sid):
        return None
    t = now if now is not None else time.time()
    rec = {k: sl.get(k) for k in STATUS_KEEP if k in sl}
    rec["updated_at"] = t
    try:
        path.mkdir(parents=True, exist_ok=True)
        fp = path / f"{sid}.json"
        fp.write_text(json.dumps(rec), encoding="utf-8")
        # Prune by the wall clock against the file's mtime -- not by `now`,
        # which tests inject; and never the file just written.
        real = time.time()
        for old in path.glob("*.json"):
            if old == fp:
                continue
            try:
                if real - old.stat().st_mtime > STATUS_TTL:
                    old.unlink()
            except OSError:
                pass
        return fp
    except OSError:
        return None


def sessions(path: Path = STATUS_DIR, max_age: float = SESSION_MAX_AGE, now: float | None = None) -> list:
    """The sessions that rendered a status line recently, newest first, in
    the flat shape the HUD draws: folder, model, context %, cost, lines."""
    t = now if now is not None else time.time()
    out = []
    for fp in path.glob("*.json") if path.is_dir() else []:
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        age = t - float(rec.get("updated_at") or 0)
        if age < 0 or age > max_age:
            continue
        cw = rec.get("context_window") or {}
        cost = rec.get("cost") or {}
        model = rec.get("model") or {}
        out.append({
            "session_id": rec.get("session_id"),
            "cwd": rec.get("cwd") or (rec.get("workspace") or {}).get("current_dir") or "",
            "model": model.get("display_name") or model.get("id") or "",
            "model_id": model.get("id") or "",
            "version": rec.get("version") or "",
            "age_s": int(age),
            "ctx_pct": cw.get("used_percentage"),
            "ctx_size": cw.get("context_window_size"),
            "cost_usd": cost.get("total_cost_usd"),
            "duration_ms": cost.get("total_duration_ms"),
            "lines_added": cost.get("total_lines_added"),
            "lines_removed": cost.get("total_lines_removed"),
            "over_200k": bool(rec.get("exceeds_200k_tokens")),
        })
    out.sort(key=lambda r: r["age_s"])
    return out


TAIL_BYTES = 262144      # the last quarter-megabyte of a transcript holds its last turn


def transcript_session(path, tail_bytes: int = TAIL_BYTES, now: float | None = None) -> dict | None:
    """What the transcript's LAST assistant record says: model, the tokens in
    context (input + cache read + cache created -- what the next turn carries),
    effort, entrypoint, version, cwd, age. The status line does not render in
    the VS Code extension, so for those sessions this is the only source; it
    knows no cost and no context-window size. Reads the tail only: a session's
    transcript runs to tens of megabytes."""
    p = Path(str(path or ""))
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            chunk = f.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    for line in reversed(chunk.splitlines()):
        if '"assistant"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant":
            continue
        m = rec.get("message") or {}
        u = m.get("usage") or {}
        ctx = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        t = now if now is not None else time.time()
        age = None
        ts = rec.get("timestamp")
        if ts:
            try:
                age = int(t - datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
            except ValueError:
                age = None
        return {
            "session_id": rec.get("sessionId") or p.stem,
            "cwd": rec.get("cwd") or "",
            "model": m.get("model") or "", "model_id": m.get("model") or "",
            "version": rec.get("version") or "",
            "effort": rec.get("effort") or "", "entrypoint": rec.get("entrypoint") or "",
            "age_s": age, "ctx_tokens": ctx, "ctx_pct": None, "ctx_size": None,
            "cost_usd": None, "duration_ms": None, "lines_added": None, "lines_removed": None,
            "over_200k": ctx > 200_000, "source": "transcript",
        }
    return None


#: model id -> the name the status line shows; the transcript carries only the id
MODEL_NAMES = {"claude-fable-5-1": "Fable 5.1", "claude-opus-5": "Opus 5", "claude-sonnet-5": "Sonnet 5",
               "claude-haiku-4-5-20251001": "Haiku 4.5"}


def merge_sessions(from_statusline: list, live: list, max_age: float = SESSION_MAX_AGE,
                   now: float | None = None) -> list:
    """One row per session. A status-line record wins (it has cost and the
    window size); a live session without one is read from its transcript.
    A transcript row borrows the window size from a status-line row of the
    same model, so its bar means something; otherwise it shows tokens only."""
    t = now if now is not None else time.time()
    rows = {r["session_id"]: dict(r, source=r.get("source") or "statusline") for r in from_statusline if r.get("session_id")}
    sizes = {r.get("model_id") or r.get("model"): r.get("ctx_size") for r in from_statusline if r.get("ctx_size")}
    for s in live or []:
        sid = str(s.get("id") or "")
        if not sid or sid in rows or not s.get("transcript"):
            continue
        r = transcript_session(s["transcript"], now=t)
        if not r or (r["age_s"] is not None and r["age_s"] > max_age):
            continue
        r["session_id"] = sid
        # The HUB's cwd is the session's root; the transcript's is wherever the
        # last tool happened to run (a subfolder, after a `cd`).
        r["cwd"] = s.get("cwd") or r["cwd"] or ""
        r["model"] = MODEL_NAMES.get(r["model_id"], r["model"])
        size = sizes.get(r["model_id"])
        if size:
            r["ctx_size"] = size
            r["ctx_pct"] = round(100.0 * r["ctx_tokens"] / size, 1)
        rows[sid] = r
    out = list(rows.values())
    out.sort(key=lambda r: (r.get("age_s") if r.get("age_s") is not None else 1e9))
    return out


def _hhmm(resets_at: str | None) -> str:
    if not resets_at:
        return ""
    try:
        dt = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00")).astimezone()
        return dt.strftime("%H:%M") if dt.date() == datetime.now().astimezone().date() else dt.strftime("%a %H:%M")
    except ValueError:
        return ""


def one_line(c: dict, now: float | None = None) -> str:
    """`5h 52% ↺09:50 · 7d 17%` plus a marker when the number is old.

    Only the two windows a person steers by are shown; the rest are in --json.
    A window at zero is not worth a character of a status line."""
    w = c.get("windows") or {}
    if not w:
        return {"needs_auth": "usage: login", "backoff": "usage: 429", "error": "usage: n/a"}.get(
            c.get("state"), "usage: --")
    parts = []
    for named, _kind, label in WINDOWS:
        x = w.get(named)
        if not x or (named not in ("five_hour", "seven_day") and not x["percent"]):
            continue
        r = _hhmm(x.get("resets_at"))
        parts.append(f"{label} {x['percent']}%" + (f" ↺{r}" if r and named in ("five_hour", "seven_day") else ""))
    line = " · ".join(parts)
    t = now if now is not None else time.time()
    age = t - float(c.get("fetched_at") or 0)
    if age >= STALE_AFTER:
        line += f" (staro {int(age // 60)} min)"
    return line


def worst(c: dict) -> int:
    return max((x["percent"] for x in (c.get("windows") or {}).values()), default=0)


def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    sl: dict = {}
    if "--statusline" in args:
        try:
            raw = sys.stdin.read() if not sys.stdin.isatty() else ""
            sl = json.loads(raw) if raw.strip() else {}
        except Exception:                                       # noqa: BLE001
            sl = {}
    if "--statusline" in args and sl:
        record_statusline(sl)          # the HUD reads it; never blocks the render
    if "--no-fetch" in args:
        c = load_cache()
    elif "--statusline" in args:
        c = from_statusline(sl) or refresh(force=False, timeout=4.0)
    else:
        c = refresh(force=force)

    if "--json" in args:
        print(json.dumps(c, indent=2))
        return 0
    if "--statusline" in args:
        head = ""
        try:
            model = ((sl.get("model") or {}).get("display_name") or "")
            cwd = Path((sl.get("workspace") or {}).get("current_dir") or sl.get("cwd") or "").name
            ctx = (sl.get("context_window") or {}).get("used_percentage")
            head = " · ".join(x for x in [model, cwd, f"ctx {round(ctx)}%" if isinstance(ctx, (int, float)) else ""] if x)
        except Exception:                                       # noqa: BLE001
            head = ""
        print((head + " · " if head else "") + one_line(c))
        return 0

    print("Claude plan usage (api/oauth/usage, the number Claude Code's /usage shows)")
    if c.get("windows"):
        for named, x in sorted((c.get("windows") or {}).items(),
                               key=lambda kv: (kv[0] not in KNOWN, kv[0])):
            print(f"  {x['label']:<14} {x['percent']:>3}%   resets {x.get('resets_at') or '?'}")
        lr = c.get("limit_reset") or {}
        if lr.get("offered"):
            print("  /limit-reset is offered on this account "
                  "(once a week, clears the 5h window, spends from the 7d one)")
        print(f"  fetched {c.get('fetched_iso', '?')}  state {c.get('state')}")
    else:
        print(f"  no number: {c.get('problem') or c.get('state')}")
    if c.get("problem") and c.get("windows"):
        print(f"  note: {c['problem']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
