#!/usr/bin/env python3
"""Local mail cache for the agent_view Mail tab — standard library `sqlite3` only.

This is the storage half of the Mail tab: a single SQLite file next to this
module (`mail_cache.sqlite`) that AUTO-CREATES on first use — no migration step,
no config. It is a normal local mail store, exactly like Thunderbird's: it holds
message CONTENT the user already received (headers, body text/html, a snippet,
and attachment *metadata* — never attachment bytes, which stay on-demand). It is
gitignored and plaintext-at-rest is acceptable, because:

  * the account PASSWORD never enters this file (it lives DPAPI-encrypted in the
    config and is decrypted only in-process at IMAP connect time — see dpapi.py);
  * everything stored here is content already delivered to this mailbox.

mailstore.py owns IMAP; this module owns SQLite. There is no import from
mailstore here, so the two stay independently testable. The public surface is:
read helpers (`read_messages`, `read_message`, `folder_synced`) that a request
serves from instantly, and write helpers a sync fills (`store_summaries`,
`store_full`, `update_seen`, `delete_uids`, folder-meta + purge).

Concurrency: WAL mode plus a fresh connection per call, so a background sync
thread can write while the request thread reads. UIDs are stored as integers so
`ORDER BY uid DESC` gives the same newest-first order the IMAP path relies on
(SEARCH returns ascending UID within one UIDVALIDITY).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent

SNIPPET_CHARS = 140            # keep in step with mailstore._snippet's cap

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    acct            TEXT    NOT NULL,
    folder          TEXT    NOT NULL,
    uid             INTEGER NOT NULL,
    from_name       TEXT,
    from_email      TEXT,
    subject         TEXT,
    date            TEXT,
    seen            INTEGER NOT NULL DEFAULT 0,
    has_attach      INTEGER NOT NULL DEFAULT 0,
    snippet         TEXT,
    body_text       TEXT,
    body_html       TEXT,
    to_json         TEXT,
    cc_json         TEXT,
    attach_json     TEXT,
    fetched_at      REAL,
    body_fetched_at REAL,
    PRIMARY KEY (acct, folder, uid)
);
CREATE TABLE IF NOT EXISTS folders (
    acct        TEXT    NOT NULL,
    folder      TEXT    NOT NULL,
    uidvalidity INTEGER,
    last_uid    INTEGER NOT NULL DEFAULT 0,
    synced_at   REAL,
    PRIMARY KEY (acct, folder)
);
"""

_DB_ENV = "AGENT_VIEW_MAIL_CACHE"     # tests point this at a throwaway file
_init_lock = threading.Lock()
_initialized_for: str | None = None   # the path the schema was last created for


def _db_path() -> Path:
    return Path(os.environ.get(_DB_ENV) or (HERE / "mail_cache.sqlite"))


def _ensure_schema() -> None:
    """Create the DB + tables once per path. Cheap and idempotent; the guard just
    avoids re-running DDL on every connection. Re-inits if the path changes (a
    test swapping in a temp DB)."""
    global _initialized_for
    path = str(_db_path())
    with _init_lock:
        if _initialized_for == path:
            return
        con = sqlite3.connect(path, timeout=5.0)
        try:
            con.execute("PRAGMA journal_mode=WAL")     # reader concurrent with writer
            con.execute("PRAGMA busy_timeout=5000")
            con.executescript(_SCHEMA)
            con.commit()
        finally:
            con.close()
        _initialized_for = path


@contextmanager
def _connect():
    _ensure_schema()
    con = sqlite3.connect(str(_db_path()), timeout=5.0)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------- #
#  Folder sync bookkeeping
# --------------------------------------------------------------------------- #
def folder_synced(acct: str, folder: str) -> bool:
    """True once a folder has been synced at least once — the signal `messages()`
    uses to decide between an instant cache read and a first-time populate."""
    with _connect() as con:
        row = con.execute("SELECT 1 FROM folders WHERE acct=? AND folder=? LIMIT 1",
                          (acct, folder)).fetchone()
        return row is not None


