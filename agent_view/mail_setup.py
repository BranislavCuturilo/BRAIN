#!/usr/bin/env python3
"""One-time mail account setup — stores an account with a DPAPI-encrypted
password. Run it in YOUR terminal: the password is read WITHOUT echo and is
never printed, logged, put in a URL, or committed. It goes straight into the
gitignored config as ciphertext bound to your Windows user.

  python mail_setup.py

Pre-filled for the Acme account; press Enter to accept each default. The only
thing you must type is the password.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mailstore  # noqa: E402

DEFAULTS = {
    "id": "acme",
    "name": "Acme",
    "email": "ana@example.com",
    "imap_host": "mail.example.com", "imap_port": 993, "imap_ssl": True,
    "smtp_host": "mail.example.com", "smtp_port": 465, "smtp_ssl": True,
    "user": "ana@example.com",
}


def ask(label, default):
    v = input(f"  {label} [{default}]: ").strip()
    return v or default


def main() -> int:
    print("Mail account setup — SSL cPanel (mail.example.com).")
    print("The password is read without echo and stored DPAPI-encrypted; Ctrl+C to cancel.\n")
    d = dict(DEFAULTS)
    try:
        d["email"] = ask("email", d["email"])
        d["user"] = ask("IMAP/SMTP username", d["email"])
        d["name"] = ask("display name", d["name"])
        d["imap_host"] = ask("IMAP host", d["imap_host"])
        d["imap_port"] = int(ask("IMAP port", d["imap_port"]))
        d["smtp_host"] = ask("SMTP host", d["smtp_host"])
        d["smtp_port"] = int(ask("SMTP port", d["smtp_port"]))
        pw = getpass.getpass("  password (no echo): ")
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled")
        return 1
    if not pw:
        print("no password entered — cancelled")
        return 1
    d["password"] = pw
    try:
        mailstore.save_account(d)
    except Exception as exc:                     # noqa: BLE001
        print(f"could not save: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    print(f"\nSaved account '{d['id']}' ({d['email']}). Password stored encrypted.")
    print("Open the visualizer → the Mail tab, pick the account, open a folder to test the connection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
