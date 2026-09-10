#!/usr/bin/env python3
"""Offline tests for dpapi.py + mailstore.py — no live mail server.

imaplib.IMAP4_SSL / smtplib.SMTP_SSL are replaced with fakes that return canned
IMAP/SMTP responses, so every code path is exercised without credentials or a
network. Run:  python test_mailstore.py
"""
from __future__ import annotations

import imaplib
import json
import os
import re
import smtplib
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Point the store AND the cache at a throwaway dir BEFORE importing mailstore.
_TMP = Path(tempfile.mkdtemp(prefix="mailtest_"))
_CFG = _TMP / "agent_view.mail.config.json"
_TRACKED_CFG = _TMP / "agent_view.config.json"
os.environ["AGENT_VIEW_MAIL_CONFIG"] = str(_CFG)
os.environ["AGENT_VIEW_MAIL_CACHE"] = str(_TMP / "mail_cache.sqlite")
os.environ["AGENT_VIEW_TRACKED_CONFIG"] = str(_TRACKED_CFG)

import dpapi          # noqa: E402
import mailstore      # noqa: E402

PASSWORD = "S3cret-Čšž-🔒-pass"     # unicode, to prove exact round-trips  # release-check: fixture

# --------------------------------------------------------------------------- #
#  A realistic multipart message: plain + html alternative, a PDF attachment,
#  and a non-ASCII (MIME-encoded on the wire) Subject and From display name.
# --------------------------------------------------------------------------- #
def _sample():
    m = EmailMessage()
    m["From"] = "Brain contributors <ana@example.com>"
    m["To"] = "Office <office@example.com>"
    m["Subject"] = "Račun 123 — čšž provera"
    m.set_content("Ovo je telo poruke.\nPozdrav,\nBane")
    m.add_alternative("<html><body><p>Ovo je <b>telo</b> poruke.</p></body></html>",
                      subtype="html")
    m.add_attachment(b"%PDF-1.4 pretend pdf bytes", maintype="application",
                     subtype="pdf", filename="report.pdf")
    return m


RAW = _sample().as_bytes()
# FLAGS + BODYSTRUCTURE metadata line, as a server sends for (FLAGS BODYSTRUCTURE):
# \Seen present, and an ("attachment" ...) disposition for has_attach.
META = (b'1 (UID 3 FLAGS (\\Seen) BODYSTRUCTURE (("text" "plain" '
        b'("charset" "utf-8") NIL NIL "7bit" 80 4)("application" "pdf" '
        b'("name" "report.pdf") NIL NIL "base64" 2400 NIL '
        b'("attachment" ("filename" "report.pdf")) NIL) "mixed"))')

LIST_DATA = [b'(\\HasNoChildren) "/" "INBOX"',
             b'(\\HasNoChildren \\Sent) "/" "Sent"',
             b'(\\HasNoChildren \\Archive) "/" "Archive"',
             b'(\\HasNoChildren \\Drafts) "/" "Drafts"']

IMAP_CALLS: list = []      # (method, *args) across all connections
IMAP_LOGIN: list = []      # (user, password) the fake actually received
SMTP_CALLS: list = []
# When set, FakeIMAP returns these raw message bytes for a BODY.PEEK[] fetch,
# so a test can drive save_attachments/message with a bespoke message.
_RAW_HOLDER: list = [None]


def _reset():
    IMAP_CALLS.clear()
    IMAP_LOGIN.clear()
    SMTP_CALLS.clear()


