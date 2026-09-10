#!/usr/bin/env python3
"""Mail backend for the agent_view Mail tab — standard library only.

No Django, no ORM, no third-party packages: `imaplib`/`smtplib`/`email`/`ssl`
plus DPAPI-at-rest (dpapi.py). One local user, so there is no connection pool —
every call opens its own IMAP/SMTP connection, does one thing, and always logs
out in a `finally`. The password is decrypted ONLY at connect time; it never
enters a return value, a log line, an exception message, or a snippet/body.

Config (decision 2026-08-19): account METADATA (host/address/user, no secret)
lives in the TRACKED agent_view.config.json next to this file, key
"mail_accounts": [{"id","name","email","imap_host","imap_port","imap_ssl",
"smtp_host","smtp_port","smtp_ssl","user"}] — so a fresh machine inherits every
known account on pull. The password NEVER lives there: `agent_view.mail.config.json`
next to this file (gitignored; override the path with env AGENT_VIEW_MAIL_CONFIG
for tests) holds ONLY the DPAPI ciphertext per account:
  {"accounts":[{"id","pass_enc", ...and, for an account not yet in the tracked
    config, the full metadata too — see accounts_public()/save_account() below}]}
`pass_enc` is the DPAPI base64 of the password — the plaintext is never stored.
The two sources are merged by `id`; override the tracked file's path with env
AGENT_VIEW_TRACKED_CONFIG (for tests; shared with monitor_client.py).

Every message is UNTRUSTED input: headers, charsets and MIME structure are parsed
defensively (missing parts, bad charsets → errors="replace", never a crash). The
HTML body is returned as a raw string for the caller to sanitise before render —
this module never executes it and never fetches anything it references.

Failures raise MailError with a SAFE message (an operation name plus, at most, an
exception *type* — never the server's text, a path, or the password).
"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import json
import mimetypes
import os
import re
import smtplib
import ssl
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from email.message import EmailMessage
from html import unescape as _html_unescape
from pathlib import Path

# dpapi is a sibling module; make the import work however this file is loaded
# (run directly, imported by server.py, or by the test) — server.py uses the
# same sys.path idiom for its local helpers.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dpapi
import tracked_config  # noqa: E402  the ONE reader/writer of the tracked config  # noqa: E402
import mailcache  # noqa: E402  — the local SQLite store; see sync section below

HERE = Path(__file__).resolve().parent

SOCKET_TIMEOUT = 30            # seconds, on every IMAP/SMTP socket
PREVIEW_BYTES = 16384          # cap the per-message body fetch used for the list
                               # snippet, so opening a folder never downloads a
                               # multi-MB attachment for 50 rows
SNIPPET_CHARS = 140
MAX_LIMIT = 500               # per-request ceiling; the Mail list pages/sorts under it

PREFETCH_BODIES = 12          # newest full bodies a background sync warms per
                              # folder, so the first message-open is instant too
REFRESH_DEBOUNCE = 15.0       # s — a background folder refresh is skipped if one
                              # ran this recently, so a burst of opens is cheap

# Special-use flag → the folder names to fall back to, and the name to create if
# neither is found. One table so archive/sent/drafts resolve the same way.
_ARCHIVE = ("\\Archive", {"archive", "archives", "inbox.archive"}, "Archive")
_SENT = ("\\Sent", {"sent", "sent items", "sent messages", "inbox.sent"}, "Sent")
_DRAFTS = ("\\Drafts", {"drafts", "inbox.drafts"}, "Drafts")


class MailError(Exception):
    """A safe, user-facing failure. Its message never contains the password."""


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #
def _config_path() -> Path:
    return Path(os.environ.get("AGENT_VIEW_MAIL_CONFIG")
                or (HERE / "agent_view.mail.config.json"))


def _tracked_config_path() -> Path:
    # Shared with monitor_client.py: same file, same override env, so both
    # modules agree on where the tracked config lives without importing server.py.
    return Path(os.environ.get("AGENT_VIEW_TRACKED_CONFIG")
                or (HERE / "agent_view.config.json"))


def _load_config() -> dict:
    fp = _config_path()
    if not fp.exists():
        return {"accounts": []}
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        raise MailError("mail config is unreadable")
    if not isinstance(d, dict):
        return {"accounts": []}
    d.setdefault("accounts", [])
    if not isinstance(d["accounts"], list):
        d["accounts"] = []
    return d


def _atomic_write_config(cfg: dict) -> None:
    # temp + os.replace, atomic on Windows. Not reusing scripts/tickets/store's
    # writer on purpose: that ties the mail tool to the tickets subsystem and
    # formats with indent=1 for tiketi.json diffs; this is a separate,
    # human-edited config where indent=2 reads better.
    fp = _config_path()
    tmp = fp.with_name(fp.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, fp)


def _load_tracked_config() -> dict:
    """The tracked agent_view.config.json for READERS: {} when absent or
    unreadable (degrade quietly). Through tracked_config, the one loader."""
    return tracked_config.load_quiet(_tracked_config_path())


def _tracked_mail_accounts() -> list:
    """The "mail_accounts" metadata list (id/name/email/host/.../user, NO
    secret) from the tracked config. Entries without an id are dropped -- they
    cannot be merged with anything."""
    accts = _load_tracked_config().get("mail_accounts")
    if not isinstance(accts, list):
        return []
    return [a for a in accts if isinstance(a, dict) and a.get("id")]


def _merged_accounts() -> list:
    """The tracked config metadata plus this machine local `pass_enc`, merged
    by `id`. A tracked account with no local ciphertext yet still appears
    (accounts_public().has_password is False for it); a purely local-only
    account (not in the tracked config, e.g. one saved before this machine
    last pulled) still works exactly as before."""
    merged = {a["id"]: dict(a) for a in _tracked_mail_accounts()}
    for a in _load_config().get("accounts", []):
        if not isinstance(a, dict) or not a.get("id"):
            continue
        aid = a["id"]
        if aid in merged:
            if a.get("pass_enc"):
                merged[aid]["pass_enc"] = a["pass_enc"]
        else:
            merged[aid] = a
    return list(merged.values())


#: The distinguishable MailError message _account() raises when an account
#: metadata is known (from the tracked config) but this machine has never
#: entered its password. server.py matches on this exact string to answer 401
#: {"error":"password_required"} instead of the generic 502.
NO_PASSWORD_MSG = "account has no password on this machine"


def _account(acct_id) -> dict:
    for a in _merged_accounts():
        if a.get("id") == acct_id:
            if not a.get("pass_enc"):
                raise MailError(NO_PASSWORD_MSG)
            return a
    raise MailError("unknown account")


def accounts_public() -> list:
    """The accounts safe to hand a browser: id, name, email, has_password --
    never `user` or `pass_enc`. `has_password` tells the Mail tab whether THIS
    machine can actually connect (a tracked account with no local ciphertext
    yet needs the one-time password screen)."""
    out = []
    for a in _merged_accounts():
        out.append({"id": a.get("id"),
                    "name": a.get("name") or a.get("id"),
                    "email": a.get("email") or "",
                    "has_password": bool(a.get("pass_enc"))})
    return out


# The persisted, non-secret account fields. `pass_enc` is written separately from
# the plaintext `password`; a plaintext password is never stored.
_ACCOUNT_FIELDS = ("id", "name", "email", "imap_host", "imap_port", "imap_ssl",
                   "smtp_host", "smtp_port", "smtp_ssl", "user")


def _write_tracked_metadata(rec: dict) -> None:
    """Push this account non-secret fields into the tracked agent_view.config.json
    ("mail_accounts"), replacing any existing entry with the same id and
    preserving every OTHER key (the gemini key, monitor block, everything) --
    the same merge-and-atomic-replace pattern server._save_config uses, written
    here so mailstore does not import server.py (see module docstring)."""
    meta = {k: rec[k] for k in _ACCOUNT_FIELDS if k in rec}

    def _m(cfg):
        accts = [a for a in (cfg.get("mail_accounts") or []) if isinstance(a, dict)]
        cur = next((a for a in accts if a.get("id") == meta["id"]), {})
        merged = dict(cur)
        merged.update(meta)              # supplied fields win, missing ones kept
        cfg["mail_accounts"] = [a for a in accts if a.get("id") != meta["id"]] + [merged]
        return cfg
    # ONE writer (tracked_config): locked, refuses to write over an unreadable
    # (merge-conflicted) file instead of rewriting it from {} without the keys.
    tracked_config.update(_m, _tracked_config_path())


def save_account(d: dict) -> None:
    """Create or replace (by `id`) an account. `d` carries a plaintext
    `password`; it is DPAPI-encrypted into `pass_enc`, which NEVER leaves this
    machine (decision 2026-08-19):

    * `id` already tracked (present in the tracked config "mail_accounts") --
      the local file gets ONLY {id, pass_enc}. The metadata already lives in
      the tracked config and stays there, untouched.
    * `id` not yet tracked (brand new, or a pre-existing local-only account) --
      the earlier behaviour, a full record locally, PLUS its metadata is
      pushed into the tracked config so every other machine picks it up on the
      next pull.

    The plaintext never touches disk either way."""
    if not isinstance(d, dict) or not d.get("id"):
        raise MailError("account needs an id")
    aid = d["id"]
    pw = d.get("password")

    if aid in {a["id"] for a in _tracked_mail_accounts()}:
        if not pw and not d.get("pass_enc"):
            raise MailError("account needs a password")
        # Metadata for a tracked account lives in the tracked config: when the
        # caller supplies any of it, update it THERE (never silently ignored).
        if any(k in d for k in _ACCOUNT_FIELDS if k != "id"):
            _write_tracked_metadata({k: d[k] for k in _ACCOUNT_FIELDS if k in d})
        rec = {"id": aid, "pass_enc": dpapi.encrypt(pw) if pw else d["pass_enc"]}
        cfg = _load_config()
        accts = [a for a in cfg.get("accounts", []) if isinstance(a, dict)]
        cfg["accounts"] = [a for a in accts if a.get("id") != aid] + [rec]
        _atomic_write_config(cfg)
        return

    cfg = _load_config()
    accts = [a for a in cfg.get("accounts", []) if isinstance(a, dict)]
    rec: dict = {}
    existing = next((a for a in accts if a.get("id") == aid), None)
    if existing:
        rec.update(existing)                 # keep pass_enc if no new password
    for k in _ACCOUNT_FIELDS:
        if k in d:
            rec[k] = d[k]
    if pw:
        rec["pass_enc"] = dpapi.encrypt(pw)
    elif d.get("pass_enc"):                  # an already-encrypted blob may pass
        rec["pass_enc"] = d["pass_enc"]      # through (e.g. re-saving metadata)
    rec.pop("password", None)                # never persist the plaintext
    if not rec.get("pass_enc"):
        raise MailError("account needs a password")
    cfg["accounts"] = [a for a in accts if a.get("id") != aid] + [rec]
    _atomic_write_config(cfg)
    _write_tracked_metadata(rec)


# --------------------------------------------------------------------------- #
#  Connections — connect-per-request, password decrypted only here.
# --------------------------------------------------------------------------- #
def _password_of(acct) -> str:
    """The account's plaintext password for THIS login only. A DPAPI blob that
    cannot be decrypted here (a config copied from another machine / Windows
    profile, an admin password reset) is exactly the case the password card
    exists for -> NO_PASSWORD_MSG, so the UI prompts instead of a generic 502
    (reviewer 2026-08-19)."""
    try:
        return dpapi.decrypt(acct.get("pass_enc") or "")
    except Exception:                                # noqa: BLE001 - never the blob or the reason
        raise MailError(NO_PASSWORD_MSG) from None


def verify_login(acct_id) -> None:
    """Log in over IMAP and log out — nothing else. Raises MailError(NO_PASSWORD_MSG)
    when the server rejects the credentials (or none are usable here); any other
    failure surfaces as the usual curated MailError."""
    acct = _account(acct_id)
    try:
        with _imap(acct):
            pass
    except Exception as exc:                         # noqa: BLE001
        raise _fail("could not verify login", exc)


@contextmanager
def _imap(acct):
    host = acct.get("imap_host") or ""
    port = int(acct.get("imap_port") or 993)
    if acct.get("imap_ssl", True):
        M = imaplib.IMAP4_SSL(host, port, timeout=SOCKET_TIMEOUT)
    else:
        M = imaplib.IMAP4(host, port, timeout=SOCKET_TIMEOUT)
        M.starttls(ssl_context=ssl.create_default_context())
    try:
        M.login(acct.get("user") or "", _password_of(acct))
        yield M
    finally:
        try:
            M.logout()
        except Exception:
            pass                             # a failed logout must not mask the op


@contextmanager
def _smtp(acct):
    host = acct.get("smtp_host") or ""
    port = int(acct.get("smtp_port") or 465)
    ctx = ssl.create_default_context()
    if acct.get("smtp_ssl", True):
        S = smtplib.SMTP_SSL(host, port, timeout=SOCKET_TIMEOUT, context=ctx)
    else:
        S = smtplib.SMTP(host, port, timeout=SOCKET_TIMEOUT)
        S.starttls(context=ctx)
    try:
        S.login(acct.get("user") or "", _password_of(acct))
        yield S
    finally:
        try:
            S.quit()
        except Exception:
            pass


#: Substrings an IMAP server puts in a login rejection. Checked against the RAW
#: exception text here (and ONLY here, before it is discarded) — never logged,
#: never handed to a caller, just used to pick the right MailError.
_AUTH_FAIL_MARKERS = ("AUTHENTICATIONFAILED", "Invalid credentials", "LOGIN failed")


def _fail(op, exc):
    """A MailError that names the operation and, at most, the exception TYPE —
    never its text (which can carry a server response or a path) or the
    password. ONE exception (decision 2026-08-19): an IMAP login REJECTION
    collapses to the same NO_PASSWORD_MSG the no-password-yet case uses —
    either way the fix is "re-enter the password" — so server.py can answer
    both with the same 401, still without ever repeating the server's text."""
    if isinstance(exc, MailError):
        return exc
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailError(NO_PASSWORD_MSG)            # SMTP rejected the login
    if isinstance(exc, imaplib.IMAP4.error):
        text = str(exc).casefold()
        if any(m.casefold() in text for m in _AUTH_FAIL_MARKERS):
            return MailError(NO_PASSWORD_MSG)
    return MailError(f"{op} ({type(exc).__name__})")


