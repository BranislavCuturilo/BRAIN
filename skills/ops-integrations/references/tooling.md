# External tools around the harness

Tools that sit *beside* Claude Code rather than inside a project. Evaluated
2026-08; **the rejections are the useful half of this file** — without them the
same tool gets proposed again every few months.

## The rule that decides all of these

**Anything that sits in the request path is a data-egress and capability
decision, not a convenience.** Ask three questions in this order:

1. **Where does the source code go?** A gateway or proxy routing a client
   codebase to third-party providers is a decision about the client's data.
2. **What capability does it silently remove?** Model tiers, context window,
   caching — all of these are properties of the direct connection.
3. **What does it inject into context, and is it bounded?** Anything writing at
   `SessionStart` spends the always-in-context budget on every future session.

Observability tools pass all three trivially, which is why they are the easy
yes. Proxies and auto-memory fail at least one, which is why they are not.

## Adopted

### headroom — live context-window usage

A statusline bar reading the session's own JSONL, tracking which model is
active. Complements the brain's cost tooling exactly: `budget.py` and
`dashboard.py` measure **static** cost — what a skill or agent costs per load —
and this measures the **live** session. Neither answers the other's question.

It injects nothing and changes no behaviour, so it cannot affect output quality.

> **Four unrelated projects share this name.** The one meant here is the
> statusline usage bar (`henchmarketing-rgb/headroom`). A different one is a
> context-compression proxy sold on cutting cost ~50% — that is a proxy, and it
> belongs under Rejected below for the reasons there.

### agent-browser — driving a real browser

`vercel-labs/agent-browser`: a CLI that snapshots the **accessibility tree** and
hands back semantic references (`@e1`, `@e2`) instead of DOM. The loop is open →
snapshot → act on refs → re-snapshot.

Why it is worth having: the alternative is dumping markup into context, and an
accessibility snapshot is both smaller and more stable across restyling.

**It does not replace a project's own driver where one exists.** A project driver
usually exists because of something generic tooling cannot do — subdomain
routing, minting an authenticated session without a password, seeding tenant
state. Adopt this for *generic* driving, keep the project driver for the hard
part, and if you are considering a swap, **measure tokens per verification both
ways first.** That comparison is cheap and it is the only honest basis.

## Rejected, and why

### A model-routing gateway in front of the harness

Local gateways that re-point the harness at many providers through one
endpoint. Rejected on three counts, any one of which is sufficient:

- **Client source code egresses to third parties.** For work under a client
  contract this is not the assistant's decision to make.
- **Every `model:` and `effort:` in the agent roster silently stops meaning
  anything.** The grades in `/brain:ops-seniority` are claims about specific
  model tiers; routed elsewhere they are decoration.
- **Capabilities attached to the direct connection are lost.** An extended
  context window behind a custom base URL has been reported as silently dropping
  back to the default — you lose it without being told.

The offer is cost. The price is provenance, capability and control, and cost was
not the constraint.

### Automatic session memory

Plugins that hook the session lifecycle, compress everything observed, and
inject recovered context at `SessionStart`. Rejected because it is **the opposite
bet to this system**, not a complement to it:

- This brain holds that a rule is written **deliberately**, carries the incident
  that produced it, and is **deleted when disproven**. Auto-captured observations
  are unreviewed, unattributed and unfalsifiable — they accumulate, and nothing
  ever removes one.
- Unbounded injection at session start collides with a measured always-in-context
  budget. The budget is the thing that keeps 50+ agents affordable.
- The underlying data is already available: `usage.py`, `attribute.py` and
  `retro.py` read the same session records directly, with no plugin and no
  injection.

If a lesson is worth keeping, `/brain:capture` writes it somewhere a human can
argue with. That difference is the whole design.

## Before adopting anything else

Say which of the three questions above it passes, what it would replace, and how
you would measure whether it helped. A tool adopted on enthusiasm is
indistinguishable at review time from one adopted on evidence — except that the
second one can be defended.
