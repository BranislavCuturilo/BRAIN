#!/usr/bin/env python3
"""Offline tests for mail_ai.py — no live mail server, no Gemini network.

mail_ai composes mailstore + gemini_client; here both are replaced with fakes so
every path runs without credentials, a network, or spending quota. The fakes are
injected by overriding mail_ai._mailstore / mail_ai._gemini (the same seam
server.py uses), and the profiles store is redirected to a throwaway dir via the
AGENT_VIEW_MAIL_PROFILES env var. Run:  python test_mail_ai.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_TMP = Path(tempfile.mkdtemp(prefix="mailai_"))
os.environ["AGENT_VIEW_MAIL_PROFILES"] = str(_TMP / "mail_profiles")

import mail_ai      # noqa: E402


# --------------------------------------------------------------------------- #
#  Fakes
# --------------------------------------------------------------------------- #
class FakeGemini:
    """Records every call and returns canned output. json_out True -> dict,
    False -> str, so it mirrors gemini_client.call's contract."""

    def __init__(self):
        self.calls = []          # (prompt, json_out)
        self.files = []          # the `files` list of each call (None when omitted)
        self.reply = {"summary": "They ask about the July invoice.",
                      "draft": "Zdravo,\n\nHvala na poruci...\nPozdrav,\nBane"}

    def call(self, prompt, *, json_out=True, temperature=0.2, api_key="", files=None):
        self.calls.append((prompt, json_out))
        self.files.append(files)
        if not json_out:
            return "This is a short summary of the message."
        if '"draft"' in prompt or "REPLY" in prompt:
            return dict(self.reply)
        # a profile-distillation call
        return {"tone": "warm-professional", "formality": "neutral",
                "language": "Serbian", "greeting": "Zdravo",
                "signoff": "Pozdrav, Bane",
                "style": "Short paragraphs, direct, friendly.",
                "persons": {"ana@example.com": "Uses first name, informal."}}


class FakeMailstore:
    """A canned Sent folder + one inbox message. Enough surface for mail_ai:
    message / messages / folders / sync_folder."""

    def __init__(self):
        self.sync_calls = []
        # Sent messages, newest-first, addressed to different groups so
        # refresh_profiles buckets across more than one.
        self._sent = {
            "10": {"uid": "10", "from_name": "Bane", "from_email": "owner@example.com",
                   "to": [{"name": "Ana", "email": "ana@example.com"}],
                   "cc": [], "subject": "Raspored", "date": "",
                   "body_text": "Zdravo Ana, saljem raspored za ovu nedelju. Pozdrav, Bane",
                   "body_html": "", "attachments": []},
            "9": {"uid": "9", "from_name": "Bane", "from_email": "owner@example.com",
                  "to": [{"name": "Kolega", "email": "kolega@example.com"}],
                  "cc": [], "subject": "Deploy", "date": "",
                  "body_text": "Cao, deploy je prosao. Javi ako treba jos nesto.",
                  "body_html": "", "attachments": []},
            "8": {"uid": "8", "from_name": "Bane", "from_email": "owner@example.com",
                  "to": [{"name": "Prof", "email": "profesor@mef.bg.ac.rs"}],
                  "cc": [], "subject": "Ispit", "date": "",
                  "body_text": "Postovani profesore, molim Vas za termin. S postovanjem, B.",
                  "body_html": "", "attachments": []},
        }
        self._inbox = {
            "3": {"uid": "3", "from_name": "Ana Anic", "from_email": "ana@example.com",
                  "to": [{"name": "Bane", "email": "owner@example.com"}],
                  "cc": [], "subject": "Pitanje o fakturi", "date": "",
                  "body_text": "Zdravo Bane, imam pitanje o julskoj fakturi. Pozdrav, Ana",
                  "body_html": "", "attachments": []},
        }

    def folders(self, acct):
        return [{"name": "INBOX", "path": "INBOX", "unseen": 1},
                {"name": "Sent", "path": "Sent", "unseen": 0}]

    def sync_folder(self, acct, folder, prefetch_bodies=0):
        self.sync_calls.append((folder, prefetch_bodies))

    def messages(self, acct, folder, limit=50, q=None):
        src = self._sent if folder == "Sent" else self._inbox
        rows = []
        for m in src.values():
            if q and q.lower() not in (m["from_email"] + " "
                                       + " ".join(t["email"] for t in m["to"])).lower():
                continue
            rows.append({"uid": m["uid"], "from_name": m["from_name"],
                         "from_email": m["from_email"], "subject": m["subject"],
                         "date": m["date"], "seen": True, "has_attach": False,
                         "snippet": m["body_text"][:140]})
        return rows[:limit]

    def message(self, acct, folder, uid):
        src = self._sent if folder == "Sent" else self._inbox
        return dict(src[str(uid)])

    def attachment(self, acct, folder, uid, part):
        # (filename, raw_bytes, content_type) — only the parts the fake declares.
        src = self._sent if folder == "Sent" else self._inbox
        for att in src[str(uid)].get("attachments", []):
            if str(att.get("part")) == str(part):
                return (att["name"], att["_bytes"], att["_ctype"])
        raise KeyError("attachment not found")


