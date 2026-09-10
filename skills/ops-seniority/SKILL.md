---
name: ops-seniority
description: >
  How the agent team is organised: grades, why specialised agents beat general
  ones, how the orchestrator picks and splits work, and how a correction becomes
  a permanent improvement. Load before assigning delegated work or when a
  delegated result came back weak.
when_to_use: >
  choosing which agent gets a task, "split this across agents", an agent hit its
  context limit, a delegated result needed rework, designing a new agent.
---

# The team

Seniority is **model tier + effort + brief** — a claim about how much judgement
a task needs, priced accordingly.

| Grade | Model · effort | For |
|---|---|---|
| **mechanical** | haiku · low | finding, listing, counting, formatting. No judgement at all. |
| **junior** | sonnet · low–high | one narrow job with an unambiguous spec. |
| **senior** | opus · xhigh | design-affecting work, review, debugging that already resisted one attempt. |
| **principal** | opus · max | security, architecture, migrations — anywhere being silently wrong is expensive. |

## Specialised juniors, not improving ones

**Juniors do not get promoted.** A narrower agent with a sharper brief and the
right preloaded references beats a general agent that has "learned" — because a
subagent cannot learn: it starts fresh every invocation and carries only its
preloaded skills.

So the way to make junior work better is **to create more, narrower juniors**,
each with:

- one job, stated in one sentence;
- the smallest tool set that does it;
- two or three preloaded references — not whole rule sets;
- a description short enough to be cheap (~120 characters for a narrow agent).

A per-file-type agent knows exactly the traps of that file. It does not need to
carry the rules for the four other file types it will never touch.

## The orchestrator decides

One agent, `orchestrator`, reads the task, picks who does what, and splits when
the work is too large. Nothing else picks agents. This keeps the choice in one
place where it can be reasoned about, rather than spread across whoever happens
to be running.

Grade the **task**, not the area. "Change a model" is junior when it adds a
nullable field and principal when it changes a uniqueness constraint on a live
table. Three questions:

1. **Is there one obviously right answer?** Yes → junior.
2. **Would being wrong be noticed immediately?** No → go up. Silence is what
   makes errors expensive.
3. **Does it touch isolation, auth, money, schema, or anything irreversible?**
   → principal, no discussion.

**Never grade down to save budget on something that must be right.** The saving
is cents; the failure is an incident.

## A correction improves the skill, never the agent

A senior reviewing junior output has **two** deliverables:

1. The correction to this output.
2. **A rule, filed into the reference the junior preloads.**

The second is the one that matters and the one that is skipped. Fixing without
filing means the same mistake tomorrow — the junior has no memory of it. **The
teaching happens by editing a file, not by explaining.**

```
junior produces → senior reviews → finding
                                     ↓
                     scribe → the reference that junior preloads
                                     ↓
                        every junior loading it is now better
```

Say so in the report: "corrected X, and added the rule to
`stack-django/references/models.md`."

**A finding you cannot phrase as a checkable rule is taste, not correctness.**
Drop it.

When a junior fails twice on the same class of task, the question is **which
rule was missing**, not why the model was weak. Fixing the rule is worth more
than fixing the output.

## Running out of context or budget

An agent approaching its limits **stops and reports** — never degrades quietly. A
subagent that silently truncates returns something that looks finished, which is
worse than an obvious failure.

> **PARTIAL.** Completed: A, B. Not reached: C, D. Suggested split: C by module
> (three independent slices); D needs C's output first.

**Then the orchestrator splits — the agent does not split itself.** Nested
self-delegation loses the coordination that makes a split correct.

Splitting rules:

- **Split along seams that already exist** — by file, by module, by review
  dimension, by ticket. Never "first half / second half".
- **Each slice independently completable and independently verifiable.** A slice
  that cannot be checked alone is not a slice.
- **Never split across an ordering dependency.** That is a pipeline, not a
  fan-out.
- **Every slice loads the references it needs.** A slice done without the right
  rules is a slice that has to be redone.
- **Say what was dropped.** Silent partial coverage reads as complete.
- Below a certain size the coordination costs more than the work. Two
  well-briefed agents usually beat six thin ones.

## Before a brief says a capability is denied, read the agent's own front matter

**Earned 2026-09-10.** The `orchestrator` brief said: *"A background subagent is
not granted the `Agent` tool, and you are one. This is not a permission you can
work around; it is what the harness hands you."* The harness hands it out fine.
The tool was denied by `disallowedTools: Write, Edit, NotebookEdit, Agent` in
the orchestrator's own front matter — the brain had switched a capability off,
and then written its own restriction down as a platform limit.

What that cost, all of it visible in the repo before anyone looked: 21 of the 55
agents had never once been invoked, because the orchestrator was the only thing
meant to select them; a workaround was built into `prompt_router.py` to reach
them by regex instead; and 27 nested runs the orchestrator had performed on
2026-08-21 sat in the run records unnoticed, because the counter that would have
shown them was reading key names the runtime never wrote.

**The rule:** a brief may state that something is impossible only after the
agent's own `tools:` and `disallowedTools:` have been read. "The platform does
not allow it" and "we turned it off" are indistinguishable from inside the
agent, and only one of them is true. Write which.

A brief is also the wrong place to record a limit that a front-matter line
already enforces: the line is the source of truth, and the prose goes stale
against it silently.

## Adding an agent

Justified by a recurring **shape of work** with a distinct brief — not by a new
topic. A new topic is usually a reference an existing agent should load.

A new agent needs: one job, the narrowest tool set, a grade from the three
questions, a short description, and `memory: user` **only** if it genuinely
benefits from remembering across sessions (reviewers, readers of people's
writing, archivists — never producers).

Agents that consistently underperform are retired, not tuned forever
(`ops-scoring`).
