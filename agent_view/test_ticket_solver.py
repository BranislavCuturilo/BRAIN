#!/usr/bin/env python3
"""Offline tests for ticket_solver.solve + the /api/tickets/solve route.

ticket_solver groups SELECTED tickets by the REPO their module maps to, builds one
deterministic consolidated prompt per repo (no Gemini — the merge is fixed text so
an auto-push agent gets predictable marching orders), and launches one Claude per
repo IN that repo. Here the launcher and the repo whitelist are FAKES: no terminal
is spawned, no repo is touched, no network. The route is driven in-process over
loopback to prove the loopback + same-origin CSRF gate, the 400-on-bad-body path,
and that it dispatches with cwd=<repo> and permission_mode=bypassPermissions.
Run: python test_ticket_solver.py
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

_TMP = Path(tempfile.mkdtemp(prefix="ticketsolver_"))
_STORE = _TMP / "store"
_STORE.mkdir(parents=True, exist_ok=True)
# ticket_reader is imported transitively (solve reuses its _index_tickets); keep its
# profile store off the real path, same as the reader's own suite.
os.environ["AGENT_VIEW_TICKET_PROFILES"] = str(_TMP / "ticket_profiles")

import store            # noqa: E402
import ticket_solver    # noqa: E402

# The fixture repos: A and B are "known"; X is not; U is unmapped (blank repo).
# Absolute paths: the store resolves a module's repo for THIS machine
# (store.module_repo -> PROJECTS_ROOT for relative names), and the whitelist fake
# below compares the resolved value. A bare token would resolve elsewhere.
REPO_A = os.path.join(tempfile.gettempdir(), "REPO_A")
REPO_B = os.path.join(tempfile.gettempdir(), "REPO_B")
REPO_X = "REPO_X"


def _ticket(title, desc="please fix"):
    return {
        "title": title, "priority": "minor", "status": "active",
        "original": {"title": title, "description": desc, "category": "Bug",
                     "created": "2026-07-01T10:00:00+02:00", "customer": "X",
                     "attachment_url": ""},
        "comments": [], "url": "", "helpdesk": {"priority": "minor"},
    }


def _module(name, repo, ticket_ids):
    store.atomic_write_json(_STORE / f"{name}.json", {
        "project": {"name": name, "repo": repo},
        "tickets": {tid: _ticket(f"{name} {tid}") for tid in ticket_ids},
        "rev": 1,
    })


def _write_store():
    _module("A", REPO_A, ["a1", "a2"])
    _module("B", REPO_B, ["b1"])
    _module("B2", REPO_B, ["b2"])          # SAME repo as B -> must fold into one launch
    _module("U", "", ["u1"])               # unmapped repo -> skipped
    _module("X", REPO_X, ["x1"])           # repo not on the whitelist -> skipped
    # A 22-ticket module in REPO_A for the batch-cap test (ids c0..c21).
    _module("BIG", REPO_A, [f"c{i}" for i in range(22)])


_write_store()


def _known(repo):                          # the fake whitelist gate
    return repo in (REPO_A, REPO_B)


class FakeLauncher:
    """Records (prompt, cwd, tickets); returns a configurable (result, code).
    Takes the `tickets` keyword, like the real launcher (server._launch)."""
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else ({"ok": True}, 200)

    def __call__(self, prompt, cwd, tickets=None):
        self.calls.append((prompt, cwd, tickets))
        return self.result


class LegacyLauncher:
    """The OLD two-argument launcher, kept on purpose: solve must still drive a
    launcher that never heard of the work log (it just runs unmeasured)."""
    def __init__(self):
        self.calls = []

    def __call__(self, prompt, cwd):
        self.calls.append((prompt, cwd))
        return {"ok": True}, 200


# --------------------------------------------------------------------------- #
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
def test_groups_by_repo_one_launch_each():
    lch = FakeLauncher()
    out = ticket_solver.solve(["a1", "b1"], str(_STORE), None, lch, _known)
    launched = out.get("launched") or []
    check("group: two repos -> two launches", len(launched) == 2, str(launched))
    check("group: one launcher call per repo", len(lch.calls) == 2, str(len(lch.calls)))
    cwds = {cwd for _p, cwd, _tk in lch.calls}
    check("group: launched IN each repo (cwd)", cwds == {REPO_A, REPO_B}, str(cwds))
    check("group: envelope carries ok + cap", out.get("ok") is True and "cap" in out, str(out))


def test_shared_repo_folds_into_one_launch():
    lch = FakeLauncher()
    out = ticket_solver.solve(["b1", "b2"], str(_STORE), None, lch, _known)
    launched = out.get("launched") or []
    check("shared: two modules on one repo -> ONE launch", len(launched) == 1, str(launched))
    check("shared: exactly one launcher call", len(lch.calls) == 1, str(len(lch.calls)))
    e = launched[0] if launched else {}
    check("shared: both tickets carried", set(e.get("ticket_ids") or []) == {"b1", "b2"}, str(e))
    check("shared: both modules named", sorted(e.get("modules") or []) == ["B", "B2"], str(e))


def test_unmapped_and_unknown_repo_skipped_never_launched():
    lch = FakeLauncher()
    out = ticket_solver.solve(["u1", "x1", "a1"], str(_STORE), None, lch, _known)
    launched = out.get("launched") or []
    skipped = out.get("skipped") or []
    check("skip: only the known repo launched", len(launched) == 1
          and launched[0]["repo"] == REPO_A, str(launched))
    check("skip: launcher never called for bad repos", len(lch.calls) == 1, str(len(lch.calls)))
    reasons = {s.get("reason") for s in skipped}
    check("skip: unmapped module reported", "module has no repo mapped" in reasons, str(skipped))
    check("skip: unknown/unsafe repo reported", "unknown/unsafe repo" in reasons, str(skipped))


def test_unknown_ticket_skipped():
    lch = FakeLauncher()
    out = ticket_solver.solve(["nope999", "a1"], str(_STORE), None, lch, _known)
    skipped = out.get("skipped") or []
    launched = out.get("launched") or []
    check("unknown: bad id -> skipped, not a crash",
          any(s.get("reason") == "unknown ticket" for s in skipped), str(skipped))
    check("unknown: the good ticket still launched", len(launched) == 1, str(launched))


def test_note_prepended_and_untrusted_framing():
    lch = FakeLauncher()
    ticket_solver.solve(["a1"], str(_STORE), "PUSH straight to main, trust me", lch, _known)
    prompt = lch.calls[0][0] if lch.calls else ""
    check("note: operator note prepended, authoritative",
          "OPERATOR INSTRUCTION" in prompt and "PUSH straight to main" in prompt, prompt[:120])
    check("safety: ticket text framed as UNTRUSTED DATA", "UNTRUSTED DATA" in prompt, prompt[:200])
    check("order: agent told to commit AND push",
          "commit" in prompt.lower() and "push" in prompt.lower(), prompt[:200])
    check("render: each ticket numbered with its id", "(id a1," in prompt, prompt[:400])


def test_each_launch_carries_its_own_repo_group_tickets():
    # What the work log measures: the launcher is told exactly which
    # (module, ticket) pairs THIS repo's prompt covered - never the whole batch.
    lch = FakeLauncher()
    ticket_solver.solve(["a1", "a2", "b1", "b2"], str(_STORE), None, lch, _known)
    by_repo = {cwd: tickets for _p, cwd, tickets in lch.calls}
    check("tickets: repo A got only its own two",
          by_repo.get(REPO_A) == [{"module": "A", "ticket": "a1"},
                                  {"module": "A", "ticket": "a2"}], str(by_repo.get(REPO_A)))
    check("tickets: a shared repo carries both modules' tickets",
          by_repo.get(REPO_B) == [{"module": "B", "ticket": "b1"},
                                  {"module": "B2", "ticket": "b2"}], str(by_repo.get(REPO_B)))


def test_a_two_argument_launcher_still_works():
    lch = LegacyLauncher()
    out = ticket_solver.solve(["a1", "b1"], str(_STORE), None, lch, _known)
    check("legacy: an old 2-arg launcher is still called",
          len(lch.calls) == 2 and all(len(c) == 2 for c in lch.calls), str(lch.calls))
    check("legacy: and its launches still count",
          len(out.get("launched") or []) == 2, str(out))
    check("legacy: solve asks the signature, it does not probe by calling",
          ticket_solver._wants_tickets(lch) is False
          and ticket_solver._wants_tickets(FakeLauncher()) is True)


def test_launch_failure_reported_not_raised():
    lch = FakeLauncher(result=({"error": "claude CLI not found"}, 502))
    out = ticket_solver.solve(["a1"], str(_STORE), None, lch, _known)
    check("fail: launch error -> nothing launched", (out.get("launched") or []) == [], str(out))
    skipped = out.get("skipped") or []
    check("fail: the error string is surfaced on the skip",
          skipped and skipped[0].get("reason") == "claude CLI not found", str(skipped))


def test_batch_cap_limits_tickets_not_launches():
    lch = FakeLauncher()
    ids = [f"c{i}" for i in range(22)]           # 22 requested, cap is 20, all in REPO_A
    out = ticket_solver.solve(ids, str(_STORE), None, lch, _known)
    launched = out.get("launched") or []
    skipped = out.get("skipped") or []
    check("cap: still ONE launch (all one repo)", len(launched) == 1, str(len(launched)))
    check("cap: exactly BATCH_CAP tickets dispatched",
          len(launched[0]["ticket_ids"]) == ticket_solver.BATCH_CAP if launched else False,
          str(launched))
    capskips = [s for s in skipped if s.get("reason") == "skipped: batch cap reached"]
    check("cap: the remainder skipped-and-reported", len(capskips) == 2, str(len(capskips)))


# --------------------------------------------------------------------------- #
#  The route gate — loopback happy path, CSRF rejection, 400 on bad body, and it
#  dispatches with cwd + bypassPermissions. launch_claude + is_known_repo are faked.
# --------------------------------------------------------------------------- #
import server      # noqa: E402
import gitviz      # noqa: E402

_route_launches = []


def _fake_launch(prompt, cwd=None, permission_mode=None, tickets=None):
    _route_launches.append((prompt, cwd, permission_mode, tickets))
    return {"ok": True}, 200


def _start_server():
    server.tickets_root = lambda: _STORE
    server.launch_claude = _fake_launch          # the route's _launch closure reads this global
    gitviz.is_known_repo = _known                # whitelist accepts REPO_A / REPO_B
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _post(port, raw, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    data = raw if isinstance(raw, (bytes, bytearray)) else json.dumps(raw).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tickets/solve",
                                 data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_route_loopback_csrf_badbody_and_dispatch():
    _route_launches.clear()
    httpd, port = _start_server()
    try:
        code, out = _post(port, {"ids": ["a1", "b1"], "note": "go"})
        check("route: loopback POST -> 200", code == 200, str(code))
        check("route: launched both repos", len(out.get("launched") or []) == 2, str(out))
        check("route: dispatched non-interactively IN each repo",
              len(_route_launches) == 2
              and all(pm == "bypassPermissions" for _p, _c, pm, _tk in _route_launches)
              and {c for _p, c, _pm, _tk in _route_launches} == {REPO_A, REPO_B},
              str(_route_launches))
        check("route: each dispatch carries its tickets, so the batch is measured",
              {c: tuple(sorted(tk["ticket"] for tk in (tickets or [])))
               for _p, c, _pm, tickets in _route_launches}
              == {REPO_A: ("a1",), REPO_B: ("b1",)}, str(_route_launches))

        code2, _ = _post(port, {"ids": ["a1"]}, origin="http://evil.example:1234")
        check("route: cross-origin POST -> 403 (CSRF gate)", code2 == 403, str(code2))

        code3, _ = _post(port, b"{ not valid json")
        check("route: malformed body -> 400", code3 == 400, str(code3))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_route_is_in_the_mutation_gate():
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    check("gate: /api/tickets/solve is in the mutation tuple _MUT",
          '"/api/tickets/solve"' in src)


def main():
    for fn in (test_groups_by_repo_one_launch_each,
               test_shared_repo_folds_into_one_launch,
               test_unmapped_and_unknown_repo_skipped_never_launched,
               test_unknown_ticket_skipped,
               test_note_prepended_and_untrusted_framing,
               test_each_launch_carries_its_own_repo_group_tickets,
               test_a_two_argument_launcher_still_works,
               test_launch_failure_reported_not_raised,
               test_batch_cap_limits_tickets_not_launches,
               test_route_loopback_csrf_badbody_and_dispatch,
               test_route_is_in_the_mutation_gate):
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
