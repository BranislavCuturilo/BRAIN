# Django migrations

Extends `stack-django`.

## Adding a required column to a populated table: three migrations

Never one. The single-migration version either fails on existing rows or silently
stamps them with a wrong default.

1. **Add it nullable**, no default.
2. **`RunPython` backfill** from wherever the real value comes from (usually the
   parent row), with a no-op `reverse_code`.
3. **Flip to `NOT NULL`** and add the index.

Splitting them also means a long backfill does not hold a schema lock.

## Data migrations

- **Use the historical model** — `apps.get_model('app', 'Model')` — never the
  imported one. The imported class is today's code; the migration must run
  against the schema as it was, and it will silently reference columns that do
  not exist yet on a fresh database.
- Historical models have **no custom methods, no `save()` override, no signals**.
  Anything your autofill or `clean()` normally does, the migration must do
  explicitly.
- **Always supply a reverse** (`migrations.RunPython.noop` is a valid answer).
  Without one the migration is irreversible and blocks every rollback below it.
- **A reverse deletes only what it can PROVE its own forward created, and its
  docstring names what does not come back.** `forwards` usually records nothing,
  so `Thing.objects.all().delete()` in `backwards` destroys every row a seed, an
  admin or a later tenant added — plus the columns only they filled in. Two
  narrowings are free and both are provable: skip rows the forward's own
  heuristic could not have matched (it keyed on a slug — so a non-matching slug
  was never touched), and skip a flag that was already set before the forward
  ran. Where nothing is provable, **keep the rows and say so**: a detached row
  is inert and a re-apply matches it again, so keeping costs nothing while
  guessing costs data. A `backwards` that canonicalises (folding several
  spellings onto one) is lossy by construction and can never be an inverse —
  write that in the docstring rather than claiming "not lossy either way".
- **Rows inserted by a data migration do not survive a test flush.** A test that
  depends on seeded reference data must create it in setup (`craft-testing`).

## DDL in a data migration on MySQL/MariaDB: `atomic = False`, or the site goes down

A `RunPython`/`RunSQL` step that runs DDL (`ALTER TABLE … CONVERT TO CHARACTER
SET`, `CREATE INDEX`, …) raises `TransactionManagementError: Executing DDL
statements while in a transaction on databases that can't perform a rollback is
prohibited` on MySQL — Django wraps each migration in a transaction and MySQL
cannot roll DDL back. Set `atomic = False` on the `Migration` class (the
statement is then not rolled back on failure — keep it idempotent).

> Measured 2026-08-19 on helpdesk.example.com: the container entrypoint runs
> `migrate` at boot, the migration raised, the container restart-looped and the
> site returned 502 for ~90 minutes. The SQLite test suite cannot catch this
> (the migration is vendor-guarded to MySQL) — so on a repo whose deploy runs
> `migrate` automatically, a MySQL-only migration needs a MySQL dry run or
> `atomic = False` by default.

## Before pushing

`python manage.py makemigrations --check --dry-run`

Non-zero means a model change has no migration. This is the cheapest deploy
failure to prevent and one of the most annoying to diagnose afterwards, because
the symptom appears on the server as a column that does not exist.

## Constraints and indexes

- Adding a constraint to a table with violating rows fails at deploy time, on
  production data you cannot see locally. Backfill or clean first, in an earlier
  migration.
- On MySQL/MariaDB a `UniqueConstraint(condition=...)` is **silently not
  created** (`models.W036`). The migration succeeds and the guarantee does not
  exist — see `references/models.md`.
- Renaming a field Django cannot detect automatically becomes drop + add, which
  is data loss. Check the generated operations before committing; if it guessed
  wrong, write `RenameField` by hand.

## Deleting a model: what `makemigrations` leaves out

The generated file looks complete and fails on MySQL. Both of these were
measured dropping two small code-list models; each raised from a statement
that names neither the constraint nor the table actually at fault, and DDL on
MySQL/MariaDB is not transactional, so a failure half-way leaves a schema no
migration describes and no `migrate --backwards` can fix.

**A named `Meta.constraints` entry is not dropped for you.** Django removes a
`unique_together` before it removes a field, and does not do the same for a
named `UniqueConstraint`. When it emits `RemoveField` for the model's own
columns BEFORE `DeleteModel` — which it does, to break foreign-key cycles —
the column is still half of that constraint, and the index rebuild raises:

```
(1072, "Key column 'organization_id' doesn't exist in table")
```

Put `RemoveConstraint` / `RemoveIndex` for every named entry at the TOP of the
operations list, ahead of the field removals.

**Depend on every migration that removed a relation INTO the model, in
whatever app that relation lived.** The autodetector only sees the app it is
generating for, so a many-to-many in another app whose through-table still
holds a foreign key into your table is invisible to it — and Django is then
free to order your `DeleteModel` first:

```
(1451, 'Cannot delete or update a parent row: a foreign key constraint fails')
```

The message names no table at all. Add the other app's migration to
`dependencies` by hand.

**Do this on a throwaway schema first, and treat the first failure as the
point of the exercise.** A separate test database earns its whole cost here:
both failures above surfaced there, and recovering meant dropping every table
and migrating from scratch — twice. That is a cheap afternoon on a schema
nobody looks at and an outage on one people use.

## Merge conflicts

Two branches each adding `0007_*` produce two leaf nodes. `makemigrations
--merge` creates the merge node — but read what it produced. If both branches
touched the same table, the merge is syntactically valid and semantically wrong,
and nothing will tell you.
