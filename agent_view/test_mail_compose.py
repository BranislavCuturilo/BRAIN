#!/usr/bin/env python3
"""Offline tests for mail_ai.compose (Feature 3) — no mail server, no Gemini.

compose turns a rough intent into {subject, draft} in the user's voice. Gemini is
faked (records the prompt, returns canned JSON) and the profiles store is a
throwaway dir. Run:  python test_mail_compose.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_TMP = Path(tempfile.mkdtemp(prefix="mailcompose_"))
os.environ["AGENT_VIEW_MAIL_PROFILES"] = str(_TMP / "mail_profiles")

import mail_ai      # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


class FakeGemini:
    """Records every call; returns a canned {subject, draft}."""

    def __init__(self):
        self.calls = []          # (prompt, json_out, api_key)

    def call(self, prompt, *, json_out=True, temperature=0.2, api_key=""):
        self.calls.append((prompt, json_out, api_key))
        return {"subject": "Weekly schedule", "draft": "Zdravo Ana,\n\n...\nPozdrav, Bane"}


def _install():
    gem = FakeGemini()
    mail_ai._gemini = lambda: gem
    return gem


def _seed_acme_style():
    prof = mail_ai.load_profiles()
    prof["groups"]["acme"] = {"tone": "warm-professional", "greeting": "Zdravo",
                               "signoff": "Pozdrav, Bane", "language": "Serbian"}
    mail_ai.save_profiles(prof)


def test_compose_with_known_group_carries_style():
    gem = _install()
    _seed_acme_style()
    out = mail_ai.compose("tell Ana the weekly schedule is ready",
                          to="ana@example.com", acct="acct1")
    check("compose: exactly one Gemini call", len(gem.calls) == 1, str(len(gem.calls)))
    prompt, json_out, api_key = gem.calls[0]
    check("compose: JSON-mode call", json_out is True)
    check("compose: UNPINNED (empty api_key -> rotates)", api_key == "", repr(api_key))
    check("compose: prompt carries the intent", "weekly schedule is ready" in prompt)
    check("compose: prompt carries the group style (learned voice)",
          "warm-professional" in prompt and "Zdravo" in prompt)
    check("compose: prompt names the resolved group 'acme'", "'acme'" in prompt)
    check("compose: group resolved to acme", out.get("group") == "acme", str(out.get("group")))
    check("compose: returns a subject", out.get("subject") == "Weekly schedule")
    check("compose: returns a draft", "Pozdrav" in (out.get("draft") or ""))


def test_compose_without_group_is_neutral():
    gem = _install()
    out = mail_ai.compose("write a short thank-you note")
    check("compose(no to): one call", len(gem.calls) == 1)
    prompt = gem.calls[0][0] if gem.calls else ""
    check("compose(no to): neutral-voice instruction present",
          "neutral, professional voice" in prompt)
    check("compose(no to): no group named", out.get("group") == "", str(out.get("group")))
    check("compose(no to): still returns subject+draft",
          bool(out.get("subject")) and bool(out.get("draft")))


def test_compose_caps_intent_length():
    gem = _install()
    huge = "x" * 5000
    mail_ai.compose(huge)
    prompt = gem.calls[0][0] if gem.calls else ""
    check("compose: intent is capped (~4000) before the prompt",
          ("x" * 4001) not in prompt and ("x" * 3999) in prompt)


def main():
    for fn in (test_compose_with_known_group_carries_style,
               test_compose_without_group_is_neutral,
               test_compose_caps_intent_length):
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