# --------------------------------------------------------------------------- #
#  Small IMAP/MIME helpers (all defensive against untrusted content)
# --------------------------------------------------------------------------- #
def _uid_str(uid) -> str:
    s = uid.decode("ascii", "replace") if isinstance(uid, bytes) else str(uid)
    # A client-supplied uid flows verbatim into UID FETCH/STORE/COPY commands
    # (imaplib does NOT sanitise args), so a uid carrying CR/LF would inject an
    # IMAP command on the authenticated session. A real UID is digits, with sets
    # (,) and ranges (:*) — reject anything else.
    if not re.match(r"^[0-9][0-9,:*]*$", s):
        raise MailError("bad uid")
    return s


_SAFE_MBOX = re.compile(r"^[A-Za-z0-9_./-]+$")


def _quote(mbox: str) -> str:
    # imaplib does NOT quote arguments (verified against the stdlib), so a mailbox
    # with a space/special must be sent as a quoted IMAP string or it splits into
    # two args. Strip CR/LF/NUL FIRST — those inject an IMAP command even inside a
    # quoted string — then quote. Plain names go through untouched.
    mbox = re.sub(r"[\r\n\x00]", "", str(mbox or ""))
    if _SAFE_MBOX.match(mbox):
        return mbox
    return '"' + mbox.replace("\\", "\\\\").replace('"', '\\"') + '"'


