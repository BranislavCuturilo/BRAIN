---
name: ops-prune
description: >
  Repairing or removing skills and agents that score badly — diagnosing WHY a
  score is weak before touching anything, and proposing every edit or deletion
  for explicit approval. Never edits or deletes a skill on its own. Load when a
  review flags a weak component, or when asked to clean up the brain.
when_to_use: >
  "this skill is bad", "clean up the skills", a retire/weak band in the score
  review, a skill that keeps getting ignored, two skills that contradict.
---

# Repairing and removing what is not working

## The gate — this is the point of the skill

**Nothing in `skills/` or `agents/` is edited or deleted without the owner
saying yes to that specific change.** Not a blanket yes to a review; one yes per
item.

Present proposals as a table and stop:

| # | File | Change | Why | Evidence |
|---|---|---|---|---|
| 1 | `skills/x/SKILL.md` | delete section "Y" | never fired in 6 months | scored 2 uses, both neutral |
| 2 | `agents/z.md` | rewrite description | never selected | 0 runs in 40 |

Then wait. Apply only the numbered items approved. If the answer is "1 and 3",
2 stays exactly as it is — do not apply it later "for consistency".

**Why the gate exists:** these files are the accumulated judgement of everyone
who has used this system. An agent deleting them is optimising a number against
knowledge it cannot see, and a deleted rule takes its post-mortem with it. The
one thing worse than a rule nobody reads is a rule that was deleted and then
re-learned the expensive way.

## Diagnose before operating — a weak score says nothing about the cause

Four failure modes look identical in the table and need opposite fixes. Getting
this wrong makes things worse, confidently.

**It never triggers.** Zero or near-zero uses. The body may be excellent. This is
a *description* problem — the `description` and `when_to_use` do not match the
words that appear when the situation arises. **Fix the frontmatter, do not touch
the body.** Rewriting good content because nobody found it is the most common
mistake here.

**It triggers but does not help.** Recorded uses, weak ratio. Usually too general
to act on: "handle errors properly" is agreeable and useless. Fix by making it
*checkable* — replace the advice with the concrete failure it prevents, or split
it into the two situations it was straddling.

**It contradicts another skill.** Then one of them is wrong, and the work is
finding out which — not softening both until they agree. Softening produces two
rules nobody can act on. Delete the loser and say so in the survivor.

**Nobody reads to the end.** Long body, and the rules near the bottom never fire
while the ones near the top do. Split into a router plus `references/`, keeping
in the router only what must never be missed.

## When deletion is right

- **The incident behind it can no longer happen** — the API is gone, the pattern
  is unreachable. Say what changed. If what changed is that *Claude Code itself*
  now does it, the answer is **archive, not delete** — see below.
- **Another skill covers it better.** Two copies drift; keep the better-placed
  one (`/brain:brain` routing law) and delete the other outright.
- **It was written from a single occurrence and never recurred.** A rule added
  for one incident is a hypothesis. If nothing has confirmed it in months of
  real work, it is costing context to no end.

**Never delete on silence alone.** A skill nobody triggered may be waiting for a
situation that has not come up; that is an argument for fixing its description,
not for removing it. Retire on a recorded loss, not on absence
(`/brain:ops-scoring`).

## When upstream shipped it: archive, never delete

`scripts/brain/upstream.py` reads the Claude Code changelog and lists what is
new since the last check. It proposes; it does not judge — a keyword match
cannot tell *covered* from *partly covered*, and the interesting case is always
the second one.

**Read the release note against the skill and pick one of three.**

**Fully covered — archive it.**

```bash
python scripts/brain/archive.py <skill> --version 2.1.261 --because "…"
```

Deleting would be wrong here even though the skill is now redundant, and the
reason is not sentiment: **you are not the only agent reading this repository.**
Codex and Gemini gained nothing when Anthropic shipped a feature, so a rule
deleted because Claude Code no longer needs it is a rule those agents will
re-derive the expensive way. `archive/` sits outside `skills/`, so Claude Code
never loads it — no description in context, no clutter — while staying plain
readable markdown for anything else pointed at the directory.

**Partly covered — redesign, and this is the common case.** Narrow the skill to
what is still uncovered rather than leaving it whole. Two rules that overlap by
half are worse than either alone: the native behaviour and the skill both look
authoritative, and the session follows whichever it read last. Say in the body
which version took over which part.

The worked example is `/skill-doctor` in 2.1.261 — it reports unused loaded
skills and their context cost, which is part of `usage.py`, part of `budget.py`
and part of the score review, and the whole of none of them.

**Unrelated — say so and move on.** Recording "checked, unrelated" is what stops
the same release being re-litigated next month.

Then `python scripts/brain/upstream.py --seen`, which is what makes the next run
show only what is genuinely new.

**Archiving is a proposal like any other here.** It goes in the numbered table
and waits for a yes. The gate at the top of this skill does not have an
exception for "upstream made it obsolete" — that judgement is exactly the kind
that looks certain and is sometimes wrong, because coverage narrows as often as
it widens.

## What survives a deletion

The score history. `score.py` deliberately keeps entries for removed components,
so "we tried this and it did not work" is still answerable next year. Do not
clean the registry when you delete the file.

If the deleted skill contained a post-mortem that is still true, **move it**
before deleting — into the skill that absorbs the responsibility. A deletion
that loses evidence is a net loss even when the file deserved to go.

## Below five uses, propose nothing structural

`MIN_USES` exists because three uses is not evidence. Under it, the only
defensible proposals are description fixes and merges of obvious duplicates.
Anything else is rewriting rules from noise — the same defect this system keeps
finding elsewhere, applied to itself.

## A deferral names the observation that would change it

**Borrowed, not yet earned.** From `one-skill-to-rule-them-all` (Eoghan Henn,
CC BY 4.0), SKILL.md:599. Review by 2026-12-09; nothing here has measured it.
It dies if a cycle records a named trigger that never fires and changes nothing
— that would make the naming ceremony.

The claim: **"leave it for now" without a named trigger is a refusal presenting
itself as a deferral.** Nobody reopens it, because there is nothing to notice.

So when the answer for a candidate is "not yet", the observation that would
change the answer goes on the same line — *"leave it; revisit if it is still
under `MIN_USES` at the next review"*, never a bare *"leave it for now"*.

Same shape as `dies_when` in `findings.py`, which records what would kill a
finding on the day it is written. That one is implemented and is the owner of
the idea; this is it pointed at a review outcome instead of a finding.

## Order of work

1. `score.py review` — the candidates and their bands.
2. `dashboard.py` — cost next to usage, so a cheap unused skill is not treated
   like an expensive one.
3. For each candidate, diagnose which of the four modes it is. Read the file;
   the band does not tell you.
4. `brain-keeper` for the structural view — duplication, contradictions, stale
   cited coordinates — as *input*, not as authority.
5. Produce the table. Stop. Wait for the numbered answer.
6. Apply what was approved, one commit, listing what was approved and by whom.
7. Re-run `health.py`.

## Related

- `/brain:ops-scoring` — where the bands come from and what they mean.
- `/brain:ops-maintain` — the periodic cycle this fits into; the practice review
  runs first, because real work outranks internal tidiness.
- `/brain:ops-postmortem` — when the trigger is a bug report rather than a score.
