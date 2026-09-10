# Magic values & single implementations

Extends `craft-code`.

## Promote on the second occurrence

A repeated status tuple, permission list, threshold or role slug becomes a named
constant the moment it appears twice. Not the third time — the second, because
the second is when they begin to drift and the drift is invisible.

Put it where the concept lives: statuses on the model, permission sets in the
permissions module, role slugs in one registry. Never a literal reached for
directly in a second file.

## Never grow a free-form config blob into a schema

```python
profile.config.get('partial_percentage', 0.5)     # undiscoverable
```

No admin affordance, no visible default, no check that two readers use the same
fallback — and they will not. The value drifts between a service and a view, and
nothing tells you.

**Read twice → promote it to a declared, typed, defaulted field.** A config blob
survives only as a bag for knobs that have not yet earned a column.

## One implementation per calculation

When a preview and the real thing compute differently, the preview is teaching
the user a lie and they will tune settings against it. Same for a report and the
screen it summarises, a client-side estimate and the server total.

Share the computation; differ only in where the inputs come from (a POST dict vs
a persisted row). If the two genuinely cannot share, say so in a comment at both
sites and add a test asserting they agree on a known case.

Guard divisors against a configured zero (`or 5`) — a customer *will* save 0 in a
field you assumed was positive.

## Two collections that must agree are one collection plus a derivation

An allow-list of extensions beside a map of extension → content type; a tuple of
statuses beside a dict of status → label. Adding an entry to one is a `KeyError`
in the other's reader — a 500 on input the first list declares valid, reachable
by anyone who can post that input.

Keep the richer structure and **derive** the rest from it (`tuple(MAP)`,
`[k for k, v in MAP.items() if …]`), then assert the derivation in a test that
loops every member. Two literals that must be edited together will be edited
apart.

## Every state the UI can show must round-trip through the store

Count the states the screen can be in, then count the values the column, the
enum or the JSON key can hold. **If the UI has more, the extra ones are lost on
reload — and the fix is in the schema, never in the UI.**

The classic shape is a boolean standing in for three states. A review panel had
*approved / rejected / not yet looked at*; the record stored `approved: bool`. So
"reject" wrote `false` — the same value as "nobody has looked at this" — and the
operator came back to a queue that had forgotten every refusal and asked them to
review it again. Nothing errored, nothing logged, and the panel was correct on
screen right up to the reload.

It hides well because it is only visible **across** a round trip: the click
paints the right thing, the state lives in a variable, and every test that
exercises the handler in one pass agrees. So test it the way it breaks — write,
re-read through the real reader, assert the state came back.

Two rules make the fix cheap:

- **Add the value, do not repurpose the flag.** A third state expressed as
  "`approved: false` plus `seen_at` is set" is a decoding rule that lives in
  whichever reader remembers it. Store the decision itself
  (`decision: "approved" | "rejected" | ""`), and keep the old boolean beside it
  as the same fact in the older shape when other code reads it.
- **Prefer a migration you do not have to run.** If the old values already
  determine the new ones — `approved: true` IS "approved", everything else IS
  "not reviewed" — put that in the ONE reader and the derivation *is* the
  migration: old records read correctly, new writes are explicit, and there is no
  backfill to get wrong.

The general form is wider than booleans: a UI that offers four sort orders and a
store that holds two, an editor with a "partially filled" state saved as empty, a
draft/sent/failed workflow persisted as `sent: bool`. **Ask it of every screen
you add a state to, before writing the handler.**

## Build references, never concatenate them

URLs, paths, identifiers: use the framework's builder. A rename then becomes one
edit instead of a grep, and a typo becomes an error instead of a 404 in
production.

## Signing and hashing payloads

**A signing payload never contains `None`.** Coerce nullable numerics to an
explicit zero *before* building the string — `None` renders as `"None"` and
produces a hash structurally different from one taken after the same fields hold
their default zero. Tamper evidence only holds if equal state means equal hash.

Use `is not None` rather than `or 0`: `or 0` also flattens a legitimate
`Decimal('0.00')` to `0`, which is a different representation and a different
hash. Be explicit about which zero you mean.
