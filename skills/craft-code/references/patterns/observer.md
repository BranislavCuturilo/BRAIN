# Observer / signals

**Reach for it when** something outside a module must react to a change inside
it and the module must not know the reactor exists — an audit log, a
denormalised counter, a notification.

**Not when** the reaction is part of the operation. If the write is meaningless
without the follow-up, that is a service with two steps, not an event. Making it
an event only hides the ordering.

## The traps — this pattern has the worst ones

- **A receiver that raises breaks the thing that fired the event.** Decide
  explicitly, per receiver, whether it is best-effort (catch, log with the
  traceback, continue) or load-bearing — and if it is load-bearing, it should not
  be a receiver at all.
- **Receivers must be registered exactly once.** Registered twice, everything
  fires twice, and that shows up as duplicate emails and doubled counters long
  after the change that caused it.
- **Never fire an event inside a transaction that can still roll back** without
  deferring it to commit. Otherwise the notification goes out for a write that
  never happened — and unlike the database, the email does not roll back.
- **Many-to-many changes do not fire the usual validation.** An invariant across
  a many-to-many needs its own receiver on the change signal; checking it on save
  does not enforce it there.
- **Signals make control flow invisible.** Three receivers on one save is a
  program nobody can read top to bottom. Past a small number, prefer an explicit
  call in a service.
