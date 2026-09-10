---
name: capture
description: >
  Turn something just learned — a defect and its fix, a correction from the user,
  a decision, a constraint discovered the hard way — into a durable rule in the
  right skill. Load at natural boundaries: after fixing a real defect, after the
  user pushes back or corrects an approach, after a design decision, at the end of
  a feature. Also invoked directly as /brain:capture. Enforces the routing law and
  the one-source-of-truth rule.
when_to_use: >
  "remember this", "zapamti ovo", "capture that", right after a bug is fixed,
  right after the user says "no, do it this way", after a decision that will
  outlive the conversation, closing out a phase of work.
---

# Capture — turning experience into rules

Without this step, every session relearns the same lessons. Nothing else in the
brain matters if this loop does not run.

## When it runs

Do it at the moment, not "later" — later never comes. Trigger on:

1. **A real defect was fixed.** Not a typo — something that shipped wrong, or
   would have.
2. **The user corrected the approach.** *This is the highest-value signal in the
   entire system.* It is direct evidence that a default was wrong. Capture it
   even when it feels minor and even when it is about style or tone.
3. **A decision was made** that will outlive the conversation — an architecture
   choice, a trade-off accepted, a path deliberately rejected.
4. **A constraint was discovered the hard way** — a tool, platform, library or
   environment behaved differently than assumed.
5. **A phase of work closed.**

Propose it in one line and write it. Do not stop the flow of work to hold a
ceremony about it, and do not ask permission for an obviously correct capture.

### The trigger is the weak part, and it fails in a predictable direction

**A self-improvement skill does not reliably fire from description matching,
because it competes for attention with the work that produced the lesson.** The
deeper you are in a task, the less likely you are to notice that something
capture-worthy just happened — and depth of engagement correlates with how
valuable the lesson is. The trigger fails hardest exactly when it matters most.

> This is not speculation about the mechanism. A separately built
> observation skill (`task-observer`) reached the same conclusion from the other
> direction and documented it: description matching alone misses the trigger
> "when you're focused on the task itself", so it tells users to invoke it from
> `CLAUDE.md` at session start rather than trust the description.

Two consequences, and the second is the load-bearing one:

- **Anchor capture to events that are already forcing a pause** — a fix landing,
  a test going green, a commit, the end of a phase. Those moments interrupt the
  work anyway, so noticing costs nothing extra.
- **Treat a missed capture as a defect in the anchor, not in your attention.**
  If a lesson had to be recovered later from a transcript or a retro, the fix is
  to name the anchor that should have caught it — not to resolve to be more
  observant next time. `/brain:ops-maintain`'s practice review exists precisely
  because some captures will be missed, and a review reading real work recovers
  what a live trigger did not.

## Procedure

**1 — State the lesson in one sentence.** If that is hard, it is not a rule yet;
it is still a story. Keep working on the sentence.

**2 — Classify by root cause, not symptom.** "Forgot the org filter" is a
symptom. "A by-pk view was authorised by a scope-wide permission instead of the
same helper the list view uses" is a root cause, and it is the version that
prevents the next occurrence in a place you have not looked yet.

**3 — Route it.** One destination only (see `/brain:brain` for the full law):

| Signal | Destination |
|---|---|
| Would be true in another repo | `craft-*` |
| True for this technology | `stack-*` |
| About how two stacks meet | `arch-seams/references/*` |
| True only in this codebase | the project's `<name>-domain` skill |
| A fact about the person, client, or plan | project memory |
| Its violation is a security incident and it cannot wait for a trigger | project `CLAUDE.md` |

A rule that is a general principle wearing project clothes gets **split**:
principle to the brain, coordinates to the project skill, cross-referenced by
name.

**4 — Search before writing, and say what this SUPERSEDES.** Grep the target
skill for the concept first. Extending an existing rule beats adding a
near-duplicate; two overlapping rules drift and then contradict each other.

The law says a rule never lives in two places. Nothing enforced it: step 4 said
*look*, and looking is exactly what gets skipped under time pressure — the same
shape as every reminder this brain has already measured and replaced with a
gate. So the step now has an output. Before writing, answer one of:

- **"This supersedes X."** Then EDIT X. Do not add beside it and plan to
  reconcile later; the two versions start drifting the moment both exist, and
  the stale one is the one someone acts on.
- **"This supersedes nothing, and here is where I looked."** Name the files
  grepped. A search that found nothing is a result and takes one line.

If you cannot say either, you have not searched yet.

**No index file, ever.** An `INDEX.md` listing what the rules are is a second
copy of every rule's identity, and it goes stale silently because nothing breaks
when it does. The routers already are the index, and `docs/` is generated from
the files rather than maintained beside them.


**5 — Write it in the house format:**

> **What broke** — one sentence, plus a concrete coordinate if it recurs.
> **Why** — the root-cause class, not the surface symptom.
> **The rule** — phrased so a future reader can *check* it before writing code.
> **Where it bit** — every known site, so stragglers can be grepped.

**6 — If it recurred, say so.** A rule that has now failed twice gets promoted:
stronger wording, a hook, a test that fails when it is violated, or a move to a
place that loads earlier. Recurrence means the current placement is not working —
rewriting the same rule harder in the same file changes nothing.

**7 — Commit the brain.** It is a git repo at `~/.claude/skills/brain`. The
commit message says what defect taught the rule.

## A request can itself be a rule

When the user asks for a **way of working** rather than a piece of work — "ask me
through the interactive picker instead of guessing", "always show me the diff
before committing", "check the ticket board before starting" — that is a standing
instruction, not a one-off task.

Do both: **carry it out now, and write it down** so it holds from here on.

Recognise it by the shape: the request describes *how* you should behave, uses
"always" / "from now on" / "every time", or is a correction to a default. If
carrying it out once would leave the next session doing the old thing, it belongs
in a file.

Route it like anything else: a way of working that would hold in another repo →
a `craft-*` or `ops-*` reference; specific to this project → its domain skill; a
preference about how the user wants to be dealt with → memory.

**Say that you did it**, in one line: "done — and recorded in `ops-delegation`
so it holds by default." The user should never have to ask twice for the same
behaviour, and should not have to wonder whether asking once was enough.

Do not do this for a one-off ("do it this way *just here*"). The tell is whether
it generalises: a preference about *this* file is not a rule.

## Do not capture

- What the code, the repo or git history already says. A rule that restates the
  implementation goes stale the moment the code changes and then actively lies.
- Anything that only matters inside this conversation.
- A rule with no failure behind it. Speculative conventions accumulate, are never
  validated, and crowd out the ones earned by a real defect.
- A duplicate. Update the original instead.

When asked to remember something in this category, say so and ask what was
non-obvious about it — the answer is usually the rule actually worth writing.

## Deleting rules

A rule proven wrong is **deleted or corrected in the same change that discovers
it**. Stale rules are worse than missing ones: a missing rule makes Claude ask,
a wrong rule makes it act confidently. This includes rules in the brain, in
project skills, and in CLAUDE.md.
