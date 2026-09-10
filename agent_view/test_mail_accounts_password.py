#!/usr/bin/env python3
"""Offline tests for the F5 mail-account HTTP contracts in server.py.

mailstore is monkeypatched directly (the SAME module object server._mailstore()
imports, per test_mail_sync.py's pattern) so no real config file, no IMAP, no
network is ever touched. What matters:
  * GET /api/mail/accounts carries `has_password` per account,
  * a MailError(mailstore.NO_PASSWORD_MSG) from a mail route becomes
    401 {"error":"password_required","acct":<id>} instead of the generic 502,
  * any OTHER MailError still gets the existing 502 (unchanged behaviour).
Run:  python test_mail_accounts_password.py
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server      # noqa: E402
import mailstore   # noqa: E402  (the same module object server._mailstore imports)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


def _start():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _post(port, path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


# --------------------------------------------------------------------------- #
def test_accounts_route_carries_has_password():
    orig = mailstore.accounts_public
    mailstore.accounts_public = lambda: [
        {"id": "with-pw", "name": "With PW", "email": "a@example.com", "has_password": True},
        {"id": "no-pw", "name": "No PW", "email": "b@example.com", "has_password": False},
    ]
    httpd, port = _start()
    try:
        code, out = _get(port, "/api/mail/accounts")
        check("accounts: -> 200", code == 200, str(code))
        accts = {a["id"]: a for a in out.get("accounts", [])}
        check("accounts: has_password True carried through",
              accts.get("with-pw", {}).get("has_password") is True, str(out))
        check("accounts: has_password False carried through",
              accts.get("no-pw", {}).get("has_password") is False, str(out))
    finally:
        mailstore.accounts_public = orig
        httpd.shutdown()
        httpd.server_close()


def test_get_route_no_password_maps_to_401():
    orig = mailstore.folders

    def _raise(acct_id):
        raise mailstore.MailError(mailstore.NO_PASSWORD_MSG)

    mailstore.folders = _raise
    httpd, port = _start()
    try:
        code, out = _get(port, "/api/mail/folders?acct=acct-needs-pw")
        check("folders: no-password -> 401 (not the generic 502)", code == 401, str(code))
        check("folders: error is password_required", out.get("error") == "password_required", str(out))
        check("folders: acct id carried through", out.get("acct") == "acct-needs-pw", str(out))
    finally:
        mailstore.folders = orig
        httpd.shutdown()
        httpd.server_close()


def test_post_route_no_password_maps_to_401():
    orig = mailstore.action

    def _raise(acct, folder, uid, op):
        raise mailstore.MailError(mailstore.NO_PASSWORD_MSG)

    mailstore.action = _raise
    httpd, port = _start()
    try:
        code, out = _post(port, "/api/mail/action",
                          {"acct": "acct-needs-pw", "folder": "INBOX", "uid": "1", "op": "seen"})
        check("action: no-password -> 401", code == 401, str(code))
        check("action: error is password_required", out.get("error") == "password_required", str(out))
        check("action: acct id carried through", out.get("acct") == "acct-needs-pw", str(out))
    finally:
        mailstore.action = orig
        httpd.shutdown()
        httpd.server_close()


def test_other_mail_error_still_gets_502():
    orig = mailstore.folders

    def _raise(acct_id):
        raise mailstore.MailError("could not list folders (error)")

    mailstore.folders = _raise
    httpd, port = _start()
    try:
        code, out = _get(port, "/api/mail/folders?acct=whatever")
        check("other MailError: still -> 502 (unchanged)", code == 502, str(code))
        check("other MailError: error is the curated message",
              out.get("error") == "could not list folders (error)", str(out))
        check("other MailError: no acct key on the generic 502 shape", "acct" not in out, str(out))
    finally:
        mailstore.folders = orig
        httpd.shutdown()
        httpd.server_close()


def main():
    for fn in (test_accounts_route_carries_has_password,
               test_get_route_no_password_maps_to_401,
               test_post_route_no_password_maps_to_401,
               test_other_mail_error_still_gets_502):
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
