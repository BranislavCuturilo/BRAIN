#!/usr/bin/env python3
"""Offline tests for server HTTP surface: JSON/SSE charset, /api/serverinfo,
/api/brainlog. The server is started on 127.0.0.1:0 (an ephemeral port) and driven
over loopback; no external network, no credentials. Run:  python test_server_routes.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server      # noqa: E402  (also puts scripts/tickets on sys.path)
import store as ticketstore  # noqa: E402
import gemini_client          # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


def _start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def _post(port, path, obj):
    """POST a JSON object, loopback, no Origin header (a non-browser caller —
    the same shape hook.py and every other test in this suite use). Returns
    `(status, parsed_json)`."""
    body = json.dumps(obj).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def _raw_headers(port, path):
    """Fetch just the status line + headers of a (possibly streaming) response via
    a raw socket, so /stream can be inspected without hanging on its body."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 65536:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    finally:
        s.close()


# --------------------------------------------------------------------------- #
def test_json_route_has_utf8_charset():
    httpd, port = _start_server()
    try:
        code, ctype, _body = _get(port, "/api/sessions")
        check("json: /api/sessions -> 200", code == 200, str(code))
        check("json: Content-Type carries charset=utf-8",
              "application/json" in ctype and "charset=utf-8" in ctype, ctype)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_sse_stream_has_utf8_charset():
    httpd, port = _start_server()
    try:
        head = _raw_headers(port, "/stream").lower()
        check("sse: event-stream Content-Type present",
              "content-type: text/event-stream" in head, head)
        check("sse: event-stream carries charset=utf-8",
              "text/event-stream; charset=utf-8" in head, head)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_static_json_type_constant():
    check("static: .json type carries charset=utf-8",
          server._STATIC_TYPES[".json"] == "application/json; charset=utf-8",
          server._STATIC_TYPES[".json"])
    check("static: JSON_CT constant is the utf-8 form",
          server.JSON_CT == "application/json; charset=utf-8", server.JSON_CT)


_DOTTED_QUAD = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def test_serverinfo_returns_ip_and_port():
    httpd, port = _start_server()
    try:
        code, ctype, body = _get(port, "/api/serverinfo")
        out = json.loads(body.decode() or "{}")
        check("serverinfo: -> 200 json", code == 200 and "application/json" in ctype, str(code))
        check("serverinfo: ip is a dotted quad",
              bool(_DOTTED_QUAD.match(str(out.get("ip") or ""))), str(out.get("ip")))
        check("serverinfo: port is the bound port", out.get("port") == port,
              f"{out.get('port')} != {port}")
        check("serverinfo: url embeds ip and port",
              out.get("url") == f"http://{out.get('ip')}:{port}/", str(out.get("url")))
        check("serverinfo: carries a host name", isinstance(out.get("host"), str))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_serverinfo_is_lan_readable_not_loopback_gated():
    # It must NOT be in the mutation (loopback-only) tuple — the phone reaches it
    # from the LAN. It is a plain GET read handled in do_GET, cors like the others.
    import inspect
    src = inspect.getsource(server.Handler.do_GET)
    check("serverinfo: routed in do_GET", '"/api/serverinfo"' in src, "")
    mut = inspect.getsource(server.Handler.do_POST)
    check("serverinfo: NOT in the mutation gate", '"/api/serverinfo"' not in mut, "")


def test_lan_ipv4_helper():
    ip = server.lan_ipv4()
    check("lan_ipv4: returns a dotted quad", bool(_DOTTED_QUAD.match(ip)), ip)


def test_brainlog_route_shape():
    # Route smoke test: shape only (entries is a list) — content depends on live
    # git history, which the parser test covers against a fixture instead.
    httpd, port = _start_server()
    try:
        code, ctype, body = _get(port, "/api/brainlog")
        out = json.loads(body.decode() or "{}")
        check("brainlog: -> 200 json", code == 200 and "application/json" in ctype, str(code))
        check("brainlog: entries is a list", isinstance(out.get("entries"), list),
              type(out.get("entries")).__name__)
    finally:
        httpd.shutdown()
        httpd.server_close()


def _gemini_ticket(reading=None, is_closed=False, triage_state=None):
    """A minimal tickets_store row — just enough for _gemini_pending_reads_count
    to classify it (helpdesk.is_closed / triage.state / reading.prompt)."""
    return {
        "title": "t", "priority": "minor", "status": "active",
        "original": {"title": "t", "description": "d", "category": "Bug",
                     "created": "2026-07-01T10:00:00+02:00", "customer": "C",
                     "attachment_url": ""},
        "comments": [], "url": "",
        "helpdesk": {"priority": "minor", "is_closed": is_closed},
        "reading": reading or {},
        "triage": {"state": triage_state} if triage_state else {},
    }


