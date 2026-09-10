# Fan-out — the mechanics, and the batches that recur here

Extends `ops-delegation`, which decides *whether*. This is *how*.

Mostly a main-loop skill, but no longer only one: `orchestrator` holds the
`Agent` tool too. The grant had been switched off in its own `disallowedTools`
and the restriction was mistaken for a harness limit — see `ops-delegation`.

**One difference when the launcher is itself an agent.** From the main loop a
background launch is fine: the notice comes back here. From inside a subagent
it does not — the notice goes to the top-level session, and the launcher never
sees its own units. So an orchestrating agent passes `run_in_background: false`
on every call, still all in one message, still concurrent. Measured 2026-09-10:
five units launched on the default setting, four notices delivered to the main
session, the orchestrator holding 1 of 5 and its turn already over.

## The mechanic

```
ONE assistant message
├── Agent(subagent_type: "brain:security",  prompt: "...")   ┐
├── Agent(subagent_type: "brain:qa",        prompt: "...")   ├ concurrent
└── Agent(subagent_type: "brain:reviewer",  prompt: "...")   ┘
```

Three calls in three messages is the same three agents, three times the
wall-clock, and three intermediate reports sitting in the main context. Nothing
warns you: the transcript looks the same, each agent works, and the result is
correct. It is only slower and more expensive, which is exactly the kind of
defect that survives forever.

**Name the plugin-scoped type** — `brain:scout`, not `scout`. A bare name
resolves to nothing and the spawn fails with "agent type not found".

**Write every prompt before sending any of them.** The failure mode is
launching the one you already thought of, reading its answer, and never
returning to name the rest.

## Where the second agent actually is

The question that produces a batch is not "should I delegate this?" but **"what
else is true at the same time and does not depend on this?"** Four seams, in the
order they show up here:

| Seam | The units |
|---|---|
| **dimension** | the same artefact, different questions: security / correctness / tests / performance |
| **module** | the same question, different subjects: one agent per app, per screen, per ticket |
| **discipline** | one change, different layers: backend, front end, translation, tests |
| **read vs write** | a reader for each large input, then one writer given their specs |

## Recipes

### Reviewing a change — the highest-value batch here

The score says why: an independent single agent found the `_record_fulfilment`
atomicity gap **and** a by-pk authz hole *"the whole 3-agent chain missed"*, and
on another round found a CSV export ignoring every filter *"the orchestrated arm
missed entirely"*. Independent perspectives beat a relay. So run them as
perspectives, not as a chain:

```
one message:
  brain:security         — isolation, by-pk authz, untrusted input on THIS diff
  brain:reviewer         — refute the change: where is it wrong
  brain:qa               — what is untested that would go unnoticed
  brain:appsec-reviewer  — only when the diff touches auth or data boundaries
```

Each gets the **same diff** and a different question. Do not let one read
another's findings first — agreement between agents that read each other is not
evidence.

### Triaging the queue — one agent per ticket

`triage` is N independent readings with no ordering between them; running it as
a loop is the queue in its purest form. Batch the readings, then do the
cross-ticket ordering yourself once they are all back — the ordering is the part
that genuinely cannot be parallelised, because it is about the whole queue.

```
one message: brain:ticket-reader × N   (one per active ticket)
then, in the main loop: the dependency-aware order across all of them
```

Keep the free path for bulk: `analyze_gemini.py` costs nothing and already
writes through the same locked writer. Batch Claude readers for the tickets that
are ambiguous or expensive to get wrong, not for all twenty.

### A ticket that spans layers

```
one message:
  brain:dj-service     — the write path, transaction boundaries
  brain:dj-templates   — the screen
  brain:translator     — the new strings
then: brain:qa over what came back
```

The last one is sequenced because it genuinely depends on the others' output.
Everything above it does not.

### A sweep over modules

One agent per module, never one agent walking every module. But read the warning
below before reaching for this.

### Read before write

When the input is large, a reader's context is thrown away and the writer starts
from thirty lines instead of fifteen hundred: `dj-model-reader`,
`dj-view-reader`, `repo-reader`, `scout`. Batch the readers; sequence the writer.

## What must NOT be fanned out

- **Anything whose parts must agree with each other.** A model with its
  migration, manager and tests is `dj-model-slice`, one agent — four agents
  produce four consistent halves of two different designs. A screen's view, URL,
  template and test is `dj-screen-slice` for the same reason: the names have to
  match.
- **Work finishable in a handful of tool calls.** The overhead is paid up front.
- **A repeated identical operation over many items.** That is a workflow, not an
  improvised fan-out — check `workflows/` first (`ops-workflows`).

> The cautionary measurement, from this brain's own score: the `authz-sweep`
> workflow spent **1.28M tokens across 14 agents in 12 minutes and produced 0
> confirmed findings** — 56 unverified artifacts of a broken comparison. Width
> is not the goal. A batch whose members cannot each be checked is a batch that
> manufactures work.

**So: cap it, and verify what comes back.** Every finding a batch returns is a
claim, and `references/briefing.md` covers who checks it. Three agents you read
properly beat fourteen you skim.
