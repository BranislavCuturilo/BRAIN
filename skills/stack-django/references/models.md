# Django models

Extends `stack-django`. Isolation rules are in `craft-security`; this is the
Django mechanics of applying them.

## Validation order — the trap that costs the most time

`Model.full_clean()` runs in this order:

1. `clean_fields()` — per-field validation, **including "this field cannot be
   null" on a required FK**
2. `clean()` — your model-wide hook
3. `validate_unique()`
4. `validate_constraints()`

So **anything you auto-fill inside `clean()` is filled too late** if a field-level
rule would already have rejected the row. The classic case is a denormalized
`organization` FK populated from a parent: the code works under a `.save()`-only
test and fails the instant a form, admin page or serializer calls `full_clean()`,
with a confusing `{'organization': ['This field cannot be null.']}`.

**Pattern — override `full_clean()`, not just `save()`:**

```python
def full_clean(self, *args, **kwargs):
    if self.organization_id is None and self.parent_id:
        self.organization_id = self.parent.organization_id
    super().full_clean(*args, **kwargs)

def save(self, *args, **kwargs):
    if self.organization_id is None and self.parent_id:
        self.organization_id = self.parent.organization_id
    super().save(*args, **kwargs)

def clean(self):
    super().clean()
    if self.organization_id is None:
        return                       # form path stamps it; service path always sets it
    if self.parent and self.parent.organization_id != self.organization_id:
        raise ValidationError({'organization': 'Does not match its parent.'})
```

All three halves. The `clean()` guard alone silently skips validation on paths
that never re-clean; the `save()` autofill alone breaks every validating path.

**`clean()` must check *every* scoped FK the model carries**, not only the one
that caused the first incident. When you add a scoped FK to a model that already
has a `clean()`, add it to the check in the same edit.

## Validation must not write

`clean()` / `full_clean()` may normalize the instance in memory. It may **never
create or update another row** — a lookup row, a code-list entry, a counter, an
"ensure it exists" parent. `full_clean()` runs on unsaved instances, on every
form that then turns out invalid, from admin and serializers, and often a second
time in a view that calls it explicitly. Anything it wrote survives a save that
never happens, and the orphan looks like data somebody entered.

Put the write on the **save path, inside `transaction.atomic()` with the row it
belongs to**, so both land or neither does:

```python
def save(self, *args, **kwargs):
    if not self.typed_name:                     # nothing to resolve
        return super().save(*args, **kwargs)
    with transaction.atomic():
        self.ref = CodeList.resolve_or_create(self.organization_id,
                                              self.typed_name)
        self.typed_name = ''
        return super().save(*args, **kwargs)
```

Two things that bite here:

- **`update_fields` silently drops the result.** A partial save that names the
  typed field but not the FK creates the row and persists nothing pointing at
  it. Either skip the work when `update_fields` does not name the input field,
  or widen `update_fields` to include what the resolution changed — decide both
  branches, do not leave it implicit.
- **The resolver's insert IS the uniqueness claim** (`craft-security` → races):
  match first, then `create()` inside its own nested `atomic()` and catch
  `IntegrityError` by re-reading, because the loser of the race must get the
  winner's row rather than a 500 — and an `IntegrityError` poisons the
  transaction it fires in, so without that inner savepoint every later statement
  raises `TransactionManagementError` instead of retrying.

## Many-to-many validation does not run in `clean()`

`clean()` never fires for m2m operations. Validate with an `m2m_changed`
receiver on `pre_add`:

```python
@receiver(m2m_changed, sender=Audit.team_members.through)
def validate_team_member_tenant(sender, instance, action, pk_set, **kwargs):
    if action == 'pre_add':
        assert_users_in_organization(pk_set, instance.organization, 'team_members')
```

Register it in `AppConfig.ready()`. Add it in the same change as the field — the
migration alone leaves the relation unguarded.

**A model added without a `clean()` at all is invisible to a "does `clean()`
validate every FK?" grep.** Periodically enumerate every scoped model, not just
the ones that already have one.

## Constraints

- **`Meta.constraints = [UniqueConstraint(fields=[...], name='...')]`**, never
  `unique_together`. Named constraints appear in the error message, are visible
  to schema tooling, and are the only form that supports conditions.
- **Scoped uniqueness is always `(organization, …)`.** A bare `unique=True` on a
  slug is a cross-tenant collision waiting to happen.
- **MySQL/MariaDB silently ignore conditional unique constraints.**
  `UniqueConstraint(..., condition=Q(...))` emits `models.W036` and is never
  created — it works only on PostgreSQL. Never rely on one for correctness there;
  enforce it in the service layer with `get_or_create` or `select_for_update`,
  and gate any test asserting an `IntegrityError` on `connection.vendor`.
- **`full_clean()` skips unique validation when a participating field is NULL**
  (mirroring SQL's `NULL != NULL`). A nullable column in a unique tuple needs an
  explicit filter-first dedup in the service.

## Choices, indexes, files

- **`TextChoices` / `IntegerChoices`**, defined once and imported — never a bare
  list of tuples duplicated across apps.
- Only *workflow* states the code branches on belong in `choices`. Business
  taxonomy the customer would rename is a table (`craft-code`).
- **`Meta.indexes` leading with the scope column** on every scoped model. This is
  the practical reason the denormalized FK exists.
- **`FileField.upload_to` is a callable** producing a scope-prefixed path, plus
  the extension validator (`craft-security` → Files). A migration that changes
  `upload_to` does **not** move existing files; their old paths stay in the
  database.

## Keeping the model thin

No presentation, no email, no notification from `save()`. Badge classes and
formatted labels are template filters in `templatetags/`. A `@property` returning
a CSS class couples the schema to a rendering framework — and blocks the second
renderer (PDF, email digest, export) you will eventually need.