def test_gemini_usage_route_has_per_model_and_forecast():
    # Two active tickets with no reading ("1", "6") count toward pending_reads;
    # a reading, a closed helpdesk ticket, and each terminal triage state are
    # excluded — the SAME "active" tickets.js's tixDone() checks.
    tmp = Path(tempfile.mkdtemp(prefix="gemroute_"))
    ticketstore.atomic_write_json(tmp / "TST.json", {
        "project": {"name": "TST", "repo": ""},
        "tickets": {
            "1": _gemini_ticket(),
            "2": _gemini_ticket(reading={"prompt": "p"}),
            "3": _gemini_ticket(is_closed=True),
            "4": _gemini_ticket(triage_state="done"),
            "5": _gemini_ticket(triage_state="solved_manually"),
            "6": _gemini_ticket(),
        },
        "rev": 1,
    })
    orig_root = server.tickets_root
    server.tickets_root = lambda: tmp
    server._tix_cache["data"] = None
    httpd, port = _start_server()
    try:
        code, ctype, body = _get(port, "/api/gemini/usage")
        out = json.loads(body.decode() or "{}")
        check("gemini/usage: -> 200 json", code == 200 and "application/json" in ctype, str(code))
        check("gemini/usage: per_model has all three configured tiers",
              all(m in (out.get("per_model") or {}) for m in
                  (gemini_client.MODEL, gemini_client.MODEL_LITE, gemini_client.MODEL_PRO)),
              str(out.get("per_model")))
        fc = out.get("forecast") or {}
        check("gemini/usage: forecast.pending_reads counts active tickets with no reading",
              fc.get("pending_reads") == 2, str(fc))
        check("gemini/usage: forecast.calls_per_ticket is 1", fc.get("calls_per_ticket") == 1)
        check("gemini/usage: forecast.calls_needed mirrors pending_reads",
              fc.get("calls_needed") == fc.get("pending_reads"))
        check("gemini/usage: forecast.fits is a bool", isinstance(fc.get("fits"), bool))
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.tickets_root = orig_root
        server._tix_cache["data"] = None


def test_sentlog_route_shape():
    # Route smoke test: shape only (entries is a list, newest first), and the
    # ?limit= clamp. A pure READ of the local sent log - it writes nothing, so
    # it is safe against whatever store this machine is configured with; the
    # entry contract itself is pinned in scripts/tickets/test_sent_log.py.
    httpd, port = _start_server()
    try:
        code, ctype, body = _get(port, "/api/tickets/sentlog?limit=5")
        out = json.loads(body.decode() or "{}")
        check("sentlog: -> 200 json", code == 200 and "application/json" in ctype, str(code))
        check("sentlog: entries is a list", isinstance(out.get("entries"), list),
              type(out.get("entries")).__name__)
        check("sentlog: honours ?limit=", len(out.get("entries") or []) <= 5,
              str(len(out.get("entries") or [])))
        code, _ctype, body = _get(port, "/api/tickets/sentlog?limit=nonsense&module=NOPE")
        out = json.loads(body.decode() or "{}")
        check("sentlog: a junk limit and an unknown module still answer 200",
              code == 200 and out.get("entries") == [], str(code))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_ticket_rows_carry_sent_counts_keyed_by_file_stem():
    # Contract the "poslato" badge depends on: every /api/tickets row carries
    # "sent" = {kind: int for kind in sent_log.KINDS}, looked up by (FILE STEM,
    # id) - the same key writeback.py logs under. Offline: load_config is stubbed to
    # a temp store, so nothing touches this machine's real tickets_store.
    import tempfile
    import server as srv
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "INV.json").write_text(json.dumps({
            "project": {"name": "INV", "helpdesk_module": "INV", "repo": ""},
            "tickets": {"1": {"title": "t1", "status": "active", "helpdesk": {"is_closed": False}},
                        "2": {"title": "t2", "status": "active", "helpdesk": {"is_closed": False}}},
            "rev": 1}), encoding="utf-8")
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "tickets"))
        import sent_log
        sent_log.append(str(root), {"module": "INV", "ticket": "1", "kind": "comment", "http": "ok"})
        sent_log.append(str(root), {"module": "INV", "ticket": "1", "kind": "estimate", "http": "ok"})
        sent_log.append(str(root), {"module": "INV", "ticket": "1", "kind": "close", "http": "fail"})
        sent_log.append(str(root), {"module": "INV", "ticket": "2", "kind": "close", "http": "unknown"})
        orig = srv.load_config
        srv.load_config = lambda: {"tickets_root": str(root)}
        try:
            data = srv._build_tickets_data()
        finally:
            srv.load_config = orig
        rows = {str(t.get("id")): t for p in data.get("projects", []) for t in p.get("tickets", [])}
        s1, s2 = rows.get("1", {}).get("sent"), rows.get("2", {}).get("sent")
        check("sent: row 1 counts ok sends only, keyed by stem",
              s1 == {"comment": 1, "close": 0, "estimate": 1, "create": 0, "edit": 0}, str(s1))
        check("sent: row 2 has every kind at 0 (unknown not counted)",
              s2 == {"comment": 0, "close": 0, "estimate": 0, "create": 0, "edit": 0}, str(s2))


def _estimate_ticket(estimated_time=None, ai=None, is_closed=False, scope="ui"):
    t = {"title": "t", "status": "active",
         "original": {"title": "t", "description": "d", "category": "Bug"},
         "reading": {"scope": scope, "complexity": "S"},
         "helpdesk": {"is_closed": is_closed, "estimated_time": estimated_time}}
    if ai:
        t["triage"] = {"ai_estimate": ai}
    return t


