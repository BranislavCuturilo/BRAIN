---
name: brain
description: >
  The constitution of the portable rule system. Load BEFORE creating, editing,
  splitting or routing any skill, sub-skill or rule, and whenever asked where a
  new rule belongs. Holds the four layers and the routing law; references/ hold
  composition, naming, splitting and lifecycle.
when_to_use: >
  "where does this rule go", "make a skill for X", "add a sub-skill", "split this
  skill", "the skills are a mess", "add a new stack", any edit under skills/ or
  agents/.
---

# The brain — constitution

One portable rule system, carried across every machine and every project. It
lives in **one git repo cloned to `~/.claude/skills/brain`** and loads as the
skills-directory plugin `brain@skills-dir`. Skills are invoked `/brain:<name>`.

Everything here is about **where knowledge goes**. Get routing right and the
system compounds; get it wrong and it rots into duplicated, contradictory prose.

## Loading is not optional. Delegating is.

**Before writing or changing code, INVOKE the skills that cover it.** Not recall
them — invoke them. `/brain:craft-code`, `/brain:craft-security`, the `stack-*`
for the technology, `/brain:craft-testing` before a test, plus the project's own
skills.

Delegation is a separate question with a different answer. *Whether* to
delegate is a cost/benefit call — but **the call has to be made out loud, at the
moment a plan is written or a phase begins.** It must not be defaulted to "no"
by whoever already has the keyboard, which is the failure this system actually
has. Measured across one session that shipped a whole Django segment — 8,700
lines, ten migrations, twenty-nine view classes: delegation happened on the
first turn and on **no** continuation turn afterwards. "Continue" carries no
decision point, and continuing alone is always the cheapest next move.

The orchestrator can still be skipped. `ops-delegation` decides the rest.
**Skills, unlike delegation, are never optional.** They are the rules
themselves; skipping them is not "working leaner", it is working without the
rules.

> Measured. A segment was written across a long session without invoking a
> single brain skill, on the reasoning that the content was already in context
> because those files had been edited earlier that day. An independent audit
> then found **six defects, two of them serious**, and both serious ones were
> covered verbatim by rules in `craft-security` and `CLAUDE.md`: every model
> guard was dead code because the service never called validation, and
> credentials sat in plaintext behind fields named `*_encrypted`.
>
> The hook had fired. The reminder was on screen. The rules were recallable.
> None of that produced the check.

**Being able to recall a rule is not the same as having applied it.** Recall
gives you the sentence; loading puts the rule in front of the specific line you
are about to write, which is where it either fires or does not. The audit is the
proof: the rules were *known* and violated anyway.

There is no mechanism that can verify a skill was loaded — a hook can remind,
never confirm. So this is a rule that only holds if it is followed deliberately,
which is exactly why it sits in the constitution rather than in a reference.

## The four layers

A rule belongs to the **outermost layer where it is still true**.

| Layer | True for | Example | Lives in |
|---|---|---|---|
| **craft** | any language, any framework, forever | "a guard checked on one resolution path must be checked on every path" | `craft-*` |
| **stack** | one technology | "field validation runs before `clean()`, so autofill in both" | `stack-*`, `ui-*` |
| **seam** | how two stacks meet | "with a SPA front end, CSRF moves to the fetch layer" | `arch-seams/references/*` |
| **domain** | this codebase only | "closing a stocktake needs full reach over every location" | `<name>-domain` in the project |

**The test:** *would this rule still be true in a different repo?* Yes → brain.
No → project.

When a rule is a general principle wearing project clothes, **split it**: the
principle to the brain, the coordinates (`file.py:120`, the exact model name) to
the project skill, cross-referenced by name.

## Routing law

Four destinations, no overlap. Pick exactly one.

| Destination | Holds | Signal |
|---|---|---|
| **brain skill** | enforceable convention reusable in another repo | "we should always…" |
| **project skill** | domain semantics, this repo's invariants, file:line | "in *this* system, X means…" |
| **memory** | facts about the person, the client, the plan | "the client decided…" |
| **project `CLAUDE.md`** | *only* what must be in context before any trigger fires | a missed check is a breach, not a style nit |

**`CLAUDE.md` is the expensive one** — read on every turn of every session.
Nothing goes there that a skill trigger can deliver in time. The deliberate
exception is a rule whose violation is a *security* incident: those cannot wait
for a trigger to fire.

**Never write the same rule in two places.** One source of truth, pointers
elsewhere. Two copies drift and then produce confidently wrong work.

## What earns a rule its place — three doors

`CLAUDE.md` states the requirement; this is what each door costs.

| Door | What it takes | Status |
|---|---|---|
| **Incident** | what broke, where, when | law |
| **Measurement** | proved here and now, instead of waiting for the break | law |
| **Borrowed** | where it came from, what would confirm or kill it, a review date | **not law until one of the first two arrives** |

