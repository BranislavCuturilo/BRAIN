#!/usr/bin/env python3
"""The brief names what a summary drops; auto-compact is vetoed when the
window still has room.

Built on a temp store and a temp git repo, so nothing here reads the real
queue. What is pinned: only the modules whose repo IS this directory are
listed (a session in one project must not be handed another's queue); closed
tickets are absent; tickets on the same screen are grouped, which is the whole
point of the thing; the cap holds; silence on a missing store; and an
automatic compact at ~88k after a 1M session already ran is blocked — that
is the 2026-09-09 thrash. Manual /compact is never blocked.

  python scripts/brain/test_compact_brief.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compact_brief as cb  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def ticket(tid, title, status="active", page="", priority="major", created="2026-09-07T10:00:00", read=True):
    t = {"status": status, "title": title,
         "original": {"title": title, "created": created},
         "helpdesk": {"priority": priority}}
    if read:
        t["reading"] = {"real_need": "x", "page": page, "scope": "ui"}
    return tid, t


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = tmp / "projects" / "myapp"
        repo.mkdir(parents=True)
        other = tmp / "projects" / "elsewhere"
        other.mkdir(parents=True)
        store = tmp / "store"
        store.mkdir()
        os.environ["PROJECTS_ROOT"] = str(tmp / "projects")

        (store / "modules.json").write_text(json.dumps({"modules": {
            "MYAPP": "myapp", "OTHER": "elsewhere", "NOREPO": ""}}), encoding="utf-8")
        (store / "MYAPP.json").write_text(json.dumps({"tickets": dict([
            ticket("00111", "Lista ne filtrira", page="items:list", priority="critical", created="2026-09-07T12:00:00"),
            ticket("00222", "Izvoz sa liste", page="items:list", created="2026-09-07T11:00:00"),
            ticket("00333", "Detalj ne prikazuje status", page="items:detail", created="2026-09-07T09:00:00"),
            ticket("00444", "Zavrseno davno", status="done", page="items:list"),
            ticket("00555", "Nije citan", read=False, created="2026-09-06T08:00:00"),
        ])}), encoding="utf-8")
        (store / "OTHER.json").write_text(json.dumps({"tickets": dict([
            ticket("09999", "Tudji tiket", page="x:y")])}), encoding="utf-8")

        print("modules_for()")
        ok(cb.modules_for(repo, store) == ["MYAPP"], f"only this repo's module: {cb.modules_for(repo, store)}")
        ok(cb.modules_for(repo / "sub" / "deep", store) == ["MYAPP"], "a subdirectory still maps to it")
        ok(cb.modules_for(tmp / "nowhere", store) == [], "an unmapped directory -> no modules, no guess")

        print("open_tickets()")
        ts = cb.open_tickets("MYAPP", store)
        ok([t["id"] for t in ts] == ["00111", "00222", "00333", "00555"],
           f"closed one absent, newest first: {[t['id'] for t in ts]}")
        ok(ts[0]["priority"] == "critical" and ts[0]["page"] == "items:list", "priority and screen read")
        ok(ts[3]["read"] is False, "an unread ticket is marked as such")
        ok(cb.open_tickets("MISSING", store) == [], "a module with no file -> []")

        print("by_screen()")
        g = cb.by_screen(ts)
        ok(list(g) == ["items:list"], f"only screens with more than one ticket: {list(g)}")
        ok(g["items:list"] == ["MYAPP#00111", "MYAPP#00222"], f"both siblings named: {g['items:list']}")

        print("build()")
        subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "prvi commit"], cwd=repo, capture_output=True)
        (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
        text = cb.build(repo, store)
        ok(text.startswith("=== BRAIN"), "a header that says what it is")
        ok("MYAPP#00111" in text and "MYAPP#00333" in text, "the open tickets are there")
        ok("00444" not in text, "the closed one is not")
        ok("09999" not in text and "OTHER" not in text, "another project's queue is never handed over")
        ok("SRODNI" in text and "items:list: MYAPP#00111, MYAPP#00222" in text, "the same-screen grouping is spelled out")
        ok("b.py" in text and "NEKOMITOVANO" in text, "uncommitted work is named")
        # ` M path` (modified, unstaged) starts with a space: stripping the whole
        # `git status` output used to eat it, and the first path lost a letter.
        (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
        t3 = cb.build(repo, store)
        line = next(l for l in t3.splitlines() if "NEKOMITOVANO" in l)
        ok("a.py" in line and "b.py" in line and ".py" in line and " .py" not in line,
           f"both paths whole: {line}")
        ok("prvi commit" in text, "the last commit is named")
        ok("tickets_store" in text, "it points at the files it came from")
        ok(len(text) <= cb.MAX_CHARS, f"within the cap: {len(text)} chars")

        big = dict([ticket(f"{i:05d}", f"tiket {i}", page="items:list", created=f"2026-09-0{i%7+1}T08:00:00")
                    for i in range(40)])
        (store / "MYAPP.json").write_text(json.dumps({"tickets": big}), encoding="utf-8")
        t2 = cb.build(repo, store)
        ok(len(t2) <= cb.MAX_CHARS and "još" in t2, f"40 tickets: capped and says so ({len(t2)} chars)")

        print("silence, not exceptions")
        ok(cb.build(tmp / "does" / "not" / "exist", store).startswith("=== BRAIN"),
           "a directory that is not a repo still returns a brief")
        ok(cb.modules_for(repo, tmp / "no-store") == [], "no store -> no modules, no raise")

        print("record() / recent()")
        log = tmp / "compacts.jsonl"
        r = cb.record({"hook_event_name": "PreCompact", "trigger": "auto",
                       "session_id": "s1", "cwd": str(repo), "custom_instructions": None}, log, now="2026-09-07T16:00:00")
        ok(r and r["trigger"] == "auto" and r["custom"] is False, f"one row: {r}")
        cb.record({"hook_event_name": "PreCompact", "trigger": "manual", "session_id": "s1",
                   "cwd": str(repo), "custom_instructions": "keep the tickets"}, log, now="2026-09-07T16:05:00")
        rows = cb.recent(log)
        ok(len(rows) == 2 and rows[1]["custom"] is True, f"appended, custom instructions noted: {rows}")
        ok(cb.record({}, log) is None and cb.record("nonsense", log) is None, "a malformed payload writes nothing")
        ok(cb.record({"hook_event_name": "PreCompact"}, tmp / "no" / "dir" / "x.jsonl") is not None
           or True, "an unwritable log is not an exception")
        ok(cb.recent(tmp / "missing.jsonl") == [], "no log -> []")

        print("should_block_auto()")
        empty = tmp / "empty.jsonl"
        ok(cb.should_block_auto("manual", "s1", 88_000, empty) is None,
           "manual /compact is never blocked")
        ok(cb.should_block_auto("auto", "s1", 911_998, empty) is None,
           "1M session actually full -> allow")
        ok(cb.should_block_auto("auto", "s1", 170_000, empty) is None,
           "200k session near the limit -> allow")
        why = cb.should_block_auto("auto", "s1", 88_000, empty)
        ok(why and "200k" in why, f"88k with no history is thrash: {why}")
        hist = tmp / "hist.jsonl"
        hist.write_text(json.dumps({"at": "2026-09-09T18:21:14", "event": "PreCompact",
                                    "trigger": "auto", "session": "big",
                                    "ctx_tokens": 911998}) + "\n", encoding="utf-8")
        why = cb.should_block_auto("auto", "big", 88_000, hist)
        ok(why and "1M" in why, f"post-compact 88k on a 1M session is blocked: {why}")
        ok(cb.should_block_auto("auto", "big", None, hist) and "unread" in (cb.should_block_auto("auto", "big", None, hist) or ""),
           "1M session with unread tokens is blocked, not guessed through")
        ok(cb.should_block_auto("auto", "other", None, hist) is None,
           "unknown session, no tokens -> leave it to the runtime")

    # The wiring itself. A brief nothing calls is a file, not a mechanism --
    # and a SessionStart matcher that stops matching fails silently, which is
    # the exact failure shape hooks.json's own eval cases exist to catch.
    print("wired into hooks.json")
    h = json.loads((cb.ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    ss = [e for e in h.get("SessionStart", []) if e.get("matcher") == "compact"]
    ok(len(ss) == 1 and "compact_brief.py" in json.dumps(ss),
       "SessionStart(compact) runs the brief: " + json.dumps(ss)[:120])
    pc = h.get("PreCompact") or []
    ok(any("compact_brief.py --record" in json.dumps(e) for e in pc),
       "PreCompact records: " + json.dumps(pc)[:120])
    ok(all("|| true" in hk.get("command", "") for e in ss + pc for hk in e.get("hooks", [])),
       "neither hook can fail the event it hangs off")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
