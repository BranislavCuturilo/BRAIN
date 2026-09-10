# Django querysets

Extends `stack-django`. Scope filtering is in `craft-security` — this is about
correctness and cost.

## Aggregate in one query, not N

```python
# wrong — one query per status
{s: qs.filter(status=s).count() for s in STATUSES}

# right
dict(qs.values_list('status').annotate(n=Count('id')))
```

Same for "latest N per related object": one `Subquery` or window annotation,
never a Python loop issuing a query per row.

```python
latest = Audit.objects.filter(location=OuterRef('pk')).order_by('-created_at')
locations.annotate(last_score=Subquery(latest.values('score')[:1]))
```

## N+1

- `select_related` for forward FK / one-to-one that the template reads.
- `prefetch_related` for reverse FK and many-to-many.
- **Never `for x in qs: x.related.method()` without a prefetch.** It looks like
  one line of Python and it is one query per row.
- A property on the model that walks a relation is an N+1 hidden behind a dot.
  When a template iterates over rows and reads such a property, the prefetch has
  to exist for the relation the property uses, not just the one you can see.
- **The fix for that N+1 is the queryset, never a second cheaper copy of the
  property.** A helper that reproduces "most of" a shared display property while
  skipping the expensive branch is a second definition of the concept, and it
  disagrees with the original exactly when that branch would have mattered —
  silently, on one screen, for the same row. The comment justifying it always
  reads "identical result today, cheaper once the tree exists", which concedes
  that the two answers differ and asserts the data will never get there.
  Delete the copy, call the property, and add the `select_related` its relations
  need. Measure both with `CaptureQueriesContext` before accepting the trade:
  the honest version is usually *faster*, because the shortcut still lazy-loaded
  one FK per row while the `select_related` version loads none. (Confirmed:
  34 map markers went 35 queries → 1 by deleting the shortcut.)

## Filtering correctness

- **A transitive filter scopes its inner queryset**
  (`region__in=allowed.filter(organization=org)`), never bare `region__in=allowed`
  (`craft-security`).
- `.filter(a=1).filter(b=2)` and `.filter(a=1, b=2)` **differ across a
  multi-valued relation**: chained filters may match different related rows, a
  single filter must match one. This is a real behaviour difference, not style.
- `.distinct()` after a join that fans out — and be aware it changes what
  `.count()` means.
- `.exists()` instead of `.count()` for a boolean test; `.count()` instead of
  `len(qs)` when you do not need the rows.

## `Trunc*` on a DateTimeField, under MySQL

With `USE_TZ=True` and a named `TIME_ZONE`, `TruncDate`/`TruncMonth`/etc. on a
**DateTimeField** compile to `DATE(CONVERT_TZ(col, 'UTC', '<zone>'))`. MySQL's
`CONVERT_TZ` with a named zone returns **NULL** unless the server's
`mysql.time_zone*` tables are loaded — a DBA step shared/cPanel hosting usually
skips, and the app's DB user may not even be able to check
(`SELECT command denied on mysql.time_zone`). Nothing raises: every row's
truncated value silently becomes NULL, so an `ORDER BY` on it sorts everything
into the NULL bucket, a `GROUP BY` collapses all rows into one, and a
`Coalesce(other, TruncDate(dt))` yields NULL for exactly the rows it was
written to rescue — the fallback it falls through TO is itself NULL.

`Trunc*` on a plain **DateField** is unaffected — no zone conversion is
emitted (`DATE_FORMAT`, not `CONVERT_TZ`) — so do not generalize this into
avoiding `Trunc*` altogether.

Fix: `Cast('created_at', DateField())` (no zone conversion — the result is the
UTC date, state that trade-off), truncate in Python, or load the server's
timezone tables.

Caught by a test that asserted the *ordering*, not just that the query
returned rows — a query silently returning wrong-but-plausible data needs an
assertion on the actual values/order to fail (`craft-testing`).

## Writes

- `update()` and `bulk_create()` **skip `save()`, `full_clean()` and signals.**
  That is exactly what you want for a large mechanical write, and exactly what
  makes them dangerous on a model whose invariants live in `clean()` or whose
  side effects live in a receiver. Choose deliberately, and note the choice.
- `select_for_update()` inside a transaction when reading a value you are about
  to write based on — otherwise it is a read-modify-write race.
- `get_or_create` is the service-layer substitute for a uniqueness guarantee the
  database will not enforce (see the conditional-constraint note in
  `references/models.md`).
