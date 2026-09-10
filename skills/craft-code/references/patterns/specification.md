# Specification

**Reach for it when** the same predicate is asked in more than one place — as a
query filter, as form validation, and as "may this user see the button" — and all
of them must agree.

**Not when** it is asked once.

## Why it exists

The failure it prevents is quiet: the list query, the permission check and the
button visibility drift apart, and you get a button that returns 403 — or worse,
one that should have and does not. **One predicate, three call sites.**

## The shape

A named rule usable both as a boolean against one instance and as a filter
against a collection. If it can only do one of those, the other call site will
reimplement it, and that reimplementation is the drift.

## The traps

- **Do not express it twice** — once in code and once as query filters that
  "mean the same thing". They do, until someone edits one of them.
- **Compose, do not copy.** A rule that is "the base rule plus one condition" is
  a composition, not a new rule with the base pasted into it.