def test_estimates_route_shape():
    # The Procene panel contract: rows + per_module + history_note, computed on
    # request. It must NOT rewrite estimates.csv - it answers a GET.
    tmp = Path(tempfile.mkdtemp(prefix="estroute_"))
    ticketstore.atomic_write_json(tmp / "INV.json", {
        "project": {"name": "INV"},
        "tickets": {
            "1": _estimate_ticket(ai={"hours": 3.0, "by": "gemini"}),
            "2": _estimate_ticket(estimated_time=2),
            "3": _estimate_ticket(),                      # unestimated -> pending
        },
        "rev": 1})
    orig_root = server.tickets_root
    server.tickets_root = lambda: tmp
    httpd, port = _start_server()
    try:
        code, ctype, body = _get(port, "/api/tickets/estimates")
        out = json.loads(body.decode() or "{}")
        check("estimates: -> 200 json", code == 200 and "application/json" in ctype, str(code))
        check("estimates: the three top-level keys",
              set(out) == {"rows", "per_module", "history_note"}, str(sorted(out)))
        check("estimates: a row carries the agreed columns",
              out["rows"] and set(out["rows"][0]) == {"module", "ticket", "estimated_h",
                                                      "raw_h", "actual_h", "by", "scope",
                                                      "complexity", "closed_at", "rating",
                                                      "rating_avg", "rating_n"},
              str(out["rows"][:1]))
        pm = {m["module"]: m for m in out["per_module"]}
        check("estimates: per_module carries the agreed columns",
              pm and set(pm["INV"]) == {"module", "n_pairs", "median_ratio",
                                          "mean_abs_err_h", "n_ai_estimates", "n_pending",
                                          "n_unmeasured"},
              str(out["per_module"]))
        check("estimates: n_pending counts the unestimated active tickets",
              pm.get("INV", {}).get("n_pending") == 1, str(out["per_module"]))
        check("estimates: n_ai_estimates counts what this brain estimated",
              pm.get("INV", {}).get("n_ai_estimates") == 1, str(out["per_module"]))
        check("estimates: history_note is a string", isinstance(out.get("history_note"), str))
        check("estimates: a GET never writes estimates.csv",
              not (tmp / "estimates.csv").exists())
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.tickets_root = orig_root


def test_ticket_rows_carry_the_estimate_block():
    # Contract the est/act column depends on: every /api/tickets row carries
    # "estimate" = {ai_h, helpdesk_h, actual_h, by}, always all four keys, and the
    # helpdesk's "0.00" (which MEANS no estimate) never reads as zero hours.
    import server as srv
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ticketstore.atomic_write_json(root / "INV.json", {
            "project": {"name": "INV"},
            "tickets": {
                "1": _estimate_ticket(estimated_time="0.00"),
                "2": _estimate_ticket(estimated_time=2),
                "3": _estimate_ticket(estimated_time=2,
                                      ai={"hours": 1.25, "by": "gemini",
                                          "basis": "(INV, ui) n=4 medijana 0.50"}),
            },
            "rev": 1})
        orig = srv.load_config
        srv.load_config = lambda: {"tickets_root": str(root)}
        try:
            data = srv._build_tickets_data()
        finally:
            srv.load_config = orig
        rows = {str(t.get("id")): t for p in data.get("projects", []) for t in p.get("tickets", [])}
        e1 = rows.get("1", {}).get("estimate")
        check("estimate row: all four keys always present",
              isinstance(e1, dict) and set(e1) == {"ai_h", "helpdesk_h", "actual_h", "by"},
              str(e1))
        check("estimate row: the helpdesk's 0.00 is not an estimate",
              e1 == {"ai_h": None, "helpdesk_h": None, "actual_h": 0.0, "by": ""}, str(e1))
        check("estimate row: a helpdesk estimate is attributed to the helpdesk",
              rows["2"]["estimate"]["helpdesk_h"] == 2.0
              and rows["2"]["estimate"]["by"] == "helpdesk", str(rows["2"]["estimate"]))
        check("estimate row: our own estimate wins the attribution",
              rows["3"]["estimate"]["ai_h"] == 1.25
              and rows["3"]["estimate"]["by"] == "gemini", str(rows["3"]["estimate"]))


def _rating_ticket(rating=None, comment="", rated_at=""):
    return {"title": "t", "status": "active",
           "original": {"title": "t", "description": "d", "category": "Bug"},
           "helpdesk": {"is_closed": False, "rating": rating,
                        "rating_comment": comment, "rated_at": rated_at}}


def test_ticket_rows_carry_the_rating_block():
    # F9 contract: every /api/tickets row carries "rating" =
    # {value, avg, n, items:[{role,rater,rating,comment,at}]}, all four keys
    # always present, normalised out of the raw helpdesk block (store.ticket_rating)
    # so the client never reaches into it. An unrated ticket (or one pulled from a
    # pre-F8/F9 helpdesk with no rating fields at all) yields {None, None, 0, []}.
    import server as srv
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ticketstore.atomic_write_json(root / "INV.json", {
            "project": {"name": "INV"},
            "tickets": {
                "1": _rating_ticket(),                                  # unrated
                "2": _rating_ticket(rating=5, comment="Odlicno",
                                    rated_at="2026-08-15T09:00:00"),     # pre-F9 single field
                "3": {"title": "t", "status": "active",                 # no helpdesk block at all
                      "original": {"title": "t", "description": "d", "category": "Bug"}},
                "4": {"title": "t", "status": "active",                 # F9: two raters
                      "original": {"title": "t", "description": "d", "category": "Bug"},
                      "helpdesk": {"is_closed": True, "rating_avg": 4.5,
                                  "ratings": [{"role": "tester", "rater": "Mika", "rating": 4,
                                              "comment": "Brzo", "rated_at": "2026-08-14T09:00:00"},
                                             {"role": "customer", "rater": "Ana", "rating": 5,
                                              "comment": "Odlicno", "rated_at": "2026-08-15T09:00:00"}]}},
            },
            "rev": 1})
        orig = srv.load_config
        srv.load_config = lambda: {"tickets_root": str(root)}
        try:
            data = srv._build_tickets_data()
        finally:
            srv.load_config = orig
        rows = {str(t.get("id")): t for p in data.get("projects", []) for t in p.get("tickets", [])}
        r1 = rows.get("1", {}).get("rating")
        check("rating row: all four keys always present",
              isinstance(r1, dict) and set(r1) == {"value", "avg", "n", "items"}, str(r1))
        check("rating row: unrated ticket is {None, None, 0, []}",
              r1 == {"value": None, "avg": None, "n": 0, "items": []}, str(r1))
        r2 = rows.get("2", {}).get("rating")
        check("rating row: a pre-F9 rated ticket folds into one item",
              r2["value"] == 5 and r2["n"] == 1
              and r2["items"] == [{"role": "customer", "rater": "", "rating": 5,
                                   "comment": "Odlicno", "at": "2026-08-15T09:00:00"}], str(r2))
        r3 = rows.get("3", {}).get("rating")
        check("rating row: a ticket with no helpdesk block at all still yields the full shape",
              r3 == {"value": None, "avg": None, "n": 0, "items": []}, str(r3))
        r4 = rows.get("4", {}).get("rating")
        check("rating row: F9 - value prefers the CUSTOMER's own rating over the average",
              r4["value"] == 5 and r4["avg"] == 4.5 and r4["n"] == 2, str(r4))
        check("rating row: F9 - every rater is listed",
              {it["role"] for it in r4["items"]} == {"tester", "customer"}, str(r4))


