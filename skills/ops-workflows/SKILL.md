---
name: ops-workflows
description: >
  What a repeated pattern should become — a script, a skill, or a multi-agent
  workflow — and how to build the third. Load when noticing the same sequence
  done twice, when asked to automate something, or before writing a workflow.
when_to_use: >
  "we keep doing this", "automate this", "make a workflow for it", the same steps
  repeated across tickets or projects, a task that is the same operation over
  many files.
---

# Workflows and what else a pattern can become

## Three mechanisms — picking wrong is the usual failure

| The repeated thing is… | It becomes | Lives in |
|---|---|---|
| a computation with an **exact** answer | a **script** | `scripts/<area>/` |
| a sequence of **judgements one agent makes** | a **skill** | `skills/<name>/` |
| the same step over **N items**, or several **independent contexts** | a **workflow** | `workflows/*.js` |

**The decision, in order:**

1. **Can a script answer it exactly?** Then it must be a script. A script is
   free, instant, and cannot be wrong. An agent counting rows, parsing JSON or
   summing a column is paying tokens to be less reliable than `sum()`.
2. **Does it need judgement but fit in one context?** A skill. Instructions the
   agent follows, loaded on demand, composable with everything else.
3. **Does it need several independent contexts, or the same judgement repeated
   over many items?** A workflow.

**Anti-patterns, all three directions:**

- *A workflow that is really a skill* — spawning agents for work one context
  handles. Pays setup cost N times for one result.
- *A skill that is really a script* — asking a model to compute what `wc -l`
  computes. Expensive, slower, and occasionally wrong in a way nobody checks.
- *A script that is really a skill* — encoding judgement as `if/elif`. Rigid,
  and it breaks on the first case the author did not imagine.

Most "let's automate this" requests are the second row, not the third.

## Writing a workflow

Workflows live in `workflows/*.js` in the brain and are invoked by path:

```
Workflow({ scriptPath: "~/.claude/skills/brain/workflows/<name>.js", args: {...} })
```

They are plain JavaScript with `agent()`, `parallel()`, `pipeline()`, `phase()`
and `log()`. The value is **deterministic control flow over model-driven steps**:
the loop, the fan-out and the ordering are code, so they behave the same every
run; only the judgement inside each step is the model.

**Default to `pipeline()`.** Item A can be in stage 3 while item B is still in
stage 1, so wall-clock is the slowest single chain rather than the sum of
slowest-per-stage. Reach for `parallel()` — a barrier — only when a stage
genuinely needs *all* of the previous stage's results together: deduplicating
across the whole set, an early exit on zero, or a comparison that references the
other findings.

"I need to flatten the results first" is **not** a reason for a barrier. Do the
transform inside a pipeline stage.

## The shape that pays for itself: read → brief → write

Separate **reading** from **writing**, always, when the input is large:

```js
pipeline(models,
  m => agent(`Read ${m} and return its structure`, {schema: MODEL_SPEC}),
  spec => agent(`Write the list view for ${spec.name}`, {model: 'sonnet'}))
```

Three gains, and the first is the big one:

- **The reader's context is thrown away.** It reads 1,500 lines; the writer
  receives 30 lines of spec. Without the split, the writer carries the whole file
  for the rest of its run.
- **The brief is a contract** — reviewable, and reusable by several writers (a
  list view, a form, a test) without re-reading anything.
- **The reader can be cheap.** Extracting structure is mechanical; deciding what
  to build is not.

Use `schema:` on the reader so it returns a validated object rather than prose
you then have to parse.

## Judge in code -- but only on normalised inputs

Deciding in JavaScript rather than in an agent is right when the comparison is
exact. It stops being exact the moment the input is prose.

> A sweep classified endpoints by substring-matching each one's *quoted
> authorization text* against the name of the list view's helper. But a
> correctly written by-pk endpoint calls a **different** function --
> `user_can_view`, not `visible_stocktakes` -- so the strings never overlapped
> and **all 74 endpoints came back suspect**. The cap then truncated at 12, and
> a real defect could sit at position 40 and never be looked at. The script was
> honest about the cap; the signal underneath it was noise.

The fix was not more clever matching. It was to **move the classification to the
agent that already has the code open** -- it can resolve a mixin, a parent class
or a dispatch override, which no substring can -- and leave the code to do what
code is good at: ranking and capping a set that is already labelled.

**The test:** would two people reading the same input produce the same label? If
yes, judge in code. If it needs reading comprehension, let the reader label it
and judge the label.

**And when a stage adds a field to an object it received, check the name is
free.** Spreading `{...s, verdict: v}` over an object that already had a
`verdict` silently replaced the reader's classification with the verifier's
result -- no error, just a wrong value downstream.

## The workflow is a graph — four rules that come with saying so

A workflow is a task graph: nodes are agents, edges are "this must finish before
that starts". Naming it that way makes four failure modes visible that
`pipeline()` and `parallel()` alone do not.

**Delete fake edges.** An arrow is real only when *work actually flows along it*
— stage B genuinely needs stage A's output. Every other edge is latency you
chose. A `parallel()` between stages is a barrier, and a barrier with no
cross-item dependency is the commonest fake edge there is: every item waits for
the slowest one and buys nothing.

**Sequential stages multiply their failure rates; parallel branches do not.**
This is the one worth internalising. Five chained stages at 80% each leave you
33% end to end — and *the chain hides which link failed*, because the last stage
still returns something. Five parallel branches at 80% leave four good results
and one identifiable failure. So **prefer width over depth**, and where depth is
unavoidable, make every stage state what it returns, so a bad link is visible
instead of laundered by the next one.

> Measured here: a three-stage sweep spent 1.28M tokens and confirmed nothing.
> Stage 2's comparison was structurally broken, but stage 3 dutifully verified
> its output and produced 56 confident artifacts. The depth is what let a broken
> middle look like a result.

**The diamond, not the chain.** When work must be verified: fan out, verify each
branch **in a context separate from the one that produced it**, then merge under
a single owner who reconciles. A verifier sharing the producer's context confirms
the producer's assumptions, which is agreement, not verification.

**Put the human gate where reversal is most expensive.** Not at the end, where
everything is already written; not at every step, where it becomes a
click-through nobody reads. One gate, at the last point before the irreversible
part — the migration, the push, the send, the delete.

## Rules

- **A workflow is written once a pattern has happened at least twice.** The first
  time is a task; the second is evidence. Writing one from a single occurrence
  encodes an assumption, not a pattern.
- **Every stage states what it returns.** A stage returning "a summary" produces a
  different shape each run and the next stage cannot rely on it.
- **Say what was skipped.** A workflow that caps at top-N or drops failures must
  `log()` it — silent truncation reads as full coverage.
- **Verification is its own stage, at a higher grade**, never the same agent that
  produced the work.
- Workflows are checked in and versioned like anything else. One that stops
  matching reality is deleted, not left to mislead.

## Recognising a new one

The signal is doing the same sequence a second time **and reaching for the same
notes**. When that happens, ask which of the three mechanisms it is — the answer
is a script more often than people expect — and write it before the third time.

`archivist`'s journal review is where this usually surfaces, because a repeated
sequence looks like a repeated decision.
