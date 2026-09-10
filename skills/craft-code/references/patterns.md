# Patterns worth reusing

Extends `craft-code`. Reuse the mechanism the codebase already has — a parallel
mechanism doing the same job as an existing one is a defect introduced at design
time, and it looks like progress while it happens.

**This page is an index. Read only the row you need** — each pattern's own file
carries when to reach for it, when not to, and the traps that come with it.
Loading all eight to use one is the cost this split exists to avoid.

| Pattern | Reach for it when | Read |
|---|---|---|
| **Strategy** | interchangeable algorithms chosen by stored data | `patterns/strategy.md` |
| **State machine** | named states, and only some moves are legal | `patterns/state-machine.md` |
| **Service layer** | logic spans models, has steps, or runs from several entry points | `patterns/service-layer.md` |
| **Repository / scoped manager** | every query must carry the same constraint | `patterns/repository.md` |
| **Factory** | creation needs coordinated steps, and half-built is invalid | `patterns/factory.md` |
| **Specification** | one predicate asked as a filter, a check, and a visibility rule | `patterns/specification.md` |
| **Observer / signals** | something outside must react, and the inside must not know it | `patterns/observer.md` |
| **Template method** | a fixed sequence differing at named points | `patterns/template-method.md` |

**Before adding any of them, check the codebase already lacks it.** The most
expensive pattern mistake is not choosing the wrong one — it is building a second
one beside the mechanism that was already there.

## Seeds and fixtures obey every rule production code obeys

The state-machine rule is broken by **seed and fixture code**, every time. A seed
that assigns a final status directly produces records structurally different from
real ones: no event fan-out, no audit trail, no derived rows. Then "works in
production, fails on seed" burns days, and the cause is invisible because the
data looks right.

**Seeds must be idempotent at the row *and* the workflow level:**

- **Every row** is a keyed upsert on `(scope, natural-key)`.
- **Every ledger-style write** is guarded so a re-run does not double-count.
- **Every lifecycle transition is re-entrant**: guard each step on the record's
  *current status*, never on "does it look empty". On the second run the record
  is already at the end state and the service will correctly refuse the step — an
  unguarded seed then crashes mid-way and leaves the data half-applied.
- **Prove it with a test that runs the command twice** and compares a snapshot. A
  seed nobody re-runs is a seed whose second run is broken.
- Spread demo data realistically. Everything parked on one record hides the bugs
  in all the others.

## Entry points are code too

A command-line entrypoint is only imported when invoked, so a syntax error in one
ships silently while the service it calls is fully tested. Every command gets at
least one invocation test with side effects suppressed (`craft-testing`).
