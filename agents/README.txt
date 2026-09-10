agents/ -- for the reader
=======================
*This file is for you, not for Claude. The full table is
[`../docs/AGENTS.md`](../docs/AGENTS.md), generated from the files themselves.*

## What an agent actually is

A markdown file. YAML frontmatter says which model it runs on, how hard it
thinks, which tools it may touch and which skills get injected at startup; the
body is its brief.

Two things about them are worth understanding, because everything else follows:

**An agent has no memory of the last time.** It starts fresh on every
invocation and carries only its preloaded skills — so you cannot teach one by
correcting it. To make an agent better you edit a *skill it preloads*. That is
why the roster is large and each brief is narrow: a specialised agent with the
right two references beats a general one that has been "told" things.

**An agent's context is thrown away when it finishes.** That is the point of
the reader agents: one reads 1,500 lines of `models.py` and returns thirty lines
of structure, and the writer never carries the file.

## How to read the roster

Names are systematic:

| Prefix | Means |
|---|---|
| `dj-*` | a Django file or surface — `dj-models`, `dj-list-view` |
| `dj-*-reader` | reads and reports; never edits |
| `dj-*-slice` | several files that must agree, done in one pass |
| everything else | a discipline — `security`, `optimizer`, `pm` |

**Grade** is model tier plus effort, and it is a claim about how much judgement
the task needs — not about how important it is. `dj-action-view` is senior
because POST-only endpoints have no page and nobody reviews them, not because
they are complicated.

## Picking one

Normally you do not — `orchestrator` does. When you pick by hand, the two
mistakes are reaching for a general agent when a narrow one exists, and asking a
writer to do the reading first.

## Adding one

Justified by a recurring **shape of work** with its own brief. A new *topic* is
usually a reference an existing agent should load, not a new agent.

It needs: one job in one sentence, the narrowest tool set, a grade, a short
description (~120 characters for a narrow agent — descriptions are in context
every session), and `memory: user` only if remembering across sessions genuinely
helps. Reviewers and readers benefit; producers do not.

Then run `python scripts/brain/docs.py` and add it to a group in that script, or
it lands under "Ungrouped".

## Retiring one

An agent that keeps producing work you throw away gets deleted, not tuned
forever. `scripts/brain/score.py review` is where that shows up.
