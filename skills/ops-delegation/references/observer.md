# Watching a long run while it happens

Extends `ops-delegation`. Read before pairing an observer to a long-running worker. Experimental.

## Watching a long run while it happens — the observer

Review runs *after* the work; an **observer** runs *during* it. That difference
matters for one failure class only: drift the worker cannot see because it is
inside its own frame — treating its own earlier output as prior state, verifying
an assumption instead of a fact, or quietly answering a different question than
the one asked. A review catches those after you have paid for the whole run.

Experimental, and the configuration is exactly three things:

```
CLAUDE_CODE_EXPERIMENTAL_OBSERVER_AGENTS=1     # environment
observer: watchdog                             # on the WORKER's frontmatter
agents/watchdog.md                             # an ordinary agent file
```

**The constraints are the design.** The observer receives a digest after each
worker turn — tool calls, user messages, results — with every entry truncated at
2,000 characters, so it reasons over summaries and must never claim a fault from
a detail that may have been cut. It has exactly one tool and may send exactly
**one** message, capped at 1,000 characters, to its own worker only. The worker
is told the message is advisory and is **never the user's consent** for anything.

One message is a hard budget, and it makes the brief write itself: **stay silent
unless the run is going to be wasted.** Style, a slower approach, a bug the
worker will find in its own review — all of these spend the message and buy
nothing. A run the observer stayed quiet through is a success, not an idle agent.

Attach one to long-horizon work where drift compounds — a migration, a wide
audit, an orchestration spanning many delegations. **Do not attach one to a task
measured in minutes**; there it costs more than it can save, and the honest
answer is no observer at all.
