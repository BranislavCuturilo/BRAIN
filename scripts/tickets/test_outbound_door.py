#!/usr/bin/env python3
"""Guard: writeback.py is the ONLY door to the helpdesk's WRITE endpoints, and
adapters/ is the only place that spells the helpdesk API paths.

Why a grep test: a second module calling `adapter.add_comment(...)` or building
`/engineer/tickets/<id>/close/` by hand would bypass claim-before-call and the
sent log — the two things that make an irreversible send answerable later. The
docstrings of writeback.py and PLAN-HUD F0 promise this guard; this file is it.

Allowed `urllib` users outside adapters/ (each talks to something that is NOT
the helpdesk write API): attachments.py (helpdesk MEDIA downloads, read-only),
gemini_client.py (Google), agent_view/gitviz.py + monitor_client.py + hook.py
(GitHub / the VPS monitor / the HUD itself), agent_view/testrun.py (posts test
results to the HUD). Add to ALLOWED_URLLIB only with a reason in this comment.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # scripts/tickets
BRAIN = HERE.parent.parent
SCAN_DIRS = (HERE, BRAIN / "agent_view")

WRITE_CALLS = re.compile(r"\.(add_comment|set_estimate|create_ticket|edit_ticket)\(|"
                         r"\.close\(\s*tid|\.close\(\s*ticket")
API_PATHS = re.compile(r"/engineer/tickets/")
ALLOWED_WRITE_CALLERS = {"writeback.py"}
ALLOWED_URLLIB = {"attachments.py", "gemini_client.py", "gitviz.py", "monitor_client.py",
                  "hook.py", "testrun.py"}         # testrun.py posts test results to the HUD itself

FAILS = []


def ck(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  -- " + detail) if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _py_files():
    for d in SCAN_DIRS:
        for fp in sorted(d.rglob("*.py")):
            if fp.name.startswith("test_") or ".venv" in fp.parts:
                continue
            yield fp


def main() -> int:
    write_callers, path_spellers, urllib_users = set(), set(), set()
    for fp in _py_files():
        text = fp.read_text(encoding="utf-8", errors="replace")
        rel = fp.name if fp.parent.name != "adapters" else "adapters/" + fp.name
        in_adapters = fp.parent.name == "adapters"
        if not in_adapters and WRITE_CALLS.search(text):
            write_callers.add(rel)
        if not in_adapters and API_PATHS.search(text):
            path_spellers.add(rel)
        if re.search(r"^\s*(import urllib\.request|from urllib\.request import|import urllib$)", text, re.M):
            if not in_adapters:
                urllib_users.add(rel)
    ck("only writeback.py calls the adapter's write methods",
       write_callers <= ALLOWED_WRITE_CALLERS, str(sorted(write_callers - ALLOWED_WRITE_CALLERS)))
    ck("no module outside adapters/ spells the helpdesk API path",
       not path_spellers, str(sorted(path_spellers)))
    ck("urllib.request users outside adapters/ are the documented ones",
       urllib_users <= ALLOWED_URLLIB, str(sorted(urllib_users - ALLOWED_URLLIB)))
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
