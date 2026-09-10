#!/usr/bin/env python3
"""Search this session's own transcript — including what compaction dropped.

**The thing worth knowing: nothing is deleted.** Compaction replaces what the
model is *carrying*, not what is on disk. `~/.claude/projects/<project>/
<session>.jsonl` keeps every message of the session, before and after every
compaction, and marks each compaction boundary (`isCompactSummary`). Measured
on a real session: 18.7 MB, 8252 records, two compactions, and a topic from the
first ten minutes still verbatim in the file hours later.

So there is nothing to intercept and nothing to copy into a second store — a
copy would only be a staler version of a file that is already complete. What
was missing is a way to READ it: `grep` over the raw JSONL answers with 200 KB
of nested JSON on one line, which is worse than no answer.

This is that reader. Regex in, readable turns out: time, who spoke, and the
text around the match, with the compaction boundaries marked so "before the
part I no longer remember" is a thing you can actually ask for.

**It reads, and only reads.** A transcript is the record of what happened; a
tool that could edit it would make every other measurement in this brain
untrustworthy.

  recall.py "<regex>"                 this project's newest session
  recall.py "<regex>" --before-compact only what a compaction dropped
  recall.py "<regex>" --all-sessions   every session of this project
  recall.py "<regex>" --context 300    characters around each hit
  recall.py --sessions                 what transcripts exist here

`compact_brief.py` names this tool in the context it hands back after a
compaction, which is the only moment it is needed and the one moment the
session cannot remember that it exists.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECTS = Path.home() / ".claude" / "projects"

#: The summarised layer, searched before the raw one. A journal entry is a
#: distilled record of WHY something was done; a transcript is everything that
#: was said on the way there. A hit in the distilled layer answers in one line
#: what the raw layer answers in twenty.
#:
#: Measured 2026-09-10: the journal held ONE dated entry against 317 commits in
#: the same thirty days, so today this layer usually returns nothing and the raw
#: scan does all the work. That is the honest state rather than a defect --
#: `archivist` now fires on SessionEnd, and this layer earns its line as the
#: journal fills. Shape borrowed from TencentDB-Agent-Memory (MIT): summarised
#: first, raw as the fallback, both capped.
JOURNAL = Path(__file__).resolve().parent.parent.parent / "journal"

MAX_HITS = 25
CONTEXT = 220
MAX_LINE = 400
SPEAKER = {"user": "ti", "assistant": "ja", "system": "sistem"}


def _norm(p) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def transcripts_for(cwd, projects: Path = PROJECTS) -> list[Path]:
    """Every transcript whose session ran in this directory, newest first.

    Found by READING each file's first record rather than by reproducing
    Claude Code's directory-name encoding: the encoding is undocumented and a
    guess at it would silently return nothing on the day it changes."""
    out = []
    here = _norm(cwd)
    if not projects.is_dir():
        return []
    for fp in projects.glob("*/*.jsonl"):
        # A transcript opens with session bookkeeping (bridge-session,
        # queue-operation) that carries no cwd -- the first record with one is
        # a few lines in. Reading only line 1 found nothing at all.
        found = ""
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for _ in range(12):
                    line = f.readline()
                    if not line:
                        break
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("cwd"):
                        found = rec["cwd"]
                        break
        except OSError:
            continue
        if found and _norm(found) == here:
            out.append(fp)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def _text_of(rec: dict) -> str:
    """One record as readable text: what was said, what tool was called with
    what, what came back. Blocks the search would otherwise miss entirely."""
    m = rec.get("message")
    if isinstance(m, str):
        return m
    if not isinstance(m, dict):
        c = rec.get("content")
        return c if isinstance(c, str) else ""
    c = m.get("content")
    if isinstance(c, str):
        return c
    if not isinstance(c, list):
        return ""
    parts = []
    for b in c:
        if not isinstance(b, dict):
            parts.append(str(b))
            continue
        t = b.get("type")
        if t == "text":
            parts.append(str(b.get("text") or ""))
        elif t == "thinking":
            parts.append(str(b.get("thinking") or ""))
        elif t == "tool_use":
            parts.append(f"[{b.get('name')}] " + json.dumps(b.get("input") or {}, ensure_ascii=False))
        elif t == "tool_result":
            r = b.get("content")
            if isinstance(r, list):
                r = " ".join(str(x.get("text") or "") for x in r if isinstance(x, dict))
            parts.append(str(r or ""))
    return "\n".join(p for p in parts if p)


def _when(rec: dict) -> str:
    ts = rec.get("timestamp")
    if not ts:
        return "     "
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().strftime("%d.%m %H:%M")
    except ValueError:
        return "     "


def scan(path: Path):
    """(index, record, text, compactions_before) for every readable record."""
    compactions = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            # A compaction writes TWO records: a system one carrying
            # compactMetadata and the summary itself. Counting both made every
            # hit claim twice as many compactions as had happened -- the count
            # is the summary, and the metadata record is skipped, not counted.
            if rec.get("isCompactSummary"):
                compactions += 1
                continue                        # the summary itself is not the record
            if "compactMetadata" in rec:
                continue
            if rec.get("type") not in ("user", "assistant", "system"):
                continue
            yield i, rec, _text_of(rec), compactions


def journal_hits(pattern: str, *, context: int = CONTEXT,
                 max_hits: int = MAX_HITS, root: Path = JOURNAL) -> list[dict]:
    """Matching lines from the journal, newest file first.

    Never raises on a missing or unreadable journal: this layer is an optional
    shortcut, and a search that dies because the shortcut is empty is worse
    than one that falls through to the raw transcript.
    """
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise SystemExit(f"  bad pattern: {exc}")
    if not root.is_dir():
        return []
    try:
        files = sorted(root.rglob("*.md"), key=lambda p: -p.stat().st_mtime)
    except OSError:
        return []
    hits: list[dict] = []
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            m = rx.search(line)
            if not m:
                continue
            a = max(0, m.start() - context // 2)
            hits.append({"file": fp.name, "line": n,
                         "excerpt": " ".join(line[a:a + context].split())[:MAX_LINE]})
            if len(hits) >= max_hits:
                return hits
    return hits


def search(path: Path, pattern: str, *, before_compact: bool = False,
           context: int = CONTEXT, max_hits: int = MAX_HITS) -> list[dict]:
    """Every matching turn, oldest first. `before_compact` keeps only what was
    said before the last compaction — the part the session no longer carries."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise SystemExit(f"  bad pattern: {exc}")
    total = 0
    for _i, _rec, _t, c in scan(path):
        total = max(total, c)
    hits = []
    for idx, rec, text, done in scan(path):
        if before_compact and done >= total:
            continue
        m = rx.search(text)
        if not m:
            continue
        a = max(0, m.start() - context // 2)
        excerpt = " ".join(text[a:a + context].split())
        hits.append({"idx": idx, "when": _when(rec),
                     "who": SPEAKER.get(rec.get("type"), str(rec.get("type"))),
                     "compactions_before": done, "excerpt": excerpt[:MAX_LINE],
                     "chars": len(text)})
        if len(hits) >= max_hits:
            break
    return hits


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    cwd = Path(os.getcwd())

    def opt(name, default):
        for f in flags:
            if f.startswith(f"--{name}="):
                return f.split("=", 1)[1]
        if f"--{name}" in sys.argv:
            i = sys.argv.index(f"--{name}")
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                return sys.argv[i + 1]
        return default

    files = transcripts_for(cwd)
    if "--sessions" in flags:
        if not files:
            print(f"  no transcript for {cwd}")
            return 0
        for fp in files:
            mb = fp.stat().st_size / 1_000_000
            n = sum(1 for _ in scan(fp))
            last = datetime.fromtimestamp(fp.stat().st_mtime).strftime("%d.%m %H:%M")
            print(f"  {fp.stem[:8]}…  {mb:6.1f} MB  {n:5} zapisa  poslednje {last}")
        return 0

    pattern = next((a for a in args if a not in ("--context", "--max")), "")
    if not pattern:
        print(__doc__.strip())
        return 1
    ctx = int(opt("context", CONTEXT))
    cap = int(opt("max", MAX_HITS))

    # Summarised layer first, raw second. `--raw-only` skips straight to the
    # transcript when the distilled answer is the wrong one -- looking for the
    # exact words someone used, rather than the decision they led to.
    jhits = [] if "--raw-only" in flags else journal_hits(
        pattern, context=ctx, max_hits=cap)
    if jhits:
        print(f"\n  žurnal — {len(jhits)} pogodak/pogodaka (sažeti sloj)")
        for h in jhits:
            print(f"  {h['file']}:{h['line']:<4} {h['excerpt']}")

    if not files:
        # The journal is searchable without any transcript at all, so silence
        # here is only correct when that layer also found nothing.
        if not jhits:
            print(f"  no transcript for {cwd} — nothing to search")
        return 0

    targets = files if "--all-sessions" in flags else files[:1]
    shown = 0
    for fp in targets:
        hits = search(fp, pattern, before_compact="--before-compact" in flags,
                      context=ctx, max_hits=cap)
        if not hits:
            continue
        print(f"\n  {fp.stem[:8]}…  {len(hits)} pogodak/pogodaka")
        for h in hits:
            tag = f"·{h['compactions_before']}saž" if h["compactions_before"] else "     "
            print(f"  [{h['when']}] {h['who']:6} {tag} #{h['idx']:<5} {h['excerpt']}")
        shown += len(hits)
    if not shown and not jhits:
        print(f"  ništa za {pattern!r} u {len(targets)} transkript(a)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
