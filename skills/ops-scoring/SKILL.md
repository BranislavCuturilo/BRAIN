---
name: ops-scoring
description: >
  Scoring skills and agents by recorded outcomes, and acting on the scores —
  which to trust, which to improve, which to retire, which to stop preloading.
  Load when a skill or agent was clearly useful or clearly got in the way, and
  when reviewing the brain periodically.
when_to_use: >
  "that skill was wrong", "that agent wasted a run", after a delegated task went
  well or badly, a periodic brain review, deciding what an agent should preload.
---

# Scoring

## What this is not

**Nothing measures this automatically.** Claude Code has no telemetry for "did
this skill help". Every number comes from an outcome someone recorded, so the
data is exactly as good as the discipline behind it.

**And a score cannot change behaviour at runtime.** An agent's `skills:` list is
static frontmatter, read at launch. The score does not select anything — it tells
you which frontmatter to edit, and which file to rewrite or delete. Acting on it
is a deliberate change, made and reviewed like any other.

Anyone claiming otherwise is describing a system that does not exist.

## A measurement that can only under-report reads as a finding

Counting is the one place where a bug produces no error, no empty screen and no
missing column — just a smaller number, sitting in a table that looks complete.
Nobody audits a number that renders.

> The usage page reported **0 uses for every skill** and **2 agents for a run
> that spawned 14**. Neither figure was a measurement failure on its face; both
> looked like real, actionable findings — *these skills are dead weight, delete
> them.* Acting on either would have deleted the most-used skills in the system.
>
> Three separate causes, all silent:
> - workflow-spawned agents are recorded in `subagents/workflows/*/agent-*.meta.json`, not as `Task` calls in the transcript, so a counter reading only transcripts sees a fraction;
> - a **preloaded** skill is injected from frontmatter at launch and never calls the `Skill` tool, so its invocation count is structurally 0 no matter how heavily it is used;
> - the frontmatter parser read only the header line of `skills:`, so the block form (`skills:` then `  - name`) yielded an empty list — the same defect that had already made `budget.py` report every agent as preloading nothing.

**Three rules, in order of how often they save you:**

1. **Before trusting a count, name the paths the thing can be reached by, and
   confirm the counter walks each one.** Two of the three causes above were
   whole reachability paths nobody had enumerated, not arithmetic mistakes.
2. **A zero must be able to distinguish "not used" from "not observable".** If a
   category *cannot* appear in the data, the column is not measuring it — label
   it, or count a proxy and say that is what it is. Never let a structural blind
   spot print the same glyph as a genuine zero.
3. **Sanity-check every counter against one figure you know independently.** The
   run that spawned 14 agents is the test case; a counter that says 2 is not
   "conservative", it is wrong. This is
   [prove the check can fail](../craft-testing/SKILL.md) applied to
   measurement — a counter that has never been checked against a known total has
   not been verified.

And when a parser reads structured configuration, **handle every spelling the
files actually use**, then assert on a known input. An empty parse result is not
a value; it is the absence of one, and it should never travel silently into a
table.

## A number the session was told to produce is not evidence

The section above is about a count that reads too low. This is the opposite
failure and it is harder to see, because the number comes out **high** and
therefore looks like success.

> `../ops-delegation/references/consult-chain.md` has said "on trial" since it
> was written, and it *does* name a kill criterion: three runs that a capable
> executor would have matched alone, and the chain goes. It has never fired.
> Nothing records a run, so the counter never reaches three — and
> `prompt_router.py` nudges toward delegation *before the first tool call*, so
> the figure we do have measures compliance with our own nudge rather than a
> reach for the chain. A threshold with no counter is not a trial; it is
> adoption with a disclaimer.

**Three rules:**

1. **The channel that primes and the channel that measures must be separate.**
   If a session's own text names the behaviour being counted, that session is
   not a sample of it. Discard it, rather than averaging it in.
2. **Record the null case out loud.** "Nobody reached for it this week" is a
   result and belongs in the table. This does not contradict *Do not record
   every use* below: that governs the log of USES, this is the log of the
   TRIAL, and a trial with no entry for "not reached for" cannot ever conclude. Left unrecorded, an idea on trial simply
   never fails, and "on trial" hardens into "adopted" by sitting still — the
   same decay `review_by` exists to stop for borrowed ideas.
3. **Name the end condition before the trial starts** — which observation
   settles it, in which direction, and by when. A trial without one is not an
   experiment, it is a permanent label.

