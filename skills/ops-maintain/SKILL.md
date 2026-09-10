---
name: ops-maintain
description: >
  Keeping the brain from rotting — what gets checked automatically, what needs a
  periodic review, and in what order. Load when the health check reports a
  problem, on a periodic review, or when the brain feels like it has drifted.
when_to_use: >
  the session-start check reported something, "review the brain", a monthly or
  quarterly tidy, "is this still accurate", after a burst of rule-writing.
---

# Maintenance

A rule system that only grows becomes contradictory, and contradictory rules are
worse than missing ones — they make confident wrong work look justified. So the
cost of keeping it true is not optional; it is the price of having it at all.

## Automatic — every session, silent unless broken

A `SessionStart` hook runs:

```bash
python ~/.claude/skills/brain/scripts/brain/health.py --quiet
```

It **prints nothing when everything is fine.** That is deliberate: a maintenance
reminder that fires every session becomes noise, and noise gets ignored — which
is exactly how a system stops being maintained.

It catches the mechanical failures: generated docs out of date, uncommitted or
unpushed changes, a skill over budget, an agent preloading a skill that does not
exist, **a reference no router links to** (which would simply never be read), a
`README.md` in `agents/` being parsed as an agent.

When it speaks, act. It only speaks about things that are actually wrong.

**A hook you just added is not running yet.** `hooks.json` is read when a
session starts, so editing it changes the NEXT session and not the one doing the
editing. This cost real confusion twice in one day: an env override removed from
`settings.json` kept firing because the running process still carried the value
it inherited at launch, and hours later a new gate stayed silent through the
exact commit it was written for -- `hooks.json` edited 23:19, session started
20:03. Both times the file on disk was correct and the symptom continued.

So verify in the environment that is RUNNING, not only on disk: a fix is not a
fix until the symptom stops. When it cannot be verified live, say that the
change takes effect next session rather than reporting it as done.

A `Stop` hook runs too:

```bash
python ~/.claude/skills/brain/scripts/brain/verify_gate.py
```

It refuses to let a turn end while an edit to `scripts/`, `skills/`, `agents/`,
`hooks/` or `evals/` is newer than the last passing run of the gate that covers
it. It advises nothing — `require_skill.py`'s finding was that advice which can
be skipped, gets skipped — it returns `decision: "block"` once, names the exact
command, and then lets go whatever happens.

The comparison is deliberately narrow: not "is this report honest", which is not
decidable, but "is the verification older than the change it claims to cover",
which is. `tests.py` and `evals.py` record when they last passed;
`scripts/brain/verified.py` prints that ledger, and `verify_gate.py --explain`
says why the hook stayed silent when it did.

## Periodic — the reviews, in this order

Cadence: **monthly**, or after a burst of rule-writing. They are ordered because
each one's output feeds the next.

### 0. Practice — what a month of real work says

```bash
python ~/.claude/skills/brain/scripts/brain/retro.py --days 30
```

Writes an evidence pack (`docs/RETRO.md`) and prints a review prompt. **Run that
prompt in a FRESH session and bring the answer back.** A system reviewing itself
in the context that built it grades its own homework — every rule looks
justified from inside the session that wrote it.

This one comes first because it is the only review fed by **reality** rather than
by the brain's own files. The other three ask "is this internally coherent"; this
one asks "did any of it help".

Read two numbers before anything else:

- **Attributed commits / total commits.** If most work bypassed the framework,
  every conclusion below is drawn from a sample nobody chose. That is the
  finding, and it outranks the rest.
- **Outcomes recorded / agent runs.** Below half, the bands are anecdote wearing
  a number. Fix the recording discipline before acting on any score.

### 1. Structure — `brain-keeper`

Duplication between two files, contradictions, rules in the wrong layer, **stale
cited coordinates** (a rule naming a file that no longer implements the pattern —
the most dangerous kind, because it reads as verified). Proposes; does not
silently rewrite.

### 2. Usefulness — `score.py review` and the dashboard

```bash
python ~/.claude/skills/brain/scripts/brain/score.py review
python ~/.claude/skills/brain/scripts/brain/dashboard.py --open
```