# --------------------------------------------------------------------------- #
#  Vraćeni / Dopune (reopened tickets): row shape + the three endpoints
# --------------------------------------------------------------------------- #
def _reopen_ticket(rounds=None, reopened=None, reopen=None, outbox=None,
                   helpdesk_closed=False):
    t = {"title": "t", "status": "active",
        "original": {"title": "t", "description": "d", "category": "Bug"},
        "helpdesk": {"is_closed": helpdesk_closed},
        "outbox": outbox if outbox is not None else []}
    if rounds is not None:
        t["rounds"] = rounds
    if reopened is not None:
        t["reopened"] = reopened
    if reopen is not None:
        t["reopen"] = reopen
    return t


def _open_round_ticket(outbox=None):
    """A ticket currently sitting in an OPEN reopen round — round 1 (the
    original close) plus round 2 (the reopen, not yet re-closed: `closed_at`
    is None). This is what `rounds.current_round`/`is_reopened_open` and the
    verdict endpoint's 200 path need."""
    return _reopen_ticket(
        rounds=[
            {"closed_at": "2026-08-01T10:00:00", "resolution": "Prvobitno resenje.",
             "closed_by": "operator"},
            {"reopened_at": "2026-08-10T09:00:00",
             "trigger": {"comment_id": 42, "author": "Klijent", "author_role": "customer"},
             "branch": None, "resolution": None, "closed_at": None},
        ],
        reopened={"at": "2026-08-10T09:00:00", "by_role": "customer",
                  "trigger_comment_id": 42},
        reopen={},
        outbox=outbox)


def _write_store(root, module, tickets, rev=1):
    ticketstore.atomic_write_json(Path(root) / f"{module}.json", {
        "project": {"name": module, "helpdesk_module": module, "repo": ""},
        "tickets": tickets, "rev": rev})


def test_ticket_row_carries_the_reopen_ledger_with_legacy_defaults():
    # Contract from plan phase 4(a): every /api/tickets row carries rounds
    # (list), reopened (dict), reopen (dict), ALWAYS — a legacy ticket that
    # carries none of the three keys must yield [] / {} / {}, never a KeyError,
    # and a real reopened ticket must carry its ledger through untouched.
    import server as srv
    rounds = [
        {"closed_at": "2026-08-01T10:00:00", "resolution": "Prvobitno.", "closed_by": "operator"},
        {"reopened_at": "2026-08-10T09:00:00",
         "trigger": {"comment_id": 42, "author": "Klijent", "author_role": "customer"},
         "branch": None, "resolution": None, "closed_at": None},
    ]
    reopened = {"at": "2026-08-10T09:00:00", "by_role": "customer", "trigger_comment_id": 42}
    reopen = {"predlog": {"summary": "x"}}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_store(root, "INV", {
            "1": _reopen_ticket(),                                # legacy: no keys at all
            "2": _reopen_ticket(rounds=rounds, reopened=reopened, reopen=reopen),
        })
        orig = srv.load_config
        srv.load_config = lambda: {"tickets_root": str(root)}
        try:
            data = srv._build_tickets_data()
        finally:
            srv.load_config = orig
        rows = {str(t.get("id")): t for p in data.get("projects", []) for t in p.get("tickets", [])}
        r1 = rows.get("1", {})
        check("reopen row: a legacy ticket -> rounds:[] reopened:{} reopen:{} (never KeyError)",
              r1.get("rounds") == [] and r1.get("reopened") == {} and r1.get("reopen") == {},
              str({k: r1.get(k) for k in ("rounds", "reopened", "reopen")}))
        r2 = rows.get("2", {})
        check("reopen row: a reopened ticket carries its ledger/marker/working block through",
              r2.get("rounds") == rounds and r2.get("reopened") == reopened
              and r2.get("reopen") == reopen,
              str({k: r2.get(k) for k in ("rounds", "reopened", "reopen")}))


def _open_round_ticket_with_dopuna(comment_id=77, at="2026-08-10T09:05:00"):
    """An open-round ticket (see `_open_round_ticket`) that also carries the
    dopuna comment itself, posted AFTER round 1's close — so
    `reopen_ai._dopuna_comments` resolves it via the timestamp window rather
    than falling back to the bare trigger-id lookup."""
    t = _open_round_ticket()
    t["comments"] = [{"id": comment_id, "at": at, "author": "Klijent",
                      "author_role": "customer", "body": "Dopuna teksta."}]
    return t