def get_folder_meta(acct: str, folder: str):
    """(uidvalidity | None, last_uid) for a folder; (None, 0) if never synced."""
    with _connect() as con:
        row = con.execute("SELECT uidvalidity, last_uid FROM folders "
                          "WHERE acct=? AND folder=?", (acct, folder)).fetchone()
        if not row:
            return None, 0
        return row["uidvalidity"], (row["last_uid"] or 0)


def set_folder_meta(acct: str, folder: str, uidvalidity, last_uid: int) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO folders (acct, folder, uidvalidity, last_uid, synced_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(acct, folder) DO UPDATE SET "
            "  uidvalidity=excluded.uidvalidity, last_uid=excluded.last_uid, "
            "  synced_at=excluded.synced_at",
            (acct, folder, uidvalidity, int(last_uid or 0), time.time()))


def purge_folder(acct: str, folder: str) -> None:
    """Drop every cached row for a folder — used when the server's UIDVALIDITY
    changed and the old UIDs no longer mean anything. The caller re-writes the
    folder meta afterward."""
    with _connect() as con:
        con.execute("DELETE FROM messages WHERE acct=? AND folder=?", (acct, folder))


def cached_uids(acct: str, folder: str) -> set:
    with _connect() as con:
        return {r["uid"] for r in con.execute(
            "SELECT uid FROM messages WHERE acct=? AND folder=?", (acct, folder))}


def uids_without_body(acct: str, folder: str, limit: int) -> list:
    """Newest-first UIDs whose full body is not cached yet — the sync's body
    prefetch worklist."""
    with _connect() as con:
        rows = con.execute(
            "SELECT uid FROM messages WHERE acct=? AND folder=? "
            "AND body_fetched_at IS NULL ORDER BY uid DESC LIMIT ?",
            (acct, folder, int(limit))).fetchall()
        return [r["uid"] for r in rows]


# --------------------------------------------------------------------------- #
#  Writes
# --------------------------------------------------------------------------- #
def store_summaries(acct: str, folder: str, rows: list) -> None:
    """Upsert header/snippet rows (the list view). Body columns are left
    untouched, so a summary re-sync never wipes an already-cached body."""
    if not rows:
        return
    with _connect() as con:
        con.executemany(
            "INSERT INTO messages "
            "  (acct, folder, uid, from_name, from_email, subject, date, "
            "   seen, has_attach, snippet, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(acct, folder, uid) DO UPDATE SET "
            "  from_name=excluded.from_name, from_email=excluded.from_email, "
            "  subject=excluded.subject, date=excluded.date, seen=excluded.seen, "
            "  has_attach=excluded.has_attach, snippet=excluded.snippet, "
            "  fetched_at=excluded.fetched_at",
            [(acct, folder, int(r["uid"]), r.get("from_name"), r.get("from_email"),
              r.get("subject"), r.get("date"), 1 if r.get("seen") else 0,
              1 if r.get("has_attach") else 0, r.get("snippet"), time.time())
             for r in rows])


def store_full(acct: str, folder: str, uid, rendered: dict) -> None:
    """Cache a full message (the render `message()` returns). Writes body + to/cc
    + attachment METADATA (never attachment bytes). On an existing summary row,
    seen/has_attach/snippet are preserved — the summary sync owns those; here we
    only add the body."""
    body_text = rendered.get("body_text") or ""
    snippet = " ".join(body_text.split())[:SNIPPET_CHARS]
    with _connect() as con:
        con.execute(
            "INSERT INTO messages "
            "  (acct, folder, uid, from_name, from_email, subject, date, "
            "   seen, has_attach, snippet, body_text, body_html, "
            "   to_json, cc_json, attach_json, fetched_at, body_fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(acct, folder, uid) DO UPDATE SET "
            "  from_name=excluded.from_name, from_email=excluded.from_email, "
            "  subject=excluded.subject, date=excluded.date, "
            "  body_text=excluded.body_text, body_html=excluded.body_html, "
            "  to_json=excluded.to_json, cc_json=excluded.cc_json, "
            "  attach_json=excluded.attach_json, fetched_at=excluded.fetched_at, "
            "  body_fetched_at=excluded.body_fetched_at",
            (acct, folder, int(uid), rendered.get("from_name"),
             rendered.get("from_email"), rendered.get("subject"),
             rendered.get("date"), 0, 1 if rendered.get("attachments") else 0,
             snippet, body_text, rendered.get("body_html") or "",
             json.dumps(rendered.get("to") or []),
             json.dumps(rendered.get("cc") or []),
             json.dumps(rendered.get("attachments") or []),
             time.time(), time.time()))