def _install(gem=None, ms=None):
    gem = gem or FakeGemini()
    ms = ms or FakeMailstore()
    mail_ai._gemini = lambda: gem
    mail_ai._mailstore = lambda: ms
    return gem, ms


# --------------------------------------------------------------------------- #
#  Harness
# --------------------------------------------------------------------------- #
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
def test_detect_bulk_sender():
    check("bulk: noreply@ sender", mail_ai.detect_bulk({"from_email": "noreply@shop.com"}))
    check("bulk: newsletter+id@ sender",
          mail_ai.detect_bulk({"from_email": "newsletter+42@list.io"}))
    check("bulk: bounce-1234@ sender",
          mail_ai.detect_bulk({"from_email": "bounce-1234@mail.phplist.example"}))
    check("bulk: a real person is NOT bulk",
          not mail_ai.detect_bulk({"from_email": "ana@example.com"}))
    check("bulk: someone named 'newsy' is NOT over-matched",
          not mail_ai.detect_bulk({"from_email": "newsy@person.com"}))


def test_detect_bulk_headers():
    # A phpList-style newsletter: unsubscribe/list headers make it bulk even from
    # a plausible-looking sender.
    phplist = {"from_email": "info@campaign.example",
               "headers": {"List-Unsubscribe": "<mailto:leave@campaign.example>",
                           "List-Id": "Promo <promo.campaign.example>",
                           "Precedence": "bulk"}}
    check("bulk: phpList List-* headers detected", mail_ai.detect_bulk(phplist))
    check("bulk: Auto-Submitted auto-generated detected",
          mail_ai.detect_bulk({"from_email": "svc@x.com",
                               "headers": {"Auto-Submitted": "auto-generated"}}))
    check("bulk: Auto-Submitted 'no' is NOT bulk",
          not mail_ai.detect_bulk({"from_email": "svc@x.com",
                                   "headers": {"Auto-Submitted": "no"}}))


def test_group_of_defaults():
    cfg = mail_ai._empty_profiles()
    check("group: @acme.me -> acme", mail_ai.group_of("ana@example.com", cfg) == "acme")
    check("group: @acme.co.me -> acme", mail_ai.group_of("x@acme.co.me", cfg) == "acme")
    check("group: @example.com -> acme", mail_ai.group_of("owner@example.com", cfg) == "acme")
    check("group: faculty .ac.rs -> fakultet",
          mail_ai.group_of("p@mef.bg.ac.rs", cfg) == "fakultet")
    check("group: .edu -> fakultet", mail_ai.group_of("s@mit.edu", cfg) == "fakultet")
    check("group: unknown -> other", mail_ai.group_of("joe@gmail.com", cfg) == "other")


def test_group_of_overrides():
    cfg = {"config": {"domain_map": {"gmail.com": "acme",
                                     "friend@proton.me": "personal"},
                      "personal": ["myfriend.rs"]}}
    check("group: domain override wins over default",
          mail_ai.group_of("someone@gmail.com", cfg) == "acme")
    check("group: address override wins",
          mail_ai.group_of("friend@proton.me", cfg) == "personal")
    check("group: personal set by domain",
          mail_ai.group_of("x@myfriend.rs", cfg) == "personal")
    check("group: an invalid group in the map is ignored (falls through)",
          mail_ai.group_of("z@gmail.com",
                           {"config": {"domain_map": {"gmail.com": "nonsense"}}}) == "other")


def test_suggest_reply_builds_prompt_and_returns():
    gem, ms = _install()
    # seed a learned acme style so the prompt must carry it
    prof = mail_ai.load_profiles()
    prof["groups"]["acme"] = {"tone": "warm-professional", "greeting": "Zdravo",
                               "signoff": "Pozdrav, Bane", "language": "Serbian"}
    mail_ai.save_profiles(prof)

    out = mail_ai.suggest_reply("acct", "INBOX", "3")
    check("suggest_reply: exactly one Gemini call", len(gem.calls) == 1, str(len(gem.calls)))
    prompt = gem.calls[0][0] if gem.calls else ""
    check("suggest_reply: JSON-mode call", gem.calls and gem.calls[0][1] is True)
    check("suggest_reply: group resolved to acme", out.get("group") == "acme", str(out.get("group")))
    check("suggest_reply: prompt carries the group style",
          "warm-professional" in prompt and "Zdravo" in prompt)
    check("suggest_reply: prompt carries the incoming body",
          "julskoj fakturi" in prompt)
    check("suggest_reply: draft returned", "Pozdrav" in (out.get("draft") or ""))
    check("suggest_reply: summary returned", bool(out.get("summary")))
    check("suggest_reply: used_person is the correspondent",
          out.get("used_person") == "ana@example.com")
    check("suggest_reply: not flagged bulk", out.get("bulk") is False)


