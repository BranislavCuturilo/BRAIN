#!/usr/bin/env python3
"""The transcript reader finds what compaction dropped, on a synthetic session.

Built on a temp `projects/` tree shaped exactly like Claude Code's: session
bookkeeping first (no cwd on those records -- reading only line one found no
transcript at all), then user/assistant turns, with a compaction in the middle
written the way the runtime writes it: a system record carrying
`compactMetadata` AND a summary record flagged `isCompactSummary`. Counting
both made every hit claim twice the compactions that had happened, which is
pinned here.

  python scripts/brain/test_recall.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recall  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def turn(role, text, cwd, ts="2026-09-05T20:48:00+02:00"):
    return {"type": role, "cwd": cwd, "timestamp": ts,
            "message": {"role": role, "content": [{"type": "text", "text": text}]}}


def tool(name, inp, cwd):
    return {"type": "assistant", "cwd": cwd, "timestamp": "2026-09-05T20:49:00+02:00",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": inp}]}}


def result(text, cwd):
    return {"type": "user", "cwd": cwd, "timestamp": "2026-09-05T20:49:30+02:00",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "content": [{"type": "text", "text": text}]}]}}


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        projects = tmp / "projects"
        repo = tmp / "myrepo"
        other = tmp / "otherrepo"
        for d in (projects / "p-myrepo", projects / "p-otherrepo"):
            d.mkdir(parents=True)
        cwd = str(repo).replace("/", "\\")

        rows = [
            {"type": "bridge-session", "id": "x"},          # no cwd -- bookkeeping
            {"type": "queue-operation"},                    # ditto
            turn("user", "hoću da pogledaš unlazy skill", cwd),
            tool("Bash", {"command": "grep -ri unlazy"}, cwd),
            result("nema unlazy lokalno", cwd),
            turn("assistant", "unlazy forsira potvrdu kvaliteta", cwd),
            {"type": "system", "cwd": cwd, "compactMetadata": {"trigger": "auto"}, "content": "compacting"},
            {"type": "user", "cwd": cwd, "isCompactSummary": True,
             "message": {"role": "user", "content": [{"type": "text", "text": "SAŽETAK: pričali smo o unlazy"}]}},
            turn("user", "sada nešto sasvim drugo o Codenotch-u", cwd, ts="2026-09-07T16:00:00+02:00"),
            turn("assistant", "Codenotch čita api/oauth/usage", cwd, ts="2026-09-07T16:01:00+02:00"),
        ]
        fp = projects / "p-myrepo" / "sess-aaa.jsonl"
        fp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        (projects / "p-otherrepo" / "sess-bbb.jsonl").write_text(
            json.dumps({"type": "bridge-session"}) + "\n"
            + json.dumps(turn("user", "unlazy u tuđem projektu", str(other)), ensure_ascii=False) + "\n",
            encoding="utf-8")

        print("transcripts_for()")
        found = recall.transcripts_for(repo, projects)
        ok([p.name for p in found] == ["sess-aaa.jsonl"],
           f"only this directory's session, found past the cwd-less opening records: {[p.name for p in found]}")
        ok(recall.transcripts_for(tmp / "nowhere", projects) == [], "an unknown directory -> nothing")
        ok(recall.transcripts_for(repo, tmp / "no-projects") == [], "no projects dir -> nothing, no raise")

        print("scan() / counting")
        seen = list(recall.scan(fp))
        ok(len(seen) == 6, f"bookkeeping and both compaction records excluded: {len(seen)}")
        ok(max(c for _i, _r, _t, c in seen) == 1,
           f"ONE compaction counted, not two (system + summary): {max(c for _i, _r, _t, c in seen)}")

        print("search()")
        hits = recall.search(fp, "unlazy")
        ok(len(hits) == 4, f"both turns, the tool call and its result all match: {len(hits)}")
        ok(hits[0]["who"] == "ti" and "unlazy skill" in hits[0]["excerpt"], f"readable, attributed: {hits[0]}")
        ok(any("[Bash]" in h["excerpt"] for h in hits), "a tool call is searchable by its input")
        ok(any("nema unlazy lokalno" in h["excerpt"] for h in hits), "a tool RESULT is searchable too")
        ok(all(h["compactions_before"] == 0 for h in hits), "all of it is from before the compaction")

        before = recall.search(fp, "unlazy", before_compact=True)
        ok(len(before) == 4, f"--before-compact keeps what the session no longer carries: {len(before)}")
        after = recall.search(fp, "Codenotch", before_compact=True)
        ok(after == [], "and drops what it still has")
        ok(len(recall.search(fp, "Codenotch")) == 2, "without the flag, the recent turns are found")
        ok(recall.search(fp, "unlazy", max_hits=1) and len(recall.search(fp, "unlazy", max_hits=1)) == 1,
           "the cap holds")
        ok(recall.search(fp, "nepostojeci-pojam") == [], "no match -> empty, not an error")
        ok(len(recall.search(fp, "unlazy", context=40)[0]["excerpt"]) <= 60, "context width is honoured")

        print("the summary record itself is never a hit")
        ok(not any("SAŽETAK" in h["excerpt"] for h in recall.search(fp, "SAŽETAK|unlazy")),
           "searching returns the real turns, not the summary that replaced them")

    # --- the summarised layer -------------------------------------------
    # A journal entry says WHY; the transcript says everything that was said on
    # the way. Searching the distilled layer first answers in one line what the
    # raw one answers in twenty. Measured 2026-09-10, the journal held ONE
    # dated entry against 317 commits in the same month, so today this layer
    # usually returns nothing -- these cases pin the behaviour for when it
    # fills, and pin that an empty or missing journal never breaks a search.
    jroot = Path(tempfile.mkdtemp()) / "journal" / "2026-09"
    jroot.mkdir(parents=True)
    (jroot / "2026-09-10-nesting.md").write_text(
        "# Zasto\nOrkestrator je dobio Agent alat nazad.\nspawnDepth je pravi kljuc.\n",
        encoding="utf-8")

    hits = recall.journal_hits("spawnDepth", root=jroot.parent)
    ok(len(hits) == 1, "the journal layer finds a distilled entry")
    ok(hits[0]["file"] == "2026-09-10-nesting.md" and hits[0]["line"] == 3,
       "and reports the file and line, not just the text")
    ok("spawnDepth" in hits[0]["excerpt"], "the excerpt carries the match")

    ok(recall.journal_hits("nepostojeci-pojam", root=jroot.parent) == [],
       "a term nobody wrote about returns nothing rather than everything")

    ok(recall.journal_hits("bilo sta", root=Path("C:/nema/ovoga/nigde")) == [],
       "a MISSING journal is silence, not a crash -- the raw layer still runs")

    ok(len(recall.journal_hits("e", root=jroot.parent, max_hits=2)) <= 2,
       "the cap holds: this layer cannot flood a prompt either")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
