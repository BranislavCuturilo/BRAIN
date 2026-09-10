#!/usr/bin/env python3
"""Offline tests for estimate.py - who is a candidate, how the calibration factor
moves, and the write rules of the pass (never overwrite, never write blind, never
send a nonsense number). Temp roots throughout, a fake helpdesk adapter and a fake
Gemini call: nothing here touches the real store, the network, or a real key.
Run: python test_estimate.py"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_estimates  # noqa: E402
import estimate  # noqa: E402
from adapters.acme_helpdesk import HelpdeskError  # noqa: E402

FAILS = []


def ck(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label
          + (("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(label)


# --------------------------------------------------------------------------- #
#  Doubles
# --------------------------------------------------------------------------- #
class FakeAdapter:
    """The helpdesk, in memory. `remote` is what a FRESH read returns for each
    ticket - the whole point of the pass is that this, not the local mirror,
    decides whether a write happens."""

    def __init__(self, remote=None, fail_get=()):
        self.remote = dict(remote or {})
        self.fail_get = set(str(t) for t in fail_get)
        self.gets = []
        self.patched = []

    detail_closed: set = set()          # tickets the FRESH read reports as closed

    def get_detail(self, ticket_id):
        tid = str(ticket_id)
        self.gets.append(tid)
        if tid in self.fail_get:
            raise HelpdeskError("GET failed: TimeoutError")
        return {"ticket_id": tid, "estimated_time": self.remote.get(tid),
                "is_closed": tid in self.detail_closed}

    def set_estimate(self, ticket_id, hours):
        self.patched.append((str(ticket_id), hours))
        self.remote[str(ticket_id)] = hours
        return {}


class FakeGemini:
    """One call, one JSON answer. With no explicit `rows` it answers for exactly
    the tickets the prompt listed (what a well-behaved model does), so the cap
    test does not have to hand-build 40 rows."""

    def __init__(self, rows=None, hours=2.0, raises=None):
        self.rows, self.hours, self.raises = rows, hours, raises
        self.calls = []

    def __call__(self, prompt, *, json_out=True, api_key="", model=""):
        self.calls.append({"prompt": prompt, "json_out": json_out,
                           "api_key": api_key, "model": model})
        if self.raises is not None:
            raise self.raises
        if self.rows is not None:
            return {"estimates": self.rows}
        return {"estimates": [{"ticket": t, "module": m, "hours": self.hours,
                               "why": "zato"} for t, m in _prompt_tickets(prompt)]}


def _prompt_tickets(prompt):
    return re.findall(r"^--- ticket (\S+) \(module (\S+)\)$", prompt, re.M)


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #
def _write_store(root, tickets, module="INV", keep=False):
    fp = Path(root) / (module + ".json")
    if keep and fp.is_file():                     # add to what is there (multi-round tests)
        d = json.loads(fp.read_text(encoding="utf-8"))
        d["tickets"].update(tickets)
        d["rev"] = int(d.get("rev") or 0) + 1
        fp.write_text(json.dumps(d), encoding="utf-8")
        return
    fp.write_text(json.dumps({"project": {"name": module}, "tickets": tickets, "rev": 1}),
                  encoding="utf-8")


def _force_interval_hours(root, work_id, hours, module="INV"):
    """Rewrite the worklog so `work_id` is ONE complete interval of `hours` for
    its ticket(s) — a measured, operator-ended run (not auto_closed)."""
    import store as _st
    import worklog as _wl
    evs = [e for e in _wl.read_events(root) if e.get("work_id") != work_id]
    starts = [e for e in _wl.read_events(root) if e.get("work_id") == work_id and e.get("ev") == "start"]
    d = Path(root) / "worklog"
    for f in d.glob("*.jsonl"):
        f.unlink()
    for e in evs:
        _st.append_jsonl(d / (str(e.get("device") or "dev") + ".jsonl"), e)
    for st in starts:
        st = dict(st); st["at"] = "2026-08-01T09:00:00"
        en = dict(st); en["ev"] = "end"; en["auto_closed"] = False
        mins = int(round(hours * 60))
        en["at"] = "2026-08-01T%02d:%02d:00" % (9 + mins // 60, mins % 60)
        _st.append_jsonl(d / "dev.jsonl", st)
        _st.append_jsonl(d / "dev.jsonl", en)


def _close_ticket(root, tid, module="INV"):
    fp = Path(root) / (module + ".json")
    d = json.loads(fp.read_text(encoding="utf-8"))
    t = d["tickets"][str(tid)]
    t.setdefault("helpdesk", {})["is_closed"] = True
    t["closed"] = {"at": "2026-08-01T15:00:00", "resolution": "x"}
    fp.write_text(json.dumps(d), encoding="utf-8")


def _ticket(estimated_time=None, is_closed=False, state=None, ai=None,
            scope="", title="t", desc="d", rating=None):
    t = {"title": title,
         "original": {"title": title, "description": desc, "category": "Bug"},
         "helpdesk": {"is_closed": is_closed, "estimated_time": estimated_time,
                      "rating": rating}}
    if state or ai:
        t["triage"] = {}
        if state:
            t["triage"]["state"] = state
        if ai:
            t["triage"]["ai_estimate"] = ai
    if scope:
        t["reading"] = {"scope": scope}
    return t


def _write_history(root, pairs, module="INV", scope="ui"):
    """A synthetic estimates.csv: `pairs` is [(estimated_h, actual_h), ...]."""
    lines = [",".join(build_estimates.HEADER)]
    for i, (est, act) in enumerate(pairs, 1):
        # raw_h == estimated_h here (uncalibrated history) unless a 3-tuple says otherwise
        raw = est
        lines.append("%s,%d,srednja,%s,%.2f,%.2f,%.2f,2026-08-%02dT09:00:00,"
                     "2026-08-%02dT15:00:00,gemini" % (module, 9000 + i, scope,
                                                       est, raw, act, i, i))
    (Path(root) / build_estimates.FILE_NAME).write_text("\n".join(lines) + "\n",
                                                        encoding="utf-8")


def _load(root, module="INV"):
    return json.loads((Path(root) / (module + ".json")).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
#  candidates()
# --------------------------------------------------------------------------- #
def test_candidates_filter():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {
            "1": _ticket(estimated_time=None),                 # null -> candidate
            "2": _ticket(estimated_time=0),                    # 0 -> candidate
            "3": _ticket(estimated_time="0.00"),               # the DecimalField string
            "4": _ticket(estimated_time=3),                    # estimated -> no
            "5": _ticket(estimated_time="2.50"),               # estimated (string) -> no
            "6": _ticket(ai={"hours": 2.0, "by": "gemini"}),   # already ours -> no
            "7": _ticket(is_closed=True),                      # closed -> no
            "8": _ticket(state="done"),                        # terminal -> no
            "9": _ticket(state="solved_manually"),             # terminal -> no
            "10": _ticket(state="working_today"),              # active state -> candidate
            "junk": "not a dict",
        })
        (Path(root) / "modules.json").write_text('{"modules": {}}', encoding="utf-8")
        got = [c["ticket"] for c in estimate.candidates(root)]
        ck("candidates: null and 0 are both 'no estimate'", "1" in got and "2" in got, got)
        ck("candidates: the helpdesk's '0.00' string is 'no estimate' too", "3" in got, got)
        ck("candidates: a real estimate excludes the ticket",
           "4" not in got and "5" not in got, got)
        ck("candidates: an existing ai_estimate excludes the ticket", "6" not in got, got)
        ck("candidates: closed and terminal-triage tickets are excluded",
           not ({"7", "8", "9"} & set(got)), got)
        ck("candidates: a non-terminal triage state stays a candidate", "10" in got, got)
        ck("candidates: the catalogs are not scanned", set(got) == {"1", "2", "3", "10"}, got)
        c = [c for c in estimate.candidates(root) if c["ticket"] == "1"][0]
        ck("candidates: the row carries the keys the prompt needs",
           set(c) == {"module", "ticket", "title", "desc", "category", "scope", "complexity"},
           sorted(c))
        ck("candidates: module is the file stem", c["module"] == "INV", c["module"])


# --------------------------------------------------------------------------- #
#  calibration
# --------------------------------------------------------------------------- #
def test_calibration_moves_toward_actual():
    with tempfile.TemporaryDirectory() as root:
        _write_history(root, [(60.0, 15.0)] * 5)               # ratio 0.25 exactly
        hist = estimate.history(root)
        f, basis = estimate.calibration_factor(hist, "INV", "ui")
        ck("calibration: 60h estimated / 15h real -> factor 0.25", f == 0.25, f)
        ck("calibration: the basis names the bucket, n and the median",
           basis.startswith("(INV, ui) n=5 medijana 0.25"), basis)
    with tempfile.TemporaryDirectory() as root:
        _write_history(root, [(2.0, 2.0)] * 4)
        f, _b = estimate.calibration_factor(estimate.history(root), "INV", "ui")
        ck("calibration: 2h estimated / 2h real -> factor 1.0", f == 1.0, f)
    with tempfile.TemporaryDirectory() as root:
        _write_history(root, [(10.0, 1.0)] * 3)                # ratio 0.1 -> clamped
        f, _b = estimate.calibration_factor(estimate.history(root), "INV", "ui")
        ck("calibration: a ratio under 0.25 clamps to 0.25", f == 0.25, f)
        _write_history(root, [(1.0, 10.0)] * 3)                # ratio 10 -> clamped
        f, _b = estimate.calibration_factor(estimate.history(root), "INV", "ui")
        ck("calibration: a ratio over 3 clamps to 3.0", f == 3.0, f)
    with tempfile.TemporaryDirectory() as root:
        _write_history(root, [(4.0, 2.0)] * 3, scope="ui")
        hist = estimate.history(root)
        f, basis = estimate.calibration_factor(hist, "INV", "logic")
        ck("calibration: an unknown scope falls back to the module", f == 0.5, f)
        ck("calibration: the module fallback says so in the basis",
           basis.startswith("(INV) n=3"), basis)
        f, basis = estimate.calibration_factor(hist, "DEMO", "ui")
        ck("calibration: an unknown module is 1.0", f == 1.0, f)
        ck("calibration: no history is spelled out", "bez istorije" in basis, basis)
    with tempfile.TemporaryDirectory() as root:
        # A row missing one half of the pair teaches nothing about the ratio.
        (Path(root) / build_estimates.FILE_NAME).write_text(
            ",".join(build_estimates.HEADER) + "\nPOPIS,1,,ui,4.00,,,,gemini\n"
            "INV,2,,ui,,3.00,,,gemini\n", encoding="utf-8")
        hist = estimate.history(root)
        ck("calibration: half-pairs are ignored", hist["pairs"] == 0, hist["pairs"])
        f, _b = estimate.calibration_factor(hist, "INV", "ui")
        ck("calibration: no usable pair -> factor 1.0", f == 1.0, f)
    with tempfile.TemporaryDirectory() as root:
        ck("calibration: a missing estimates.csv is not an error",
           estimate.history(root)["pairs"] == 0)


def test_prompt_carries_the_history_block_and_the_tickets():
    with tempfile.TemporaryDirectory() as root:
        _write_history(root, [(4.0, 2.0)] * 6)
        hist = estimate.history(root)
        cands = [{"module": "INV", "ticket": "77", "title": "Naslov", "desc": "Opis",
                  "category": "Bug", "scope": "ui", "complexity": "S"}]
        p = estimate.build_prompt(cands, hist["text"])
        ck("prompt: the history block is present",
           "HISTORY" in p and "median ratio 0.50" in p, p[:200])
        ck("prompt: the history block names the bucket and n",
           "INV / ui" in p and "n=6" in p)
        ck("prompt: the history block carries example pairs", "est 4.00h -> real 2.00h" in p)
        ck("prompt: the ticket is listed with module, title and description",
           "--- ticket 77 (module INV)" in p and "Naslov" in p and "Opis" in p)
        ck("prompt: it asks for the agreed JSON shape",
           '"estimates"' in p and '"hours"' in p and '"why"' in p)
        ck("prompt: it forbids 0 and pins the scale", "Never 0" in p and "0.25-1" in p)
        ck("prompt: the ticket text is marked untrusted", "UNTRUSTED DATA" in p)
        empty = estimate.build_prompt(cands, estimate.NO_HISTORY_TEXT)
        ck("prompt: with no history it says so instead of lying about scale",
           "none yet" in empty)


# --------------------------------------------------------------------------- #
#  run()
# --------------------------------------------------------------------------- #
def test_run_writes_calibrated_and_rounded_hours():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket(scope="ui")})
        _write_history(root, [(4.0, 2.0)] * 4)                 # factor 0.5
        ad, gem = FakeAdapter(), FakeGemini(hours=7.0)         # 7.0 * 0.5 = 3.5
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None, gemini_call=gem)
        ck("run: exactly ONE gemini call for the whole batch", len(gem.calls) == 1, len(gem.calls))
        ck("run: the call asks for JSON", gem.calls[0]["json_out"] is True)
        ck("run: the key is passed through", gem.calls[0]["api_key"] == "k")
        ck("run: the helpdesk got the calibrated hours", ad.patched == [("1", 3.5)], ad.patched)
        ck("run: the report row carries hours, raw, factor and basis",
           rep["estimated"] == [{"module": "INV", "ticket": "1", "hours": 3.5,
                                 "raw": 7.0, "factor": 0.5,
                                 "basis": "(INV, ui) n=4 medijana 0.50"}], rep["estimated"])
        ck("run: candidates is the count found", rep["candidates"] == 1, rep["candidates"])
        tri = _load(root)["tickets"]["1"]["triage"]["ai_estimate"]
        ck("run: triage.ai_estimate mirrors hours/by and the estimator's extras",
           tri["hours"] == 3.5 and tri["by"] == "gemini" and tri["raw"] == 7.0
           and tri["factor"] == 0.5 and tri["why"] == "zato" and tri["basis"], tri)
        ck("run: the estimated ticket is no longer a candidate",
           estimate.candidates(root) == [], estimate.candidates(root))


def test_run_rounds_to_a_quarter_hour_and_clamps():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket(), "2": _ticket(), "3": _ticket()})
        ad = FakeAdapter()
        gem = FakeGemini(rows=[{"ticket": "1", "module": "INV", "hours": 1.3},
                               {"ticket": "2", "module": "INV", "hours": 0.05},
                               {"ticket": "3", "module": "INV", "hours": 190}])
        estimate.run(root, "k", adapter=ad, log=lambda *_a: None, gemini_call=gem)
        got = dict(ad.patched)
        ck("run: 1.3h rounds to the quarter hour (1.25)", got.get("1") == 1.25, got)
        ck("run: below the floor clamps to 0.25 (0 is never sent)", got.get("2") == 0.25, got)
        ck("run: above the ceiling clamps to 40", got.get("3") == 40.0, got)


def test_run_never_overwrites_an_estimate_that_appeared_since_the_sync():
    # THE test of this phase: the local mirror says "no estimate", the helpdesk
    # says 3 - somebody estimated it between the sync and now. No PATCH, and the
    # mirror is refreshed so the next pass agrees with the helpdesk.
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket(estimated_time=None), "2": _ticket()})
        ad = FakeAdapter(remote={"1": 3})
        gem = FakeGemini(hours=5.0)
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None, gemini_call=gem)
        ck("fresh read: the ticket estimated meanwhile got NO patch",
           [p for p in ad.patched if p[0] == "1"] == [], ad.patched)
        ck("fresh read: it is reported as skipped, with the reason",
           [s for s in rep["skipped"]
            if s["ticket"] == "1" and s["reason"] == "helpdesk already estimated"],
           rep["skipped"])
        ck("fresh read: the local mirror is refreshed to the helpdesk's value",
           _load(root)["tickets"]["1"]["helpdesk"]["estimated_time"] == 3,
           _load(root)["tickets"]["1"]["helpdesk"])
        ck("fresh read: no ai_estimate is invented for it",
           not (_load(root)["tickets"]["1"].get("triage") or {}).get("ai_estimate"))
        ck("fresh read: it is no longer a candidate",
           "1" not in [c["ticket"] for c in estimate.candidates(root)])
        ck("fresh read: the OTHER ticket was still written",
           dict(ad.patched).get("2") == 5.0, ad.patched)
        ck("fresh read: every ticket is re-read before its write",
           sorted(ad.gets) == ["1", "2"], ad.gets)


def test_run_skips_when_the_fresh_read_fails():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket(), "2": _ticket()})
        ad = FakeAdapter(fail_get=["1"])
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None,
                           gemini_call=FakeGemini(hours=2.0))
        ck("unreachable: no blind write when the read failed",
           [p for p in ad.patched if p[0] == "1"] == [], ad.patched)
        ck("unreachable: reported as skipped with the reason",
           [s for s in rep["skipped"]
            if s["ticket"] == "1" and s["reason"] == "helpdesk unreachable"], rep["skipped"])
        ck("unreachable: one failure does not stop the pass",
           dict(ad.patched).get("2") == 2.0, ad.patched)


def test_run_rejects_nonsense_numbers():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {str(i): _ticket() for i in range(1, 8)})
        ad = FakeAdapter()
        gem = FakeGemini(rows=[
            {"ticket": "1", "module": "INV", "hours": 0},
            {"ticket": "2", "module": "INV", "hours": -1},
            {"ticket": "3", "module": "INV", "hours": 99999},
            {"ticket": "4", "module": "INV", "hours": "not a number"},
            {"ticket": "5", "module": "INV", "hours": float("nan")},
            {"ticket": "6", "module": "INV", "hours": None},
            {"ticket": "7", "module": "INV", "hours": 2},
        ])
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None, gemini_call=gem)
        ck("bad hours: 0, -1, 99999, text, NaN and null are all refused",
           [p[0] for p in ad.patched] == ["7"], ad.patched)
        ck("bad hours: each refusal is reported",
           len([s for s in rep["skipped"] if s["reason"] == "bad hours"]) == 6,
           rep["skipped"])
        ck("bad hours: a refused ticket is never even read from the helpdesk",
           ad.gets == ["7"], ad.gets)


def test_run_ignores_a_ticket_the_model_invented():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket(), "2": _ticket(estimated_time=5),
                            "3": _ticket(is_closed=True)})
        ad = FakeAdapter()
        gem = FakeGemini(rows=[{"ticket": "2", "module": "INV", "hours": 3},
                               {"ticket": "3", "module": "INV", "hours": 3},
                               {"ticket": "999", "module": "INV", "hours": 3},
                               {"ticket": "1", "module": "INV", "hours": 3},
                               {"ticket": "1", "module": "INV", "hours": 8}])
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None, gemini_call=gem)
        ck("untrusted answer: only the batch's own tickets are written",
           ad.patched == [("1", 3.0)], ad.patched)
        ck("untrusted answer: the strays are reported, not silently dropped",
           len([s for s in rep["skipped"] if s["reason"] == "not a candidate"]) == 3,
           rep["skipped"])
        ck("untrusted answer: a repeated ticket is written once",
           [s for s in rep["skipped"] if s["reason"].startswith("duplicate")], rep["skipped"])


def test_run_caps_the_batch():
    with tempfile.TemporaryDirectory() as root:
        n = estimate.MAX_CANDIDATES + 5
        _write_store(root, {str(i): _ticket() for i in range(1, n + 1)})
        ad, gem = FakeAdapter(), FakeGemini(hours=1.0)
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None, gemini_call=gem)
        ck("cap: the report counts every candidate found", rep["candidates"] == n, rep["candidates"])
        ck("cap: only MAX_CANDIDATES reach the prompt",
           len(_prompt_tickets(gem.calls[0]["prompt"])) == estimate.MAX_CANDIDATES,
           len(_prompt_tickets(gem.calls[0]["prompt"])))
        ck("cap: still exactly one gemini call", len(gem.calls) == 1, len(gem.calls))
        ck("cap: the overflow is reported as skipped 'cap'",
           len([s for s in rep["skipped"] if s["reason"] == "cap"]) == 5, rep["skipped"])
        ck("cap: exactly MAX_CANDIDATES were written",
           len(ad.patched) == estimate.MAX_CANDIDATES, len(ad.patched))


def test_a_gemini_failure_writes_nothing():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket()})
        ad = FakeAdapter()
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None,
                           gemini_call=FakeGemini(raises=RuntimeError("boom")))
        ck("gemini down: nothing is written", ad.patched == [] and ad.gets == [])
        ck("gemini down: the failure is reported, not swallowed", rep["errors"], rep)
        ck("gemini down: run() does not raise", rep["estimated"] == [])
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket()})
        ad = FakeAdapter()
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None,
                           gemini_call=lambda *_a, **_k: {"nonsense": True})
        ck("gemini junk: an answer without 'estimates' writes nothing",
           ad.patched == [] and rep["errors"], rep)


def test_no_adapter_means_no_gemini_call_at_all():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket()})
        gem = FakeGemini()
        rep = estimate.run(root, "k", adapter=None, log=lambda *_a: None, gemini_call=gem)
        ck("no creds: the shared free quota is not spent", gem.calls == [], gem.calls)
        ck("no creds: reported as an error", rep["errors"], rep)


def test_estimates_csv_is_rebuilt_after_a_pass():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket()})
        fp = Path(root) / build_estimates.FILE_NAME
        ck("csv: absent before the pass", not fp.exists())
        estimate.run(root, "k", adapter=FakeAdapter(), log=lambda *_a: None,
                     gemini_call=FakeGemini(hours=2.0))
        text = fp.read_text(encoding="utf-8")
        ck("csv: rebuilt with the new estimate and its date",
           "INV,1," in text and ",2.00," in text and "gemini" in text, text)
        ck("csv: the header is the agreed one",
           text.splitlines()[0] == ",".join(build_estimates.HEADER))
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket(estimated_time=4)})     # no candidates at all
        estimate.run(root, "k", adapter=FakeAdapter(), log=lambda *_a: None,
                     gemini_call=FakeGemini())
        ck("csv: a pass that changed nothing writes no file",
           not (Path(root) / build_estimates.FILE_NAME).exists())


def test_row_estimate_contract():
    r = estimate.row_estimate(_ticket(estimated_time="0.00"), 1.5)
    ck("row: all four keys are always present",
       set(r) == {"ai_h", "helpdesk_h", "actual_h", "by"}, sorted(r))
    ck("row: the helpdesk's '0.00' is NOT shown as an estimate of zero",
       r["helpdesk_h"] is None and r["by"] == "", r)
    ck("row: measured hours pass through", r["actual_h"] == 1.5, r)
    r = estimate.row_estimate(_ticket(estimated_time=3), None)
    ck("row: a helpdesk estimate is a number and is attributed",
       r["helpdesk_h"] == 3.0 and r["by"] == "helpdesk" and r["ai_h"] is None, r)
    r = estimate.row_estimate(_ticket(estimated_time=3, ai={"hours": 2.5, "by": "gemini"}), 4)
    ck("row: our own estimate wins the attribution",
       r["ai_h"] == 2.5 and r["helpdesk_h"] == 3.0 and r["by"] == "gemini", r)


def test_summary_shape():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket(), "2": _ticket(estimated_time=4, rating=5),
                            "3": _ticket(estimated_time=6)})       # has an estimate, no rating
        _write_history(root, [(4.0, 2.0), (2.0, 3.0)])
        res = estimate.summary(root)
        ck("summary: the three keys of the contract",
           set(res) == {"rows", "per_module", "history_note"}, sorted(res))
        ck("summary: reading it does NOT rewrite estimates.csv",
           (Path(root) / build_estimates.FILE_NAME).read_text(encoding="utf-8")
           .splitlines()[1].startswith("INV,9001"))
        ck("summary: a row carries the agreed columns",
           res["rows"] and set(res["rows"][0]) == {"module", "ticket", "estimated_h", "raw_h",
                                                   "actual_h", "by", "scope",
                                                   "complexity", "closed_at", "rating",
                                                   "rating_avg", "rating_n"},
           res["rows"][:1])
        by_ticket = {r["ticket"]: r for r in res["rows"]}
        ck("summary: F8 - a row carries the ticket's rating, read straight from the store",
           by_ticket.get("2", {}).get("rating") == 5, res["rows"])
        ck("summary: F9 - a pre-F9 (single-field) rating still counts as n=1",
           by_ticket.get("2", {}).get("rating_n") == 1, res["rows"])
        ck("summary: an unrated (but estimated) ticket's row carries rating None",
           by_ticket.get("3", {}).get("rating") is None
           and by_ticket.get("3", {}).get("rating_n") == 0, res["rows"])
        pm = {m["module"]: m for m in res["per_module"]}
        ck("summary: per_module carries the agreed columns",
           pm and set(next(iter(pm.values()))) == {"module", "n_pairs", "median_ratio",
                                                   "mean_abs_err_h", "n_ai_estimates",
                                                   "n_pending", "n_unmeasured"}, res["per_module"])
        ck("summary: n_pending counts the unestimated active tickets",
           pm.get("INV", {}).get("n_pending") == 1, res["per_module"])
        ck("summary: history_note is a string", isinstance(res["history_note"], str))



def test_the_closed_loop_converges_to_actual_not_its_square_root():
    """estimate -> measure -> re-estimate, several rounds. The model always says
    8 h, the truth is always 4 h. Calibrating against the WRITTEN value would
    settle ~44% high (f = sqrt(0.5)); against the RAW model number it must reach
    and hold 4.0 (factor 0.5) — reviewer finding 1, 2026-08-19."""
    with tempfile.TemporaryDirectory() as root:
        # 6 rounds: each round one fresh candidate is estimated, then "closed"
        # with a measured 4 h, and the CSV is rebuilt from the store + worklog.
        import worklog as _wl
        written = []
        for rnd in range(1, 7):
            tid = str(rnd)
            _write_store(root, {tid: _ticket(scope="ui")}, keep=True)
            ad, gem = FakeAdapter(), FakeGemini(hours=8.0)
            rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None, gemini_call=gem)
            written.append(rep["estimated"][0]["hours"] if rep["estimated"] else None)
            # measure 4 h and close it, then rebuild the CSV
            _wl.start(root, "w" + tid, [{"module": "INV", "ticket": tid}], kind="claude")
            evs = _wl.read_events(root)
            _wl.end(root, "w" + tid)
            _force_interval_hours(root, "w" + tid, 4.0)
            _close_ticket(root, tid)
            build_estimates.build(root)
        ck("loop: written hours per round", True, written)
        ck("loop: round 2 already lands on the truth", written[1] == 4.0, written)
        ck("loop: and it STAYS there (no sqrt drift)", all(w == 4.0 for w in written[1:]), written)


def test_a_ticket_closed_on_the_helpdesk_since_the_sync_is_not_estimated():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _ticket(scope="ui")})
        ad, gem = FakeAdapter(), FakeGemini(hours=2.0)
        ad.detail_closed = {"1"}
        rep = estimate.run(root, "k", adapter=ad, log=lambda *_a: None, gemini_call=gem)
        ck("closed: nothing PATCHed", ad.patched == [], ad.patched)
        ck("closed: skipped with the reason", any(x.get("reason") == "closed on the helpdesk"
                                                 for x in rep["skipped"]), rep["skipped"])

def main() -> int:
    for fn in (test_the_closed_loop_converges_to_actual_not_its_square_root,
               test_a_ticket_closed_on_the_helpdesk_since_the_sync_is_not_estimated,
               test_candidates_filter, test_calibration_moves_toward_actual,
               test_prompt_carries_the_history_block_and_the_tickets,
               test_run_writes_calibrated_and_rounded_hours,
               test_run_rounds_to_a_quarter_hour_and_clamps,
               test_run_never_overwrites_an_estimate_that_appeared_since_the_sync,
               test_run_skips_when_the_fresh_read_fails,
               test_run_rejects_nonsense_numbers,
               test_run_ignores_a_ticket_the_model_invented,
               test_run_caps_the_batch, test_a_gemini_failure_writes_nothing,
               test_no_adapter_means_no_gemini_call_at_all,
               test_estimates_csv_is_rebuilt_after_a_pass,
               test_row_estimate_contract, test_summary_shape):
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            ck("%s (raised): %s: %s" % (fn.__name__, type(exc).__name__, exc), False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