_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')


def _parse_list_line(raw):
    """(flags, delimiter, mailbox) from one LIST/LSUB line, or None. The mailbox
    is returned in its raw (modified-UTF-7) form — that is what SELECT/STATUS
    must be given back."""
    if isinstance(raw, tuple):
        raw = raw[0]
    if not isinstance(raw, (bytes, bytearray)):
        return None
    m = _LIST_RE.match(bytes(raw).strip())
    if not m:
        return None
    flags = [f.decode("ascii", "replace") for f in m.group("flags").split()]
    delim = m.group("delim").decode("ascii", "replace").strip('"')
    name = m.group("name").decode("ascii", "replace").strip()
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return flags, delim, name


def _decode_mutf7(s: str) -> str:
    """RFC 3501 modified UTF-7 mailbox name → unicode, for DISPLAY only. Defensive:
    a malformed run is left as-is rather than raising."""
    import base64
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c != "&":
            out.append(c)
            i += 1
            continue
        j = s.find("-", i)
        if j == -1:
            out.append(s[i:])
            break
        chunk = s[i + 1:j]
        if chunk == "":
            out.append("&")
        else:
            b64 = chunk.replace(",", "/")
            try:
                out.append(base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be"))
            except Exception:
                out.append(s[i:j + 1])
        i = j + 1
    return "".join(out)


def _first_literal(data):
    """The first literal payload (the bytes half of a (metadata, literal) tuple)
    in a FETCH response, or None."""
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _meta_line(data):
    """The response metadata bytes (FLAGS/BODYSTRUCTURE live here) from a FETCH
    whose items may be plain bytes or (metadata, literal) tuples."""
    for item in data or []:
        if isinstance(item, tuple) and item and isinstance(item[0], (bytes, bytearray)):
            return bytes(item[0])
        if isinstance(item, (bytes, bytearray)):
            return bytes(item)
    return b""


def _flag_seen(meta: bytes) -> bool:
    m = re.search(rb"FLAGS\s+\(([^)]*)\)", meta or b"")
    return bool(m) and b"\\Seen" in m.group(1)


def _bs_has_attach(meta: bytes) -> bool:
    # Heuristic for the list badge: an attachment shows as a
    # ("attachment" ...) disposition in BODYSTRUCTURE. message() does the
    # authoritative per-part walk; this only lights a badge cheaply.
    return b"attachment" in (meta or b"").lower()


def _dh(raw) -> str:
    """Decode a possibly MIME-encoded header (RFC 2047) to a clean string."""
    if not raw:
        return ""
    try:
        parts = []
        for text, enc in email.header.decode_header(str(raw)):
            if isinstance(text, bytes):
                parts.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                parts.append(text)
        return "".join(parts).strip()
    except Exception:
        return str(raw)


def _addr(raw):
    """(display_name, email_address) from a From/Sender header, both decoded."""
    if not raw:
        return "", ""
    name, addr = email.utils.parseaddr(str(raw))
    return _dh(name), addr


def _addr_list(values):
    out = []
    for name, addr in email.utils.getaddresses(values or []):
        if not name and not addr:
            continue
        out.append({"name": _dh(name), "email": addr})
    return out


def _is_attachment(part) -> bool:
    disp = (part.get("Content-Disposition") or "").lower()
    if "attachment" in disp:
        return True
    if "inline" in disp:
        return False
    if part.get_content_maintype() == "multipart":
        return False
    return bool(part.get_filename())


def _decode_part(part) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""


def _body_of_type(msg, want) -> str:
    """First non-attachment part of `want` content-type, decoded to str."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == want and not _is_attachment(part):
                return _decode_part(part)
        return ""
    if msg.get_content_type() == want:
        return _decode_part(msg)
    return ""


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _html_unescape(text)


def _snippet(msg) -> str:
    text = _body_of_type(msg, "text/plain")
    if not text:
        text = _strip_html(_body_of_type(msg, "text/html"))
    return re.sub(r"\s+", " ", text).strip()[:SNIPPET_CHARS]


def _sanitise_search(q: str) -> str:
    # CR/LF/NUL would let a crafted query inject extra IMAP commands even inside a
    # quoted string; strip them before the term is embedded.
    return re.sub(r"[\r\n\x00]", " ", q).strip()


def _is_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _search_uids(M, q):
    if not q:
        typ, data = M.uid("SEARCH", "ALL")
    else:
        q = _sanitise_search(q)
        term = '"' + q.replace("\\", "\\\\").replace('"', '\\"') + '"'
        crit = ("OR", "OR", "FROM", term, "SUBJECT", term, "BODY", term)
        if _is_ascii(q):
            typ, data = M.uid("SEARCH", *crit)
        elif "UTF8=ACCEPT" in getattr(M, "capabilities", ()):
            try:
                M.enable("UTF8=ACCEPT")       # RFC 6855: lets the term be UTF-8
            except Exception:
                pass
            typ, data = M.uid("SEARCH", "CHARSET", "UTF-8", *crit)
        else:
            typ, data = M.uid("SEARCH", "ALL")   # cannot filter non-ASCII here
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _special_folder(M, spec):
    """Resolve a special-use folder (archive/sent/drafts). Prefer the RFC 6154
    \\Special-use flag, then a known name, else create the default. Returns the
    raw mailbox path to hand SELECT/COPY/APPEND."""
    special_use, names, default = spec
    fallback = None
    try:
        typ, data = M.list()
    except Exception:
        typ, data = "NO", []
    if typ == "OK":
        for raw in data or []:
            parsed = _parse_list_line(raw)
            if not parsed:
                continue
            flags, _delim, mbox = parsed
            if special_use in flags:
                return mbox
            if _decode_mutf7(mbox).lower() in names and fallback is None:
                fallback = mbox
    if fallback:
        return fallback
    try:
        M.create(_quote(default))
        try:
            M.subscribe(_quote(default))
        except Exception:
            pass
    except Exception:
        pass                                  # COPY/APPEND will surface a failure
    return default


# --------------------------------------------------------------------------- #
#  Public read API
# --------------------------------------------------------------------------- #
def folders(acct_id) -> list:
    """[{name, path, unseen}] for every selectable mailbox. `unseen` via STATUS
    UNSEEN, tolerated as 0 on servers that do not support it."""
    acct = _account(acct_id)
    try:
        with _imap(acct) as M:
            typ, data = M.list()
            if typ != "OK" or not data:
                return []
            out = []
            for raw in data:
                parsed = _parse_list_line(raw)
                if not parsed:
                    continue
                flags, _delim, mbox = parsed
                if "\\Noselect" in flags:
                    continue
                out.append({"name": _decode_mutf7(mbox), "path": mbox,
                            "unseen": _status_unseen(M, mbox)})
            return out
    except Exception as exc:
        raise _fail("could not list folders", exc)


def _status_unseen(M, mbox) -> int:
    try:
        typ, data = M.status(_quote(mbox), "(UNSEEN)")
        if typ == "OK" and data and data[0]:
            m = re.search(rb"UNSEEN\s+(\d+)", data[0] if isinstance(data[0], bytes)
                          else bytes(data[0]))
            if m:
                return int(m.group(1))
    except Exception:
        pass                                  # STATUS unsupported → unknown, not fatal
    return 0


def messages(acct_id, folder, limit=50, q=None) -> list:
    """Newest-first summaries of a folder (optionally matching `q` over
    FROM/SUBJECT/BODY): [{uid, from_name, from_email, subject, date, seen,
    has_attach, snippet}].

    Served from the local cache (`mailcache`) — instant. A folder that has never
    been synced is populated ONCE here so the very first open is still correct;
    every open after that reads only SQLite. Freshness is the server's job: it
    calls `refresh_folder_async` on open and `start_sync` periodically. `q` is
    matched against cached content (FROM/SUBJECT/SNIPPET and any cached body)."""
    _account(acct_id)                                 # validate → MailError if unknown
    try:
        limit = max(1, min(int(limit or 50), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = 50
    if not mailcache.folder_synced(acct_id, folder):
        sync_folder(acct_id, folder)                  # first-time populate (summaries)
    rows = mailcache.read_messages(acct_id, folder, limit, q)
    return rows or []


def _summarize(M, uid):
    uid_s = _uid_str(uid)
    seen = has_attach = False
    typ, meta = M.uid("FETCH", uid_s, "(FLAGS BODYSTRUCTURE)")
    if typ == "OK":
        line = _meta_line(meta)
        seen, has_attach = _flag_seen(line), _bs_has_attach(line)
    typ, data = M.uid("FETCH", uid_s, "(BODY.PEEK[]<0.%d>)" % PREVIEW_BYTES)
    raw = _first_literal(data) if typ == "OK" else None
    msg = email.message_from_bytes(raw or b"")
    name, addr = _addr(msg.get("From"))
    return {"uid": uid_s, "from_name": name, "from_email": addr,
            "subject": _dh(msg.get("Subject")), "date": _dh(msg.get("Date")),
            "seen": seen, "has_attach": has_attach, "snippet": _snippet(msg)}


def message(acct_id, folder, uid) -> dict:
    """A full message: {uid, from_name, from_email, to, cc, subject, date,
    body_text, body_html, attachments:[{name, size, mime, inline, part}]}. The
    attachments list carries EVERY real part — inline (embedded) ones too, flagged
    `inline` — so the UI can list them all; `part` is the id `attachment()`
    consumes.

    Cache-first: a body already cached is returned instantly. Otherwise it is
    fetched live once and cached (headers + body + to/cc + attachment metadata —
    never the attachment bytes, which `attachment()` still streams on demand)."""
    acct = _account(acct_id)
    cached = mailcache.read_message(acct_id, folder, uid)
    if cached is not None:
        return cached
    try:
        with _imap(acct) as M:
            typ, _dat = M.select(_quote(folder), readonly=True)
            if typ != "OK":
                raise MailError("could not open folder")
            msg = _fetch_full(M, uid)
            rendered = _render_message(msg, uid)
    except Exception as exc:
        raise _fail("could not read message", exc)
    try:
        mailcache.store_full(acct_id, folder, rendered["uid"], rendered)
    except Exception:
        pass                                          # caching must never fail a read
    return rendered


def _fetch_full(M, uid):
    typ, data = M.uid("FETCH", _uid_str(uid), "(BODY.PEEK[])")
    raw = _first_literal(data) if typ == "OK" else None
    if raw is None:
        raise MailError("message not found")
    return email.message_from_bytes(raw)


def _iter_real_attachments(msg):
    """Yield (walk_index, part, inline) for EVERY real attachment of `msg`.

    This is the ONE attachment part-walk: _render_message (the detail payload),
    attachment() (a single download) and save_attachments() all consume it, so
    every consumer enumerates the SAME set with the SAME stable `part` id. It is
    broader than an 'attachment'-disposition check — a Content-Disposition: inline
    part (an image embedded in the HTML body) IS a real file and is yielded with
    inline=True. Only multipart containers and the displayed body (a text/plain or
    text/html leaf with no filename that is not dispositioned as an attachment) are
    skipped. The index is enumerate(msg.walk()) — identical across every consumer,
    so a missing or duplicate filename can never make two parts collide."""
    for idx, part in enumerate(msg.walk()):
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            yield idx, part, False
            continue
        inline = "inline" in disp
        if (part.get_content_type() in ("text/plain", "text/html")
                and not part.get_filename() and not inline):
            continue                              # the displayed body — never a file
        yield idx, part, inline


def _render_message(msg, uid):
    name, addr = _addr(msg.get("From"))
    attachments = []
    for idx, part, inline in _iter_real_attachments(msg):
        payload = part.get_payload(decode=True) or b""
        cid = (part.get("Content-ID") or "").strip("<>")
        fname = _dh(part.get_filename()) or cid or f"part-{idx}"
        # `part` id: the walk index (stable — attachment() walks identically) so
        # a missing/duplicate filename can never make two attachments collide.
        attachments.append({"name": fname, "size": len(payload),
                            "mime": part.get_content_type() or "application/octet-stream",
                            "inline": inline, "part": str(idx)})
    return {"uid": _uid_str(uid), "from_name": name, "from_email": addr,
            "to": _addr_list(msg.get_all("To")),
            "cc": _addr_list(msg.get_all("Cc")),
            "subject": _dh(msg.get("Subject")), "date": _dh(msg.get("Date")),
            "body_text": _body_of_type(msg, "text/plain"),
            "body_html": _body_of_type(msg, "text/html"),
            "attachments": attachments}


# --------------------------------------------------------------------------- #
#  Cache sync — the ONLY writer into mailcache. Reads (messages/message) never
#  touch the network beyond a one-time populate; freshness lives here, and the
#  server drives it (start_sync on boot + periodically, refresh_folder_async on
#  a folder open). Sync is incremental: after the first pass a quiet folder costs
#  one SEARCH plus one batched FLAGS fetch — no per-message round trips.
# --------------------------------------------------------------------------- #
def _uidvalidity(M, folder):
    """The folder's UIDVALIDITY — the server's promise that UIDs still mean what
    the cache thinks. A change means the folder was renumbered and the cache must
    be purged. Read from the SELECT's untagged response, then STATUS as a
    fallback; None if the server offers neither."""
    try:
        _typ, dat = M.response("UIDVALIDITY")
        if dat and dat[0]:
            raw = dat[0] if isinstance(dat[0], bytes) else bytes(dat[0])
            m = re.search(rb"(\d+)", raw)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    try:
        typ, dat = M.status(_quote(folder), "(UIDVALIDITY)")
        if typ == "OK" and dat and dat[0]:
            raw = dat[0] if isinstance(dat[0], bytes) else bytes(dat[0])
            m = re.search(rb"UIDVALIDITY\s+(\d+)", raw)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def _all_uids(M):
    """Every UID in the selected folder, ascending — one SEARCH round trip."""
    out = []
    for u in _search_uids(M, None):
        try:
            out.append(int(u.decode("ascii") if isinstance(u, bytes) else u))
        except (ValueError, AttributeError):
            continue
    return sorted(out)


def _fetch_seen_flags(M, uids):
    """{uid: seen} for a set of UIDs in ONE FETCH — the cheap reconciliation the
    steady-state sync runs so read rows never show a stale unread badge."""
    if not uids:
        return {}
    set_str = _uid_str(",".join(str(u) for u in uids))
    typ, data = M.uid("FETCH", set_str, "(FLAGS)")
    out = {}
    if typ != "OK":
        return out
    for item in data or []:
        if isinstance(item, tuple) and item and isinstance(item[0], (bytes, bytearray)):
            line = bytes(item[0])
        elif isinstance(item, (bytes, bytearray)):
            line = bytes(item)
        else:
            continue
        m = re.search(rb"UID\s+(\d+)", line)
        if m:
            out[int(m.group(1))] = _flag_seen(line)
    return out


# --------------------------------------------------------------------------- #
#  Sync observers — a consumer (the server's new-mail notifier) registers here to
#  be told, after each folder sync, the UID snapshot the sync ALREADY fetched. It
#  costs NO extra IMAP round trip: it reuses `server_uids` the sync computed.
#  mailstore never imports its consumer; it just calls whatever registered, and a
#  failing observer is swallowed so it can never break a sync (the signals
#  discipline — a downstream side effect must not roll back the sync).
# --------------------------------------------------------------------------- #
_sync_observers: list = []


def add_sync_observer(fn) -> None:
    """Register fn(acct_id, folder, uidvalidity, server_uids) to run after every
    folder sync. Registered once at startup; a repeat registration is ignored."""
    if fn not in _sync_observers:
        _sync_observers.append(fn)


def _notify_sync_observers(acct_id, folder, uidvalidity, server_uids) -> None:
    for fn in list(_sync_observers):
        try:
            fn(acct_id, folder, uidvalidity, list(server_uids))
        except Exception:
            pass                                  # an observer must never break sync


def _sync_folder_on(M, acct_id, folder, prefetch_bodies):
    """One incremental sync of `folder` over an already-open, logged-in IMAP
    connection `M`. Split from sync_folder so sync_all can reuse a single
    connection across every folder."""
    typ, _dat = M.select(_quote(folder), readonly=True)
    if typ != "OK":
        raise MailError("could not open folder")
    uidvalidity = _uidvalidity(M, folder)
    cached_uv, last_uid = mailcache.get_folder_meta(acct_id, folder)
    if cached_uv is not None and uidvalidity is not None and cached_uv != uidvalidity:
        mailcache.purge_folder(acct_id, folder)       # server renumbered — start over
        last_uid = 0
    server_uids = _all_uids(M)
    server_set = set(server_uids)
    cached = mailcache.cached_uids(acct_id, folder)
    gone = cached - server_set
    if gone:
        mailcache.delete_uids(acct_id, folder, gone)   # deleted on the server
    new_uids = [u for u in server_uids if u > (last_uid or 0)]
    if new_uids:
        rows = [s for s in (_summarize(M, u) for u in new_uids) if s]
        mailcache.store_summaries(acct_id, folder, rows)
    still_here = sorted(server_set & cached)
    if still_here:
        mailcache.update_seen(acct_id, folder, _fetch_seen_flags(M, still_here))
    for u in mailcache.uids_without_body(acct_id, folder, prefetch_bodies):
        try:
            mailcache.store_full(acct_id, folder, u,
                                 _render_message(_fetch_full(M, u), u))
        except Exception:
            pass                                       # one bad body must not abort
    new_last = max(server_uids) if server_uids else (last_uid or 0)
    mailcache.set_folder_meta(acct_id, folder, uidvalidity, new_last)
    # Hand the snapshot we already fetched to any observer (the new-mail notifier),
    # so it detects growth with no extra IMAP round trip.
    _notify_sync_observers(acct_id, folder, uidvalidity, server_uids)


def sync_folder(acct_id, folder, prefetch_bodies=0) -> None:
    """Bring one folder's cache up to date over a fresh connection. Summaries
    only by default; pass `prefetch_bodies` to also warm that many newest bodies.
    Runs synchronously — the async wrappers below put it on a thread."""
    acct = _account(acct_id)
    try:
        with _imap(acct) as M:
            _sync_folder_on(M, acct_id, folder, prefetch_bodies)
    except Exception as exc:
        raise _fail("could not sync folder", exc)


def sync_all(acct_id, prefetch_bodies=PREFETCH_BODIES) -> None:
    """Sync every selectable folder over ONE connection. Bodies are prefetched
    for INBOX only (where opens concentrate); other folders warm their bodies on
    first open and on the next refresh. Runs synchronously — see start_sync."""
    acct = _account(acct_id)
    try:
        with _imap(acct) as M:
            typ, data = M.list()
            mboxes = []
            if typ == "OK":
                for raw in data or []:
                    parsed = _parse_list_line(raw)
                    if not parsed:
                        continue
                    flags, _delim, mbox = parsed
                    if "\\Noselect" in flags:
                        continue
                    mboxes.append(mbox)
            for mbox in mboxes:
                pf = prefetch_bodies if mbox.upper() == "INBOX" else 0
                try:
                    _sync_folder_on(M, acct_id, mbox, pf)
                except Exception:
                    continue                           # one bad folder must not abort
    except Exception as exc:
        raise _fail("could not sync account", exc)


# --------------------------------------------------------------------------- #
#  Background drivers — non-blocking freshness. The server owns the schedule;
#  these just guarantee a folder/account is never synced by two threads at once,
#  and that a burst of opens does not fan out into a burst of connections.
# --------------------------------------------------------------------------- #
_sync_lock = threading.Lock()
_syncing: set = set()          # keys currently being synced: (acct, folder) or (acct, "*")
_last_sync: dict = {}          # key -> monotone-ish wall time of last completion


def _release(key) -> None:
    with _sync_lock:
        _syncing.discard(key)
        _last_sync[key] = time.time()


def refresh_folder_async(acct_id, folder, prefetch_bodies=PREFETCH_BODIES) -> bool:
    """Kick a background sync of one folder and return immediately. Debounced: a
    no-op (returns False) if a sync for this folder is already running or one
    finished within REFRESH_DEBOUNCE seconds. Call it on every folder open — the
    open itself still reads instantly from cache."""
    key = (acct_id, folder)
    with _sync_lock:
        if key in _syncing:
            return False
        if time.time() - _last_sync.get(key, 0.0) < REFRESH_DEBOUNCE:
            return False
        _syncing.add(key)

    def _run():
        try:
            sync_folder(acct_id, folder, prefetch_bodies)
        except Exception:
            pass                                       # background: never surfaces
        finally:
            _release(key)

    threading.Thread(target=_run, daemon=True).start()
    return True


def start_sync(acct_id) -> bool:
    """Kick a background full-account sync and return immediately. Returns False
    if one is already running for this account. Call on server start and on a
    periodic timer."""
    key = (acct_id, "*")
    with _sync_lock:
        if key in _syncing:
            return False
        _syncing.add(key)

    def _run():
        try:
            sync_all(acct_id)
        except Exception:
            pass
        finally:
            _release(key)

    threading.Thread(target=_run, daemon=True).start()
    return True


def attachment(acct_id, folder, uid, part) -> tuple:
    """(filename, raw_bytes, content_type) for one attachment, addressed by the
    `part` id message() returned (its walk index, or a Content-ID)."""
    acct = _account(acct_id)
    try:
        with _imap(acct) as M:
            typ, _dat = M.select(_quote(folder), readonly=True)
            if typ != "OK":
                raise MailError("could not open folder")
            msg = _fetch_full(M, uid)
    except Exception as exc:
        raise _fail("could not read attachment", exc)
    target = str(part)
    for idx, p, _inline in _iter_real_attachments(msg):
        cid = (p.get("Content-ID") or "").strip("<>")
        if target == str(idx) or (cid and target == cid):
            payload = p.get_payload(decode=True) or b""
            fname = _dh(p.get_filename()) or cid or f"part-{idx}"
            return (fname, payload, p.get_content_type() or "application/octet-stream")
    raise MailError("attachment not found")


# --------------------------------------------------------------------------- #
#  Saving attachments to disk. EVERY mail filename is attacker-controlled, so it
#  is reduced to a single safe path component (basename only, no separators, no
#  '..', no control chars, none of the Windows-reserved characters) before it is
#  ever joined to a directory — this is a path-traversal surface, treated like an
#  untrusted upload name. The folder name is built the same way from the message
#  date/sender/subject. Attachment bytes are never logged or returned.
# --------------------------------------------------------------------------- #
DEFAULT_ATTACHMENTS_DIR = "~/mail_attachments"
SAVE_TOTAL_CAP = 100 * 1024 * 1024              # per-message ceiling on bytes written
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
# Illegal on Windows (< > : " / \ | ? *); stripped so mkdir/open never fails on a
# subject like "Re: ..." and so no separator can survive the basename step.
_WIN_RESERVED_CH = re.compile(r'[<>:"/\\|?*]')
_WIN_RESERVED_NAMES = {"con", "prn", "aux", "nul",
                       *(f"com{i}" for i in range(1, 10)),
                       *(f"lpt{i}" for i in range(1, 10))}


def _attachments_dir() -> Path:
    """The configured root for saved attachments — mail-config `attachments_dir`,
    default ~/mail_attachments, with `~` expanded. The caller creates the tree."""
    raw = _load_config().get("attachments_dir") or DEFAULT_ATTACHMENTS_DIR
    return Path(os.path.expanduser(str(raw or DEFAULT_ATTACHMENTS_DIR)))


def _safe_component(raw, maxlen) -> str:
    """One UNTRUSTED string → one filesystem-safe path component, or '' if nothing
    survives (the caller supplies a fallback). Basename only — both '/' and '\\'
    are cut so no directory prefix survives — control chars and Windows-reserved
    characters removed, whitespace collapsed, leading/trailing dots+spaces stripped
    (which also neutralises a bare '.'/'..'), then length-bounded."""
    s = str(raw or "").replace("\\", "/")
    s = s.split("/")[-1]                         # drop any path; keep the last segment
    s = _CTRL.sub("", s)
    s = _WIN_RESERVED_CH.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(". ")
    return s[:maxlen].strip(". ")


def _safe_attachment_name(raw, ordinal) -> str:
    """A saved attachment's on-disk name from an attacker-controlled filename:
    reduced to a safe basename, an empty/degenerate result → attachment_<n>.bin,
    a reserved Windows device name prefixed with '_', and the extension preserved
    when the name is truncated for length."""
    name = _safe_component(raw, 200)
    if not name:
        return f"attachment_{ordinal}.bin"
    if name.rpartition(".")[0].lower() in _WIN_RESERVED_NAMES or name.lower() in _WIN_RESERVED_NAMES:
        name = "_" + name
    if len(name) > 100:
        stem, dot, ext = name.rpartition(".")
        if dot and 1 <= len(ext) <= 12:
            name = ((stem[:100 - len(ext) - 1].strip(". ") or "file") + "." + ext)
        else:
            name = name[:100].strip(". ")
    return name or f"attachment_{ordinal}.bin"


def _slug_date(raw) -> str:
    """YYYY-MM-DD from a message Date header; today if it is missing/unparseable."""
    try:
        dt = email.utils.parsedate_to_datetime(str(raw or ""))
        if dt is not None:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return time.strftime("%Y-%m-%d")


def _message_slug(date_hdr, from_name, from_email, subject) -> str:
    """`<YYYY-MM-DD>_<from-name-or-email>_<subject>`, each piece sanitised on its
    OWN (so a '/' in the sender can never eat the date) and the whole bounded to
    ~80 chars. Never empty and — being date-prefixed — never a reserved name."""
    date = _slug_date(date_hdr)
    who = _safe_component(from_name or from_email, 40) or "unknown"
    subj = _safe_component(subject, 60) or "no-subject"
    return f"{date}_{who}_{subj}"[:80].strip(". ") or date


def _unique_name(name, used_lower: set) -> str:
    """`name`, or the first free `name (1).ext`, `name (2).ext`… — matched
    case-insensitively so it holds on case-insensitive filesystems. `used_lower`
    is updated with the chosen name."""
    stem, dot, ext = name.rpartition(".")
    base, suffix = (stem, "." + ext) if dot else (name, "")
    cand, i = name, 1
    while cand.lower() in used_lower:
        cand = f"{base} ({i}){suffix}"
        i += 1
    used_lower.add(cand.lower())
    return cand


def save_attachments(acct_id, folder, uid) -> dict:
    """Fetch the message ONCE and write EVERY real attachment (inline images
    included, body parts excluded) into <attachments_dir>/<message-slug>/. Returns
    {path, saved, skipped} — `saved` the written names, `skipped` [{name, reason}].

    Every attachment filename is attacker-controlled and is reduced to a safe
    basename before being joined to the folder, so this can never write outside the
    slug folder (a resolved-path check backs the sanitiser up). Total bytes are
    capped per message; anything over is skipped and reported. Never logs or
    returns attachment bytes."""
    acct = _account(acct_id)
    try:
        with _imap(acct) as M:
            typ, _dat = M.select(_quote(folder), readonly=True)
            if typ != "OK":
                raise MailError("could not open folder")
            msg = _fetch_full(M, uid)
    except Exception as exc:
        raise _fail("could not save attachments", exc)

    from_name, from_email = _addr(msg.get("From"))
    slug = _message_slug(msg.get("Date"), from_name, from_email, _dh(msg.get("Subject")))
    try:
        dest_dir = _attachments_dir() / slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        base_real = os.path.realpath(str(dest_dir))
    except Exception as exc:
        raise _fail("could not create attachments folder", exc)

    used_lower: set = set()
    try:
        for existing in os.listdir(base_real):     # de-dupe against a prior save too
            used_lower.add(existing.lower())
    except OSError:
        pass

    saved: list = []
    skipped: list = []
    total = 0
    ordinal = 0
    for _idx, part, _inline in _iter_real_attachments(msg):
        ordinal += 1
        name = _safe_attachment_name(
            _dh(part.get_filename()) or (part.get("Content-ID") or "").strip("<>"),
            ordinal)
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            skipped.append({"name": name, "reason": "unreadable"})
            continue
        if not payload:
            skipped.append({"name": name, "reason": "empty"})
            continue
        if len(payload) > SAVE_TOTAL_CAP or total + len(payload) > SAVE_TOTAL_CAP:
            skipped.append({"name": name, "reason": "over size cap"})
            continue
        final = _unique_name(name, used_lower)
        target = os.path.realpath(os.path.join(base_real, final))
        # Defence in depth: the sanitised name already cannot escape, but confirm
        # the resolved path stays inside the folder before a single byte is written.
        try:
            inside = os.path.commonpath([base_real, target]) == base_real
        except ValueError:                          # different drive → definitely outside
            inside = False
        if not inside:
            skipped.append({"name": final, "reason": "unsafe path"})
            continue
        try:
            with open(target, "wb") as fh:
                fh.write(payload)
        except Exception:
            skipped.append({"name": final, "reason": "write failed"})
            continue
        total += len(payload)
        saved.append(final)

    return {"path": base_real, "saved": saved, "skipped": skipped}


def reveal_in_file_manager(path) -> None:
    """Best-effort: open `path` in the OS file manager. Fire-and-forget and fully
    fail-safe — it must NEVER raise into or delay a response. The server calls this
    only for a loopback client, because it acts on the machine the server runs on."""
    try:
        p = str(path)
        if sys.platform.startswith("win"):
            os.startfile(p)                         # noqa — no shell; opens Explorer
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", p], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Public write API
# --------------------------------------------------------------------------- #
_ACTIONS = ("seen", "unseen", "delete", "archive")


def action(acct_id, folder, uid, op) -> None:
    """Mutate one message: seen/unseen (STORE ±\\Seen), delete (STORE \\Deleted +
    EXPUNGE), or archive (COPY to the Archive folder, then delete)."""
    if op not in _ACTIONS:
        raise MailError("unknown action")
    acct = _account(acct_id)
    uid_s = _uid_str(uid)
    try:
        with _imap(acct) as M:
            typ, _dat = M.select(_quote(folder))          # writable, not readonly
            if typ != "OK":
                raise MailError("could not open folder")
            if op == "seen":
                M.uid("STORE", uid_s, "+FLAGS", "(\\Seen)")
            elif op == "unseen":
                M.uid("STORE", uid_s, "-FLAGS", "(\\Seen)")
            elif op == "delete":
                M.uid("STORE", uid_s, "+FLAGS", "(\\Deleted)")
                M.expunge()
            elif op == "archive":
                dest = _special_folder(M, _ARCHIVE)
                typ, _c = M.uid("COPY", uid_s, _quote(dest))
                if typ != "OK":
                    raise MailError("could not archive message")
                M.uid("STORE", uid_s, "+FLAGS", "(\\Deleted)")
                M.expunge()
        _cache_after_action(acct_id, folder, uid_s, op)
    except Exception as exc:
        raise _fail("action failed", exc)


def _cache_after_action(acct_id, folder, uid_s, op) -> None:
    """Reflect a just-committed action in the cache so the list updates at once,
    without waiting for the next background sync. Swallows everything — a cache
    hiccup must never turn a successful IMAP action into a failure."""
    if not uid_s.isdigit():                            # only single-uid actions cache
        return
    try:
        if op == "seen":
            mailcache.set_seen_one(acct_id, folder, uid_s, True)
        elif op == "unseen":
            mailcache.set_seen_one(acct_id, folder, uid_s, False)
        elif op in ("delete", "archive"):
            mailcache.delete_one(acct_id, folder, uid_s)
    except Exception:
        pass


def _build_message(acct, to, cc, subject, body, attachments=None) -> EmailMessage:
    msg = EmailMessage()
    frm = acct.get("email") or acct.get("user") or ""
    msg["From"] = frm
    if to:
        msg["To"] = _as_header(to)
    if cc:
        msg["Cc"] = _as_header(cc)
    msg["Subject"] = subject or ""
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()
    msg.set_content(body or "")
    for att in attachments or []:
        fname = att.get("name") or "attachment"
        raw = att.get("bytes") or b""
        ctype, _enc = mimetypes.guess_type(fname)
        maintype, subtype = (ctype.split("/", 1) if ctype and "/" in ctype
                             else ("application", "octet-stream"))
        msg.add_attachment(raw, maintype=maintype, subtype=subtype, filename=fname)
    return msg


def send(acct_id, to, cc, subject, body, attachments) -> None:
    """Send a plaintext message (+ attachments) over SMTP as the account, then
    best-effort APPEND a copy to Sent."""
    acct = _account(acct_id)
    frm = acct.get("email") or acct.get("user") or ""
    recipients = _recipients(to) + _recipients(cc)
    if not recipients:
        raise MailError("no recipients")
    msg = _build_message(acct, to, cc, subject, body, attachments)
    try:
        with _smtp(acct) as S:
            S.send_message(msg, from_addr=frm, to_addrs=recipients)
    except Exception as exc:
        raise _fail("could not send", exc)
    _append(acct, _SENT, "\\Seen", msg)       # best-effort; failure never raises


def save_draft(acct_id, to, cc, subject, body) -> None:
    """APPEND a plaintext draft to the Drafts folder. Unlike the Sent copy this
    IS the operation, so a failure raises."""
    acct = _account(acct_id)
    msg = _build_message(acct, to, cc, subject, body)
    try:
        with _imap(acct) as M:
            dest = _special_folder(M, _DRAFTS)
            typ, _dat = M.append(_quote(dest), "\\Draft",
                                 time.time(), msg.as_bytes())
            if typ != "OK":
                raise MailError("could not save draft")
    except Exception as exc:
        raise _fail("could not save draft", exc)


def _append(acct, spec, flags, msg) -> None:
    """Best-effort APPEND of `msg` to a special-use folder. A failed Sent copy
    must never fail an otherwise-successful send, so everything here is
    swallowed."""
    try:
        with _imap(acct) as M:
            dest = _special_folder(M, spec)
            M.append(_quote(dest), flags, time.time(), msg.as_bytes())
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Address helpers
# --------------------------------------------------------------------------- #
def _split_addrs(v):
    if v is None:
        return []
    items = list(v) if isinstance(v, (list, tuple)) else re.split(r"[;,]", str(v))
    return [s.strip() for s in items if s and str(s).strip()]


def _as_header(v) -> str:
    return ", ".join(_split_addrs(v))


def _recipients(v):
    out = []
    for item in _split_addrs(v):
        _name, addr = email.utils.parseaddr(item)
        if addr:
            out.append(addr)
    return out
