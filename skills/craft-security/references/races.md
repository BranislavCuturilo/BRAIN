# Races & concurrency

Extends `craft-security`.

## Claim the row; do not check then act

An idempotency, replay or one-shot gate **inserts and catches the uniqueness
violation**:

```python
try:
    Nonce.objects.create(value=nonce)      # this IS the gate
except IntegrityError:
    return reject('replay')
```

`exists()`-then-`create()` is a time-of-check race: two concurrent requests both
pass the check and both proceed. Under normal load you never see it; under a
retry storm or a double-clicked button you see it constantly, and the symptom is
duplicated work rather than an error.

An early `exists()` is fine as a **fast reject**, but it is not the gate.

The same shape covers: post-once ledgers, awaiting-response tasks, "send the
welcome email once", and anything a user can trigger twice by double-clicking.

## Read-modify-write needs a lock

Computing a delta from a current balance and then writing it back is a race
unless the read is locked inside the transaction. Re-read the value **at the
moment of writing**, not when the page was rendered — otherwise activity between
render and submit is silently overwritten.

## Some databases ignore conditional unique constraints

A partial unique (`condition=Q(...)`) is **silently not created** on MySQL and
MariaDB — the migration succeeds, a warning is emitted, and the guarantee does
not exist.

So: never rely on a conditional unique for correctness there. The service layer
is the real guard (`get_or_create`, a locked read). And gate any test asserting a
database-level error on the engine that actually enforces it, or the test passes
for the wrong reason on one engine and fails confusingly on the other.

Related: unique validation is **skipped when a participating column is NULL**
(SQL's `NULL != NULL`). A nullable column in a unique tuple needs an explicit
filter-first dedup in the service.

## Self-referential traversal carries a cycle guard

`ancestors()` / `descendants()` on a self-FK model keeps a `seen` set.

A validation-time cycle check only fires on validated writes. A cycle introduced
by a shell, a raw SQL fix, a data migration or a restored backup turns every
traversal into an infinite loop — and these run on permission resolution and
escalation routing, so it is a denial-of-service primitive, not a display bug.

Never trust the database to be acyclic because your `clean()` says it should be.