**Measurement is the fast door**, and it is under-used. `dead.py`'s
framework-bucket rule was earned in one afternoon by counting: 20 of 22
findings on one real app were framework-called noise. Nothing had broken. The
number was the proof.

**A borrowed idea is welcome.** What must never happen is that it becomes
*indistinguishable* from an earned one — so it keeps its origin and its review
date in the text, and it is written as a hypothesis ("this claims…", "worth
testing whether…") rather than an imperative. A third door with no marker is
just the first door with the evidence deleted.

**Three independent occurrences substitute for an incident.** One site
complaining is a gripe; the same shape landing in three places that did not
talk to each other is structure. (From icm-architect, MIT — the threshold, not
the method.)

**An extreme constant is a symptom, not a finding.** Before a measured number
becomes a rule, check it against a hand-counted sample — and check hardest when
it comes out clean. 100%, 0%, and "always exactly 1" are the shapes a broken
counter produces, and a broken counter is indistinguishable from a strong
result once the number is in the file.

> Measured 2026-09-10, three in one day, each already quoted in the rules as
> evidence. *"269 of 269 launches were one agent alone — 100%"* counted
> `tool_use` blocks per transcript RECORD, and Claude Code writes one record per
> block, so the test for a batch was unreachable; regrouped, 73%. *"0 of 487
> runs were nested"* read `depth` and `parentAgentType` while the runtime writes
> `spawnDepth` and `parentAgentId`, so every lookup missed; with the right keys,
> 33 of 496. `episode.py` reported `max_width: 1` for every session on the same
> per-record mistake, and its test stayed green because the fixture packed
> several blocks into one record — a shape the runtime never emits.
>
> The second of those was load-bearing: a whole rule, a workaround in the
> router, and an agent's brief were built on a zero that could not have been
> anything else.

The cheap check is the one that would have caught all three: take five items by
hand and see whether the script agrees. **This is the measurement door's own
version of "verify before you believe" — a number you produced is a source you
are quoting.**

**Where the incident door does not apply at all:**

- **A domain with no history yet.** The first Django project had no Django
  incidents. Demanding one there yields a brain with no rules.
- **Prevention whose incident is unaffordable even once** — never commit
  credentials, never point a suite at production. You do not get to learn
  those by experience.
- **The business and process side.** The incident is a lost client six months
  later and is never attributable to one decision. Those are not skill rules
  at all: they live in a project note with a review date, and they are judged
  by whether they changed a decision. If in three months none did, they go.

*Earned by the operator's correction (2026-09-09): "nije problem ako se već
nije desio incident, može da pomogne svakako" — the old wording banned
learning from outside, which was never its job.*

## Read on demand

| Working on | Read |
|---|---|
| creating a skill, splitting one, a description that will not trigger | `references/splitting.md` |
| two skills that overlap or contradict; who owns a concept | `references/composition.md` |
| naming, adding a new stack, adding a stack combination | `references/naming.md` |
| deleting a rule, a stale cited reference, onboarding a repository | `references/lifecycle.md` |

## Agents are part of this

Agents live in `agents/` and travel with the brain. An agent is a **brief plus a
grade** (model tier + effort), and the skills it preloads are its knowledge — a
subagent starts fresh every invocation and remembers nothing of a correction.

**So teaching an agent means editing a skill.** A senior's review finding is
filed into the skill the junior preloads, and every junior loading it is
permanently better. See `ops-seniority` for grades and the review-and-teach loop.

Adding an agent is justified by a recurring *shape of work* with a distinct
brief — not by a new topic. A new topic is usually a reference an existing agent
should load.

## Size budget — the constraint behind everything

**Once a skill loads, its full text stays in context for the whole session**, and
its *description* is in context in **every** session whether used or not. A
`references/*.md` file costs nothing until read.

That is why the layout is few routers and unlimited references, why descriptions
are kept under ~450 characters, and why a `SKILL.md` past ~200 lines is a signal
to split. The numbers and the method: `references/splitting.md`.

Check with `scripts/budget.py` after any change.

## A skill edit is a behaviour change, so prove it

`evals/` is the regression suite for this system — the hooks driven through
their real contract, the routing checked against the files, and a paid tier
that runs `claude -p` against a fixture and asserts on what was written.

```bash
python scripts/brain/evals.py        # free; also runs in CI on every push
```

**Run it after editing any skill, hook or description.** Nothing else here
fails visibly: a skill rewritten into something that no longer triggers, a glob
that stops matching the file it was written for, and a rule that has quietly
come to exist in two layers all look exactly like a working system until a
ticket goes wrong months later. On its first run the suite found two live
defects, one of them in the router that fires before every prompt.

Adding a case: `evals/README.md`. Every case must cite the incident it comes
from — the same law the rules follow.