def test_ticket_row_carries_dopuna_ids():
    # Vraćeni / Dopune: `dopuna_ids` is the server-computed window of dopuna
    # comment ids for the CURRENT open reopen round (reopen_ai._dopuna_comments),
    # always present on every row — [] for a legacy/never-reopened ticket, never
    # a KeyError — and non-empty (the actual dopuna comment id(s)) for a ticket
    # currently sitting in an open round.
    import server as srv
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_store(root, "DEMO", {
            "1": _open_round_ticket_with_dopuna(comment_id=77),
            "2": _reopen_ticket(),                     # legacy: never reopened
        })
        orig = srv.load_config
        srv.load_config = lambda: {"tickets_root": str(root)}
        try:
            data = srv._build_tickets_data()
        finally:
            srv.load_config = orig
        rows = {str(t.get("id")): t for p in data.get("projects", []) for t in p.get("tickets", [])}
        r1 = rows.get("1", {})
        check("dopuna_ids: an open reopen round carries the dopuna comment id(s)",
              r1.get("dopuna_ids") == [77], str(r1.get("dopuna_ids")))
        r2 = rows.get("2", {})
        check("dopuna_ids: a legacy/never-reopened ticket -> [] (always present, never KeyError)",
              r2.get("dopuna_ids") == [], str(r2.get("dopuna_ids")))


def test_reopen_verdict_endpoint():
    tmp = Path(tempfile.mkdtemp(prefix="reopenverdict_"))
    _write_store(tmp, "DEMO", {
        "1": _open_round_ticket(),
        "2": _reopen_ticket(),          # legacy: no open round to classify
    })
    orig_root = server.tickets_root
    server.tickets_root = lambda: tmp
    server._tix_cache["data"] = None
    httpd, port = _start_server()
    try:
        code, j = _post(port, "/api/tickets/reopen/verdict",
                        {"dir": "DEMO", "id": "1", "verdict": {"branch": "Z"}})
        check("verdict: an unknown branch -> 400", code == 400, f"{code} {j}")
        d = json.loads((tmp / "DEMO.json").read_text(encoding="utf-8"))
        check("verdict: the bad branch left the ledger untouched",
              d["tickets"]["1"]["rounds"][-1]["branch"] is None,
              str(d["tickets"]["1"]["rounds"][-1]))

        code, j = _post(port, "/api/tickets/reopen/verdict",
                        {"dir": "DEMO", "id": "2", "verdict": {"branch": "B"}})
        check("verdict: a ticket with no open reopen round -> 404 "
              "(record_verdict's SystemExit)", code == 404, f"{code} {j}")

        # the TOP of the chain: an open round, a valid branch -> 200, and
        # rounds.record_verdict (the ONE ledger writer) actually stamped it.
        code, j = _post(port, "/api/tickets/reopen/verdict",
                        {"dir": "DEMO", "id": "1", "verdict": {"branch": "B", "confidence": 0.9}})
        check("verdict: a valid branch on an OPEN round -> 200", code == 200, f"{code} {j}")
        check("verdict: the response echoes the branch", j.get("branch") == "B", str(j))
        d = json.loads((tmp / "DEMO.json").read_text(encoding="utf-8"))
        t1 = d["tickets"]["1"]
        check("verdict: the ledger's current round is stamped",
              t1["rounds"][-1]["branch"] == "B", str(t1["rounds"][-1]))
        check("verdict: the AI working block carries the SAME branch (one writer, both places)",
              t1.get("reopen", {}).get("branch") == "B"
              and t1["reopen"].get("confidence") == 0.9, str(t1.get("reopen")))
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.tickets_root = orig_root
        server._tix_cache["data"] = None
        shutil.rmtree(tmp, ignore_errors=True)


def test_reopen_reply_endpoint():
    import writeback
    tmp = Path(tempfile.mkdtemp(prefix="reopenreply_"))
    _write_store(tmp, "DEMO", {
        "1": _open_round_ticket(outbox=[
            {"id": "posted1", "body": "vec poslato", "attachments": [], "posted": True},
            {"id": "draft1", "body": "Molimo zatvorite sami.", "attachments": [], "posted": False},
        ]),
    })
    orig_root = server.tickets_root
    server.tickets_root = lambda: tmp
    server._tix_cache["data"] = None

    # The reply endpoint must reach the helpdesk ONLY through server._helpdesk_adapter
    # — the single documented seam a test muzzles to prove nothing leaves the machine.
    # Muzzle it here (a bare stub, since writeback.run is spied and never uses it) and
    # assert the endpoint actually passed THIS object through, so a future inline
    # get_adapter() in the handler (a second, unlocked door) fails this test.
    seam_adapter = object()
    orig_adapter = server._helpdesk_adapter
    server._helpdesk_adapter = lambda: seam_adapter

    calls = []

    def fake_run(root, module, tid, close_resolution=None, estimate=None,
                source="acme_helpdesk", adapter=None, **kw):
        calls.append({"module": module, "tid": tid, "close_resolution": close_resolution,
                     "only_drafts": kw.get("only_drafts"), "adapter": adapter})
        return {"posted": list(kw.get("only_drafts") or []), "failed": [], "ambiguous": [],
               "closed": False, "comments": []}

    orig_run = writeback.run
    writeback.run = fake_run
    httpd, port = _start_server()
    try:
        code, j = _post(port, "/api/tickets/reopen/reply",
                        {"dir": "DEMO", "id": "1", "draft_id": "nope"})
        check("reply: an unknown draft_id -> 404", code == 404, f"{code} {j}")
        check("reply: an unknown draft_id never reaches writeback.run — no silent send",
              calls == [], str(calls))

        code, j = _post(port, "/api/tickets/reopen/reply",
                        {"dir": "DEMO", "id": "1", "draft_id": "posted1"})
        check("reply: an already-posted draft -> 409", code == 409, f"{code} {j}")
        check("reply: an already-posted draft never reaches writeback.run either — "
              "not a silent no-op success", calls == [], str(calls))

        # the positive twin: a real, unposted draft DOES reach writeback.run —
        # exactly once, comment-only, and never the close path.
        code, j = _post(port, "/api/tickets/reopen/reply",
                        {"dir": "DEMO", "id": "1", "draft_id": "draft1"})
        check("reply: a valid unposted draft -> 200", code == 200, f"{code} {j}")
        check("reply: writeback.run WAS called, exactly once", len(calls) == 1, str(calls))
        check("reply: comment-only — close_resolution is None, never the close path",
              calls[0]["close_resolution"] is None, str(calls[0]))
        check("reply: only the named draft goes, never the whole outbox",
              calls[0]["only_drafts"] == ["draft1"], str(calls[0]))
        check("reply: routed through server._helpdesk_adapter (the single seam), "
              "not an inline get_adapter()", calls[0]["adapter"] is seam_adapter,
              str(calls[0].get("adapter")))
    finally:
        writeback.run = orig_run
        server._helpdesk_adapter = orig_adapter
        httpd.shutdown()
        httpd.server_close()
        server.tickets_root = orig_root
        server._tix_cache["data"] = None
        shutil.rmtree(tmp, ignore_errors=True)


