---
name: ops-delegation
description: >
  When to delegate work to a subagent or a workflow and when to just do it, which
  agent to use, and how to brief one. Load BEFORE spawning any subagent or
  workflow, when a task looks wide enough to parallelise, or when delegation is
  producing thrash instead of speed. Pairs with ops-models, which decides the
  model and effort each delegate gets.
when_to_use: >
  "use subagents", "fan this out", "run this in parallel", "audit the whole
  codebase", deciding between doing a search inline vs spawning a scout, a task
  spanning many independent files or modules.
---

# Delegation policy

Delegation is not free. Every subagent re-establishes context from scratch,
re-explores, reports back, and then its report has to be read. That overhead is
real and it is paid up front, before any benefit arrives.

## Independent work goes in ONE message, or it is a queue

Agent calls in the **same** assistant message run concurrently. Calls in
separate messages run one after another, and each result lands back in the main
context before the next one starts. Those two things look identical while you
are writing them and are a different system.

> Measured over 37 sessions, `scripts/brain/delegation.py`:
> **172 of 235 launches were one agent alone — 73%.** The other 63 were real
> batches, two of them six wide. Running the solo ones one at a time cost
> **1,264s of wall-clock** that batching would not have. 0 of 487 recorded runs
> were nested.
>
> **The 100% that used to be printed here was a bug, not a finding.** Both
> counters grouped `tool_use` blocks per transcript RECORD, and Claude Code
> writes one record per block — every record of a message sharing its
> `message.id`. `any(width > 1)` was therefore unreachable: a batch was
> arithmetically invisible, so the number described the file format, not the
> work. The rule survives at 73%; the certainty did not.
> `scripts/brain/transcript.py` now owns the grouping — **never count blocks
> per record.**

So the decision is not only *whether* to delegate. It is:

1. **Name every independent unit before launching any of them.** The second unit
   is the one that never gets named, because after the first result arrives you
   are reading, not planning.
2. **Emit them in one message.** N `Agent` calls, one assistant turn.
3. **Sequence only true dependencies** — B needs A's output. "B is easier to read
   after A" is not a dependency.

**The orchestrator can do this for you, and could not before.** Its brief said a
background subagent is never granted the `Agent` tool and that this was what the
harness handed it. It was not: the tool was denied by `disallowedTools` in the
agent's own front matter — the brain had switched it off and then recorded its
own restriction as a platform limit. Measured 2026-09-10: a `general-purpose`
subagent launched `brain:scout`, which returned a correct answer, no error. The
belief cost three of the orchestrator's four recorded runs — briefed to
delegate, unable to, it did the work itself.

So there are two shapes, not one:

- **Fan out from the main loop** when you already know the split. Cheaper, and
  it is what the score prefers.
- **Hand the whole thing to `orchestrator`** when working out the split needs
  more codebase reading than this context should carry, or when the units' raw
  output would flood it. What they return lands in the orchestrator's context
  and dies there; only its synthesis comes back.

`references/fan-out.md` has the mechanics and the recipes for work that recurs
here.

## Delegate when — and only when — one of these is true

- **Wide and independent.** Many files, modules or candidates to inspect, with no
  ordering between them. One agent per slice, all launched in a single message so
  they run concurrently.
- **Context that must not land in the main thread.** A search that would return
  thousands of lines when only the conclusion matters. The subagent reads the
  noise; you keep the answer — which holds only while the answer comes back AS
  an answer. See *Ask for a conclusion* under **Cap it**.
- **An independent perspective is the point.** Adversarial review, a second
  opinion on a design, verifying a claim you already believe.
- **Isolation is required.** Parallel edits that would collide → `isolation:
  worktree`.

## Do not delegate

- Work finishable in a handful of tool calls. Reading three files and editing one
  is faster inline than briefing an agent about it.
- **Re-running your own checks.** Reading back what you just wrote to confirm it
  does what you intended belongs in the main loop, where the full context of the
  change already exists. This is **not** the same as an independent attempt to
  REFUTE it — that is `reviewer`, and it is one of the reasons above to delegate.
  Do not let this bullet cancel that one. Measured: `security` sits at 16 runs
  and `reviewer` at 0, because an audit reads as permitted and a review of code
  the main thread just wrote reads as banned. In an implementation session, all
  review is review of your own work; if that disqualifies it, nothing is ever
  reviewed.
- Anything needing conversation history the subagent will not have.
- Splitting one modest job into pieces just to look parallel. Parallel agents are
  for genuinely separate tracks, not for slicing a single small task.

## Cap it

- Prefer one well-briefed agent over three vaguely-briefed ones.
- Never exceed ~20 concurrent agents without an explicit request.
- **Brief precisely the first time.** Launch → wait → re-brief is the most
  expensive possible shape; it pays the setup cost twice for one result.
- **Ask for a conclusion, never a dump.** A subagent's report is the one thing
  in the context that can never be reclaimed afterwards, so the brief has to
  name the return shape — the finding and its coordinates, not the file
  contents. A bloated report is a briefing defect, the same as a wrong one.
  `references/briefing.md` has the measurement and what is reclaimable.
- **Commit to the delegation.** Once an agent reports, do not re-derive its
  findings or redo its work. If its output cannot be trusted, that is a briefing
  problem or a model-tier problem (`ops-models` → escalation), not a reason to
  duplicate the work.
- Report what a subagent could not determine. A confident summary of an agent
  that actually failed is worse than saying it failed.

## Read on demand

| Doing | Read |
|---|---|
| **launching more than one — the batch, and the recipes that recur here** | `references/fan-out.md` |
| about to launch one — what goes in the brief, and who checks the result | `references/briefing.md` |
| something is running in the background and you want to touch git | `references/safety.md` |
| reaching for a consultant, or judging whether the last one earned it | `references/consult-chain.md` |
| picking WHICH agent | `references/roster.md` |
| pairing an observer to a long run | `references/observer.md` |
