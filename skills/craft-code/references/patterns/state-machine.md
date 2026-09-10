# State machine

**Reach for it when** a record moves through named states and only some moves
are legal — draft to review to approved, requested to ordered to fulfilled.

**Not when** the field is only a label and nothing branches on it.

## The rule that gets broken

**Every transition goes through the machine — never assign the status field and
save somewhere convenient.** A direct assignment skips the guard, the side
effect and the audit record, and it survives review because it looks like
ordinary code.

State machines also invite a validation gap: a transition that sets fields and
saves without full validation lets a partially-valid row through, because the
caller assumes the machine validated and the machine assumes the caller did.

## The traps

- **The legal-transition table is data, not a branch chain.** A chain grows one
  branch per state and nobody can see the whole graph at once.
- **Guard and side effect belong together, inside the transition.** Split apart,
  one runs without the other the first time someone calls it from a new place.
- **Terminal states need a stated rule.** Whether a closed record can reopen is
  a decision; leaving it unstated means the answer depends on which endpoint you
  happen to call.
