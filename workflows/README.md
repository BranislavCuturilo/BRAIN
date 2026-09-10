# workflows/ — for the reader

*This file is for you, not for Claude.*

## What a workflow is

A JavaScript file that orchestrates several agents with **deterministic control
flow**: the loop, the fan-out and the ordering are code and behave identically
every run; only the judgement inside each step is a model.

Run one by path:

```
Workflow({ scriptPath: "~/.claude/skills/brain/workflows/crud-scaffold.js",
           args: { app: "stocktaking", model: "RegistryItem" } })
```

They live here rather than in `.claude/workflows/` so that the whole brain stays
one clone. `/workflows` shows progress while one runs.

## When a workflow is the right answer — and when it is not

A repeated pattern becomes **one of three things**, and picking wrong is the
usual mistake (`/brain:ops-workflows`):

| The repeated thing is | It becomes |
|---|---|
| a computation with an exact answer | a **script** — free, instant, cannot be wrong |
| judgements one agent makes | a **skill** |
| the same step over N items, or several independent contexts | a **workflow** |

Most "let's automate this" requests are the middle row. A workflow that is really
a skill spawns agents for work one context handles and pays the setup cost N
times for one result.

## What is here

| File | What it does |
|---|---|
| `crud-scaffold.js` | Reads a model once, maps the app's conventions once, then five writers build list/detail/create/update/delete in parallel, and a reviewer a grade up checks they agree. |
| `authz-sweep.js` | Per app: which visibility helper is authoritative and every by-pk endpoint. Judges in plain code, then `security` tries to **refute** each finding before it is reported. |

## Reading one

Two things in these files are deliberate and worth copying:

**`pipeline()` over `parallel()`.** A pipeline lets item A reach stage 3 while
item B is still in stage 1, so the wall-clock is the slowest single chain rather
than the sum of slowest-per-stage. `parallel()` is a barrier — use it only when a
stage genuinely needs all previous results together.

**Judge in code, not in an agent.** `authz-sweep` compares strings in JavaScript
to decide which endpoints are suspicious. A model would be slower, cost tokens,
and occasionally be wrong about a string comparison.

## Writing one

Write it after the pattern has happened **twice**. The first time is a task; the
second is evidence. Then: `export const meta` first (a pure literal), every stage
states what it returns, use `schema:` so a reader hands back a validated object
instead of prose, and `log()` anything you cap or drop — silent truncation reads
as full coverage.

`Date.now()` and `Math.random()` are unavailable inside a workflow; pass
timestamps through `args`.