def update_seen(acct: str, folder: str, mapping: dict) -> None:
    """Reconcile the \\Seen flag for already-cached UIDs against the server."""
    if not mapping:
        return
    with _connect() as con:
        con.executemany(
            "UPDATE messages SET seen=? WHERE acct=? AND folder=? AND uid=?",
            [(1 if seen else 0, acct, folder, int(uid))
             for uid, seen in mapping.items()])


def delete_uids(acct: str, folder: str, uids) -> None:
    uids = list(uids)
    if not uids:
        return
    with _connect() as con:
        con.executemany(
            "DELETE FROM messages WHERE acct=? AND folder=? AND uid=?",
            [(acct, folder, int(u)) for u in uids])


def set_seen_one(acct: str, folder: str, uid, seen: bool) -> None:
    """Reflect a just-issued seen/unseen action immediately, without waiting for
    the next sync."""
    with _connect() as con:
        con.execute("UPDATE messages SET seen=? WHERE acct=? AND folder=? AND uid=?",
                    (1 if seen else 0, acct, folder, int(uid)))


def delete_one(acct: str, folder: str, uid) -> None:
    """Drop a row the user just deleted/archived, so it leaves the list at once."""
    with _connect() as con:
        con.execute("DELETE FROM messages WHERE acct=? AND folder=? AND uid=?",
                    (acct, folder, int(uid)))


# --------------------------------------------------------------------------- #
#  Reads — the instant path
# --------------------------------------------------------------------------- #
def _like_escape(term: str) -> str:
    # A user typing % or _ must match those characters literally, not the SQL
    # wildcards — otherwise "%" would match everything. Paired with ESCAPE '\'.
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Folders whose NAME contains one of these tokens are Trash/Junk/Spam across every
# provider naming ([Gmail]/Trash, Deleted Items, INBOX.Junk, …) — excluded from
# the RAG search by default so a deleted or spam mail never answers a question.
# One definition, reused by search_account (SQL) and is_excluded_folder (Python).
EXCLUDED_FOLDER_TOKENS = ("trash", "junk", "spam", "deleted")


def is_excluded_folder(folder: str) -> bool:
    """True if `folder` is a Trash/Junk/Spam-style mailbox by name."""
    low = (folder or "").lower()
    return any(tok in low for tok in EXCLUDED_FOLDER_TOKENS)


def read_messages(acct: str, folder: str, limit: int, q: str | None = None):
    """Newest-first summaries from the cache, or None if the folder was never
    synced (the caller then populates once). Shape matches mailstore.messages():
    [{uid, from_name, from_email, subject, date, seen, has_attach, snippet}]."""
    if not folder_synced(acct, folder):
        return None
    sql = ("SELECT uid, from_name, from_email, subject, date, seen, has_attach, "
           "snippet FROM messages WHERE acct=? AND folder=?")
    params: list = [acct, folder]
    if q:
        like = "%" + _like_escape(q.strip()) + "%"
        sql += (" AND (from_name LIKE ? ESCAPE '\\' OR from_email LIKE ? ESCAPE '\\' "
                "OR subject LIKE ? ESCAPE '\\' OR snippet LIKE ? ESCAPE '\\' "
                "OR IFNULL(body_text,'') LIKE ? ESCAPE '\\')")
        params += [like, like, like, like, like]
    sql += " ORDER BY uid DESC LIMIT ?"
    params.append(int(limit))
    with _connect() as con:
        rows = con.execute(sql, params).fetchall()
    return [{"uid": str(r["uid"]), "from_name": r["from_name"] or "",
             "from_email": r["from_email"] or "", "subject": r["subject"] or "",
             "date": r["date"] or "", "seen": bool(r["seen"]),
             "has_attach": bool(r["has_attach"]), "snippet": r["snippet"] or ""}
            for r in rows]


