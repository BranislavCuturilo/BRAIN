#!/usr/bin/env python3
"""Offline tests for ticket_merger.merge + the /api/tickets/merge route.

The merger analyses the SELECTED tickets through ticket_reader (stubbed here: a
canned per-ticket reading, no Gemini) and asks Gemini ONCE per repo for a
consolidated prompt (stubbed: canned JSON, or a failure to prove the
deterministic fallback). No network, no real store, nothing launched. The route
is driven in-process over loopback to prove the loopback + same-origin gate,
the 400-on-bad-body path, and the response shape.
Run: python test_ticket_merger.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts" / "tickets"))

_TMP = Path(tempfile.mkdtemp(prefix="ticketmerger_"))
_STORE = _TMP / "store"
_STORE.mkdir(parents=True, exist_ok=True)
os.environ["AGENT_VIEW_TICKET_PROFILES"] = str(_TMP / "ticket_profiles")
os.environ["PROJECTS_ROOT"] = str(_TMP / "PROJ")

import store            # noqa: E402
import ticket_reader    # noqa: E402
import ticket_merger    # noqa: E402

REPO_A = str(_TMP / "PROJ" / "app_a")
REPO_B = str(_TMP / "PROJ" / "app_b")

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Fixture store: modules A1 + A2 share repo app_a; B on app_b; U unmapped.
# --------------------------------------------------------------------------- #
def _ticket(title, customer="Ana Anic", desc="please fix"):
    return {"title": title, "priority": "minor", "status": "active",
            "original": {"title": title, "description": desc, "category": "Bug",
                         "created": "2026-07-01T10:00:00+02:00", "customer": customer,
                         "attachment_url": ""},
            "comments": [], "url": "", "helpdesk": {"priority": "minor", "is_closed": False}}


def _write_store():
    store.atomic_write_json(_STORE / "A1.json", {
        "project": {"name": "A1", "repo": "app_a"},
        "tickets": {"100": dict(_ticket("Fix header text"),
                                analysis={"suggested_agents": ["dj-templates", "frontend-junior"],
                                          "suggested_skills": ["ui-bootstrap"]}),
                    "101": _ticket("Move the div")}, "rev": 1})
    store.atomic_write_json(_STORE / "A2.json", {
        "project": {"name": "A2", "repo": "app_a"},
        "tickets": {"200": _ticket("Typo on invoice page", "Bob Bobic")}, "rev": 1})
    store.atomic_write_json(_STORE / "B.json", {
        "project": {"name": "B", "repo": "app_b"},
        "tickets": {"300": _ticket("Button colour")}, "rev": 1})
    store.atomic_write_json(_STORE / "U.json", {
        "project": {"name": "U", "repo": ""},
        "tickets": {"400": _ticket("Unmapped module ticket")}, "rev": 1})
    store.atomic_write_json(_STORE / "modules.json", {"modules": {}})


_write_store()


# --------------------------------------------------------------------------- #
#  Stubs: ticket_reader.analyze (per-ticket reading) and the merge Gemini door
# --------------------------------------------------------------------------- #
_reader_calls = []


def _fake_analyze(ids, root, key=""):
    """Canned reading per id; id 101 fails like a bad model reply would."""
    _reader_calls.append(list(ids))
    index = ticket_reader._index_tickets(root)
    out = []
    for tid in ids:
        e = index.get(str(tid))
        if e is None:
            out.append({"ticket_id": tid, "title": "", "creator": "", "module": "",
                        "error": "unknown ticket"})
            continue
        t, module = e
        title = t["original"]["title"]
        if tid == "101":
            out.append({"ticket_id": tid, "title": title, "creator": "Ana Anic",
                        "module": module, "error": "model returned no actionable prompt"})
            continue
        out.append({"ticket_id": tid, "title": title, "creator": t["original"]["customer"],
                    "module": module, "real_need": f"NEED for {title}",
                    "style_note": "terse", "prompt": f"PROMPT for {title}"})
    return {"ok": True, "results": out, "cap": ticket_reader.BATCH_CAP, "skipped": 0}


class FakeGemini:
    def __init__(self, fail=False, bad=False):
        self.calls = []
        self.fail = fail
        self.bad = bad
        self.MODEL_PRO = "pro"

    def model_for(self, c):
        return "pro" if c == "PRO" else "default"

    def call(self, prompt, *, json_out=True, api_key="", model=None, files=None):
        self.calls.append({"prompt": prompt, "model": model})
        if self.fail:
            raise ValueError("boom")
        if self.bad:
            return {"prompt": ""}
        return {"prompt": "MERGED PLAN\n1. #100 ...", "summary": "small UI fixes",
                "order_note": "header first"}


def _install(gem):
    ticket_reader.analyze = _fake_analyze
    ticket_merger._gemini = lambda: gem
    return gem


# --------------------------------------------------------------------------- #
def test_groups_by_repo_and_merges():
    gem = _install(FakeGemini())
    _reader_calls.clear()
    out = ticket_merger.merge(["100", "101", "200", "300", "999", "100"], str(_STORE))
    check("merge: ok envelope", out.get("ok") is True and "groups" in out, str(out)[:200])
    groups = out["groups"]
    check("merge: two repos -> two groups", len(groups) == 2, str([g["repo"] for g in groups]))
    ga = groups[0]
    check("merge: shared repo groups two modules", sorted(ga["modules"]) == ["A1", "A2"], str(ga["modules"]))
    check("merge: group repo resolved for this machine", Path(ga["repo"]) == Path(REPO_A), ga["repo"])
    check("merge: ticket ids deduped and ordered", ga["ticket_ids"] == ["100", "101", "200"], str(ga["ticket_ids"]))
    check("merge: unknown id reported, not merged", out["unknown"] == ["999"], str(out["unknown"]))
    check("merge: one Gemini call per repo", len(gem.calls) == 2, str(len(gem.calls)))
    check("merge: merge call goes to the DEFAULT tier first (quota that lasts)", all(c["model"] == "default" for c in gem.calls))
    check("merge: readings are DATA in the merge call",
          '"real_need": "NEED for Fix header text"' in gem.calls[0]["prompt"])
    check("merge: reader failure carried into the merge as reader_error",
          '"reader_error": "model returned no actionable prompt"' in gem.calls[0]["prompt"])
    check("merge: source gemini", ga["source"] == "gemini" and ga["merge_error"] == "")
    check("merge: prompt = context header + model plan + evidence",
          ga["prompt"].startswith("Objedinjeni upit — modul: A1, A2")
          and "MERGED PLAN" in ga["prompt"] and "### Izvor tiketa" in ga["prompt"], ga["prompt"][:120])
    check("merge: evidence lists every ticket of the group",
          all(f"#{t} (modul" in ga["prompt"] for t in ("100", "101", "200")))
    check("merge: evidence does NOT list the other repo's ticket", "#300 (modul" not in ga["prompt"])
    check("merge: summary/order_note surfaced", ga["summary"] == "small UI fixes" and ga["order_note"] == "header first")
    call = gem.calls[0]["prompt"]
    check("brain: triage routing hint is DATA in the merge call",
          '"dj-templates"' in call and '"frontend-junior"' in call and '"ui-bootstrap"' in call)
    check("brain: ticket without a hint carries empty lists", '"suggested_agents": []' in call)
    check("brain: model is told to add the Brain: line per item", "`Brain: <agent(s)> — <skill(s)>`" in call)
    check("brain: rules block appended AFTER the plan and BEFORE the evidence",
          0 < ga["prompt"].find("MERGED PLAN") < ga["prompt"].find("### Način rada — brain plugin") < ga["prompt"].find("### Izvor tiketa"))
    check("brain: rules name the orchestrator, skills and capture",
          all(k in ga["prompt"] for k in ("brain:orchestrator", "brain:stack-django", "brain:capture", "/brain:tickets done")))
    tk = {t["ticket_id"]: t for t in ga["tickets"]}
    check("merge: per-ticket real_need surfaced", tk["100"]["real_need"] == "NEED for Fix header text")
    check("merge: per-ticket reader error surfaced", tk["101"]["error"] == "model returned no actionable prompt")
    check("merge: reader called in one chunk (<= BATCH_CAP)",
          _reader_calls == [["100", "101", "200", "300", "999"]], str(_reader_calls))


class TierGemini(FakeGemini):
    """Default tier fails (503-like), the PRO tier answers."""
    def call(self, prompt, *, json_out=True, api_key="", model=None, files=None):
        self.calls.append({"prompt": prompt, "model": model})
        if model == "default":
            raise ValueError("HTTP 503")
        return {"prompt": "PRO-TIER PLAN", "summary": "", "order_note": ""}


def test_default_503_falls_to_pro_tier_before_fallback():
    gem = _install(TierGemini())
    out = ticket_merger.merge(["100"], str(_STORE))
    g = out["groups"][0]
    check("tier: default then pro tried", [c["model"] for c in gem.calls] == ["default", "pro"],
          str([c["model"] for c in gem.calls]))
    check("tier: pro tier's plan used, source gemini",
          g["source"] == "gemini" and "PRO-TIER PLAN" in g["prompt"], g["prompt"][:200])


def test_fallback_when_merge_call_fails():
    for gem in (FakeGemini(fail=True), FakeGemini(bad=True)):
        _install(gem)
        out = ticket_merger.merge(["100", "200"], str(_STORE), note="prvo header")
        g = out["groups"][0]
        check("fallback: both tiers were tried", [c["model"] for c in gem.calls][-2:] == ["default", "pro"],
              str([c["model"] for c in gem.calls]))
        check("fallback: source fallback + error text", g["source"] == "fallback" and g["merge_error"], str(g["merge_error"]))
        check("fallback: prompt still usable (items per ticket)",
              "#100 — Fix header text" in g["prompt"] and "#200 — Typo on invoice page" in g["prompt"], g["prompt"][:300])
        check("fallback: operator note kept", "NAPOMENA OPERATERA" in g["prompt"] and "prvo header" in g["prompt"])
        check("fallback: fixed rules present", "jedan commit po tiketu" in g["prompt"])
        check("fallback: source pointer appended", "### Izvor tiketa" in g["prompt"] and "show_ticket.py" in g["prompt"])
        check("fallback: brain rules appended too", "### Način rada — brain plugin" in g["prompt"])


def test_note_reaches_the_merge_call():
    gem = _install(FakeGemini())
    ticket_merger.merge(["100"], str(_STORE), note="samo CSS, ne diraj python")
    check("note: folded into the merge call", "OPERATOR NOTE" in gem.calls[0]["prompt"]
          and "samo CSS, ne diraj python" in gem.calls[0]["prompt"])


def test_unmapped_repo_group():
    _install(FakeGemini())
    out = ticket_merger.merge(["400"], str(_STORE))
    g = out["groups"][0]
    check("unmapped: group carries empty repo", g["repo"] == "" and g["modules"] == ["U"])
    check("unmapped: header says so", "Repo:" not in g["prompt"].split("\n")[0])


def test_cap_and_chunking():
    _install(FakeGemini())
    _reader_calls.clear()
    ids = [str(100 + i) for i in range(25)]         # 25 requested, cap 20, reader chunks of 10
    out = ticket_merger.merge(ids, str(_STORE), cap=20)
    check("cap: extras reported as skipped", out["skipped"] == 5 and out["cap"] == 20, str(out["skipped"]))
    check("cap: reader called in BATCH_CAP-sized chunks",
          [len(c) for c in _reader_calls] == [10, 10], str([len(c) for c in _reader_calls]))


def test_empty_selection():
    _install(FakeGemini())
    out = ticket_merger.merge([], str(_STORE))
    check("empty: no groups, ok", out["ok"] is True and out["groups"] == [])
    out = ticket_merger.merge("not-a-list", str(_STORE))
    check("empty: non-list ids tolerated", out["groups"] == [])


# --------------------------------------------------------------------------- #
#  Route
# --------------------------------------------------------------------------- #
import server      # noqa: E402


def _start_server():
    server.tickets_root = lambda: _STORE
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _post(port, raw, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    data = raw if isinstance(raw, (bytes, bytearray)) else json.dumps(raw).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tickets/merge",
                                 data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_route():
    _install(FakeGemini())
    httpd, port = _start_server()
    try:
        code, out = _post(port, {"ids": ["100", "300"], "note": "n"})
        check("route: loopback POST -> 200", code == 200, str(code))
        check("route: two groups for two repos", len(out.get("groups") or []) == 2, str(out)[:200])
        check("route: each group carries a prompt", all(g.get("prompt") for g in out["groups"]))
        code2, _ = _post(port, {"ids": ["100"]}, origin="http://evil.example:1234")
        check("route: cross-origin POST -> 403 (CSRF gate)", code2 == 403, str(code2))
        code3, _ = _post(port, b"{ not valid json")
        check("route: malformed body -> 400", code3 == 400, str(code3))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_route_is_in_the_mutation_gate():
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    check("gate: /api/tickets/merge is in the mutation tuple _MUT", '"/api/tickets/merge"' in src)


def main():
    for fn in (test_groups_by_repo_and_merges, test_default_503_falls_to_pro_tier_before_fallback,
               test_fallback_when_merge_call_fails,
               test_note_reaches_the_merge_call, test_unmapped_repo_group,
               test_cap_and_chunking, test_empty_selection,
               test_route, test_route_is_in_the_mutation_gate):
        fn()
    failed = [r for r in _results if not r[1]]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