def test_klasifikuj_shows_the_prompt_and_never_launches_by_itself():
    """The operator's report (2026-08-28): "kada kliknem klasifikuj … treba da se
    predlog upita da dobijem a ne da se otvori terminal". The button chained
    /reopen/classify straight into /api/claude/launch, so one click opened a
    terminal and the prompt itself was never shown."""
    js = (HERE / "web" / "tickets.js").read_text(encoding="utf-8")
    fn = js.split("function tixReopenClassify(")[1].split("\n  function ")[0]
    check("klasifikuj: the handler no longer launches anything",
          "/api/claude/launch" not in fn, fn[-200:])
    check("klasifikuj: it opens the prompt preview instead",
          "tixReopenPromptModal(" in fn, fn[-200:])
    modal = js.split("function tixReopenPromptModal(")[1].split("\n  function ")[0]
    check("klasifikuj: the preview offers Kopiraj and a SEPARATE launch button",
          "tkAnCopy(" in modal and "tkAnLaunch(" in modal, "")
    check("klasifikuj: the prompt is written as TEXT — it embeds the customer's dopuna",
          "pre.textContent=String(d.prompt" in modal
          and 'innerHTML=String(d.prompt' not in modal, "")
    check("klasifikuj: the launch is measured against this ticket",
          "module:mod,ticket:String(id)" in modal, "")
    check("klasifikuj: and the preview says the send waits for the operator",
          "tvoju potvrdu" in modal, "")


def test_reopen_learn_endpoint():
    # /api/tickets/reopen/learn: build the LEARNING prompt for a ticket whose
    # reopen round is present (learning runs AFTER close, so it does NOT gate on
    # is_reopened_open — only on the ledger carrying a round with reopened_at).
    # IDENTICAL in shape to /reopen/classify: a pure READ of the store — no
    # write to tickets.json, no writeback.run call (no helpdesk send at all).
    import writeback
    tmp = Path(tempfile.mkdtemp(prefix="reopenlearn_"))
    _write_store(tmp, "DEMO", {
        "1": _open_round_ticket(),
        "2": _reopen_ticket(),          # legacy: no reopen round to learn from
    })
    orig_root = server.tickets_root
    server.tickets_root = lambda: tmp
    server._tix_cache["data"] = None

    def _no_send(*a, **k):
        raise AssertionError("reopen/learn reached writeback.run — it must be a pure read")

    orig_run = writeback.run
    writeback.run = _no_send
    before = (tmp / "DEMO.json").read_text(encoding="utf-8")
    httpd, port = _start_server()
    try:
        code, j = _post(port, "/api/tickets/reopen/learn", {"dir": "NOPE", "id": "1"})
        check("learn: an unknown module -> 404", code == 404, f"{code} {j}")

        code, j = _post(port, "/api/tickets/reopen/learn", {"dir": "DEMO", "id": "2"})
        check("learn: a ticket with no reopen round -> 400", code == 400, f"{code} {j}")

        code, j = _post(port, "/api/tickets/reopen/learn", {"dir": "DEMO", "id": "1"})
        check("learn: a ticket WITH a reopen round -> 200", code == 200, f"{code} {j}")
        check("learn: the response carries a non-empty prompt",
              isinstance(j.get("prompt"), str) and len(j["prompt"]) > 0,
              str(j.get("prompt"))[:80])
        check("learn: the response carries cwd", "cwd" in j, str(j))

        after = (tmp / "DEMO.json").read_text(encoding="utf-8")
        check("learn: pure read — the store file is byte-identical after the call",
              after == before, "store changed")
    finally:
        writeback.run = orig_run
        httpd.shutdown()
        httpd.server_close()
        server.tickets_root = orig_root
        server._tix_cache["data"] = None
        shutil.rmtree(tmp, ignore_errors=True)