Cost, measured usage and recorded outcomes side by side. What to do with each
shape:

| Pattern | Action |
|---|---|
| costs a lot, never used | the description is not triggering — rewrite it, or delete the thing |
| used a lot, expensive per call | split what it preloads into references |
| used a lot, scores weak | almost always too general to act on. Sharpen or split |
| scores `retire` | delete it. A rule proven unhelpful is worse than a missing one |
| unproven for months | it is not being triggered; the description is the problem |

### 2b. History — what the log names that the tree no longer has

```bash
python ~/.claude/skills/brain/scripts/brain/graph.py --dangling
```

`health.py` reads the working tree; this reads the commit history, and the two
see different rot. A `Brain-Agents:` trailer naming an agent that has since been
deleted, or a commit citing a ticket the store never held, is invisible to a
tree-based check and stays true forever in the log. Deliberately narrow: broken
`references/` links, unrouted references and a missing preload stay health.py's
and are not repeated here.

The same tool answers the question that used to need four: **`graph.py trace
DEMO#43417`** walks ticket → the commits that closed it → the agents that wrote
them → the skills they ran under → any finding filed against those. Why it is a
script and not a graph database — and the two conditions that would change that
— is in its own docstring, which is the only place that argument is written.

### 3. Reasoning — `archivist`

Reads the journal back and finds what no single session shows: a surprise that
recurs (a missing rule), a decision re-litigated twice (it was written where
nobody finds it), an agent whose output keeps getting reworked (its brief is
wrong).

### 4. Upstream — what Anthropic shipped that a skill was working around

```bash
python ~/.claude/skills/brain/scripts/brain/upstream.py
```

This runs with the reviews above because it needs the same eyes: the previous
three ask whether a skill still earns its place *here*, and this one asks
whether the problem it solves still exists at all. A skill written to route
around a missing capability keeps costing context long after the capability
ships, and nothing notices — a skill has no expiry, and the changelog is read
by nobody.

The script lists what is new and never decides. **The verdict — covered, partly
covered, unrelated — and what to do about each is `/brain:ops-prune`,
"When upstream shipped it".** Record the review with
`upstream.py --seen` so the next run shows only what is genuinely new.

## Deleting is the maintenance

Most of this work is subtraction, and it is the part that gets skipped.

- **A rule proven wrong is deleted in the same change that discovers it.** Not
  softened, not marked "deprecated" — deleted. A stale rule makes Claude act
  confidently; a missing one makes it ask.
- **An agent that keeps producing work you throw away is retired**, not tuned
  forever.
- **A skill nobody triggers in months** either has the wrong description or no
  reason to exist. Both are fixed by editing, not by waiting.
- **A script replaced by a better one** moves to `scripts/archive/` with a line
  saying what replaced it. That is for internal churn only — a *skill* retired
  because Claude Code now does it natively goes to the top-level `archive/`
  instead, which exists to stay readable by Codex and Gemini. Two directories,
  two audiences; do not merge them.

If a review adds more than it removes, twice running, the brain is growing faster
than it is being validated. Say so.

## When a rule keeps coming back

A lesson captured for the **third** time is not a writing problem — it means the
rule is in the wrong place or is not loading in time. Move it outward (a
reference to a router, a router to always-in-context), attach it to a hook, or
pin it with a test. Rewriting it harder in the same file changes nothing.

## After any maintenance

```bash
python ~/.claude/skills/brain/scripts/brain/docs.py        # regenerate the tables
python ~/.claude/skills/brain/scripts/budget.py            # confirm the cost moved
python ~/.claude/skills/brain/scripts/brain/health.py      # confirm it is clean
# stage the paths YOU touched -- never `git add -A` here (CLAUDE.md).
# A maintenance pass runs beside a live ticket sync and a regenerated
# dashboard; `-A` adopts both under a message describing neither.
git -C ~/.claude/skills/brain add skills/ agents/ scripts/ docs/
git -C ~/.claude/skills/brain commit && git -C ~/.claude/skills/brain push
```

Unpushed maintenance exists on one machine only, which is the same as not having
done it.
