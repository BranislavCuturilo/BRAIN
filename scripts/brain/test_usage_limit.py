#!/usr/bin/env python3
"""The plan-usage reader, proven without the network and without the real token.

The response shape is not a published API, so it is pinned here on a payload
captured from the endpoint (trimmed of its tail of null keys). If Anthropic
renames a field this is the file that goes red first. Everything else is the
arithmetic that keeps the reader from ever hammering the endpoint or blocking
a render: the poll floor, the 429 doubling with a cap, Retry-After that may
only raise, the merge of named windows over `limits[]`, and an expired token
that is never sent.

  python scripts/brain/test_usage_limit.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_limit as ul  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


# captured payload (codenotch Tests/UsageResponseTests.swift), values 0-100
REAL = {
    "five_hour": {"utilization": 52.0, "resets_at": "2026-08-28T09:50:00.316290+00:00",
                  "limit_dollars": None, "used_dollars": None},
    "seven_day": {"utilization": 17.0, "resets_at": "2026-09-02T17:00:00.316321+00:00", "limit_dollars": None},
    "seven_day_opus": None,
    "nimbus_quill": {"utilization": 0.0, "resets_at": None},
    "limits": [
        {"kind": "session", "group": "session", "percent": 52, "severity": "normal",
         "resets_at": "2026-08-28T09:50:00.316290+00:00", "scope": None, "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 17, "severity": "normal",
         "resets_at": "2026-09-02T17:00:00.316321+00:00", "scope": None, "is_active": False},
    ],
}


def creds(tmp: Path, expires_at_ms: int, token: str = "tok-not-real", name: str = ".credentials.json") -> Path:
    p = tmp / name
    p.write_text(json.dumps({"claudeAiOauth": {"accessToken": token, "refreshToken": "r",
                                               "expiresAt": expires_at_ms, "subscriptionType": "max"}}),
                 encoding="utf-8")
    return p


def main() -> int:
    print("parse()")
    p = ul.parse(REAL)
    ok(p["windows"]["five_hour"]["percent"] == 52 and p["windows"]["seven_day"]["percent"] == 17, "named windows read, 0-100 kept")
    ok(p["windows"]["five_hour"]["resets_at"].startswith("2026-08-28T09:50"), "reset time kept")
    ok("seven_day_opus" not in p["windows"], "a null named window with no limits entry is absent, not 0")
    only_limits = {"five_hour": None, "seven_day": None,
                   "limits": [{"kind": "session", "percent": 80, "resets_at": "x"},
                              {"kind": "weekly_opus", "percent": 5, "resets_at": "y"}]}
    q = ul.parse(only_limits)
    ok(q["windows"]["five_hour"]["percent"] == 80 and q["windows"]["seven_day_opus"]["percent"] == 5,
       "limits[] fills a window the named object left null")
    dropped = dict(REAL, limits=[])          # the session fell out of limits[] at its reset
    ok(ul.parse(dropped)["windows"]["five_hour"]["percent"] == 52, "named window survives limits[] dropping it")
    ok(ul.parse({})["windows"] == {}, "empty payload -> no windows, no crash")
    # Discovered, not listed: a window the endpoint invents next month must not
    # need a code change to be seen. `nimbus_quill` was live and silently
    # dropped for as long as the tuple was the only source.
    disc = ul.parse({"nimbus_quill": {"utilization": 7.0, "resets_at": "x"},
                     "cinder_cove": None, "spend": {"percent": 3}})
    ok(list(disc["windows"]) == ["nimbus_quill"], f"an unnamed window is found: {list(disc['windows'])}")
    ok(disc["windows"]["nimbus_quill"]["label"] == "nimbus_quill", "and keeps its key as its label")
    ok("spend" not in disc["windows"], "an object with no utilization is not a window")
    ok(ul.parse({})["limit_reset"]["offered"] is False, "no juniper_tide -> /limit-reset not offered")
    ok(ul.parse({"juniper_tide": {"x": 1}})["limit_reset"]["offered"] is True,
       "juniper_tide present -> the once-a-week session reset is in play")
    ok("juniper_tide" not in ul.parse({"juniper_tide": {"utilization": 5}})["windows"],
       "juniper_tide is a signal, never counted as a usage window")

    print("next_floor()")
    ok(ul.next_floor(1, None) == 60 and ul.next_floor(2, None) == 120 and ul.next_floor(5, None) == 900,
       "doubles from 60 s, capped at 15 min")
    ok(ul.next_floor(1, "0") == 60, "Retry-After: 0 does not lower the floor")
    ok(ul.next_floor(1, "300") == 300 and ul.next_floor(1, "99999") == 900, "Retry-After raises, capped")
    ok(ul.next_floor(3, "garbage") == 240, "unparseable Retry-After ignored")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cache = tmp / "usage-limit.json"
        now = 1_800_000_000.0
        calls: list[tuple[str, float]] = []

        def fetch_ok(token, timeout):
            calls.append((token, timeout))
            return 200, REAL, None

        print("read_token()")
        t, prob = ul.read_token(creds(tmp, int((now + 3600) * 1000)), now=now)
        ok(t == "tok-not-real" and not prob, "a live token is returned")
        t, prob = ul.read_token(creds(tmp, int((now - 1) * 1000)), now=now)
        ok(t == "" and "expired" in prob, "an expired token is refused with a sentence")
        t, prob = ul.read_token(tmp / "nope.json", now=now)
        ok(t == "" and "no credentials" in prob, "missing file -> sentence, no exception")

        print("refresh(): floor, backoff, auth")
        live = creds(tmp, int((now + 100_000) * 1000))   # must outlive every step below (up to now + 5000)
        c = ul.refresh(fetch=fetch_ok, cache_path=cache, creds=live, now=now)
        ok(c["state"] == "ok" and c["windows"]["five_hour"]["percent"] == 52, "first call fetches")
        ok(calls[0][0] == "tok-not-real", "the token goes to the fetcher and nowhere else")
        c = ul.refresh(fetch=fetch_ok, cache_path=cache, creds=live, now=now + 30)
        ok(len(calls) == 1 and c["state"] == "ok", "30 s later: served from cache, no call")
        c = ul.refresh(fetch=fetch_ok, cache_path=cache, creds=live, now=now + 30, force=True)
        ok(len(calls) == 2, "--force ignores the poll floor")
        c = ul.refresh(fetch=fetch_ok, cache_path=cache, creds=live, now=now + 91)
        ok(len(calls) == 3, "61 s after the last (forced) fetch: fetches again")

        def fetch_429(token, timeout):
            calls.append((token, timeout))
            return 429, {}, "0"

        c = ul.refresh(fetch=fetch_429, cache_path=cache, creds=live, now=now + 200)
        ok(c["state"] == "backoff" and c["consecutive_429"] == 1 and c["floor_until"] == now + 260,
           f"first 429: floor 60 s, Retry-After 0 ignored: {c.get('problem')}")
        ok(c["windows"]["five_hour"]["percent"] == 52, "the last good number is kept through a 429")
        n = len(calls)
        c = ul.refresh(fetch=fetch_429, cache_path=cache, creds=live, now=now + 230, force=True)
        ok(len(calls) == n, "--force does NOT override a 429 backoff")
        c = ul.refresh(fetch=fetch_429, cache_path=cache, creds=live, now=now + 261)
        ok(c["consecutive_429"] == 2 and c["floor_until"] == now + 261 + 120, "second 429 doubles")
        c = ul.refresh(fetch=fetch_ok, cache_path=cache, creds=live, now=now + 1000)
        ok(c["state"] == "ok" and c["consecutive_429"] == 0, "a 200 resets the backoff")

        c = ul.refresh(fetch=lambda t, to: (401, {}, None), cache_path=cache, creds=live, now=now + 2000)
        ok(c["state"] == "needs_auth" and "401" in c["problem"], "401 -> needs_auth")
        expired = creds(tmp, int((now - 1) * 1000), name="expired.json")   # its own file: `live` must stay live
        n = len(calls)
        c = ul.refresh(fetch=fetch_ok, cache_path=cache, creds=expired, now=now + 3000)
        ok(len(calls) == n and c["state"] == "needs_auth", "an expired token is never sent")
        c = ul.refresh(fetch=lambda t, to: (0, {}, None), cache_path=cache, creds=live, now=now + 4000)
        ok(c["state"] == "stale" and c["windows"], f"network error keeps the last number, marked stale: {c.get('state')}")
        c = ul.refresh(fetch=lambda t, to: (200, {"weird": 1}, None), cache_path=cache, creds=live, now=now + 5000)
        ok(c["state"] == "error" and "shape moved" in c.get("problem", ""), f"200 with an unknown shape says so: {c.get('state')} {c.get('problem')}")

        print("from_statusline()")
        sl = {"model": {"display_name": "Fable 5.1"},
              "rate_limits": {"five_hour": {"used_percentage": 46.0, "resets_at": "2026-09-07T15:20:00+00:00"},
                              "seven_day": {"used_percentage": 33.4, "resets_at": "2026-09-08T02:00:00+00:00"}}}
        f = ul.from_statusline(sl, now=now)
        ok(f["windows"]["five_hour"]["percent"] == 46 and f["windows"]["seven_day"]["percent"] == 33,
           "Claude Code's own rate_limits object is read, no token, no network")
        ok(f["source"] == "statusline" and f["state"] == "ok", "marked as coming from the status line")
        ok(ul.from_statusline({"model": {}}) is None and ul.from_statusline({"rate_limits": {}}) is None
           and ul.from_statusline(None) is None, "no rate_limits -> None (fall back to the cache)")

        print("record_statusline() / sessions()")
        sdir = tmp / "statusline"
        payload = {"session_id": "abc-123", "cwd": "C:/projects/acme-audit", "version": "2.1.258",
                   "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"},
                   "cost": {"total_cost_usd": 0.4213, "total_duration_ms": 7_500_000,
                            "total_lines_added": 120, "total_lines_removed": 8},
                   "context_window": {"used_percentage": 41.2, "context_window_size": 1_000_000},
                   "exceeds_200k_tokens": False,
                   "rate_limits": {"five_hour": {"used_percentage": 46}},
                   "transcript_path": "C:/x/t.jsonl"}
        fp = ul.record_statusline(payload, sdir, now=now)
        ok(fp is not None and fp.name == "abc-123.json", "one file per session id")
        raw = json.loads(fp.read_text(encoding="utf-8"))
        ok("rate_limits" not in raw and "transcript_path" not in raw and raw["model"]["display_name"] == "Fable 5.1",
           "only the kept keys are written")
        ok(ul.record_statusline({"session_id": "../evil"}, sdir, now=now) is None
           and ul.record_statusline({}, sdir, now=now) is None, "a bad or missing session id writes nothing")
        stale = sdir / "old.json"
        stale.write_text(json.dumps({"session_id": "old", "updated_at": now - 7200}), encoding="utf-8")
        ss = ul.sessions(sdir, now=now + 60)
        ok([s["session_id"] for s in ss] == ["abc-123"], f"an hour-old session is not listed: {[s['session_id'] for s in ss]}")
        s0 = ss[0]
        ok(s0["model"] == "Fable 5.1" and s0["ctx_pct"] == 41.2 and s0["ctx_size"] == 1_000_000
           and s0["cost_usd"] == 0.4213 and s0["lines_added"] == 120 and s0["age_s"] == 60,
           f"the flat HUD shape: {s0}")
        ok(ul.sessions(tmp / "nowhere", now=now) == [], "no directory -> []")

        print("transcript_session() / merge_sessions()")
        tr = tmp / "e275.jsonl"
        older = {"type": "assistant", "sessionId": "e275", "cwd": "C:/x", "version": "2.1.261",
                 "timestamp": "2027-01-15T08:00:00.000Z", "effort": "high", "entrypoint": "claude-vscode",
                 "message": {"model": "claude-fable-5-1", "usage": {"input_tokens": 10, "cache_read_input_tokens": 100,
                                                                      "cache_creation_input_tokens": 5, "output_tokens": 7}}}
        last = dict(older, timestamp="2027-01-15T08:10:00.000Z",
                    message={"model": "claude-fable-5-1", "usage": {"input_tokens": 32, "cache_read_input_tokens": 572907,
                                                                     "cache_creation_input_tokens": 1586, "output_tokens": 2841}})
        junk = json.dumps({"type": "user", "message": {"content": "x" * 900}})
        lines = [json.dumps(older)] + [junk] * 700 + [json.dumps(last), json.dumps({"type": "user", "message": {"content": "later"}})]
        tr.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
        big = tr.stat().st_size
        t_last = datetime.fromisoformat("2027-01-15T08:10:00+00:00").timestamp()
        r = ul.transcript_session(tr, now=t_last + 300)
        ok(big > ul.TAIL_BYTES and r is not None, f"read from the tail of a {big // 1024} KB file")
        ok(r["ctx_tokens"] == 574525 and r["model_id"] == "claude-fable-5-1", f"the LAST assistant record's context: {r and r['ctx_tokens']}")
        ok(r["age_s"] == 300 and r["effort"] == "high" and r["entrypoint"] == "claude-vscode" and r["version"] == "2.1.261",
           "age from its timestamp, effort, entrypoint, version")
        ok(r["ctx_pct"] is None and r["cost_usd"] is None and r["source"] == "transcript", "no percentage, no cost: it does not know them")
        ok(ul.transcript_session(tmp / "missing.jsonl") is None, "missing transcript -> None")

        sl_rows = [{"session_id": "abc-123", "model": "Fable 5.1", "model_id": "claude-fable-5-1", "ctx_size": 1_000_000,
                    "ctx_pct": 41.2, "cost_usd": 0.42, "age_s": 60}]
        live = [{"id": "abc-123", "cwd": "C:/a", "transcript": str(tr)},      # has a status line: wins
                {"id": "e275", "cwd": "C:/x", "transcript": str(tr)},         # vscode: from the transcript
                {"id": "ghost", "cwd": "C:/g", "transcript": str(tmp / "missing.jsonl")}]
        merged = ul.merge_sessions(sl_rows, live, now=t_last + 300)
        ok([m["session_id"] for m in merged] == ["abc-123", "e275"], f"status line first, transcript second, ghost dropped: {[m['session_id'] for m in merged]}")
        e = merged[1]
        ok(e["model"] == "Fable 5.1" and e["ctx_size"] == 1_000_000 and e["ctx_pct"] == 57.5,
           f"the transcript row borrows the window size of the same model: {e['ctx_pct']}")
        ok(merged[0]["cost_usd"] == 0.42 and merged[0]["source"] == "statusline", "the status-line row is untouched")
        ok(e["cwd"] == "C:/x", "the transcript row carries the HUB's cwd, not the last tool's")
        sub = ul.merge_sessions([], [{"id": "e275", "cwd": "C:/root", "transcript": str(tr)}], now=t_last + 300)
        ok(sub[0]["cwd"] == "C:/root", "  ...even when the transcript says a subfolder")
        alone = ul.merge_sessions([], [{"id": "e275", "cwd": "C:/x", "transcript": str(tr)}], now=t_last + 300)
        ok(alone[0]["ctx_pct"] is None and alone[0]["ctx_tokens"] == 574525, "no size to borrow -> tokens only, no bar")
        ok(ul.merge_sessions([], [{"id": "e275", "transcript": str(tr)}], now=t_last + 7200) == [], "an hour-old transcript is not listed")

        print("one_line()")
        good = {"state": "ok", "fetched_at": now, "windows": ul.parse(REAL)["windows"]}
        line = ul.one_line(good, now=now)
        ok(line.startswith("5h 52%") and "7d 17%" in line, f"compact line: {line}")
        ok("staro" not in line, "fresh number has no age marker")
        old = ul.one_line(good, now=now + 2000)
        ok("(staro 33 min)" in old, f"an old number says how old: {old}")
        ok(ul.one_line({"state": "needs_auth"}) == "usage: login", "no number, needs login")
        ok(ul.one_line({"state": "backoff"}) == "usage: 429", "no number, backoff")
        ok(ul.worst(good) == 52, "worst window for health")
        raw = json.dumps(good)
        ok("tok-not-real" not in raw and "tok-not-real" not in json.dumps(ul.load_cache(cache)),
           "the token is in neither the cache nor the printable state")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