class FakeIMAP:
    capabilities = ("IMAP4REV1", "UTF8=ACCEPT")

    def __init__(self, host="", port=993, timeout=None, ssl_context=None):
        IMAP_CALLS.append(("init", host, port))

    def login(self, user, password):
        IMAP_LOGIN.append((user, password))       # capture to prove decrypt works
        return ("OK", [b"LOGIN completed"])

    def logout(self):
        return ("BYE", [b"bye"])

    def list(self, *a):
        IMAP_CALLS.append(("list",))
        return ("OK", list(LIST_DATA))

    def status(self, mailbox, names):
        return ("OK", [b'"%b" (UNSEEN 4)' % mailbox.encode()])

    def select(self, mailbox, readonly=False):
        IMAP_CALLS.append(("select", mailbox, readonly))
        return ("OK", [b"3"])

    def response(self, name):
        # The sync path reads UIDVALIDITY from the SELECT's untagged response.
        if name == "UIDVALIDITY":
            return ("UIDVALIDITY", [b"1"])
        return (name, [None])

    def uid(self, command, *args):
        IMAP_CALLS.append(("uid", command, args))
        if command == "SEARCH":
            return ("OK", [b"1 2 3"])
        if command == "FETCH":
            req = args[1] if len(args) > 1 else ""
            uidset = str(args[0]) if args else ""
            # A FLAGS-only batched fetch: one line per UID, seq==uid is fine since
            # the parser reads only the UID. Checked before BODYSTRUCTURE so a
            # plain "(FLAGS)" request does not fall through to the metadata line.
            if "FLAGS" in req and "BODY" not in req:
                return ("OK", [b"%b (UID %b FLAGS (\\Seen))" % (u.encode(), u.encode())
                               for u in uidset.split(",") if u])
            if "BODYSTRUCTURE" in req:
                return ("OK", [META])
            if "BODY.PEEK[]" in req:
                raw = _RAW_HOLDER[0] if _RAW_HOLDER[0] is not None else RAW
                return ("OK", [(b"3 (BODY[] {%d}" % len(raw), raw), b")"])
            return ("OK", [None])
        if command in ("STORE", "COPY"):
            return ("OK", [b"1"])
        return ("OK", [b""])

    def expunge(self):
        IMAP_CALLS.append(("expunge",))
        return ("OK", [b"1"])

    def create(self, mailbox):
        IMAP_CALLS.append(("create", mailbox))
        return ("OK", [b"created"])

    def subscribe(self, mailbox):
        return ("OK", [b"subscribed"])

    def append(self, mailbox, flags, date_time, message):
        IMAP_CALLS.append(("append", mailbox, flags))
        return ("OK", [b"APPEND completed"])

    def enable(self, cap):
        return ("OK", [b"ENABLED"])


class FakeSMTP:
    def __init__(self, host="", port=0, timeout=None, context=None):
        SMTP_CALLS.append(("init", host, port))

    def login(self, user, password):
        SMTP_CALLS.append(("login", user))        # never record the password
        return (235, b"ok")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        SMTP_CALLS.append(("send", from_addr, list(to_addrs or []),
                           msg.get("Subject"), msg.get("From")))
        return {}

    def quit(self):
        return (221, b"bye")


imaplib.IMAP4_SSL = FakeIMAP
smtplib.SMTP_SSL = FakeSMTP


# --------------------------------------------------------------------------- #
#  Tiny assertion harness
# --------------------------------------------------------------------------- #
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
def test_dpapi_roundtrip():
    enc = dpapi.encrypt(PASSWORD)
    check("dpapi: ciphertext is not the plaintext", PASSWORD not in enc)
    check("dpapi: round-trip exact (unicode)", dpapi.decrypt(enc) == PASSWORD,
          f"got {dpapi.decrypt(enc)!r}")
    check("dpapi: two encryptions both decrypt", dpapi.decrypt(dpapi.encrypt("x")) == "x")


def test_save_account_never_leaks_plaintext():
    _reset()
    mailstore.save_account({
        "id": "acme", "name": "Acme", "email": "ana@example.com",
        "imap_host": "mail.example.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "mail.example.com", "smtp_port": 465, "smtp_ssl": True,
        "user": "ana@example.com", "password": PASSWORD,
    })
    disk = _CFG.read_text(encoding="utf-8")
    check("save_account: config has pass_enc", '"pass_enc"' in disk)
    check("save_account: plaintext password NOT on disk", PASSWORD not in disk)
    check("save_account: no `password` key on disk", '"password"' not in disk)

    pub = mailstore.accounts_public()
    flat = repr(pub)
    check("accounts_public: one account", len(pub) == 1)
    check("accounts_public: exposes id/name/email",
          pub[0].get("id") == "acme" and pub[0].get("email"))
    check("accounts_public: no pass_enc/user leak",
          "pass_enc" not in flat and "user" not in pub[0])


