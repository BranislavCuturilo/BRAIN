# Transactions & partial failure

Extends `craft-code`.

## More than one write for related rows → one transaction

The failure is always the same shape: the first write lands, the second raises,
and the system is left in a state the UI then reports as authoritative. A status
changed with no log entry. A parent created with no children. A notification
marked consumed for a record that was never created.

**A single write needs no wrapper.** When you add the second one, add the wrapper
in the same change — that is the moment it becomes necessary and the moment it is
cheapest to add.

**Do not wrap asynchronous side effects in the transaction that triggered them.**
A failed notification must not roll back the state change it was reporting.
Those run after commit, log their own failures, and never propagate.

## Partial success needs a savepoint per item

A `try/except` inside one outer transaction **does not work**, and the way it
fails is genuinely deceptive.

After any database error the transaction is poisoned: every later statement in it
also fails. So the loop *looks* like it is logging the bad row and continuing —
it prints warnings, it reaches the end, it reports "3 rows skipped" — while it
has actually aborted everything, including the rows it claims to have imported.

```python
for row in rows:
    try:
        with transaction.atomic():      # nested savepoint — this is the fix
            import_row(row)
    except (DataError, ValidationError) as exc:
        errors.append((row, exc))
```

**Cache corollary:** if the loop body mutates an in-memory cache mirroring rows
the savepoint just rolled back, evict the keys that item added. Otherwise the
next item references a foreign key to a row that no longer exists, and the error
appears one iteration away from its cause.

## A base method that can bail must say so

When a subclass calls `super()` and then performs its own side effect, it needs a
way to detect a short-circuit:

```python
def form_valid(self, form):                 # base
    if not self._may_create():
        self._blocked = True
        return self.form_invalid(form)
    return super().form_valid(form)

def form_valid(self, form):                 # subclass
    response = super().form_valid(form)
    if getattr(self, '_blocked', False):
        return response                      # do NOT run the side effect
    self.token.consume(self.object)
    return response
```

**Returning from the parent does not mean the parent succeeded.** Without the
flag, a blocked create still consumes a one-shot token and links to a `self.object`
that is stale or `None`.

## Loops that represent one logical change

A loop upserting N rows that together are *one* decision — a set of feature
toggles, a package application, a settings page save — must be atomic even when
each row is individually valid. Partial application of a logically-atomic change
is the defect, and it leaves the system in a state no one designed.
