# Extension without modification

Extends `craft-code`.

## No per-customer branches in shared code

`if customer == 'x'` is a promise you will fork the codebase later. Behaviour
that varies by customer is configuration: a feature flag, a per-customer
settings row, a strategy selected by key.

Before shipping a feature, ask: *would a different kind of customer need to fork
code to use this?* If yes, redesign now — retrofitting multi-tenancy into a
branched feature costs several times more.

## Business taxonomy is rows, not literals

Categories, tiers, statuses a customer would rename, colours they would rebrand,
thresholds they would tune — all rows in a table.

The only literals that stay in code are:
- workflow states the code itself branches on (`draft`, `submitted`, `locked`);
- documented synthetic sentinels, marked as reserved so nobody assigns them as
  real values ("no data yet" grey).

A sample spreadsheet from the first customer is a **spec, not a model**. The real
system is always more flexible than the first customer's version of it.

## A branch chain that dispatches becomes a registry

```python
HANDLERS = {'sla_breach': _sla_breach, 'ko_triggered': _ko_triggered}
handler = HANDLERS.get(key, _default)
```

The dictionary is then the source of truth, and adding a case is one row instead
of an edit in three places that nothing keeps in sync — the choices list, the
dispatch, and the docstring.

An unknown key should be non-fatal where that is meaningful (a trigger fired by
cron rather than by an event), and rejected at validation time where it is not.

## Dispatch on the slug, not the object

The classic failure: the key is a *related object* rather than its identifier, so
the lookup always misses and **every case silently falls through to the
default**. It looks like the default path is working; it is swallowing
everything.

```python
parser = PARSERS.get(question.answer_type, _text)          # WRONG — FK object
parser = PARSERS.get(question.answer_type.input_type, _text)  # right — slug
```

Reach for `.code` / `.slug` / `.input_type`, and pin it with a test asserting a
**non-default** case actually reaches its handler. A test that only checks the
default passes against the bug.

## Strategy selection is a registry key, never an import path

A stored string that selects a behaviour class is looked up in a dictionary. It
is never fed to a dynamic import — that is remote code execution with extra steps
(`craft-security`).
