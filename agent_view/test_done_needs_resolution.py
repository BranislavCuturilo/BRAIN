#!/usr/bin/env python3
"""A ticket cannot be marked done without the text the customer will read.

Measured 2026-09-10: four tickets sat `done` locally with a drafted reply and
screenshots in the outbox, and none of them had ever reached the helpdesk. The
system was behaving correctly -- `close_out` refuses to close a ticket whose
`triage.resolution` is empty, because the alternative once sent an engineer's
changelog to a customer (2026-08-19, #08597 and #93164). What it did not do was
SAY so anywhere a person looks: the refusal list rode in the `title` attribute
of the rescan button.

So the refusal moved to the moment of the click. This file pins that, and pins
the two ways it could go wrong: refusing something legitimate, and accepting the
internal report as if it were customer text.

  python agent_view/test_done_needs_resolution.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def store(triage: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "DEMO.json").write_text(json.dumps({
        "rev": 0,
        "tickets": {"99974": {"title": "t", "status": "active",
                              "helpdesk": {"id": "99974"},
                              "triage": triage}},
    }, ensure_ascii=False), encoding="utf-8")
    return d


def patch(dirpath: Path, body: dict):
    server._resolve_ticket_file = lambda _n: dirpath / "DEMO.json"      # noqa: ARG005
    return server.patch_triage("DEMO", "99974", body, None)


def triage_of(dirpath: Path) -> dict:
    d = json.loads((dirpath / "DEMO.json").read_text(encoding="utf-8"))
    return d["tickets"]["99974"].get("triage") or {}


def main() -> int:
    real = server._resolve_ticket_file
    try:
        print("done with nothing written is refused, and the message says what to write")
        d = store({})
        res, code = patch(d, {"state": "done"})
        ok(code == 422, f"422, not a silent accept: {code}")
        ok(res.get("field") == "resolution", f"it names the field: {res}")
        ok("kupca" in res.get("error", ""), "and says it in the operator's language")
        ok(triage_of(d).get("state") != "done", "and NOTHING was written")

        print("the internal report does NOT satisfy it")
        # This is the incident the whole rule came from: the report is a list of
        # commits and files, and it once went to a customer.
        d = store({"report": "2026-09-10 DONE (Faza 3). CostCenter model + migracija 0028."})
        _, code = patch(d, {"state": "done"})
        ok(code == 422, f"a report is not customer text: {code}")

        print("whitespace is not text either")
        d = store({"resolution": "   \n  "})
        _, code = patch(d, {"state": "done"})
        ok(code == 422, f"{code}")

        print("done WITH a resolution goes through")
        d = store({"resolution": "Polje je dodato i podaci su uvezeni."})
        _, code = patch(d, {"state": "done"})
        ok(code == 200, f"{code}")
        ok(triage_of(d)["state"] == "done", "and it is stored")

        print("both in ONE patch is the normal path and must not deadlock")
        # The operator types the text and clicks done in the same action; a guard
        # that only reads the STORED resolution would refuse this forever.
        d = store({})
        _, code = patch(d, {"state": "done", "resolution": "Rešeno, ekran je ispravljen."})
        ok(code == 200, f"state + resolution together: {code}")
        ok(triage_of(d)["resolution"].startswith("Rešeno"), "and both landed")

        print("EVERY state that means done is gated, not just the word `done`")
        # solved_manually and nonsense map to done in store._TRIAGE_TO_STATUS,
        # so close_out will refuse them the same way. Guarding only the literal
        # "done" would leave two doors into the same dead end.
        for st in ("solved_manually", "nonsense"):
            d = store({})
            _, code = patch(d, {"state": st})
            ok(code == 422, f"state {st!r} needs customer text too: {code}")

        print("and the states that do NOT mean done are untouched")
        for st in ("", "working_today", "ignore_today", "ignore_indefinitely"):
            d = store({})
            _, code = patch(d, {"state": st})
            ok(code == 200, f"state {st!r} is not gated: {code}")

        print("editing something else on an already-done ticket is not blocked")
        # The four tickets that exist today are done with no resolution. Touching
        # their order or context must keep working, or the guard breaks the
        # backlog it was written to fix.
        d = store({"state": "done"})
        _, code = patch(d, {"order": 3})
        ok(code == 200, f"no `state` in the patch -> no gate: {code}")
        ok(triage_of(d).get("order") == 3, "and the edit landed")

        print("clearing the resolution while done is still refused")
        d = store({"state": "done", "resolution": "bilo je nesto"})
        _, code = patch(d, {"state": "done", "resolution": ""})
        ok(code == 422, f"you cannot empty it and stay done: {code}")
        ok(triage_of(d)["resolution"] == "bilo je nesto", "the old text survives the refusal")
    finally:
        server._resolve_ticket_file = real

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
