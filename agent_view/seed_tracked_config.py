#!/usr/bin/env python3
"""One-time migration (decision 2026-08-19, F5): copy this machine's LOCAL
mail-account metadata and monitor URL/token into the TRACKED
agent_view.config.json, so a fresh machine inherits them on `git pull` instead
of re-entering them by hand. Copies NO mail secret; it DOES copy the monitor
token (see below):

  * mail:    agent_view.mail.config.json accounts -> tracked "mail_accounts"
             (id/name/email/host/port/ssl/user only -- pass_enc stays local).
  * monitor: agent_view.monitor.config.json monitor_url/monitor_token ->
             tracked "monitor": {"url", "token"} -- yes, the token is copied
             into the tracked file ON PURPOSE, per the F5 decision (read-only
             production credential, private repo, sole access).

Run ONCE per machine, after pulling this change and before deleting the old
per-machine files (nothing here deletes them -- they stay as the last-resort
fallback monitor_client.py still reads).

Prints ONLY a count, never a key, a token, or a path with a secret in it.
  python seed_tracked_config.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mailstore  # noqa: E402  -- reuses its config paths + tracked-config writer


def _seed_mail() -> int:
    """Push every local mail account's metadata into the tracked config. Reuses
    mailstore._write_tracked_metadata (the SAME writer save_account uses for a
    new account) so this is not a second implementation of that merge."""
    local = mailstore._load_config().get("accounts", [])
    n = 0
    for acct in local:
        if not isinstance(acct, dict) or not acct.get("id"):
            continue
        mailstore._write_tracked_metadata(acct)   # picks only _ACCOUNT_FIELDS; drops pass_enc
        n += 1
    return n


def _monitor_config_path() -> Path:
    # monitor_client's OLD per-machine file -- read directly (not via
    # monitor_client.config(), which would also resolve env/tracked and could
    # report "configured" from a source other than this exact file).
    return HERE / "agent_view.monitor.config.json"


def _seed_monitor() -> bool:
    fp = _monitor_config_path()
    if not fp.exists():
        return False
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(d, dict):
        return False
    url = (d.get("monitor_url") or "").strip()
    token = (d.get("monitor_token") or "").strip()
    if not (url and token):
        return False
    import tracked_config

    def _m(cfg):
        cfg["monitor"] = {"url": url, "token": token}
        return cfg
    tracked_config.update(_m, mailstore._tracked_config_path())   # locked; refuses an unreadable file
    return True


def main() -> int:
    n_mail = _seed_mail()
    monitor_ok = _seed_monitor()
    print(f"seeded: {n_mail} mail accounts, monitor: {'yes' if monitor_ok else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
