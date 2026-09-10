#!/usr/bin/env python3
"""Offline tests for the brain-update log PARSER (server.parse_brainlog).

The parser is exercised against a FABRICATED `git log --name-only` string — never
live history — so the assertions are stable. Run:  python test_brainlog.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server      # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


M = server._BRAINLOG_MARK

# Two commits, name-only. TAB separates hash/date-time/subject on the header line;
# the following lines are the touched paths (blank line between commits).
SAMPLE = "\n".join([
    f"{M}f5a7aca\t2026-08-09 18:22:15\tfeat(agent_view): Mail UI overhaul",
    "skills/ui-bootstrap/references/forma.md",
    "skills/ui-bootstrap/SKILL.md",
    "agent_view/server.py",                 # outside skills/ agents/ — ignored
    "",
    f"{M}6a963a2\t2026-08-08 23:30:45\tfeat(tickets): AI triage + write-back",
    "agents/ticket-reader.md",
    "skills/tickets/SKILL.md",
    "",
])


def test_parse_basic():
    entries = server.parse_brainlog(SAMPLE)
    check("parse: four in-scope entries (2+2, server.py ignored)",
          len(entries) == 4, str(len(entries)))
    e0 = entries[0]
    check("parse: first entry is newest (skill ui-bootstrap)",
          e0["skill"] == "ui-bootstrap" and e0["kind"] == "skill", str(e0))
    check("parse: header fields split correctly",
          e0["hash"] == "f5a7aca" and e0["date"] == "2026-08-09"
          and e0["time"] == "18:22:15"
          and e0["subject"] == "feat(agent_view): Mail UI overhaul", str(e0))
    check("parse: path preserved", e0["path"] == "skills/ui-bootstrap/references/forma.md", str(e0))

    kinds = {(e["kind"], e["skill"]) for e in entries}
    check("parse: agent entry kind/name from file stem",
          ("agent", "ticket-reader") in kinds, str(kinds))
    check("parse: skill entry for tickets",
          ("skill", "tickets") in kinds, str(kinds))
    check("parse: non skills/agents path is ignored",
          all("server.py" not in e["path"] for e in entries), str(entries))
    check("parse: every entry has the required keys",
          all(set(e) == {"path", "skill", "kind", "date", "time", "subject", "hash"}
              for e in entries), str(entries[0]))
    # commit 2's entries carry commit 2's metadata, not commit 1's
    e_agent = next(e for e in entries if e["kind"] == "agent")
    check("parse: agent entry carries its own commit's hash/subject",
          e_agent["hash"] == "6a963a2"
          and e_agent["subject"] == "feat(tickets): AI triage + write-back", str(e_agent))


def test_parse_cap():
    lines = [f"{M}abc1234\t2026-01-01 00:00:00\tsubject"]
    for i in range(500):
        lines.append(f"skills/skill{i}/SKILL.md")
    entries = server.parse_brainlog("\n".join(lines), cap=200)
    check("parse(cap): capped at 200", len(entries) == 200, str(len(entries)))


def test_parse_windows_backslash_paths():
    text = "\n".join([
        f"{M}deadbee\t2026-02-02 12:00:00\twin paths",
        "skills\\ui-bootstrap\\SKILL.md",
        "",
    ])
    entries = server.parse_brainlog(text)
    check("parse(win): backslash path normalised, skill parsed",
          len(entries) == 1 and entries[0]["skill"] == "ui-bootstrap"
          and entries[0]["path"] == "skills/ui-bootstrap/SKILL.md", str(entries))


def test_parse_empty_and_garbage():
    check("parse(empty): '' -> []", server.parse_brainlog("") == [])
    check("parse(garbage): lines with no header are ignored",
          server.parse_brainlog("skills/x/SKILL.md\nagents/y.md") == [])


def test_brainlog_data_no_crash():
    # Robust to no-git / errors: always a dict with an entries list. Runs against
    # the real brain repo here; content is not asserted (that is parse's job).
    out = server.brainlog_data()
    check("brainlog_data: returns {entries:[...]}",
          isinstance(out, dict) and isinstance(out.get("entries"), list), str(type(out)))


def main():
    for fn in (test_parse_basic, test_parse_cap, test_parse_windows_backslash_paths,
               test_parse_empty_and_garbage, test_brainlog_data_no_crash):
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