def test_suggest_reply_bulk_no_gemini():
    gem, ms = _install()
    ms._inbox["3"]["from_email"] = "noreply@acme.me"     # make it look automated
    out = mail_ai.suggest_reply("acct", "INBOX", "3")
    check("suggest_reply(bulk): NO Gemini call", len(gem.calls) == 0, str(len(gem.calls)))
    check("suggest_reply(bulk): flagged bulk", out.get("bulk") is True)
    check("suggest_reply(bulk): empty draft", out.get("draft") == "")
    check("suggest_reply(bulk): carries a reason", bool(out.get("reason")))


def test_suggest_reply_addresses_the_real_sender():
    # Bug 1 regression: the draft greeted "Irena" (a name baked into the learned
    # group greeting) instead of the actual sender. The learned greeting is fed in
    # as style, but the prompt must ALSO carry the real sender by name in an
    # explicit 'Replying to' field, plus the never-invent-a-name instruction.
    gem, ms = _install()
    prof = mail_ai.load_profiles()
    prof["groups"]["acme"] = {"tone": "warm-professional",
                               "greeting": "Postovana Irena,",   # wrong name in style
                               "signoff": "Pozdrav, Bane", "language": "Serbian"}
    mail_ai.save_profiles(prof)
    ms._inbox["3"]["from_name"] = "Andjela Petrovic"

    mail_ai.suggest_reply("acct", "INBOX", "3")
    prompt = gem.calls[0][0] if gem.calls else ""
    check("suggest_reply(name): prompt carries the real sender name",
          "Andjela Petrovic" in prompt)
    check("suggest_reply(name): prompt has an explicit 'Replying to' recipient field",
          "Replying to: Andjela Petrovic" in prompt)
    check("suggest_reply(name): prompt tells the model NEVER to invent a name",
          "NEVER invent" in prompt and "only name" in prompt.lower())


def test_suggest_reply_neutral_greeting_when_name_unknown():
    # No display name → the prompt must instruct a neutral, nameless greeting
    # rather than leaving the model to guess one.
    gem, ms = _install()
    ms._inbox["3"]["from_name"] = ""
    mail_ai.suggest_reply("acct", "INBOX", "3")
    prompt = gem.calls[0][0] if gem.calls else ""
    check("suggest_reply(no-name): prompt marks the display name unknown",
          "display name unknown" in prompt)
    check("suggest_reply(no-name): prompt asks for a neutral, nameless greeting",
          "Greet neutrally" in prompt)


def test_suggest_reply_includes_attachment():
    import base64
    # Bug 2 regression: the reply prompt must carry the message's attachment inline
    # (same path as summarize) so the model can answer USING it — e.g. solve the
    # problem in the attached PDF instead of asking the sender to send a solution.
    gem, ms = _install()
    pdf_bytes = b"%PDF-1.4 problem set: solve item 1"
    ms._inbox["3"]["attachments"] = [
        {"name": "zadaci.pdf", "size": len(pdf_bytes), "part": "1",
         "_bytes": pdf_bytes, "_ctype": "application/pdf"},
    ]
    mail_ai.suggest_reply("acct", "INBOX", "3")
    check("suggest_reply(att): still exactly one Gemini call", len(gem.calls) == 1,
          str(len(gem.calls)))
    check("suggest_reply(att): the reply is a JSON-mode call",
          gem.calls and gem.calls[0][1] is True)
    files = gem.files[0] if gem.files else None
    check("suggest_reply(att): the PDF is passed as one inline file part",
          isinstance(files, list) and len(files) == 1, repr(files))
    part = files[0] if isinstance(files, list) and files else {}
    check("suggest_reply(att): inline part carries the pdf mime",
          part.get("mime") == "application/pdf", str(part.get("mime")))
    check("suggest_reply(att): inline part carries the base64 of the raw bytes",
          part.get("data") == base64.b64encode(pdf_bytes).decode("ascii"))
    prompt = gem.calls[0][0] if gem.calls else ""
    check("suggest_reply(att): prompt notes the included attachment", "zadaci.pdf" in prompt)
    check("suggest_reply(att): prompt tells the model to answer using attachments",
          "attachment" in prompt.lower())


def test_suggest_reply_no_attachment_omits_files():
    # Plain email keeps the old behaviour: no `files` on the Gemini call.
    gem, ms = _install()
    mail_ai.suggest_reply("acct", "INBOX", "3")
    check("suggest_reply(plain): files omitted from the call (back-compat)",
          gem.files == [None], repr(gem.files))


