# Factory

**Reach for it when** creating an object needs several coordinated steps —
related rows, a snapshot of configuration, defaults resolved from elsewhere — and
getting one wrong leaves a half-built record.

**Not when** it is one constructor call with arguments.

## The shape

One entry point returning a complete, valid object, with the whole creation in a
single transaction so a failure leaves nothing behind.

## The traps

- **Snapshot what must not change later.** If a record has to be judged by the
  configuration in force when it was created, the factory copies that
  configuration onto the record. Reading the live config later silently rewrites
  history the day someone edits it.
- **Validate before the first write, not between writes.** A factory that saves
  a parent and then fails validating a child has already committed the parent
  unless the whole thing is one transaction.
- **Do not let it grow a branch per caller.** Two callers wanting different
  results means two factories, or one with an explicit mode argument — not a
  chain testing where the call came from.