def test_folders():
    _reset()
    fs = mailstore.folders("acme")
    names = {f["name"] for f in fs}
    check("folders: INBOX/Sent/Archive/Drafts listed",
          {"INBOX", "Sent", "Archive", "Drafts"} <= names, str(names))
    inbox = next(f for f in fs if f["name"] == "INBOX")
    check("folders: unseen parsed from STATUS", inbox["unseen"] == 4,
          str(inbox["unseen"]))


def test_connect_decrypts_password():
    _reset()
    mailstore.folders("acme")               # any op forces a login
    check("connect: fake login saw exactly the plaintext",
          IMAP_LOGIN and IMAP_LOGIN[0][1] == PASSWORD)
    check("connect: login user is the account user",
          IMAP_LOGIN and IMAP_LOGIN[0][0] == "ana@example.com")


def test_messages_summary():
    _reset()
    msgs = mailstore.messages("acme", "INBOX", limit=50)
    check("messages: newest-first, 3 rows", len(msgs) == 3, str(len(msgs)))
    m = msgs[0]
    check("messages: from_email parsed", m["from_email"] == "ana@example.com")
    check("messages: from_name decoded (RFC2047)", m["from_name"] == "Brain contributors",
          repr(m["from_name"]))
    check("messages: subject decoded (RFC2047)", m["subject"] == "Račun 123 — čšž provera",
          repr(m["subject"]))
    check("messages: seen from FLAGS", m["seen"] is True)
    check("messages: has_attach from BODYSTRUCTURE", m["has_attach"] is True)
    check("messages: snippet from text/plain", "Ovo je telo poruke" in m["snippet"],
          repr(m["snippet"]))
    check("messages: snippet carries no HTML", "<" not in m["snippet"])


def test_message_full():
    _reset()
    d = mailstore.message("acme", "INBOX", 3)
    check("message: body_text present", "Ovo je telo poruke" in d["body_text"])
    check("message: body_html present", "<b>telo</b>" in d["body_html"])
    check("message: to parsed", d["to"] and d["to"][0]["email"] == "office@example.com")
    check("message: one attachment", len(d["attachments"]) == 1, str(d["attachments"]))
    att = d["attachments"][0] if d["attachments"] else {}
    check("message: attachment name", att.get("name") == "report.pdf")
    check("message: attachment size > 0", att.get("size", 0) > 0)
    # The detail payload the frontend builds against enumerates mime + inline.
    check("message: attachment mime", att.get("mime") == "application/pdf",
          repr(att.get("mime")))
    check("message: attachment inline flag is False for a real attachment",
          att.get("inline") is False, repr(att.get("inline")))
    check("message: attachment carries a part id", att.get("part") is not None)


def test_attachment_fetch():
    _reset()
    d = mailstore.message("acme", "INBOX", 3)
    part = d["attachments"][0]["part"]
    fname, raw, ctype = mailstore.attachment("acme", "INBOX", 3, part)
    check("attachment: filename", fname == "report.pdf")
    check("attachment: bytes returned", raw.startswith(b"%PDF"))
    check("attachment: content-type", ctype == "application/pdf", ctype)


def test_action_seen():
    _reset()
    mailstore.action("acme", "INBOX", 3, "seen")
    stores = [c for c in IMAP_CALLS if c[0] == "uid" and c[1] == "STORE"]
    check("action seen: STORE +FLAGS \\Seen issued",
          any(c[2][1] == "+FLAGS" and "\\Seen" in c[2][2] for c in stores), str(stores))
    check("action seen: writable SELECT (not readonly)",
          any(c[0] == "select" and c[2] is False for c in IMAP_CALLS))


