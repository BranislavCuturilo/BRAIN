---
name: ops-postmortem
description: >
  Turning a reported bug into a fix in the agent or skill that produced it —
  attributing the code, judging whether the fault is the code or the brief, and
  changing the source so the same defect is not written again. Load when a user
  reports something broken, badly implemented, or needing rework.
when_to_use: >
  "users reported", "this doesn't work", "this was implemented badly", a bug
  report on shipped code, a rework request, the second time the same kind of
  defect appears.
---

# From a bug report back to the brain

A bug fixed only in the code will be written again by the same agent, from the
same brief, next week. The report is evidence about the *producer*, not only
about the line.

## The loop

```
python ~/.claude/skills/brain/scripts/brain/blame.py <path>:<line>
```

Returns the commit, and its `Brain-Agents:` / `Brain-Skills:` trailers — who
wrote it and what they were working from. Then:

1. **Fix the code.** Users are waiting, and a fix in a skill ships to nobody.
2. **Judge the source** — the section below.
3. **Change it, or deliberately do not**, and say which.
4. **Record it:** `score.py record agent <name> hindered "<what it produced>"`.
   Recording is what makes the *second* occurrence visible; without it, every
   report looks like the first.

If the commit comes back **UNATTRIBUTED**, the loop stops at step 1 — say so
plainly rather than guessing a culprit, and start attributing from this commit
on (`/brain:craft-git`). Attribution invented after the fact is worse than
none, because it looks authoritative.

## Whose fault is it — the honest test

Read the agent's brief and the skills it preloaded, then ask **one** question:

> Following that brief exactly, would a competent worker have produced this
> defect?

- **Yes** → the fault is in the **brief or the skill**. This is the case worth
  finding, and the fix belongs there. The agent did what it was told.
- **No — the brief covers it and the agent missed it anyway** → this is an
  execution miss, not a rule defect. Record `hindered` and stop. **Do not
  rewrite a rule from one miss.** A rule added for a single incident is how a
  skill becomes a wall of noise nobody reads, and noise is why the *next* rule
  gets skipped.
- **The brief is silent and the rule is genuinely general** → add it, at the
  outermost layer where it stays true (`/brain:brain` routing law).

## Most defects surface late, and the loop has to survive that

A rule is rarely disproven by the run that broke it. It is disproven weeks
later, by somebody clicking through the thing by hand and finding it does not
behave. By then the session that wrote the code is gone, the transcript may be
gone, and nobody remembers which decision produced it.

That delay is the reason for every mechanism in this loop, and it changes what
counts as diligence:

- **Attribution must be written at the time**, because it cannot be
  reconstructed later. A commit trailer costs seconds when the work is fresh and
  is impossible to recover once the transcript is cleaned up.
- **A rule added today may be judged in two months.** Write it so it can be
  judged: name the incident, state the consequence, keep it falsifiable. "Be
  careful with X" cannot be disproven and therefore cannot be removed.
- **Do not read the absence of complaints as evidence.** Recent work has not
  been proven good, only not yet exercised. `/brain:ops-maintain`'s practice
  review exists exactly to look at the window that has now aged enough to have
  produced complaints.
- **When a late report does arrive, resist dating the defect from the report.**
  Ask when the code was written and what else shipped in that same session, then
  check those too. A bad session usually produces more than one bad decision.

## One incident or a pattern

Before changing anything general, look for the second occurrence:

```
python ~/.claude/skills/brain/scripts/brain/blame.py --agent <name>
```

Every file that agent shipped. **The same defect class in three files is a
defect in the agent; one occurrence is an incident.** The distinction decides
whether you edit a brief or just fix the line — and getting it wrong in the
cautious direction is expensive, because every added rule costs context in
every future run.

## Where the fix goes

| The defect would recur… | Fix in |
|---|---|
| in any language, any project | a `craft-*` skill |
| in any project on this technology | a `stack-*` skill |
| only where these two technologies meet | an `arch-seams` reference |
| only in this repo | the project's own skill or `CLAUDE.md` |
| only when this one agent runs | that agent's brief |

An agent brief is the **narrowest** of these, so prefer it when the fault is
about *how this worker approaches the job* rather than *what is true about the
code*. A rule about the code belongs in a skill, where every agent gets it.

## What a good outcome looks like

The change names the report that caused it. A rule with no incident behind it
is someone's preference; a rule that names its incident can be deleted later by
anyone who can show the incident no longer applies.

Write it as the consequence, not the anecdote: *"a by-pk action view authorizes
through the same helper the list view uses"* — with the post-mortem in one
indented line beneath, not three paragraphs.

## Related

- `/brain:capture` — routing a lesson to the right file; this skill is the case
  where the lesson arrived as a **user's bug report** rather than from your own work.
- `/brain:ops-scoring` — the record that turns single reports into a track record.
- `/brain:craft-git` — writing the trailers this whole loop depends on.
