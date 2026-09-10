---
name: ops-models
description: >
  Which model and which effort level to spend on which kind of task, and when to
  escalate. Load BEFORE spawning a subagent or a workflow, when asked to reduce
  token/usage burn, when a session feels slow or expensive, or when a cheap model
  returned weak output and you must decide whether to retry harder. Covers the
  model tiers, the effort ladder, the false economies, and the escalation rule.
when_to_use: >
  "which model for this", "trosim previse tokena", "usage limit", "make this
  cheaper/faster", "should I use haiku here", picking model/effort for an Agent
  or Workflow call, deciding whether to re-run a weak result on a stronger model.
---

# Model & effort policy

Two dials, not one: **which model** and **how much effort**. Most waste comes
from moving the wrong dial.

## What can actually be controlled

| Dial | Who sets it |
|---|---|
| Main session model | **The user only**, via `/model`. Claude cannot switch it — it can only recommend, and say why. |
| Main session effort | The user, via settings / `/config` — and capped from above by the `maxEffortLevel` setting (2.1.267), top-level or per model under `modelSettings`, which binds every provider including Bedrock, Vertex and Foundry. A lower level can still be chosen; a higher one cannot. |
| **Subagent model + effort** | **Claude, per call** — `Agent(model:)`, agent-file `model:`/`effort:` frontmatter, `agent(prompt, {model, effort})` in a workflow. This is where the real leverage is. |

So the policy below is *advice* for the main loop and *binding* for everything
delegated.

## The tiers

Verify pricing with the `claude-api` skill before quoting numbers — the table
below is a cached snapshot, and rates move.

| Tier | Model | Use for | Do **not** use for |
|---|---|---|---|
| Mechanical | `haiku` | finding files, listing call sites, log/CSV munging, renames, "does string X appear anywhere", formatting | anything with a judgement call |
| Routine | `sonnet` | reading and summarising a ticket, single-file edits with a clear spec, writing tests for existing behaviour, drafting docs, translation | security, data-model, or cross-cutting decisions |
| Hard | `opus` | architecture, planning, multi-file features, migrations, security review, anything touching auth/isolation/money, debugging something that already resisted one attempt | trivial mechanical work — waste with no upside |
| Frontier | `fable` | only on explicit request, or a genuinely unsolved long-horizon problem | routine work; the price premium buys nothing there |

**Rule of thumb:** if getting it wrong is cheap and obvious, go down a tier. If
getting it wrong is *silent*, stay on `opus`. Silence is the cost multiplier —
a wrong answer nobody notices is paid for later, at a much worse exchange rate.

## The effort ladder

`low` · `medium` · `high` · `xhigh` · `max`

- **Default for coding and agentic work: `xhigh`.** For everything else: `high`.
- **Then sweep down.** On current models `low` and `medium` are much stronger
  than their names suggest, and effort inherited from an older model's habits is
  usually set too high. Try one tier down on a real task before assuming you need
  the ceiling.
- `max` only when correctness matters more than time and cost, and the problem
  is genuinely hard. It can overthink simple work and return *worse* answers.
- `low` for mechanical subagents — it produces fewer, more consolidated tool
  calls, less preamble, terser output. Exactly what a scout should do.

## Three false economies

**1. Lowering effort to shorten output.** It doesn't reliably work; it cuts
*thinking*, not verbosity. To get shorter answers, ask for shorter answers.

**2. Assuming low effort is always cheaper end to end.** On agentic work, more
effort up front frequently means fewer turns, fewer wrong paths, and less total
spend. A cheap model that needs four correction rounds costs more than one
that got it right once — in tokens, in wall-clock, and in review attention.

**3. Downgrading the model to save money on something that must be right.**
Security review, tenant/data isolation, migrations, anything touching money or
auth: stay on `opus`. The saving is measured in cents; the failure is measured
in incidents.

## Escalation rule

When a cheaper tier returns a weak result — hedged, "I could not determine",
generic, or contradicting something known — **re-run the same task one tier up.
Do not iterate at the same tier.** Two failed attempts at the same level cost
more than one attempt at the level that could actually do it, and they poison
the context with wrong intermediate conclusions.

Escalate immediately, without a second cheap attempt, when the task turns out to
involve: cross-tenant or auth boundaries, a schema/migration change, concurrency,
money, or an irreversible action.

## Spending report

When a task's cost is unusual — a big fan-out, a long agentic run, a repeated
retry — say so in one line: what was spent, on what, and whether a cheaper shape
would have worked. On a subscription the currency is rate-limit headroom rather
than dollars, but the policy is identical: the limit is a shared budget, and
burning it on mechanical work is what makes it run out mid-afternoon. The
number is `scripts/brain/usage_limit.py` -- the same 5-hour / 7-day percentages
`/usage` shows, carried in the status line and reported by `health.py` above
80 % of a window.

Never silently truncate scope to save budget. If cost is the reason to do less,
say that out loud and let the user choose.

## Related

- `ops-delegation` — *whether* to delegate at all, and to which agent.
- `claude-api` (bundled) — authoritative, current model IDs, pricing, effort
  semantics. Read it rather than quoting numbers from memory.
