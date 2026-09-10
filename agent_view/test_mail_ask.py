#!/usr/bin/env python3
"""Offline tests for the Mail RAG side-chat: mail_ai.ask + mailcache.search_account.

ask() is exercised with a FAKE cache index (candidates: subject/snippet/sender,
NO body), a FAKE mailstore whose message() returns the FULL bodies on demand, and
a FAKE gemini that echoes its prompt — injected via the same seams server.py uses
(mail_ai._mailcache / mail_ai._mailstore / mail_ai._gemini). This proves the
two-stage retrieval: the index finds candidates, then full bodies are fetched so
Gemini sees real content (the credential lives ONLY in a fetched body). Trash is
excluded. search_account is exercised against a throwaway SQLite DB. No network,
no credentials, no quota. Run:  python test_mail_ask.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_TMP = Path(tempfile.mkdtemp(prefix="mailask_"))
os.environ["AGENT_VIEW_MAIL_CACHE"] = str(_TMP / "mail_cache.sqlite")
os.environ["AGENT_VIEW_MAIL_PROFILES"] = str(_TMP / "mail_profiles")

import mail_ai      # noqa: E402
import mailcache    # noqa: E402


# --------------------------------------------------------------------------- #
#  Fakes — a Gemini that ECHOES its prompt (so the prompt can be asserted from
#  both the recorded call and the returned answer), a cache INDEX of candidate
#  summaries (no bodies), and a mailstore whose message() serves full bodies.
# --------------------------------------------------------------------------- #
class FakeGeminiEcho:
    def __init__(self):
        self.calls = []          # (prompt, json_out, api_key)

    def call(self, prompt, *, json_out=True, temperature=0.2, api_key=""):
        self.calls.append((prompt, json_out, api_key))
        return prompt            # echo the prompt back as the "answer"


class FakeCache:
    """Mimics mailcache.search_account's contract: returns candidate summaries and
    EXCLUDES Trash/Junk/Spam folders (reusing the one shared decision)."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = []        # (acct, terms, limit)

    def search_account(self, acct, terms, limit):
        self.queries.append((acct, list(terms), limit))
        kept = [dict(r) for r in self.rows
                if not mailcache.is_excluded_folder(r.get("folder"))]
        return kept[:limit]


class FakeMailstore:
    """message() returns the FULL body for a (folder, uid) on demand and records
    which were fetched — proving ask() reaches past the cached snippet."""

    def __init__(self, bodies):
        self.bodies = bodies
        self.fetched = []        # (folder, uid)

    def message(self, acct, folder, uid):
        self.fetched.append((folder, str(uid)))
        return dict(self.bodies[(folder, str(uid))])


# Candidates as the cache INDEX holds them: subject/snippet/sender only, NO body.
# A Trash row carries a matching term too — it must be excluded, not answered.
CANDIDATE_ROWS = [
    {"folder": "INBOX", "uid": "42", "from_name": "Fasper Hosting",
     "from_email": "support@fasper.example", "subject": "FTP pristup",
     "date": "", "snippet": "kredencijali za FTP", "body_text": ""},
    {"folder": "Sent", "uid": "7", "from_name": "Bane",
     "from_email": "owner@example.com", "subject": "Re: FTP pristup",
     "date": "", "snippet": "hvala na FTP", "body_text": ""},
    {"folder": "Trash", "uid": "99", "from_name": "Fasper",
     "from_email": "old@fasper.example", "subject": "Stari FTP nalog",
     "date": "", "snippet": "obrisan fasper ftp", "body_text": ""},
]

# The FULL bodies fetched on demand. The credential lives ONLY here (never in a
# candidate snippet), so its presence in the prompt proves a full-body fetch.
BODIES = {
    ("INBOX", "42"): {
        "uid": "42", "from_name": "Fasper Hosting",
        "from_email": "support@fasper.example", "subject": "FTP pristup",
        "date": "", "to": [], "cc": [],
        "body_text": ("Vasi FTP kredencijali: host ftp.fasper.example "
                      "korisnik bane_ftp lozinka Hunter2Secret port 21"),
        "body_html": "", "attachments": []},
    ("Sent", "7"): {
        "uid": "7", "from_name": "Bane", "from_email": "owner@example.com",
        "subject": "Re: FTP pristup", "date": "", "to": [], "cc": [],
        "body_text": "Hvala, potvrdjujem prijem FTP kredencijala za hosting.",
        "body_html": "", "attachments": []},
}


def _install(rows=None, bodies=None):
    gem = FakeGeminiEcho()
    cache = FakeCache(CANDIDATE_ROWS if rows is None else rows)
    ms = FakeMailstore(BODIES if bodies is None else bodies)
    mail_ai._gemini = lambda: gem
    mail_ai._mailcache = lambda: cache
    mail_ai._mailstore = lambda: ms
    return gem, cache, ms


