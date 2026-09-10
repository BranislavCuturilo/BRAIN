# Template method

**Reach for it when** several flows share a fixed sequence and differ only at
named points. Your framework's class-based views are already this pattern —
most of the time the right move is extending theirs, not writing your own.

**Not when** the subclasses override most of the steps. That is not a shared
skeleton; it is two algorithms sharing a name.

## The traps

- **A base method that can bail must say so.** If the skeleton can stop early —
  a guard failing, a validation returning a response — every override has to know
  whether calling the parent means "continue" or "we are done". Left
  undocumented, half the subclasses call the parent and then keep working on an
  aborted request.
- **Do not add a hook with no caller.** An override point nobody overrides is
  speculative structure; add it when the second case actually exists.
- **Overriding a step to do something it was not for** — using a validation hook
  to write to the database — makes the skeleton unreadable and the ordering
  accidental.
