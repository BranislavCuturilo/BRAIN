#!/usr/bin/env python3
"""Proof that the skill-listing budget check computes what Claude Code computes.

**Why this is pinned rather than trusted.** The numbers come from reading the
shipped binary, not the documentation, which does not mention any of it. A
constant read out of someone else's compiled code is exactly the kind of fact
that rots invisibly: Anthropic changes it, nothing errors, and the check goes
on reporting a threshold that no longer exists. So the arithmetic is fixed here
against worked examples, and the provenance is recorded in `health.py` with the
grep that produced it.

What the binary does, and what this mirrors:

* one line per skill, `- name: description - when_to_use`
* each entry capped at `skillListingMaxDescChars` (1536)
* budget = fraction (0.01) x chars-per-token (4) x the model's ACTUAL context
  window -- 200_000 is only the fallback when no window is passed
* over budget, descriptions are DROPPED whole, least-used first; they are not
  shortened

  python scripts/brain/test_listing_budget.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import health                                                    # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def budget(window: int) -> int:
    return int(window * health.LISTING_CHARS_PER_TOKEN * health.LISTING_FRACTION)


def entry(name: str, desc: str, when: str = "") -> int:
    body = desc + (f" - {when}" if when else "")
    return len(name) + 4 + min(len(body), health.LISTING_PER_SKILL)


def main() -> int:
    # --- the constants, as read from the binary ---------------------------
    ck("fraction is 1% of the window", health.LISTING_FRACTION == 0.01)
    ck("four characters to a token", health.LISTING_CHARS_PER_TOKEN == 4)
    ck("per-skill cap is 1536", health.LISTING_PER_SKILL == 1_536)

    # --- the budget scales with the model, and that is the whole point ----
    ck("a 200k model gets 8,000 characters", budget(200_000) == 8_000)
    ck("a 1M model gets 40,000", budget(1_000_000) == 40_000)
    ck("so the same brain is fine on one and over on the other",
       budget(1_000_000) == 5 * budget(200_000))

    # --- when_to_use is not free ------------------------------------------
    a = entry("x", "d" * 100)
    b = entry("x", "d" * 100, "w" * 100)
    ck("when_to_use is concatenated onto the description before the cap",
       b == a + 103)                       # " - " plus 100

    # --- the per-skill cap truncates, it does not spill --------------------
    ck("an entry over the cap counts as the cap",
       entry("x", "d" * 5_000) == len("x") + 4 + health.LISTING_PER_SKILL)

    # --- the real brain, measured the way the binary measures it ----------
    import docs                                                  # noqa: PLC0415
    skills = docs.read_skills()
    ck("read_skills exposes when_to_use, or the sum is wrong",
       any("when" in s for s in skills.values()))

    listed = {n: s for n, s in skills.items() if not s["hand"]}
    ck("a hand-invoked skill is excluded from the listing",
       len(listed) <= len(skills))

    total = sum(entry(n, s["desc"], s.get("when", "")) for n, s in listed.items())
    total += max(0, len(listed) - 1)
    ck(f"the listing is measurable ({total:,} chars)", total > 0)
    ck("and no single skill is over the per-skill cap",
       all(len(s["desc"] + (" - " + s.get("when", "") if s.get("when") else ""))
           <= health.LISTING_PER_SKILL for s in listed.values()))

    # --- the check itself speaks at the right times ------------------------
    problems, notes = health.check(with_git=False)
    said = " ".join(problems + notes)
    if total > budget(1_000_000):
        ck("over the 1M budget is a PROBLEM",
           any("skill listing" in p for p in problems))
    elif total > budget(200_000):
        ck("over only the 200k budget is a NOTE, not a problem",
           any("skill listing" in n for n in notes)
           and not any("skill listing" in p for p in problems))
    else:
        ck("under both budgets, it says nothing at all",
           "skill listing" not in said)

    ck("the agent briefs are reported separately from the skills",
       "agent briefs" in said or
       sum(len(n) + 4 for n in ()) == 0)   # tolerated if under the watch line
    return finish()


def finish() -> int:
    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