def test_action_archive():
    _reset()
    mailstore.action("acme", "INBOX", 3, "archive")
    kinds = [(c[1], c[2]) for c in IMAP_CALLS if c[0] == "uid"]
    check("action archive: COPY issued", any(k[0] == "COPY" for k in kinds))
    check("action archive: COPY targets Archive",
          any(k[0] == "COPY" and "Archive" in " ".join(map(str, k[1])) for k in kinds))
    check("action archive: STORE \\Deleted then EXPUNGE",
          any(k[0] == "STORE" and "\\Deleted" in k[1][2] for k in kinds)
          and any(c[0] == "expunge" for c in IMAP_CALLS))


def test_action_rejects_unknown_op():
    _reset()
    try:
        mailstore.action("acme", "INBOX", 3, "burn")
        ok = False
    except mailstore.MailError:
        ok = True
    check("action: unknown op raises MailError", ok)


def test_send():
    _reset()
    mailstore.send("acme", "office@example.com", "cc@example.com",
                   "Pozdrav", "Telo mejla.", [{"name": "a.txt", "bytes": b"hello"}])
    sends = [c for c in SMTP_CALLS if c[0] == "send"]
    check("send: SMTP send_message issued", len(sends) == 1)
    if sends:
        _t, from_addr, to_addrs, subj, hdr_from = sends[0]
        check("send: From is the account email", from_addr == "ana@example.com")
        check("send: envelope has both recipients",
              set(to_addrs) == {"office@example.com", "cc@example.com"}, str(to_addrs))
        check("send: From header set to account", "ana@example.com" in hdr_from)
    check("send: password never recorded by SMTP fake",
          all(PASSWORD not in repr(c) for c in SMTP_CALLS))
    check("send: best-effort APPEND to Sent",
          any(c[0] == "append" and "Sent" in str(c[1]) for c in IMAP_CALLS))


def test_save_draft():
    _reset()
    mailstore.save_draft("acme", "office@example.com", "", "Nacrt", "Draft telo.")
    check("save_draft: APPEND to Drafts with \\Draft flag",
          any(c[0] == "append" and "Drafts" in str(c[1]) and "Draft" in str(c[2])
              for c in IMAP_CALLS), str([c for c in IMAP_CALLS if c[0] == "append"]))


def test_unknown_account():
    try:
        mailstore.folders("nope")
        ok = False
    except mailstore.MailError as e:
        ok = "unknown account" in str(e)
    check("unknown account raises a safe MailError", ok)


# --------------------------------------------------------------------------- #
#  F5 -- tracked-config account metadata + per-machine password.
# --------------------------------------------------------------------------- #
def _write_tracked_cfg(extra):
    base = {}
    if _TRACKED_CFG.exists():
        base = json.loads(_TRACKED_CFG.read_text(encoding="utf-8"))
    base.update(extra)
    _TRACKED_CFG.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")


def _reset_tracked_cfg():
    if _TRACKED_CFG.exists():
        _TRACKED_CFG.unlink()