def test_summarize():
    gem, ms = _install()
    s = mail_ai.summarize("acct", "INBOX", "3")
    check("summarize: one non-JSON Gemini call",
          len(gem.calls) == 1 and gem.calls[0][1] is False)
    check("summarize: returns a string", isinstance(s, str) and bool(s))
    check("summarize: no attachments -> files omitted (back-compat request body)",
          gem.files == [None])


def test_summarize_includes_supported_attachments():
    import base64
    gem, ms = _install()
    pdf_bytes = b"%PDF-1.4 fake report body"
    # One readable PDF (included), one oversized PDF (skipped by the size cap
    # BEFORE any fetch), one binary .zip (skipped by unsupported type).
    ms._inbox["3"]["attachments"] = [
        {"name": "report.pdf", "size": len(pdf_bytes), "part": "1",
         "_bytes": pdf_bytes, "_ctype": "application/pdf"},
        {"name": "huge.pdf", "size": mail_ai.ATTACH_INLINE_CAP + 1, "part": "2",
         "_bytes": b"", "_ctype": "application/pdf"},
        {"name": "archive.zip", "size": 1234, "part": "3",
         "_bytes": b"PK\x03\x04binary", "_ctype": "application/zip"},
    ]
    s = mail_ai.summarize("acct", "INBOX", "3")
    check("summarize(att): still exactly one Gemini call", len(gem.calls) == 1,
          str(len(gem.calls)))
    files = gem.files[0] if gem.files else None
    check("summarize(att): the PDF is passed as one inline file part",
          isinstance(files, list) and len(files) == 1, repr(files))
    part = files[0] if isinstance(files, list) and files else {}
    check("summarize(att): inline part carries the pdf mime",
          part.get("mime") == "application/pdf", str(part.get("mime")))
    check("summarize(att): inline part carries the base64 of the raw bytes",
          part.get("data") == base64.b64encode(pdf_bytes).decode("ascii"),
          str(part.get("data")))
    prompt = gem.calls[0][0] if gem.calls else ""
    check("summarize(att): prompt notes the included PDF", "report.pdf" in prompt)
    check("summarize(att): prompt notes the skipped oversized/binary files",
          "huge.pdf" in prompt and "archive.zip" in prompt)
    check("summarize(att): the .zip is NEVER an inline part (unsupported)",
          all(p.get("mime") != "application/zip" for p in (files or [])))
    check("summarize(att): returns a string", isinstance(s, str) and bool(s))


def test_refresh_profiles_call_bounded():
    gem, ms = _install()
    out = mail_ai.refresh_profiles("acct", max_samples=20)
    groups_seen = set(out["groups"])
    # Sent mail spans acme (ana@example.com), acme (kolega@example.com) and
    # fakultet (…@mef.bg.ac.rs): three distinct groups → three calls, never more.
    check("refresh: one Gemini call per group (bounded)",
          out["calls"] == len(groups_seen), f"calls={out['calls']} groups={groups_seen}")
    check("refresh: never exceeds the group count",
          out["calls"] <= len(mail_ai.GROUPS), str(out["calls"]))
    check("refresh: reached all three groups",
          groups_seen == {"acme", "acme", "fakultet"}, str(groups_seen))
    check("refresh: warmed Sent in one batched sync",
          any(f == "Sent" for f, _pf in ms.sync_calls))

    saved = mail_ai.load_profiles()
    check("refresh: a group style was persisted",
          bool(saved["groups"].get("acme", {}).get("style")))
    check("refresh: a per-person note was persisted",
          "ana@example.com" in saved["persons"])
    check("refresh: sample count accumulates on the group",
          saved["groups"]["acme"].get("samples", 0) >= 1)


def test_refresh_incremental():
    gem, ms = _install()
    mail_ai.refresh_profiles("acct", max_samples=20)
    first = mail_ai.load_profiles()["groups"]["acme"]["samples"]
    mail_ai.refresh_profiles("acct", max_samples=20)
    second = mail_ai.load_profiles()["groups"]["acme"]["samples"]
    check("refresh: samples accumulate across runs (incremental, not rebuilt)",
          second > first, f"{first} -> {second}")


def main():
    for fn in (test_detect_bulk_sender, test_detect_bulk_headers,
               test_group_of_defaults, test_group_of_overrides,
               test_suggest_reply_builds_prompt_and_returns,
               test_suggest_reply_bulk_no_gemini,
               test_suggest_reply_addresses_the_real_sender,
               test_suggest_reply_neutral_greeting_when_name_unknown,
               test_suggest_reply_includes_attachment,
               test_suggest_reply_no_attachment_omits_files, test_summarize,
               test_summarize_includes_supported_attachments,
               test_refresh_profiles_call_bounded, test_refresh_incremental):
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