# --------------------------------------------------------------------------- #
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
def test_keywords():
    kw = mail_ai._keywords("pronađi mi kredencijale za fasper FTP")
    check("keywords: drops stopwords mi/za", "mi" not in kw and "za" not in kw, str(kw))
    check("keywords: drops the search verb 'pronađi'", "pronađi" not in kw, str(kw))
    check("keywords: keeps content terms",
          {"fasper", "ftp", "kredencijale"} <= set(kw), str(kw))


def test_keywords_all_stopwords_falls_back_to_raw():
    # A question that reduces to nothing distinctive must still yield search terms
    # (the raw tokens), rather than silently retrieving nothing.
    kw = mail_ai._keywords("mi za i u na")
    check("keywords(fallback): non-empty raw terms when nothing distinctive",
          bool(kw), str(kw))
    check("keywords(fallback): raw terms are the de-duped tokens",
          set(kw) <= {"mi", "za", "i", "u", "na"} and len(kw) == len(set(kw)), str(kw))


def test_ask_fetches_full_bodies_and_excludes_trash():
    gem, cache, ms = _install()
    out = mail_ai.ask("acct", "pronađi mi kredencijale za fasper FTP")

    check("ask: exactly one Gemini call", len(gem.calls) == 1, str(len(gem.calls)))
    prompt = gem.calls[0][0] if gem.calls else ""
    check("ask: non-JSON (text) call", bool(gem.calls) and gem.calls[0][1] is False)
    check("ask: UNPINNED (empty api_key -> key rotation)",
          bool(gem.calls) and gem.calls[0][2] == "")
    check("ask: prompt carries the untrusted-data guard", "UNTRUSTED DATA" in prompt)

    # The credential lives ONLY in the fetched full body — its presence proves the
    # body was fetched on demand, not read from the 140-char snippet.
    check("ask: FULL body fetched — credential present in the prompt",
          "Hunter2Secret" in prompt, prompt[:200])
    check("ask: full bodies fetched for BOTH non-trash candidates",
          ("INBOX", "42") in ms.fetched and ("Sent", "7") in ms.fetched, str(ms.fetched))
    check("ask: Trash candidate body NEVER fetched",
          ("Trash", "99") not in ms.fetched, str(ms.fetched))

    src = out.get("sources") or []
    check("ask: sources mirror the non-trash candidates (count 2)", len(src) == 2, str(src))
    check("ask: Trash excluded from sources",
          all(s.get("folder") != "Trash" and s.get("uid") != "99" for s in src), str(src))
    check("ask: source shape is folder/uid/subject/from",
          bool(src) and set(src[0]) == {"folder", "uid", "subject", "from"},
          str(src[0] if src else None))
    check("ask: first source is the INBOX FTP mail",
          bool(src) and src[0]["uid"] == "42"
          and src[0]["from"] == "support@fasper.example"
          and src[0]["folder"] == "INBOX", str(src[0] if src else None))
    check("ask: answer returned (echoed prompt carries the guard)",
          "UNTRUSTED DATA" in (out.get("answer") or ""))
    check("ask: cache searched with the extracted keywords (stopwords dropped)",
          bool(cache.queries) and "fasper" in cache.queries[0][1]
          and "mi" not in cache.queries[0][1], str(cache.queries))
    check("ask: retrieval capped at RAG_CANDIDATES",
          bool(cache.queries) and cache.queries[0][2] == mail_ai.RAG_CANDIDATES,
          str(cache.queries))


def test_ask_body_truncation_and_total_cap():
    # A body longer than RAG_BODY_CHARS is truncated per-message; the total across
    # candidates never exceeds RAG_TOTAL_BODY_CHARS.
    big = "A" * (mail_ai.RAG_BODY_CHARS + 5000)
    rows = [{"folder": "INBOX", "uid": str(i), "from_name": "X",
             "from_email": "x@y.z", "subject": f"s{i}", "date": "",
             "snippet": "", "body_text": ""} for i in range(1, 6)]
    bodies = {("INBOX", str(i)): {"uid": str(i), "from_name": "X",
              "from_email": "x@y.z", "subject": f"s{i}", "date": "", "to": [],
              "cc": [], "body_text": big, "body_html": "", "attachments": []}
              for i in range(1, 6)}
    gem, cache, ms = _install(rows=rows, bodies=bodies)
    out = mail_ai.ask("acct", "aaaa content term")
    prompt = gem.calls[0][0] if gem.calls else ""
    # The prompt has framing text too, so bound generously: total body bytes must
    # not exceed the cap. Count the run of 'A's.
    a_run = prompt.count("A")
    check("ask(cap): total body content within RAG_TOTAL_BODY_CHARS",
          a_run <= mail_ai.RAG_TOTAL_BODY_CHARS, str(a_run))
    check("ask(cap): still exactly one Gemini call", len(gem.calls) == 1)


