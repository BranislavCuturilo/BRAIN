#!/usr/bin/env python3
"""The two capability routes, over a real loopback server.

  GET  /api/capabilities         what each tab needs and whether it has it
  POST /api/capabilities/secret  the reader typed a credential

Three things have to hold, and the last is why this file exists:

  * the GET answers on an EMPTY config -- a fresh clone is exactly when this
    endpoint matters, and one that only works once configured is useless to the
    person it was written for;
  * no configured value is ever serialised to the browser, secret or not;
  * the POST accepts only the credential keys the manifest declares. It writes a
    file, and an unbounded key would let a caller choose that file's shape.

  python agent_view/test_capability_routes.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import capabilities  # noqa: E402
import server        # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []
FAKE = "ehd_" + "f" * 40


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def start():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(port, path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body}


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    # An empty tracked config: the state a fresh clone is actually in.
    cfg = tmp / "agent_view.config.json"
    cfg.write_text("{}", encoding="utf-8")
    real_cfg, real_secrets = server.CONFIG, capabilities.SECRETS_PATH
    server.CONFIG = cfg

    # A real git repo whose .gitignore covers the secrets file, so the guard is
    # exercised for real rather than through the not-a-repo shortcut.
    repo = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True)
    (repo / ".gitignore").write_text("secrets.json\n", encoding="utf-8")
    secrets = repo / "secrets.json"
    capabilities.SECRETS_PATH = secrets

    httpd, port = start()
    try:
        print("GET answers on an EMPTY config -- the state a fresh clone is in")
        code, rows = get(port, "/api/capabilities")
        ok(code == 200 and isinstance(rows, list) and rows, f"200 and a list: {code}")
        tick = next(r for r in rows if r["id"] == "tickets")
        ok(tick["state"] == "not_configured", f"tickets: {tick['state']}")
        ok(tick["ask"] == "/setup tickets", "and it carries the line to paste")

        print("POST refuses a key the manifest does not declare")
        code, body = post(port, "/api/capabilities/secret",
                          {"key": "../../etc/passwd", "value": "x"})
        ok(code == 400 and "unknown" in body.get("error", ""), f"{code} {body}")
        code, body = post(port, "/api/capabilities/secret",
                          {"key": "helpdesk_url", "value": "x"})
        ok(code == 400, f"a NON-secret key is refused too -- this route writes "
                        f"credentials only: {code}")
        ok(not secrets.exists(), "and nothing was written by either attempt")

        print("POST refuses an empty value")
        code, _ = post(port, "/api/capabilities/secret",
                       {"key": "helpdesk_token", "value": "   "})
        ok(code == 400, f"{code}")

        print("POST stores a real one, and never echoes it back")
        code, body = post(port, "/api/capabilities/secret",
                          {"key": "helpdesk_token", "value": FAKE})
        ok(code == 200 and body.get("saved") == "helpdesk_token", f"{code} {body.get('saved')}")
        ok(FAKE not in json.dumps(body), "the response does not contain the value")
        ok(json.loads(secrets.read_text(encoding="utf-8"))["helpdesk_token"] == FAKE,
           "but the gitignored file does")

        print("and the snapshot that comes back has moved the tab on")
        rows = body["capabilities"]
        tick = next(r for r in rows if r["id"] == "tickets")
        ok(tick["state"] == "not_configured",
           "still not_configured -- the URL is a separate thing and is still unset")

        print("no configured value reaches the browser, secret or not")
        cfg.write_text(json.dumps({"helpdesk_url": "https://hd.example.com",
                                   "gemini_api_key": "AIzaLEAKME"}),  # release-check: fixture
                       encoding="utf-8")
        code, rows = get(port, "/api/capabilities")
        blob = json.dumps(rows)
        ok("AIzaLEAKME" not in blob, "no secret")
        ok("hd.example.com" not in blob, "no address either")
        tick = next(r for r in rows if r["id"] == "tickets")
        ok(tick["state"] == "ready",
           f"url from config + token from the secrets file -> ready: {tick['state']}")
    finally:
        httpd.shutdown()
        server.CONFIG, capabilities.SECRETS_PATH = real_cfg, real_secrets

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