def test_merged_accounts_tracked_metadata_plus_local_password():
    _reset()
    _reset_tracked_cfg()
    _write_tracked_cfg({"mail_accounts": [
        {"id": "tracked1", "name": "Tracked One", "email": "one@example.com",
         "imap_host": "imap.example.com", "imap_port": 993, "imap_ssl": True,
         "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_ssl": True,
         "user": "one@example.com"},
    ]})
    # No local pass_enc yet for tracked1 -> visible, but has_password False.
    pub = {a["id"]: a for a in mailstore.accounts_public()}
    check("merged: tracked-only account is visible", "tracked1" in pub, str(pub))
    check("merged: tracked-only account has_password is False",
          pub["tracked1"]["has_password"] is False, str(pub.get("tracked1")))
    check("merged: metadata (name/email) came from the tracked config",
          pub["tracked1"]["email"] == "one@example.com", str(pub.get("tracked1")))

    try:
        mailstore._account("tracked1")
        ok = False
    except mailstore.MailError as e:
        ok = str(e) == mailstore.NO_PASSWORD_MSG
    check("merged: _account() on a passwordless tracked account raises NO_PASSWORD_MSG", ok)

    # save_account with just a password (tracked1 already has tracked metadata)
    mailstore.save_account({"id": "tracked1", "password": PASSWORD})
    pub = {a["id"]: a for a in mailstore.accounts_public()}
    check("merged: after password save, has_password is True",
          pub["tracked1"]["has_password"] is True, str(pub.get("tracked1")))
    check("merged: metadata is UNCHANGED by the password-only save",
          pub["tracked1"]["email"] == "one@example.com", str(pub.get("tracked1")))

    local_disk = json.loads(_CFG.read_text(encoding="utf-8"))
    local_rec = next(a for a in local_disk["accounts"] if a["id"] == "tracked1")
    check("merged: local file for a tracked account holds ONLY id+pass_enc",
          set(local_rec.keys()) == {"id", "pass_enc"}, str(local_rec))
    check("merged: local record has no metadata leak (imap_host etc.)",
          "imap_host" not in local_rec, str(local_rec))

    tracked_disk = json.loads(_TRACKED_CFG.read_text(encoding="utf-8"))
    check("merged: tracked config still carries no pass_enc",
          "pass_enc" not in tracked_disk["mail_accounts"][0], str(tracked_disk))
    _reset_tracked_cfg()


def test_save_account_new_pushes_metadata_to_tracked_config():
    _reset()
    _reset_tracked_cfg()
    mailstore.save_account({
        "id": "brandnew", "name": "Brand New", "email": "new@example.com",
        "imap_host": "imap.example.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_ssl": True,
        "user": "new@example.com", "password": PASSWORD,
    })
    tracked_disk = json.loads(_TRACKED_CFG.read_text(encoding="utf-8"))
    meta = next((a for a in tracked_disk.get("mail_accounts", []) if a["id"] == "brandnew"), None)
    check("new account: metadata landed in the tracked config", meta is not None, str(tracked_disk))
    check("new account: tracked metadata carries no secret",
          meta is not None and "pass_enc" not in meta and "password" not in meta, str(meta))
    check("new account: tracked metadata has the email", meta is not None and meta.get("email") == "new@example.com")

    local_disk = json.loads(_CFG.read_text(encoding="utf-8"))
    local_rec = next(a for a in local_disk["accounts"] if a["id"] == "brandnew")
    check("new account: local file still keeps the full record (pass_enc + metadata)",
          "pass_enc" in local_rec and local_rec.get("email") == "new@example.com", str(local_rec))
    _reset_tracked_cfg()


def test_tracked_writer_preserves_other_keys():
    _reset()
    _reset_tracked_cfg()
    _write_tracked_cfg({"gemini_api_key": "SOME-OTHER-KEY", "monitor": {"url": "u", "token": "t"}})  # release-check: fixture
    mailstore.save_account({
        "id": "another", "name": "Another", "email": "another@example.com",
        "imap_host": "imap.example.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_ssl": True,
        "user": "another@example.com", "password": PASSWORD,
    })
    tracked_disk = json.loads(_TRACKED_CFG.read_text(encoding="utf-8"))
    check("tracked writer: unrelated key (gemini_api_key) survives",
          tracked_disk.get("gemini_api_key") == "SOME-OTHER-KEY", str(tracked_disk))
    check("tracked writer: unrelated key (monitor) survives",
          tracked_disk.get("monitor") == {"url": "u", "token": "t"}, str(tracked_disk))
    check("tracked writer: the new account metadata is present",
          any(a["id"] == "another" for a in tracked_disk.get("mail_accounts", [])), str(tracked_disk))
    _reset_tracked_cfg()


