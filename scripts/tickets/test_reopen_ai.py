#!/usr/bin/env python3
"""Offline tests for reopen_ai.py — the dopuna-focused Gemini predlog +
Claude adjudication prompts. No network: gemini_client.call is monkeypatched
in-process (never a subprocess, so nothing crosses a process boundary and no
credential scrubbing is needed here). A temp dir stands in for the ticket
store, written directly via store.atomic_write_json (same shape test_sync.py
uses). Run: python test_reopen_ai.py"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gemini_client  # noqa: E402  monkeypatched .call, never a real key
import reopen_ai       # noqa: E402  module under test
import rounds           # noqa: E402  the reopen ledger owner
import store             # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


# --------------------------------------------------------------------------- #
#  Sentinels — the exact strings a passing test must find (dopuna, round-1
#  resolution, title) and must NEVER find (original.description == the
#  initial ticket body, never sent to either prompt).
# --------------------------------------------------------------------------- #
TITLE_SENTINEL = "Kasa ne startuje TITLE_SENTINEL"
ROUND1_RESOLUTION_SENTINEL = "Restartovan servis ROUND1_RESOLUTION_SENTINEL"
DOPUNA_SENTINEL = "Opet se javlja greska DOPUNA_SENTINEL"
DESCRIPTION_SENTINEL = "ORIGINAL_DESCRIPTION_SENTINEL nikad ne sme da procuri"


def _open_reopen_ticket(tid_field=None):
    """A ticket in an OPEN reopen round, shaped as `rounds.open_round` would
    leave it, WITH the dopuna comment present in `comments` (test_sync's own
    `_reopened_ticket` fixture omits `comments`, which is fine for the sync
    tests but not for reopen_ai — it reads the dopuna text FROM `comments`)."""
    t = {
        "title": TITLE_SENTINEL,
        "original": {"title": TITLE_SENTINEL, "description": DESCRIPTION_SENTINEL},
        "status": "active",
        "triage": {"state": "", "resolution": ""},
        "helpdesk": {"is_closed": False},
        "comments": [
            {"id": 500, "author": "Operator", "author_role": "agent",
             "at": "2026-08-09T10:00:00", "body": "poruka pre zatvaranja, nije dopuna"},
            {"id": 501, "author": "Klijent", "author_role": "customer",
             "at": "2026-08-11T09:00:00", "body": DOPUNA_SENTINEL, "attachments": []},
        ],
        "rounds": [
            {"closed_at": "2026-08-10T08:00:00", "resolution": ROUND1_RESOLUTION_SENTINEL,
             "closed_by": "operator"},
            {"reopened_at": "2026-08-11T09:00:00",
             "trigger": {"comment_id": 501, "author": "Klijent", "author_role": "customer"},
             "branch": None, "resolution": None, "closed_at": None},
        ],
        "reopened": {"at": "2026-08-11T09:00:00", "by_role": "customer",
                     "trigger_comment_id": 501},
        "reopen": {},
    }
    if tid_field is not None:
        t["id"] = tid_field
    return t


def _plain_ticket():
    """An ordinary active ticket — never in a reopen round at all."""
    return {"title": "Obican tiket", "original": {"title": "Obican tiket"},
            "status": "active", "triage": {"state": "", "resolution": ""},
            "helpdesk": {"is_closed": False}, "comments": []}


def _closed_round_ticket():
    """A reopen round that has ALREADY been re-closed this round
    (`rounds[-1].closed_at` set, exactly as `close_current_round` leaves it —
    the `reopened` marker is NOT cleared by a close, so this fixture keeps it
    too) — `is_reopened_open` is False purely on the `closed_at` check, so
    run() must skip it exactly like a plain ticket."""
    t = _open_reopen_ticket()
    t["rounds"][-1]["closed_at"] = "2026-08-12T10:00:00"
    t["rounds"][-1]["branch"] = "B"
    return t


def _write_store(root, tickets, module="INV"):
    d = {"project": {"name": module, "repo": "inv", "helpdesk_module": module},
         "statuses": {}, "tickets": tickets, "rev": 0}
    (Path(root) / f"{module}.json").write_text(json.dumps(d), encoding="utf-8")
    return d


def _read(root, module="INV"):
    return json.loads((Path(root) / f"{module}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
#  predlog_prompt
# --------------------------------------------------------------------------- #
def test_predlog_prompt_scope():
    t = _open_reopen_ticket()
    p = reopen_ai.predlog_prompt(t)
    ck("predlog_prompt: contains the dopuna comment body", DOPUNA_SENTINEL in p)
    ck("predlog_prompt: contains round-1's resolution", ROUND1_RESOLUTION_SENTINEL in p)
    ck("predlog_prompt: contains the title", TITLE_SENTINEL in p)
    ck("predlog_prompt: does NOT contain original.description (absence twin)",
       DESCRIPTION_SENTINEL not in p)
    ck("predlog_prompt: presence twin still true on the same object (not a wrong-page pass)",
       DOPUNA_SENTINEL in reopen_ai.predlog_prompt(t))


# --------------------------------------------------------------------------- #
#  adjudication_prompt
# --------------------------------------------------------------------------- #
def test_adjudication_prompt_scope_and_branches():
    t = _open_reopen_ticket()
    p = reopen_ai.adjudication_prompt(t, "INV", repo="inv")
    ck("adjudication_prompt: branch A defined", "A = korisnik nije na" in p)
    ck("adjudication_prompt: branch B defined", "B = stvarna dopuna" in p)
    ck("adjudication_prompt: branch C defined", "C = prijavljuje da i dalje" in p)
    ck("adjudication_prompt: ask-when-unsure instruction present", "AskUserQuestion" in p)
    ck("adjudication_prompt: does NOT contain original.description", DESCRIPTION_SENTINEL not in p)
    ck("adjudication_prompt: contains round-1 resolution too", ROUND1_RESOLUTION_SENTINEL in p)
    ck("adjudication_prompt: contains the dopuna body", DOPUNA_SENTINEL in p)


def test_adjudication_prompt_verdict_command_stamped_id():
    """Phase-4 contract: the endpoint must stamp `t['id'] = tid` before calling
    adjudication_prompt. A stamped id renders the EXACT verdict command with
    that id; an UNSTAMPED ticket renders the visible placeholder, never a
    wrong/blank id silently swallowed into the command."""
    stamped = _open_reopen_ticket(tid_field="4711")
    p_stamped = reopen_ai.adjudication_prompt(stamped, "INV")
    expected = "python ~/.claude/skills/brain/scripts/tickets/rounds.py verdict --module INV --id 4711"
    ck("adjudication_prompt: exact verdict CLI command present (stamped id)", expected in p_stamped)
    ck("adjudication_prompt: no placeholder leaks when id IS stamped",
       "<ID_TIKETA>" not in p_stamped)

    unstamped = _open_reopen_ticket()  # no "id" key at all
    p_unstamped = reopen_ai.adjudication_prompt(unstamped, "INV")
    ck("adjudication_prompt: unstamped ticket -> visible placeholder, not a wrong id",
       "--id <ID_TIKETA>" in p_unstamped)
    ck("adjudication_prompt: placeholder command uses the module too",
       "--module INV --id <ID_TIKETA>" in p_unstamped)


def test_adjudication_prompt_carries_the_job_through_to_the_customer():
    """The prompt used to STOP at the verdict — it classified A/B/C, wrote the
    verdict back, and said nothing about delivering anything. The operator's ask
    (2026-08-28): one prompt that does the triage, CHECKS what the customer
    reported, and then either comments without closing or does the work and
    closes."""
    t = _open_reopen_ticket(tid_field="4711")
    p = reopen_ai.adjudication_prompt(t, "INV", repo="inv")
    ck("deliver: the prompt says the job is not done at the verdict",
       "Posao nije gotov presudom" in p)
    ck("deliver: it is told to CHECK the report, not take it on trust",
       "PROVERI ŠTA JE KUPAC PRIJAVIO" in p and "reprodukuje" in p)
    ck("deliver: comment-only names the exact command, WITHOUT --close",
       "writeback.py --module INV --id 4711 --post-outbox" in p)
    ck("deliver: the closing shape is the other exact command",
       "--module INV --id 4711 --close --post-outbox" in p)
    ck("deliver: cannot-reproduce is comment + questions and explicitly no close",
       "NE MOŽEŠ da reprodukuješ" in p and "NE ZATVARAJ tiket" in p)
    ck("deliver: the writeback path is absolute — the cwd is the target repo",
       "python ~/.claude/skills/brain/scripts/tickets/writeback.py" in p)
    ck("deliver: an ambiguous send is never retried blindly", "ambiguous" in p)
    ck("deliver: and the customer-facing text is still the customer's language, "
       "never a changelog", "commit" in p and "report" in p)


def test_nothing_reaches_the_customer_without_the_operator_saying_so():
    """A posted helpdesk comment cannot be edited or withdrawn, and two real ones
    landed on a live ticket in one hour on 2026-08-20 because something sent
    without asking. The rule has to be IN the prompt, at the top — not implied by
    a skill the session may or may not load."""
    p = reopen_ai.adjudication_prompt(_open_reopen_ticket(tid_field="4711"), "INV")
    ck("confirm: the rule is stated", "NIŠTA NE ODLAZI KUPCU BEZ MOJE POTVRDE" in p)
    ck("confirm: it names the mechanism and the exact text",
       "AskUserQuestion" in p and "TAČAN tekst" in p)
    ck("confirm: and it is at the TOP, before any command it could be skimmed past",
       "NIŠTA NE ODLAZI KUPCU" in p[:1400])
    ck("confirm: a refusal leaves the ticket alone", "ne šalješ ništa" in p)


def test_a_branch_the_operator_already_chose_is_obeyed_not_re_derived():
    """The operator marks the branch in the lane and THEN presses Klasifikuj
    ("oznacim da b resi … kada kliknem klasifikuj"). Re-classifying would throw
    that decision away. An advisory Gemini guess is NOT a decision."""
    undecided = _open_reopen_ticket(tid_field="4711")
    p0 = reopen_ai.adjudication_prompt(undecided, "INV")
    ck("branch: with nothing decided, it still classifies",
       "GRANA — klasifikuj u ta" in p0 and "GRANA JE VEĆ ODLUČENA" not in p0)

    decided = _open_reopen_ticket(tid_field="4711")
    decided["rounds"][-1]["branch"] = "B"
    p1 = reopen_ai.adjudication_prompt(decided, "INV")
    ck("branch: a decided branch is stated as decided", "GRANA JE VEĆ ODLUČENA: **B**" in p1)
    ck("branch: and the classify step becomes a confirm step",
       "GRANA — potvrdi granu B" in p1 and "GRANA — klasifikuj" not in p1)
    ck("branch: it must STOP rather than switch branches on its own",
       "nikad je ne menjaj sam" in p1)

    guessed = _open_reopen_ticket(tid_field="4711")
    guessed["reopen"] = {"suggested_branch": "A"}
    p2 = reopen_ai.adjudication_prompt(guessed, "INV")
    ck("branch: Gemini's suggested_branch does NOT count as decided",
       "GRANA JE VEĆ ODLUČENA" not in p2 and "GRANA — klasifikuj" in p2)

    mirrored = _open_reopen_ticket(tid_field="4711")
    mirrored["reopen"] = {"branch": "c"}
    ck("branch: the working block's branch counts too, case-insensitively",
       "GRANA JE VEĆ ODLUČENA: **C**" in reopen_ai.adjudication_prompt(mirrored, "INV"))


# --------------------------------------------------------------------------- #
#  run() — the Gemini predlog sweep
# --------------------------------------------------------------------------- #
class _FakeGemini:
    """Records every prompt it was called with; returns whatever `results`
    (a list, consumed in order) says, or a fixed dict when `results` is None."""
    def __init__(self, results=None, fixed=None):
        self.calls = []
        self._results = list(results) if results is not None else None
        self._fixed = fixed

    def __call__(self, prompt, **kw):
        self.calls.append(prompt)
        if self._results is not None:
            return self._results.pop(0)
        return self._fixed


def _patched(fake):
    """Swap gemini_client.call for `fake` and return the restorer. reopen_ai's
    `_gemini` does a fresh `import gemini_client` per call, but that import
    hits the SAME cached module object in sys.modules, so patching the
    attribute here is visible to reopen_ai without touching reopen_ai itself."""
    orig = gemini_client.call
    gemini_client.call = fake
    return orig


def test_run_selects_only_open_round_without_predlog():
    with tempfile.TemporaryDirectory() as root:
        tickets = {
            "1": _open_reopen_ticket(),      # eligible
            "2": _plain_ticket(),             # never reopened -> skipped
            "3": _closed_round_ticket(),      # round already re-closed -> skipped
        }
        _write_store(root, tickets)
        fake = _FakeGemini(fixed={"predlog": "Predlog tekst.", "suggested_branch": "B",
                                  "confidence": "high", "suggested_reply": "Hvala."})
        orig = _patched(fake)
        try:
            done, failed = reopen_ai.run(root, "INV")
        finally:
            gemini_client.call = orig
        ck("run: exactly one ticket processed (open-round, no predlog yet)", done == 1 and failed == 0)
        ck("run: exactly one Gemini call made", len(fake.calls) == 1)
        d = _read(root)
        ck("run: predlog written into t['reopen'] for the eligible ticket",
           d["tickets"]["1"]["reopen"].get("predlog") == "Predlog tekst.")
        ck("run: plain ticket untouched (presence twin: still no reopen block content)",
           d["tickets"]["2"].get("reopen") in (None, {}))
        ck("run: closed-round ticket's reopen block untouched by this pass",
           "predlog" not in (d["tickets"]["3"].get("reopen") or {}))
        ck("run: never touches rounds[cur].branch — stays the Gemini-untouched value",
           d["tickets"]["1"]["rounds"][-1]["branch"] is None)
        ck("run: suggested_branch is a SEPARATE, advisory field (not the ledger branch)",
           d["tickets"]["1"]["reopen"].get("suggested_branch") == "B"
           and "branch" not in d["tickets"]["1"]["reopen"])


def test_run_prompt_sent_to_gemini_excludes_description():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _open_reopen_ticket()})
        fake = _FakeGemini(fixed={"predlog": "x"})
        orig = _patched(fake)
        try:
            reopen_ai.run(root, "INV")
        finally:
            gemini_client.call = orig
        ck("run: exactly one prompt captured", len(fake.calls) == 1)
        sent = fake.calls[0]
        ck("run: the prompt actually SENT to Gemini contains the dopuna",
           DOPUNA_SENTINEL in sent)
        ck("run: the prompt actually SENT to Gemini does NOT contain original.description",
           DESCRIPTION_SENTINEL not in sent)


def test_run_idempotent_within_same_round():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _open_reopen_ticket()})
        fake = _FakeGemini(fixed={"predlog": "Prvi predlog."})
        orig = _patched(fake)
        try:
            done1, failed1 = reopen_ai.run(root, "INV")
            ck("run/idempotent: first pass writes one predlog", done1 == 1 and failed1 == 0)
            done2, failed2 = reopen_ai.run(root, "INV")
        finally:
            gemini_client.call = orig
        ck("run/idempotent: re-run within the same round is a no-op (skips)",
           done2 == 0 and failed2 == 0)
        ck("run/idempotent: only ONE Gemini call total across both passes",
           len(fake.calls) == 1)
        d = _read(root)
        ck("run/idempotent: the original predlog is kept, not overwritten",
           d["tickets"]["1"]["reopen"]["predlog"] == "Prvi predlog.")


def test_run_defensive_against_non_dict_gemini_result():
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, {"1": _open_reopen_ticket()})
        fake = _FakeGemini(fixed=["not", "a", "dict"])   # malformed model reply
        orig = _patched(fake)
        try:
            done, failed = reopen_ai.run(root, "INV")
        finally:
            gemini_client.call = orig
        ck("run/defensive: does not crash on a non-dict Gemini result", True)
        ck("run/defensive: counted as failed, not done", done == 0 and failed == 1)
        d = _read(root)
        ck("run/defensive: no predlog was written (presence twin: reopen stays empty)",
           d["tickets"]["1"].get("reopen") == {})
        ck("run/defensive: rounds ledger untouched", d["tickets"]["1"]["rounds"][-1]["branch"] is None)


# --------------------------------------------------------------------------- #
#  Prompt injection hardening — the dopuna (untrusted customer text going into a
#  repo-scoped Claude session) is fenced with a per-call random nonce, so a
#  crafted comment cannot forge a section marker / operator note / verdict command.
# --------------------------------------------------------------------------- #
def _fence_nonce(prompt, kind="UNTRUSTED_DOPUNA"):
    m = re.search(r"<<<%s id=([0-9a-f]+)>>>" % kind, prompt)
    return m.group(1) if m else None


def test_dopuna_is_fenced_with_random_nonce():
    t = _open_reopen_ticket(tid_field="4711")
    for name, build in (("predlog", lambda: reopen_ai.predlog_prompt(t)),
                        ("adjudication", lambda: reopen_ai.adjudication_prompt(t, "INV"))):
        p = build()
        open_n = _fence_nonce(p, "UNTRUSTED_DOPUNA")
        close_n = _fence_nonce(p, "END_UNTRUSTED_DOPUNA")
        ck(f"{name}: dopuna wrapped in an UNTRUSTED_DOPUNA fence", open_n is not None)
        ck(f"{name}: fence has a matching END marker with the SAME nonce",
           close_n is not None and close_n == open_n)
        start = p.find(f"<<<UNTRUSTED_DOPUNA id={open_n}>>>")
        end = p.find(f"<<<END_UNTRUSTED_DOPUNA id={open_n}>>>")
        ck(f"{name}: the dopuna body sits BETWEEN the two fence lines",
           -1 < start < p.find(DOPUNA_SENTINEL) < end)
        ck(f"{name}: the fence instruction names the nonce so the model knows the authoritative fence",
           open_n is not None and f"id={open_n}" in p[:start])
    # a fresh nonce per call — a crafted comment cannot guess the fence to close it early
    n1 = _fence_nonce(reopen_ai.adjudication_prompt(t, "INV"))
    n2 = _fence_nonce(reopen_ai.adjudication_prompt(t, "INV"))
    ck("fence nonce is RANDOM per call (two calls differ)", bool(n1) and bool(n2) and n1 != n2)


def main():
    for fn in (test_predlog_prompt_scope,
               test_adjudication_prompt_scope_and_branches,
               test_adjudication_prompt_verdict_command_stamped_id,
               test_adjudication_prompt_carries_the_job_through_to_the_customer,
               test_nothing_reaches_the_customer_without_the_operator_saying_so,
               test_a_branch_the_operator_already_chose_is_obeyed_not_re_derived,
               test_dopuna_is_fenced_with_random_nonce,
               test_run_selects_only_open_round_without_predlog,
               test_run_prompt_sent_to_gemini_excludes_description,
               test_run_idempotent_within_same_round,
               test_run_defensive_against_non_dict_gemini_result):
        fn()
    print(f"\n{len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