def test_ask_no_hits_still_answers_safely():
    gem, cache, ms = _install(rows=[])
    out = mail_ai.ask("acct", "gde su fasper kredencijali")
    check("ask(no hits): still one call", len(gem.calls) == 1, str(len(gem.calls)))
    check("ask(no hits): empty sources", out.get("sources") == [], str(out.get("sources")))
    check("ask(no hits): no body fetch attempted", ms.fetched == [], str(ms.fetched))
    check("ask(no hits): guard still present in the prompt",
          bool(gem.calls) and "UNTRUSTED DATA" in gem.calls[0][0])


def test_ask_all_stopword_question_still_searches():
    # With the raw-term fallback, an all-stopword question no longer retrieves
    # nothing — it issues a cache query on the raw tokens and still answers once.
    gem, cache, ms = _install()
    out = mail_ai.ask("acct", "mi za i u na")
    check("ask(fallback): a cache query IS issued on the raw terms",
          len(cache.queries) == 1 and bool(cache.queries[0][1]), str(cache.queries))
    check("ask(fallback): still exactly one Gemini call", len(gem.calls) == 1)


# --------------------------------------------------------------------------- #
#  search_account against a real (throwaway) SQLite cache
# --------------------------------------------------------------------------- #
def _seed(acct, folder, uid, subject, body, from_email="x@y.z", from_name="X"):
    mailcache.store_summaries(acct, folder, [
        {"uid": uid, "from_name": from_name, "from_email": from_email,
         "subject": subject, "date": "", "seen": False, "has_attach": False,
         "snippet": (body or "")[:60]}])
    if body is not None:
        mailcache.store_full(acct, folder, uid, {
            "from_name": from_name, "from_email": from_email, "subject": subject,
            "date": "", "body_text": body, "body_html": "",
            "to": [], "cc": [], "attachments": []})


def test_search_account_real_sqlite():
    _seed("acme", "INBOX", 42, "FTP pristup",
          "FTP kredencijali: lozinka Hunter2Secret", "support@fasper.example", "Fasper")
    _seed("acme", "Archive", 5, "Nevezano", "nista ovde", "neko@drugo.rs", "Neko")
    # a matching mail sitting in Trash must NOT be retrieved (deleted != answer)
    _seed("acme", "Trash", 70, "FTP u kanti", "fasper ftp tajna", "old@fasper.example", "Old")
    # a DIFFERENT account with a matching mail must NOT leak into results
    _seed("other", "INBOX", 99, "FTP drugi tenant", "fasper ftp tajna",
          "support@fasper.example", "Fasper")

    hits = mailcache.search_account("acme", ["fasper", "ftp"], 8)
    uids = {h["uid"] for h in hits}
    check("search_account: finds the FTP mail", "42" in uids, str(uids))
    check("search_account: excludes unrelated mail", "5" not in uids, str(uids))
    check("search_account: excludes Trash/Junk/Spam folders", "70" not in uids, str(uids))
    check("search_account: account-scoped (no cross-account leak)",
          "99" not in uids, str(uids))
    check("search_account: body_text available for the prompt",
          any("Hunter2Secret" in (h.get("body_text") or "") for h in hits))
    check("search_account: empty terms -> []", mailcache.search_account("acme", [], 6) == [])


def test_is_excluded_folder():
    check("excluded: [Gmail]/Trash", mailcache.is_excluded_folder("[Gmail]/Trash"))
    check("excluded: INBOX.Junk", mailcache.is_excluded_folder("INBOX.Junk"))
    check("excluded: Deleted Items", mailcache.is_excluded_folder("Deleted Items"))
    check("excluded: Spam", mailcache.is_excluded_folder("Spam"))
    check("excluded: INBOX is kept", not mailcache.is_excluded_folder("INBOX"))
    check("excluded: Sent is kept", not mailcache.is_excluded_folder("Sent"))


def test_search_account_like_escape():
    # a term with a SQL wildcard must match literally, not act as a wildcard
    _seed("esc", "INBOX", 1, "100% bonus", "b", "a@a", "A")
    _seed("esc", "INBOX", 2, "plain subject", "b", "b@b", "B")
    hits = mailcache.search_account("esc", ["100%"], 6)
    uids = {h["uid"] for h in hits}
    check("search_account: % is matched literally (LIKE escaped)", uids == {"1"}, str(uids))


def main():
    for fn in (test_keywords, test_keywords_all_stopwords_falls_back_to_raw,
               test_ask_fetches_full_bodies_and_excludes_trash,
               test_ask_body_truncation_and_total_cap,
               test_ask_no_hits_still_answers_safely,
               test_ask_all_stopword_question_still_searches,
               test_search_account_real_sqlite, test_is_excluded_folder,
               test_search_account_like_escape):
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
