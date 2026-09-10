---
name: craft-reuse
description: >
  Write code once and reuse it: how to find what already exists before adding
  anything, when duplication is genuinely the right call, and how to extract
  without creating the opposite problem. Load BEFORE adding any new function,
  class, component, template or helper, and during review. Quality over quantity —
  less code doing more, and one place to fix each bug.
when_to_use: >
  about to write a new function/class/component/helper, "is there already
  something for this", copy-pasting a block, a review that smells duplicated,
  refactoring, "we have three versions of this", naming a new utility.
---

# Reuse

**The goal is not fewer lines. The goal is one place to fix each bug.**

Duplication's real cost arrives later: a defect is fixed in one copy, the twin
keeps shipping it, and nobody knows the twin exists. That is why this matters
more than tidiness.

## Search before you write — every time

Before adding any function, class, component or helper, spend thirty seconds
looking. Grep for:

- **the noun** — the domain concept (`invoice`, `signature`, `balance`)
- **the verb** — what it does (`calculate`, `resolve`, `normalize`, `format`)
- **the shape** — a distinctive line from what you were about to write

Check the obvious naming variants before concluding it does not exist: singular
and plural, snake and camel, abbreviations, and the other language's word for it.
A domain concept in a Serbian-language business often exists under both names.

Delegate the search to `scout` when the codebase is large — it is cheap, and it
is exactly what that agent is for.

Then decide, in this order:

1. **It exists and fits** → use it.
2. **It exists and almost fits** → extend it, if the extension serves the *same*
   reason it exists. A parameter is fine. A boolean that switches between two
   unrelated behaviours is not — that is two functions wearing one name.
3. **It exists but is wrong for you** → say so explicitly and write the new one
   next to it with a comment on why they differ. Silent divergence is what
   produces three versions.
4. **It does not exist** → write it, in the right place (below).

## DRY applies to knowledge, not to text

This is the distinction that keeps reuse from becoming its own disease.

**Two pieces of code that look identical but would change for different reasons
are not duplication.** Merging them couples two things that were independent, and
the next change to one breaks the other. The classic version: a validation rule
and a display rule that happen to use the same threshold today.

**Two pieces of code that encode the same decision are duplication even when they
look nothing alike.** A tax rate in a service and the same rate in a template, a
status list in Python and the same list in JavaScript, a calculation implemented
once for the real path and again for the preview.

The test: **when this changes, must both change together?** If yes, it is one
thing and must live in one place. If no, leave them apart.

## When to extract

- **Second occurrence, same reason to change** → extract now. Waiting for a third
  means the second one has already diverged.
- **Second occurrence, different reasons** → leave both.
- **Never extract on the first occurrence.** An abstraction built for one caller
  is a guess about the second, and the guess is usually wrong (`craft-code` —
  speculative abstraction).

Extract the *concept*, not the *lines*. If naming the extracted function is hard,
that is the signal that the two call sites were not actually doing the same
thing.

## The second write path omits — it does not copy

Duplication hunting finds the twin that was pasted. It does not find the twin
written from scratch that left out everything which is not the write itself: the
"last activity" marker, the notification, the audit row, the cache bust. Nothing
*looks* duplicated, because the second copy does not exist — the API saves the
comment and the customer is simply never told.

**When you add a second entry point to an existing record type, read the first
path to the end and list what it does after the write.** Each item is then either
called through a shared service or explicitly declined in a comment. The funnel
belongs in the service layer, and the side effects go outside the transaction:
a notification for a write that then rolls back cannot be recalled.

## Where shared code lives

- **Shared code lives in shared infrastructure, not in whichever feature needed
  it first.** Otherwise the second consumer imports a feature module, and the
  dependency graph inverts (`craft-code`).
- **No `utils` god-module.** A module everything imports becomes a place nobody
  can change. Name modules after what they are about — `money`, `dates`,
  `signatures` — not after the fact that they are shared.
- **One implementation per calculation**, with the callers differing only in
  where the inputs come from. A preview that scores differently from the real
  thing is teaching users a lie.
- **A component with a required wrapper is one component.** If every caller has
  to remember to add the same surrounding markup, the wrapper belongs inside.

## Symptoms to act on

| You see | It means |
|---|---|
| a block pasted with one identifier renamed | extract now — this is the canonical case |
| the same constant in two files | promote it (`craft-code`) |
| a fix applied in one place and its twin left alone | the twin is already shipping the bug |
| three functions whose names differ only by a suffix (`_v2`, `_new`, `_old`) | nobody dared delete; find out which is live and delete the rest |
| a `TODO: same as above` | write it down properly or extract it |
| a template block copied across pages | it is a component |

## In review

Ask: *does this already exist? Will a fix here need to be repeated elsewhere?
Would a reader find this by searching for the obvious word?* The last one matters
more than it sounds — code that cannot be found gets rewritten, and then there
are two.