def read_message(acct: str, folder: str, uid):
    """A full cached message, or None if it is not cached WITH its body (the
    caller then fetches it live and calls store_full). Shape matches
    mailstore.message()."""
    try:
        uid_i = int(uid)
    except (TypeError, ValueError):
        return None
    with _connect() as con:
        r = con.execute(
            "SELECT uid, from_name, from_email, subject, date, body_text, "
            "body_html, to_json, cc_json, attach_json, body_fetched_at "
            "FROM messages WHERE acct=? AND folder=? AND uid=?",
            (acct, folder, uid_i)).fetchone()
    if not r or r["body_fetched_at"] is None:
        return None

    def _loads(s):
        try:
            v = json.loads(s or "[]")
            return v if isinstance(v, list) else []
        except Exception:
            return []

    return {"uid": str(r["uid"]), "from_name": r["from_name"] or "",
            "from_email": r["from_email"] or "", "to": _loads(r["to_json"]),
            "cc": _loads(r["cc_json"]), "subject": r["subject"] or "",
            "date": r["date"] or "", "body_text": r["body_text"] or "",
            "body_html": r["body_html"] or "",
            "attachments": _loads(r["attach_json"])}


def search_account(acct: str, terms, limit: int):
    """Newest-first messages for ONE account whose subject, body, snippet, or
    sender matches ANY of the given keyword `terms` (OR-combined), across every
    folder EXCEPT Trash/Junk/Spam — the retrieval half of the Mail side-chat (RAG).

    Deliberately NOT read_messages: that one is single-folder and matches a single
    query string; this spans folders and ORs several keywords, so a
    natural-language question can hit on any of its content terms. Trash/Junk/Spam
    are excluded so a deleted or spam mail never answers a question (see
    EXCLUDED_FOLDER_TOKENS). Ordering: the cache has no normalized message
    timestamp (`date` is a raw header string), so rows are ordered by fetched_at
    then uid — for INBOX, synced as mail arrives, that is newest-first; a coarse
    but acceptable proxy across folders for a top-K retrieval. An empty/blank term
    list returns []."""
    terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not terms:
        return []
    clause = ("(subject LIKE ? ESCAPE '\\' OR IFNULL(body_text,'') LIKE ? ESCAPE '\\' "
              "OR IFNULL(snippet,'') LIKE ? ESCAPE '\\' "
              "OR IFNULL(from_name,'') LIKE ? ESCAPE '\\' "
              "OR IFNULL(from_email,'') LIKE ? ESCAPE '\\')")
    where = " OR ".join(clause for _ in terms)
    params: list = [acct]
    for t in terms:
        like = "%" + _like_escape(t) + "%"
        params += [like, like, like, like, like]
    # Exclude Trash/Junk/Spam by folder name — filter at the SQL level so the LIMIT
    # top-K is not crowded out by deleted/spam rows.
    excl = " AND ".join("LOWER(IFNULL(folder,'')) NOT LIKE ?"
                        for _ in EXCLUDED_FOLDER_TOKENS)
    params += ["%" + tok + "%" for tok in EXCLUDED_FOLDER_TOKENS]
    sql = ("SELECT folder, uid, from_name, from_email, subject, date, snippet, "
           "body_text FROM messages WHERE acct=? AND (" + where + ") "
           "AND " + excl + " "
           "ORDER BY fetched_at DESC, uid DESC LIMIT ?")
    params.append(int(limit))
    with _connect() as con:
        rows = con.execute(sql, params).fetchall()
    return [{"folder": r["folder"], "uid": str(r["uid"]),
             "from_name": r["from_name"] or "", "from_email": r["from_email"] or "",
             "subject": r["subject"] or "", "date": r["date"] or "",
             "snippet": r["snippet"] or "", "body_text": r["body_text"] or ""}
            for r in rows]