def test_imap_auth_rejection_maps_to_no_password_msg():
    # A WRONG (but present) password: the IMAP server rejects the login. This
    # must collapse to the SAME NO_PASSWORD_MSG as "no password at all" -- the
    # fix in both cases is "re-enter the password" -- and never repeat the
    # server's own rejection text.
    _reset()
    orig_login = FakeIMAP.login

    def _reject(self, user, password):
        raise imaplib.IMAP4.error("b'AUTHENTICATIONFAILED' login failed")

    FakeIMAP.login = _reject
    try:
        try:
            mailstore.folders("acme")
            ok = False
            detail = "did not raise"
        except mailstore.MailError as e:
            ok = str(e) == mailstore.NO_PASSWORD_MSG
            detail = str(e)
        check("imap auth rejection: collapses to NO_PASSWORD_MSG", ok, detail)
        check("imap auth rejection: never repeats the server's raw text",
              "AUTHENTICATIONFAILED" not in detail, detail)
    finally:
        FakeIMAP.login = orig_login


def test_mixed_case_rejection_smtp_auth_and_undecryptable_blob_all_prompt():
    # Reviewer 2026-08-19: (a) "NO Login failed." (mixed case) must count as a
    # rejection; (b) an SMTP auth failure must prompt too; (c) a DPAPI blob that
    # cannot be decrypted HERE (config copied from another machine) is exactly
    # the case the password card exists for. And the positive twin: a login
    # that succeeds is NOT reported as a password problem.
    _reset()
    orig_login = FakeIMAP.login

    def _reject_mixed(self, user, password):
        raise imaplib.IMAP4.error("NO Login failed.")
    FakeIMAP.login = _reject_mixed
    try:
        try:
            mailstore.folders("acme")
            check("mixed-case rejection: prompts", False, "did not raise")
        except mailstore.MailError as e:
            check("mixed-case rejection: prompts", str(e) == mailstore.NO_PASSWORD_MSG, str(e))
    finally:
        FakeIMAP.login = orig_login

    err = mailstore._fail("send failed", smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed"))
    check("smtp auth failure: prompts", str(err) == mailstore.NO_PASSWORD_MSG, str(err))
    check("smtp auth failure: never repeats the server's text", "5.7.8" not in str(err))
    other = mailstore._fail("send failed", smtplib.SMTPServerDisconnected("gone"))
    check("other smtp failure: stays a curated 502 message", str(other) != mailstore.NO_PASSWORD_MSG, str(other))

    orig_dec = dpapi.decrypt
    dpapi.decrypt = lambda blob: (_ for _ in ()).throw(RuntimeError("DPAPI: key not valid for use in specified state"))
    try:
        try:
            mailstore.folders("acme")
            check("undecryptable blob: prompts", False, "did not raise")
        except mailstore.MailError as e:
            check("undecryptable blob: prompts", str(e) == mailstore.NO_PASSWORD_MSG, str(e))
            check("undecryptable blob: never leaks the DPAPI reason", "DPAPI" not in str(e), str(e))
    finally:
        dpapi.decrypt = orig_dec
    # positive twin: a good login is not a password problem
    try:
        mailstore.verify_login("acme")
        check("verify_login: a good password passes silently", True)
    except mailstore.MailError as e:
        check("verify_login: a good password passes silently", False, str(e))


# --------------------------------------------------------------------------- #
#  save_attachments — every filename is attacker-controlled. These drive it with
#  a canned message (via _RAW_HOLDER) and point attachments_dir at a temp folder.
# --------------------------------------------------------------------------- #
def _set_attachments_dir(path):
    cfg = json.loads(_CFG.read_text(encoding="utf-8")) if _CFG.exists() else {"accounts": []}
    cfg["attachments_dir"] = str(path)
    _CFG.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def _msg_two_attachments():
    m = EmailMessage()
    m["From"] = "Poslovni Partner <partner@example.com>"
    m["To"] = "Office <office@example.com>"
    m["Subject"] = "Ponuda: dva fajla"           # the ':' must not break mkdir on Windows
    m["Date"] = "Mon, 10 Aug 2026 09:30:00 +0200"
    m.set_content("Vidi priloge.")
    m.add_attachment(b"first file bytes", maintype="application",
                     subtype="octet-stream", filename="dokument.txt")
    m.add_attachment(b"#!/bin/sh\necho pwned", maintype="application",
                     subtype="octet-stream", filename="../../evil.sh")
    return m


def _msg_name_collision():
    m = EmailMessage()
    m["From"] = "X <x@example.com>"
    m["Subject"] = "dupe"
    m["Date"] = "Tue, 11 Aug 2026 12:00:00 +0200"
    m.set_content("body")
    m.add_attachment(b"one", maintype="text", subtype="plain", filename="report.pdf")
    m.add_attachment(b"two", maintype="text", subtype="plain", filename="sub/report.pdf")
    return m


def test_save_attachments_sanitizes_and_stays_inside_folder():
    _reset()
    root = _TMP / "attach_out"
    _set_attachments_dir(root)
    _RAW_HOLDER[0] = _msg_two_attachments().as_bytes()
    try:
        res = mailstore.save_attachments("acme", "INBOX", 5)
    finally:
        _RAW_HOLDER[0] = None
    folder = Path(res["path"])
    check("save: folder is under the configured root",
          str(folder.resolve()).startswith(str(root.resolve())), res["path"])
    check("save: folder name carries the message date", "2026-08-10" in folder.name, folder.name)
    check("save: both attachments saved", len(res["saved"]) == 2, str(res["saved"]))
    check("save: malicious '../../evil.sh' reduced to the basename 'evil.sh'",
          "evil.sh" in res["saved"], str(res["saved"]))
    check("save: no saved name carries a separator or '..'",
          all("/" not in n and "\\" not in n and ".." not in n for n in res["saved"]),
          str(res["saved"]))
    check("save: every saved file exists INSIDE the slug folder",
          all((folder / n).is_file() for n in res["saved"]), str(res["saved"]))
    # The escape target of an un-neutralised '../../evil.sh' (two levels above the
    # slug folder) must NOT exist — proof nothing wrote outside the folder.
    escaped = folder.parent.parent / "evil.sh"
    check("save: nothing escaped the folder (no ../../evil.sh)",
          not escaped.exists(), str(escaped))
    check("save: path is absolute", os.path.isabs(res["path"]), res["path"])


def test_save_attachments_dedupes_name_collision():
    _reset()
    root = _TMP / "attach_dedupe"
    _set_attachments_dir(root)
    _RAW_HOLDER[0] = _msg_name_collision().as_bytes()
    try:
        res = mailstore.save_attachments("acme", "INBOX", 6)
    finally:
        _RAW_HOLDER[0] = None
    check("save-dedupe: two files saved", len(res["saved"]) == 2, str(res["saved"]))
    check("save-dedupe: saved names are distinct", len(set(res["saved"])) == 2, str(res["saved"]))
    check("save-dedupe: the collision got a ' (1)' suffix keeping the extension",
          "report (1).pdf" in res["saved"], str(res["saved"]))
    folder = Path(res["path"])
    check("save-dedupe: both files exist on disk",
          all((folder / n).is_file() for n in res["saved"]), str(res["saved"]))


def main():
    for fn in (test_dpapi_roundtrip, test_save_account_never_leaks_plaintext,
               test_folders, test_connect_decrypts_password, test_messages_summary,
               test_message_full, test_attachment_fetch, test_action_seen,
               test_action_archive, test_action_rejects_unknown_op, test_send,
               test_save_draft, test_unknown_account,
               test_merged_accounts_tracked_metadata_plus_local_password,
               test_save_account_new_pushes_metadata_to_tracked_config,
               test_tracked_writer_preserves_other_keys,
               test_imap_auth_rejection_maps_to_no_password_msg,
               test_mixed_case_rejection_smtp_auth_and_undecryptable_blob_all_prompt,
               test_save_attachments_sanitizes_and_stays_inside_folder,
               test_save_attachments_dedupes_name_collision):
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
