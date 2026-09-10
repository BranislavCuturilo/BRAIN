# Decision records

## What goes in git, and what goes in a file

Two different questions get confused, and they have different right answers:

| Question | Where it belongs | Why |
|---|---|---|
| **who wrote this** | git — commit trailers | derived from the change itself, so it cannot rot |
| **why is it like this** | a decision record | a dated choice, which git only holds as prose scattered across commit bodies |

Provenance is never a comment. A comment claiming authorship is a hand-maintained
copy of something git computes automatically: three agents edit the function over
six months, nobody updates the comment, and it now attributes their work to the
first author. Worse than absent, because it reads as authoritative.

**Rationale is different.** It is not derived from anything — it exists only in
someone's head until written down — and it does not go stale the way a
description does, because it records *a choice made on a date*, not *what the
code currently is*. Superseding it does not falsify it.

## The shape

`docs/decisions/NNNN-slug.md` in the **project** repo, not the brain: the
decision is about this codebase.

```markdown
# 0007 — Partial fulfilment posts N movements

Date: 2026-08-04
Status: Accepted            <!-- or: Superseded by 0011 -->

## Context
What was true that forced a choice. The constraint, not the history.

## Decision
One paragraph. What we do.

## Consequences
What this costs, and what it now prevents. The part people skip and
the part that is worth reading two years later.

## Rejected
The option a reasonable person would try, and the specific reason it loses.
This is what stops the same debate being reopened every six months.
```

## The pointer in the code

One line, and **only where the code looks wrong without it**:

```python
# decision: docs/decisions/0007-partial-fulfilment.md
```

Not on every function. A pointer everywhere costs context on every read for a
question almost nobody is asking, which is the trap the whole
router-plus-references design exists to avoid. Put it exactly where a competent
reader would otherwise think *"this is the wrong way round"* and try to fix it.

`blame.py <file>` surfaces these alongside the commit attribution, so the two
halves — who, and why — arrive together.

## Superseding, never deleting

A decision that stops being true gets `Status: Superseded by NNNN` and stays.
The new record says what changed. Deleting it loses the reason the old approach
was rejected, and it will be proposed again.

**This is why decision records need no garbage collector.** Nothing rots, so
nothing has to be swept up — which is the difference between this and a folder of
free-form context files that accumulate until someone has to judge, file by file,
which are still true. A store that needs a cleaner is a store whose contents were
never derived from anything checkable.

## When to write one

Only when **all three** hold, or it becomes ceremony:

- a reasonable person would choose differently,
- the choice is expensive to reverse (schema, money, an external contract, a
  security boundary, a data migration),
- and the reason is not visible in the code.

A naming choice is not a decision record. A test-directory layout is not a
decision record. "We post N movements instead of amending one, because an
amended movement destroys the audit trail the regulator requires" is.

## Review

`brain-keeper`'s structure pass reads `docs/decisions/` alongside the skills: a
record whose Context no longer describes the codebase is a superseding candidate,
and a rule in a skill that contradicts an Accepted decision is a real conflict —
one of the two is wrong, and finding out which is the point.
