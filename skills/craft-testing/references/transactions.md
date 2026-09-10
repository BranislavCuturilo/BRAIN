# Rolled-back tests, and the write that poisons the rest

Extends `craft-testing`. Read when a suite wraps each test in a transaction, and whenever a test asserts that a write FAILS.

The traceback names whatever ran next, so it blames an innocent line and usually looks like teardown is broken.

## In a rolled-back test, a FAILED write poisons everything after it

Wrapping each test in a transaction and rolling it back is the single biggest
speed-up available on a suite that otherwise truncates. The cost is one rule,
and it is not the rule people expect.

**Any database operation that FAILS marks the connection as needing a rollback.
Every query after it — in the test, in `tearDown`, anywhere — then raises**
(`TransactionManagementError` on Django, "current transaction is aborted" on
raw PostgreSQL). The traceback names whatever ran next, so it blames an
innocent line and usually looks like teardown is broken.

So an expected failure must be contained:

```python
with self.assertRaises(SomeError):
    with transaction.atomic():          # the savepoint that gets unwound
        obj.save()
```

Two things make this easy to under-scope:

- **The exception class tells you nothing.** Grepping for `IntegrityError`
  finds a fraction of them. A validator firing from an ORM hook raises
  `ValidationError`, and the transaction is just as poisoned. **Search for the
  failed WRITE, not for the exception** — every `assertRaises` with a
  `save`/`create`/`add`/`delete` under it is a candidate.
- **Some framework internals give you no savepoint to unwind to.** Django's
  m2m `add()` runs its hooks inside `transaction.atomic(savepoint=False)`, so a
  receiver that raises marks the OUTER transaction for rollback with nothing to
  restore. Those are not "at risk" — they fail every time.

And the flip side, so the fix does not become cargo cult: a `ValidationError`
raised by validation that runs BEFORE any write never touched the database, and
wrapping it adds noise. Check which one you have.