def test_reopen_endpoints_are_loopback_gated():
    # All routes live in _MUT: a non-loopback (LAN) peer is refused on
    # every one of them, and the refused reply must not have reached
    # writeback.run either. The positive (loopback) case is proven by the 200s
    # in test_reopen_verdict_endpoint / test_reopen_reply_endpoint /
    # test_reopen_learn_endpoint above.
    import writeback
    tmp = Path(tempfile.mkdtemp(prefix="reopengate_"))
    _write_store(tmp, "DEMO", {
        "1": _open_round_ticket(outbox=[
            {"id": "draft1", "body": "x", "attachments": [], "posted": False}]),
    })
    orig_root = server.tickets_root
    server.tickets_root = lambda: tmp
    server._tix_cache["data"] = None
    orig_local = server.Handler._client_is_local
    server.Handler._client_is_local = lambda self: False
    calls = []
    orig_run = writeback.run
    writeback.run = lambda *a, **k: (calls.append((a, k)),
                                     {"posted": [], "failed": [], "ambiguous": [],
                                      "closed": False, "comments": []})[1]
    httpd, port = _start_server()
    try:
        code, j = _post(port, "/api/tickets/reopen/verdict",
                        {"dir": "DEMO", "id": "1", "verdict": {"branch": "B"}})
        check("gate: a LAN peer is refused on /reopen/verdict", code == 403, f"{code} {j}")
        code, j = _post(port, "/api/tickets/reopen/reply",
                        {"dir": "DEMO", "id": "1", "draft_id": "draft1"})
        check("gate: a LAN peer is refused on /reopen/reply", code == 403, f"{code} {j}")
        check("gate: the refused reply never reached writeback.run", calls == [], str(calls))
        code, j = _post(port, "/api/tickets/reopen/classify", {"dir": "DEMO", "id": "1"})
        check("gate: a LAN peer is refused on /reopen/classify", code == 403, f"{code} {j}")
        code, j = _post(port, "/api/tickets/reopen/learn", {"dir": "DEMO", "id": "1"})
        check("gate: a LAN peer is refused on /reopen/learn", code == 403, f"{code} {j}")
    finally:
        writeback.run = orig_run
        server.Handler._client_is_local = orig_local
        httpd.shutdown()
        httpd.server_close()
        server.tickets_root = orig_root
        server._tix_cache["data"] = None
        shutil.rmtree(tmp, ignore_errors=True)


def test_reopen_reply_is_a_no_write_under_readonly_env():
    # BRAIN_HELPDESK_READONLY=1 must turn the reply endpoint into a pure no-op:
    # the per-draft report shows it failed, and nothing landed. Proven two ways:
    # (1) end-to-end through the real (unpatched) writeback.run, with DUMMY
    #     credentials (never real ones) and a transport spy scoped to the
    #     adapter module's OWN `urllib` name — so the test client's own
    #     loopback POST (which also goes through urllib.request.urlopen) is
    #     untouched;
    # (2) the spy's positive twin (test_helpdesk_guard.py's own technique): with
    #     the guard cleared, the SAME adapter call really does reach the spy —
    #     proving the negative result in (1) is the guard's doing, not a dead
    #     assertion.
    import types
    import urllib.error as _real_urllib_error
    import urllib.parse as _real_urllib_parse
    import urllib.request as _real_urllib_request
    from adapters import acme_helpdesk as hd_adapter

    tmp = Path(tempfile.mkdtemp(prefix="reopenreadonly_"))
    _write_store(tmp, "DEMO", {
        "1": _open_round_ticket(outbox=[
            {"id": "draft1", "body": "Molimo zatvorite sami.", "attachments": [], "posted": False}]),
    })
    orig_root = server.tickets_root
    server.tickets_root = lambda: tmp
    server._tix_cache["data"] = None

    attempts = []

    def _spy(*a, **kw):
        attempts.append(1)
        raise AssertionError("a real HTTP request left the reopen/reply endpoint")

    # Only `.request.urlopen` is swapped for the spy — `.parse` (the module
    # builds every path with `urllib.parse.quote` BEFORE the write guard runs)
    # and `.error` stay the real submodules, or path-building itself breaks
    # with an AttributeError that masquerades as "the guard blocked it".
    stub_request_ns = types.SimpleNamespace(
        **{k: getattr(_real_urllib_request, k) for k in dir(_real_urllib_request)
           if not k.startswith("__")})
    stub_request_ns.urlopen = _spy
    orig_urllib = hd_adapter.urllib
    hd_adapter.urllib = types.SimpleNamespace(
        request=stub_request_ns, parse=_real_urllib_parse, error=_real_urllib_error)

    env_keys = ("HELPDESK_URL", "HELPDESK_TOKEN", "BRAIN_HELPDESK_READONLY",
               "BRAIN_NO_SEND", "PYTEST_CURRENT_TEST")
    saved_env = {k: os.environ.get(k) for k in env_keys}

    def _restore_env():
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    try:
        os.environ["HELPDESK_URL"] = "https://example.invalid"
        os.environ["HELPDESK_TOKEN"] = "not-a-real-token"
        os.environ["BRAIN_HELPDESK_READONLY"] = "1"
        os.environ.pop("BRAIN_NO_SEND", None)
        os.environ.pop("PYTEST_CURRENT_TEST", None)

        httpd, port = _start_server()
        try:
            code, j = _post(port, "/api/tickets/reopen/reply",
                            {"dir": "DEMO", "id": "1", "draft_id": "draft1"})
            check("readonly: the endpoint still answers 200 — in-band failure, not a crash",
                  code == 200, f"{code} {j}")
            rep = j.get("report") or {}
            check("readonly: the draft is reported failed, never posted",
                  rep.get("posted") == [] and rep.get("failed") == ["draft1"], str(rep))
            d = json.loads((tmp / "DEMO.json").read_text(encoding="utf-8"))
            dr = next(x for x in d["tickets"]["1"]["outbox"] if x["id"] == "draft1")
            check("readonly: nothing left the machine — the draft is still unposted on disk",
                  dr.get("posted") is not True, str(dr))
            check("readonly: the transport spy was never even reached (blocked pre-flight)",
                  attempts == [], str(attempts))
        finally:
            httpd.shutdown()
            httpd.server_close()

        # positive twin, offline: with every guard cleared, the SAME adapter call
        # really does reach the transport.
        _restore_env()
        os.environ["HELPDESK_URL"] = "https://example.invalid"
        os.environ["HELPDESK_TOKEN"] = "not-a-real-token"
        for k in ("BRAIN_HELPDESK_READONLY", "BRAIN_NO_SEND", "PYTEST_CURRENT_TEST"):
            os.environ.pop(k, None)
        keep_argv, sys.argv = sys.argv, ["writeback.py"]      # neutralise the test_*.py guard too
        try:
            adapter = hd_adapter.AcmeHelpdesk()
            try:
                adapter.add_comment("1", "x")
                check("readonly: positive twin — the guard-free call reaches the transport",
                      False, "no AssertionError raised")
            except AssertionError:
                check("readonly: positive twin — the guard-free call reaches the transport",
                      attempts == [1], str(attempts))
            except hd_adapter.HelpdeskError as exc:
                check("readonly: positive twin — the guard-free call reaches the transport",
                      False, f"blocked instead: {exc}")
        finally:
            sys.argv = keep_argv
    finally:
        _restore_env()
        hd_adapter.urllib = orig_urllib
        server.tickets_root = orig_root
        server._tix_cache["data"] = None
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_estimate_pass_runs_only_on_the_operator_rescan():
    # The boot pass is INCREMENTAL and does NOT sync with the helpdesk, so its
    # mirror is stale by definition - estimating from it would be exactly the
    # overwrite the fresh read exists to prevent. Pinned on the source because
    # the alternative is a rescan that really calls Gemini.
    import inspect
    src = inspect.getsource(server._run_gemini_rescan_inner)
    calls = [ln for ln in src.splitlines() if "_run_estimate_pass(" in ln]
    guard = src.split("_run_estimate_pass(key)")[0].rstrip().splitlines()[-1]
    check("estimate pass: called exactly once from the rescan", len(calls) == 1, str(calls))
    check("estimate pass: and only under `if not incremental`",
          guard.strip() == "if not incremental:", guard)
    ep = inspect.getsource(server._run_estimate_pass)
    check("estimate pass: skips with a status when the helpdesk creds are missing",
          '"status": "estimate-skipped"' in ep)
    check("estimate pass: broadcasts the estimated count and the skipped count",
          '"status": "estimated"' in ep and '"n": len(est)' in ep)
    check("estimate pass: leaves an ai_log entry with action estimate",
          '"action": "estimate"' in ep)


