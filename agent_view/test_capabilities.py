#!/usr/bin/env python3
"""An unconfigured tab says what to do, and a credential never reaches git.

The second half is the one with a history. This repository shipped three live
secrets in a tracked file because writing to the wrong path is one line of code
and nothing refused it. Here something refuses it, and this is the test that
proves the refusal is real rather than a comment.

  python agent_view/test_capabilities.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capabilities as C  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def by_id(rows, fid):
    return next(r for r in rows if r["id"] == fid)


def git_repo(files: dict, ignore: str = "") -> Path:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=d, capture_output=True)
    if ignore:
        (d / ".gitignore").write_text(ignore, encoding="utf-8")
    for k, v in files.items():
        (d / k).write_text(v, encoding="utf-8")
    return d


def main() -> int:
    print("nothing set: every tab says the ONE line to paste into Claude")
    rows = C.snapshot({})
    ok(all(r["state"] != C.READY for r in rows),
       f"nothing is ready on an empty config: {[(r['id'], r['state']) for r in rows]}")
    ok(all(r["ask"] for r in rows), "and every tab offers a command, ready or not")
    t = by_id(rows, "tickets")
    ok(t["state"] == C.NOT_CONFIGURED, f"tickets has an address to set first: {t['state']}")
    ok(t["ask"] == "/setup tickets", f"a command, not a sentence: {t['ask']!r}")
    ok(t["why"], "and it says why the reader should bother at all")

    print("a key-only feature skips the conversation and shows the box")
    # `ai` needs nothing BUT a credential. Routing it through "/setup ai" first
    # would spend a turn to arrive at "paste your key", which is the box we could
    # have drawn immediately. It still carries `ask` for someone with no key yet.
    a = by_id(rows, "ai")
    ok(a["state"] == C.NEEDS_SECRET, f"straight to the input: {a['state']}")
    ok(a["ask"] == "/setup ai" and a["missing"][0]["hint"],
       "with both a command and where to get the key")

    print("an empty string is NOT configured -- the template ships every field empty")
    # The trap: the shipped example has "helpdesk_url": "" so the file parses.
    # Reading that as set makes the tab render ready and fail on use.
    half = {"helpdesk_url": "   ", "helpdesk_token": ""}
    ok(by_id(C.snapshot(half), "tickets")["state"] == C.NOT_CONFIGURED,
       "whitespace is empty too")

    print("address known, credential missing -> an input, not a conversation")
    cfg = {"helpdesk_url": "https://hd.example.com"}
    t = by_id(C.snapshot(cfg), "tickets")
    ok(t["state"] == C.NEEDS_SECRET, f"state: {t['state']}")
    ok([m["key"] for m in t["missing"]] == ["helpdesk_token"],
       f"only the missing one is offered: {t['missing']}")
    ok(t["missing"][0]["secret"] is True, "and it is marked secret")

    print("the browser is never told a value, only which box to draw")
    blob = json.dumps(C.snapshot({"helpdesk_url": "https://hd.example.com",
                                  "gemini_api_key": "AIzaSECRETVALUE"}))  # release-check: fixture
    ok("AIzaSECRETVALUE" not in blob, "no configured value is serialised")
    ok("hd.example.com" not in blob, "not even a non-secret one")

    print("a secret in the secrets FILE counts as configured")
    rows = C.snapshot({"helpdesk_url": "https://hd.example.com"},
                      secrets={"helpdesk_token": "t"})
    ok(by_id(rows, "tickets")["state"] == C.READY,
       "the tab goes ready without the token ever touching the tracked config")

    print("nested keys work -- monitor.url / monitor.token")
    rows = C.snapshot({"monitor": {"url": "https://m.example.com", "token": ""}})
    m = by_id(rows, "monitor")
    ok(m["state"] == C.NEEDS_SECRET and [x["key"] for x in m["missing"]] == ["monitor.token"],
       f"dotted lookup: {m}")

    print("a list field is configured only when it has entries")
    ok(by_id(C.snapshot({"mail_accounts": []}), "mail")["state"] == C.NOT_CONFIGURED,
       "an empty list is not an account")
    ok(by_id(C.snapshot({"mail_accounts": [{"id": "x"}]}), "mail")["state"] == C.READY,
       "one entry is")

    print("save_secret REFUSES a path git would stage")
    tracked = git_repo({"agent_view.secrets.json": "{}"})
    try:
        C.save_secret("helpdesk_token", "ehd_real", path=tracked / "agent_view.secrets.json")
        ok(False, "it wrote a credential into a tracked path")
    except C.SecretRefused as e:
        ok("gitignore" in str(e), f"refused: {str(e)[:60]}")
    ok(json.loads((tracked / "agent_view.secrets.json").read_text(encoding="utf-8")) == {},
       "and the file was left exactly as it was")

    print("and writes when the path IS ignored")
    safe = git_repo({}, ignore="agent_view.secrets.json\n")
    p = C.save_secret("helpdesk_token", "ehd_real", path=safe / "agent_view.secrets.json")
    ok(p.exists() and C.load_secrets(p)["helpdesk_token"] == "ehd_real", "written")
    C.save_secret("monitor.token", "mt", path=p)
    got = C.load_secrets(p)
    ok(got["monitor"]["token"] == "mt" and got["helpdesk_token"] == "ehd_real",
       f"a second, nested secret does not clobber the first: {got}")

    print("a missing or corrupt secrets file is empty, never an exception")
    ok(C.load_secrets(Path(tempfile.mkdtemp()) / "nope.json") == {}, "absent")
    bad = Path(tempfile.mkdtemp()) / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    ok(C.load_secrets(bad) == {}, "unparseable")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