*The framing of trials as needing separated channels came from
`one-skill-to-rule-them-all` (Eoghan Henn, CC BY 4.0); the consult-chain
measurement above is ours and is what earned it a place here.*

## A new agent has to beat the stock one

**Admission rule: a new agent ships only after it has beaten `general-purpose`
on the same input, with the comparison recorded.** Both arms go in the registry
— `score.py record agent general-purpose ...` accepts baselines precisely so the
losing side can be written down.

This exists because the alternative is unfalsifiable. A specialist agent always
*sounds* better than the default; the only way to know is to give the default
the same task and read both answers. Measured twice so far, the stock agent won
both times — once finding a user-visible bug the orchestrated arm missed
entirely, once finding an authorization hole a three-agent consultant chain
walked past.

A roster is not too big because of its token cost — 50 agents of tight
descriptions is a couple of thousand tokens, well inside budget. It is too big
when agents are chosen *badly*, and that only shows up in real work. Cold agents
are not evidence of bloat: they may simply be waiting for a task that has not
come up yet. Do not retire on silence; retire on a recorded loss.

## Recording

One command, at the moment it is obvious:

```bash
python ~/.claude/skills/brain/scripts/brain/score.py record \
    skill craft-code helped "caught the dispatch-key bug before it shipped"
python ~/.claude/skills/brain/scripts/brain/score.py record \
    agent qa hindered "wrote tests against the service, not the reported defect"
```

Outcomes: `helped` · `hindered` · `neutral`.

**Record `hindered` honestly.** A skill that sent the work in the wrong
direction, an agent whose output had to be thrown away, a rule that turned out to
be stale — those are the entries with real information in them. A registry where
everything helped is a registry nobody is reading.

**Always attach the note.** `helped` with no note is a number; `helped` with a
note is evidence you can act on three months later.

**Do not record every use.** Record when the outcome was *clear* in either
direction. Neutral is for "loaded, made no difference" — worth logging
occasionally so a heavily-loaded skill that never changes anything shows up.

## Bands

| Band | Meaning | Do |
|---|---|---|
| `proven` | consistently helps | preload it more widely; trust it in reviews |
| `working` | helps more than not | leave alone |
| `unproven` | under 5 recorded uses | **not a bad score** — nobody has said either way |
| `weak` | used often, rarely decisive | read the notes: usually too vague, too long, or in the wrong layer |
| `retire` | negative over enough uses | delete it, or rewrite from what the notes say went wrong |

`weak` is the interesting band. A weak skill is almost never *wrong* — it is
usually **too general to act on**, or it loads at the wrong moment. Both are
fixed by splitting it into references with sharper triggers (`brain` →
`brain/references/splitting.md`), not by adding more words.

## Acting on the review

```bash
python ~/.claude/skills/brain/scripts/brain/score.py review
```

- **retire** → delete the file. A rule proven unhelpful is worse than a missing
  one; keeping it "just in case" is how the brain fills with noise.
- **weak** → split it, sharpen the description, or move it to the right layer.
- **proven + narrow** → add it to the `skills:` list of agents that would benefit.
- **proven + large** → split it anyway. A big skill everyone preloads is the most
  expensive thing in the system (`scripts/budget.py`).
- **unproven for a long time** → it is not being triggered. The description is
  the problem, not the content.

An agent that keeps scoring `hindered` is **retired, not tuned forever**. Its job
either goes to a narrower agent with a sharper brief, or back to the main loop.

## A faster first look, which answers a smaller question

```
/skill-doctor
```

Claude Code 2.1.261 added it: which loaded skills went unused this session, and
what they cost in context. It is one command against `budget.py` plus
`usage.py`, so use it as the cheap first pass.

**Do not mistake it for this review.** It reports what was *loaded and unused*
right now. It says nothing about agents, nothing across days, and nothing about
whether a skill that DID fire actually helped — which is the only question the
bands above answer. A skill it calls unused may be the one waiting for a
situation that has not come up, and retiring on that is the exact mistake
`/brain:ops-prune` warns about: **retire on a recorded loss, not on absence.**

## Where this fits

`brain-keeper` audits *structure* — duplication, contradictions, stale
references. This scores *usefulness*. A skill can be perfectly structured and
still never help anyone; only the record shows that.

`archivist`'s periodic journal review is the third input: it finds patterns
across sessions that no single outcome shows.
