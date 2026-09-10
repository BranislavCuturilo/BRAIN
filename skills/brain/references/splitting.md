# Splitting — how to get a thousand units without a thousand descriptions

Extends `brain`. Read before creating a skill or splitting an existing one.

## The one number that governs everything

| Thing | When it costs | How much |
|---|---|---|
| a top-level skill's `description` + `when_to_use` | **every session, always** | ~150 tokens each |
| a top-level skill's **body** | once, when the skill loads — then stays all session | 500–2,500 tokens |
| a `references/*.md` file | **only when actually read** | free until then |
| an agent's `description` | **every session, always** | ~70 tokens each |
| an agent's preloaded skill bodies | **every invocation of that agent** | see `scripts/budget.py` |

So: **bodies are cheap and references are free, but descriptions are not.**
Twenty skills cost ~3,000 tokens before anyone types anything. Two hundred would
cost 30,000 — every session, whether used or not.

There is no way around it. `disable-model-invocation: true` does remove a
description from context, but it **also blocks the skill from being preloaded
into subagents and from auto-loading** — so it only fits skills you invoke by
hand, never a rule set.

## Therefore: few routers, unlimited references

```
craft-security/                       ← ONE description, always on
├── SKILL.md                          ← router: the rules that apply to
│                                        everything in this area + a table
│                                        saying which reference covers what
└── references/                       ← unlimited, 1–3 KB each, FREE
    ├── isolation.md
    ├── authorization.md
    ├── untrusted-input.md
    ├── files.md
    └── races.md
```

Target: **router under 3 KB, each reference 1–3 KB.** A reference that passes
~4 KB is two references.

This is how you get a thousand focused units. They are references, not skills.

## When something must be its own skill

Only when **it has to trigger on its own**, without a parent being loaded first.
A reference is only reachable through its router — so if a task would load the
child but never the parent, the child needs its own description.

`craft-testing` is not a reference under `craft-code`: you can be writing tests
without writing production code, and the trigger must fire on its own.
`craft-security/references/races.md` is a reference: you never think about
races without already being in security territory.

The test: **would a plausible task load only the child?** Yes → skill. No →
reference.

## Writing a router

A router is not an index. It contains:

1. **The rules that apply to every task in its area** — the ones you would
   otherwise repeat in each reference.
2. **A table: "working on X → read `references/<topic>.md`".** Concrete triggers, not
   topic names. "adding a file upload" beats "files".
3. Nothing else. Detail belongs downstream.

If the router grows past ~3 KB, the rules that apply to *everything* have
stopped applying to everything — some of them belong in a reference.

## Writing a reference

- **One concern.** If the title needs "and", it is two files.
- **Self-contained enough to act on**, but it may assume the router was read.
- **Cross-reference by name, never restate** (`brain` → composition). A copy for
  convenience is how two versions of a rule come to exist.
- Concrete over abstract: the failure, the rule, the fix. One example, not three.

## Splitting an existing skill

1. Group its rules by *the task that needs them*, not by topic. The grouping that
   matters is "what would someone be doing when they need this".
2. What survives in the router: only what every group needs.
3. Each group becomes a reference; add its row to the router's table.
4. **Re-check every agent that preloads this skill** — the point of the split is
   that agents now carry the router instead of the whole body. Run
   `scripts/budget.py` before and after; if the number did not drop, the split
   did not work.
5. Cross-references from other skills still point at the *skill*, which is
   correct — the router routes them onward.

## Descriptions

The always-on budget is almost entirely descriptions, so this is where restraint
pays:

- **Target 300–450 characters** for `description` + `when_to_use` combined.
- **Lead with the trigger, not the summary.** Claude reads it to decide *whether
  to load*, not to learn the content. "Load BEFORE writing a query" is worth more
  than a paragraph on what querying is.
- **Write the trigger in the vocabulary of someone who has NOT yet classified
  the problem.** Every technical word — `izolacija`, `tenant`, `authz`,
  `race condition` — is one you reach for *after* you already know what kind of
  problem you have. A trigger keyed on those fires for the sessions that least
  need it and stays silent for the ones that most do.

  > Measured, 2026-08-31, by the eval suite rather than by review:
  > `prompt_router.py` matched `security|bezbedn|authz|izolacij|tenant|leak`.
  > The prompt "da li klijent A moze da vidi podatke klijenta B" — the plainest
  > way the question is actually asked — matched none of them, so the
  > most-repeated defect in this brain's history had no rule named for the
  > sentence that describes it most directly.

  The `tickets` doctrine already says this about the people who write tickets;
  it is equally true of the person typing the prompt. Add the plain phrasing
  alongside the technical one, and write an eval case in the plain phrasing —
  the technical one will pass either way, which is why it proves nothing.
- **File-type and other narrow agents get ~120 characters.** A mechanical agent
  needs a mechanical description.
- Never restate the body. The description is a routing decision.

Run `scripts/budget.py` after any change. It flags anything over budget and shows
what each agent now pays per invocation.