def test_helpdesk_pull_runs_before_the_gemini_gate():
    """The queue is reconciled even when Gemini cannot be used, and every
    Rescan leaves a durable record.

    Measured: DEMO#01621 was opened on the helpdesk at 14:08 and was still
    absent from the store at 15:40. The last real pull had been at 12:47 and
    nothing in ai_log.json said a Rescan had been attempted since -- because
    the pull sat behind `if not key: return` and the only Rescan entry was
    written `if report`, i.e. when Gemini had actually analysed something. A
    press that reconciled nothing and a press that never happened left the
    same evidence: none.
    """
    import inspect                                  # local, like the estimate test
    src = inspect.getsource(server._run_gemini_rescan_inner)
    lines = [ln.strip() for ln in src.splitlines()]
    pull = next(i for i, ln in enumerate(lines) if "rescan_all(" in ln)
    gate = next(i for i, ln in enumerate(lines) if ln.startswith("key = _gemini_key("))
    nokey = next(i for i, ln in enumerate(lines) if '"status": "no-key"' in ln)
    check("rescan: the helpdesk pull comes BEFORE the Gemini key gate",
          pull < gate, f"pull at {pull}, gate at {gate}")
    check("rescan: the no-key return is after the pull too",
          pull < nokey, f"pull at {pull}, no-key at {nokey}")
    check("rescan: only one Gemini key gate in the function",
          sum(1 for ln in lines if ln.startswith("key = _gemini_key(")) == 1)

    # the durable record, both ways
    sync_block = src.split("rescan_all(")[1].split("except Exception as exc:")[0]
    check("rescan: a successful sync is written to ai_log, not only broadcast",
          '_ai_log({"action": "rescan", "by": "helpdesk"' in sync_block, sync_block[:200])
    skipped = src.split('"status": "sync-skipped"')[1][:600]
    check("rescan: a SKIPPED sync is written to ai_log too",
          '_ai_log(' in skipped and "PRESKO" in skipped, skipped[:200])
    check("rescan: the skip record names what to check",
          "helpdesk_url" in skipped or "helpdesk_token" in skipped, skipped[:200])


def main():
    for fn in (test_helpdesk_pull_runs_before_the_gemini_gate,
               test_json_route_has_utf8_charset, test_sse_stream_has_utf8_charset,
               test_ticket_rows_carry_sent_counts_keyed_by_file_stem,
               test_static_json_type_constant, test_serverinfo_returns_ip_and_port,
               test_serverinfo_is_lan_readable_not_loopback_gated, test_lan_ipv4_helper,
               test_brainlog_route_shape, test_gemini_usage_route_has_per_model_and_forecast,
               test_sentlog_route_shape, test_estimates_route_shape,
               test_ticket_rows_carry_the_estimate_block,
               test_ticket_rows_carry_the_rating_block,
               test_ticket_row_carries_the_reopen_ledger_with_legacy_defaults,
               test_ticket_row_carries_dopuna_ids,
               test_reopen_verdict_endpoint,
               test_reopen_reply_endpoint,
               test_klasifikuj_shows_the_prompt_and_never_launches_by_itself,
               test_reopen_learn_endpoint,
               test_reopen_endpoints_are_loopback_gated,
               test_reopen_reply_is_a_no_write_under_readonly_env,
               test_the_estimate_pass_runs_only_on_the_operator_rescan):
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
