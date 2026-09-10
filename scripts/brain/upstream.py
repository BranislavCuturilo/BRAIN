#!/usr/bin/env python3
"""What shipped upstream that this brain may have been working around?

**The decay this catches.** A skill written to route around something Claude
Code could not do is correct on the day it is written and slowly becomes a lie.
The capability ships, the skill stays, and it costs context in every session
while sometimes contradicting the native behaviour. Nothing notices, because a
skill has no expiry and the changelog is read by nobody.

Found on the first run, in 2.1.261:

    Added /skill-doctor to show which loaded skills go unused and what they
    cost in context, so you can prune them

That overlaps `usage.py`, `budget.py` and part of `ops-prune` -- partially, not
wholly, which is the interesting case and the one that needs a redesign rather
than a deletion.

**This script proposes; it never archives.** Matching a release note to a skill
is a judgement: "covered", "partly covered" and "unrelated" look identical to a
keyword search. It reads the changelog, works out what is NEW since the last
time it was run, and says which brain files mention the same things. Deciding
what that means is `/brain:ops-upstream`.

  upstream.py                what shipped since the last check, and what it touches
  upstream.py --since 2.1.0  from a specific version
  upstream.py --seen         record today's version as reviewed
  upstream.py --json
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
SEEN = ROOT / "journal" / "upstream-seen.json"
CHANGELOG = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"

#: Words that appear in half the release notes and match half the brain.
NOISE = {
    "added", "fixed", "changed", "removed", "improved", "now", "when", "with",
    "from", "that", "this", "the", "and", "for", "not", "you", "your", "its",
    "session", "sessions", "claude", "code", "file", "files", "output", "run",
    "running", "settings", "setting", "command", "commands", "tool", "tools",
    "agent", "agents", "skill", "skills", "hook", "hooks", "model", "models",
    "error", "errors", "message", "messages", "would", "could", "after",
    "before", "instead", "rather", "which", "while", "into", "over", "under",
}


def fetch() -> str:
    sys.path.insert(0, str(ROOT / "scripts" / "reach"))
    try:
        import channels as ch                                    # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(f"scripts/reach is required: {exc}")
    text, _via = ch.web(CHANGELOG)
    return text


def releases(text: str) -> list[tuple[str, list[str]]]:
    """[(version, [entry, …])] newest first."""
    out, ver, items = [], None, []
    for line in text.splitlines():
        m = re.match(r"^#{1,3}\s+(\d+\.\d+\.\d+)\s*$", line.strip())
        if m:
            if ver:
                out.append((ver, items))
            ver, items = m.group(1), []
            continue
        if ver and line.strip().startswith(("- ", "* ")):
            items.append(line.strip()[2:].strip())
    if ver:
        out.append((ver, items))
    return out


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def signals(entry: str) -> set[str]:
    """The EXACT identifiers in a release note: slash commands, flags, settings
    keys, backticked names.

    Bare prose words were tried first and had to go. They matched "through",
    "status", "raise" and "saved" across half the repository -- 14 of ~30
    entries "touched the brain", which is a list nobody reads. An identifier
    either appears in the brain or it does not.
    """
    out: set[str] = set()
    # The slash must START the token. Without the look-behind, "progress/status"
    # in a UI skill yields "/status" and the note about the /status COMMAND
    # appears to touch a Bootstrap rule about progress indicators.
    out |= {m for m in re.findall(r"(?<![\w/])/[a-z][\w-]{2,}", entry)}
    out |= {m for m in re.findall(r"--[a-z][\w-]{2,}", entry)}
    out |= {m for m in re.findall(r"`([^`\s]{3,40})`", entry)}
    out |= {m for m in re.findall(r"\b([a-z]+[A-Z][A-Za-z]{3,})\b", entry)}
    return {s for s in out if len(s) >= 4 and s.lower() not in NOISE}


def brain_index() -> dict[str, str]:
    """path -> lowercased text, for the files a release note could make stale."""
    idx = {}
    for pat in ("skills/**/*.md", "agents/*.md", "scripts/**/*.py",
                "CLAUDE.md", "README.md", "hooks/hooks.json"):
        for f in ROOT.glob(pat):
            if any(p in ("__pycache__", "archive", ".git") for p in f.parts):
                continue
            if f.name in ("upstream.py", "test_upstream.py"):
                continue        # a tool citing its own docstring is a loop
            try:
                idx[str(f.relative_to(ROOT)).replace("\\", "/")] = \
                    f.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
    return idx


def touches(sig: set[str], idx: dict[str, str], min_hits: int = 1) -> list[tuple[str, list[str]]]:
    """Which brain files mention this note's exact identifiers.

    ONE hit is enough now that only identifiers are searched -- `/skill-doctor`
    appearing anywhere in the brain is a fact, not a coincidence.

    The boundary is enforced on BOTH sides, and fixing only one side was not
    enough. `signals()` learned that "progress/status" is not the /status
    command; this searched with a plain substring test, so the same file matched
    anyway. An identifier has to sit at a token boundary in the file too.

    The index is lowercased, so the needle is as well -- otherwise every
    camelCase settings key (`maxOutputTokens`) silently never matched.
    """
    pats = {s: re.compile(r"(?<![\w/-])" + re.escape(s.lower()) + r"(?![\w-])")
            for s in sig}
    hits = []
    for path, text in idx.items():
        found = sorted(s for s, pat in pats.items() if pat.search(text))
        if len(found) >= min_hits:
            hits.append((path, found[:6]))
    hits.sort(key=lambda h: -len(h[1]))
    return hits[:5]


def skill_names() -> list[str]:
    return [p.parent.name for p in ROOT.glob("skills/*/SKILL.md")]


def tool_names() -> list[str]:
    """One entry per tool NAME. Deduplicated because this list is read by a
    human making a judgement, and `sync.py` printed twice reads as two tools."""
    return sorted({p.name for p in ROOT.glob("scripts/**/*.py")
                   if not p.name.startswith("test_") and p.name != "__init__.py"
                   and "__pycache__" not in p.parts and "archive" not in p.parts})


def load_seen() -> dict:
    if SEEN.is_file():
        try:
            return json.loads(SEEN.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def declared_exits() -> list[tuple[str, str]]:
    """Every place that already said what would end it: a `DIES WHEN:` line in a
    module or skill, and any finding carrying `dies_when`.

    This is the half of the review that is NOT a guess. Everything above is a
    keyword match and says so; these are sentences the rule wrote about itself,
    so judging them against a release note is a comparison rather than an
    inference. Written after a pass over 2.1.262-2.1.267 where 6 entries looked
    like they touched our files and 4 of the 6 were coincidence.
    """
    out: list[tuple[str, str]] = []
    for base in ("scripts", "skills", "agents", "hooks"):
        d = ROOT / base
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix not in (".py", ".md") or "archive" in f.parts:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # At the START of a line only. Matched anywhere, this file found
            # ITSELF -- its own docstring names the marker while explaining it,
            # and a collector that reports its own prose is a false positive in
            # the one place that must not have them.
            m = re.search(r"^[ 	]*DIES WHEN:", text, re.M)
            if not m:
                continue
            after = text[m.end():]
            said = " ".join(after.split("\n\n")[0].split())
            out.append((f.relative_to(ROOT).as_posix(), said))
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "brain"))
        import findings as F                                      # noqa: PLC0415
        for row in F.across():
            if row.get("dies_when"):
                out.append((f"finding {row['id'][:40]}", str(row["dies_when"])))
    except Exception:                                             # noqa: BLE001
        pass
    return out


def main() -> int:
    args = sys.argv[1:]
    seen = load_seen()

    def opt(flag):
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None

    text = fetch()
    rel = releases(text)
    if not rel:
        print("  could not parse the changelog")
        return 1
    latest = rel[0][0]

    if "--seen" in args:
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        SEEN.write_text(json.dumps({"version": latest, "at": date.today().isoformat()},
                                   indent=2) + "\n", encoding="utf-8")
        print(f"  recorded {latest} as reviewed")
        return 0

    since = opt("--since") or seen.get("version")
    if since:
        new = [(v, items) for v, items in rel if vtuple(v) > vtuple(since)]
    else:
        new = rel[:1]          # first ever run: just the latest

    idx = brain_index()
    added, report = [], []
    for ver, items in new:
        for entry in items:
            # A FIX to something never worked around is not our business; a new
            # or changed CAPABILITY is. That is the only mechanical filter here.
            if not re.match(r"^(added|changed|improved)", entry, re.I):
                continue
            hit = touches(signals(entry), idx)
            added.append({"version": ver, "entry": entry, "touches": hit})
            if hit:
                report.append(added[-1])

    if "--json" in args:
        print(json.dumps({"latest": latest, "since": since,
                          "capabilities": added,
                          "skills": sorted(skill_names()),
                          "tools": sorted(tool_names())}, indent=2, ensure_ascii=False))
        return 0

    print("=" * 74)
    print(f"UPSTREAM — Claude Code {latest}" +
          (f", new since {since}" if since else " (first check)"))
    print("=" * 74)
    if not since:
        print("  No previous check recorded, so only the newest release is read.")
    print(f"  {len(new)} release(s), {len(added)} new/changed capabilit(y/ies)\n")

    for r in added:
        mark = "!" if r["touches"] else " "
        print(f"  {mark} [{r['version']}] {r['entry'][:92]}")
        for path, words in r["touches"]:
            print(f"        names {', '.join(words[:3])}  ->  {path}")

    exits = declared_exits()
    if exits:
        print()
        print("-" * 74)
        print("  DECLARED EXIT CONDITIONS -- compare these to the entries above.")
        print("  These are not keyword matches: each one is a sentence the rule")
        print("  wrote about what would end it, so this half is a comparison.")
        print()
        for where, said in exits:
            print(f"  {where}")
            print(f"      dies when: {said[:150]}")
        print()
        print("  A condition that has arrived is a REASON TO LOOK, never a licence")
        print("  to delete -- the rule may still stand for a second reason nobody")
        print("  wrote down. /brain:ops-prune, and the owner says yes per item.")

    print()
    print("-" * 74)
    print("  JUDGE THESE AGAINST WHAT THE BRAIN ALREADY DOES:")
    print()
    print("  skills : " + ", ".join(sorted(skill_names())))
    print()
    print("  tools  : " + ", ".join(sorted(tool_names())))
    print()
    print("  `!` marks an entry whose exact identifiers already appear in the")
    print("  brain. The ABSENCE of a mark means nothing: /skill-doctor shares no")
    print("  word with budget.py or usage.py, and supersedes part of both. That")
    print("  is why every capability is listed, not only the matches.")
    print()
    print("  NOT A VERDICT. A keyword match cannot tell 'covered' from 'partly")
    print("  covered' from 'coincidence' — that is the judgement, and it is")
    print("  /brain:ops-upstream. Fully covered -> archive/ (never delete: another")
    print("  agent has no such capability). Partly -> redesign the skill down to")
    print("  what is still uncovered. Then: upstream.py --seen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
