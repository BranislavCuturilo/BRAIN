---
name: research
description: >
  What to do when you do not know the answer: say so, look it up properly,
  verify it against reality, and then turn it into a rule. Load whenever you are
  uncertain about an API, a library, a version, a best practice, an error nobody
  recognises, or a technology decision — before answering from a guess.
when_to_use: >
  "I'm not sure", an unfamiliar library or API, an error message you do not
  recognise, "what is the best practice for X", a version-specific question, a
  new technology being evaluated, anything where your training data may be stale.
---

# Research

## Say it before you search

**Name the uncertainty out loud, first.** "I do not know whether this version
supports X — let me check" is a useful sentence. A confident guess that turns out
wrong costs far more than the sentence, because it gets acted on without review.

Silent guessing is the failure this skill exists to prevent. Searching is the
easy part.

## Reach beyond keyword search

`WebSearch` is keyword search over pages. Stack Overflow, an issue tracker, a
package index and a security feed answer questions it answers badly, and this
project needs all of them -- `references/reach.md`, and
`scripts/reach/reach.py doctor` for what is reachable right now. Every channel
is keyless. **An unreachable source is a gap to NAME, never a licence to answer
from memory.**

## Read the code before you read the internet

The most common wasted search is looking up how something behaves when the source
is in the repository, installed in the virtualenv, or visible in a lock file.

Order of authority:

1. **The actual code and environment on this machine.** The installed version,
   the real function signature, the actual configuration. Nothing outranks this.
2. **Official documentation for the exact version in use.** A doc page for a
   different major version is a different product.
3. **The library's own source or changelog.** Faster than a forum for "does this
   parameter exist".
4. **Reputable secondary sources**, dated and cross-checked.
5. **Forum and blog answers** — the weakest, and the most likely to be years out
   of date while reading as current.

**Check the date on everything.** APIs drift; a top-ranked answer from three
years ago is often actively wrong now.

## Verify before you use it

A search result is a hypothesis. Confirm it against this project: run it, read
the installed signature, check the version. **Never present a searched claim as
established fact without saying where it came from** — cite the source in one
short clause so the reader can weigh it.

When sources disagree, say so and say which you are acting on and why. Manufactured
consensus is worse than an honest "these two disagree".

## For Claude and Anthropic APIs specifically

Load the bundled `claude-api` skill. It is authoritative over both training
memory *and* a web search for model IDs, pricing, effort semantics, and SDK
shapes — that area moves fast enough that search results are frequently stale.

## Then learn from it

**The research is only half the job.** What was learned goes into a rule, or the
next session repeats the same search:

- A framework or library behaviour that surprised you → the relevant `stack-*`
  skill.
- A best practice adopted → `craft-*`, if it is true beyond this technology.
- A fact about this codebase or client → the project skill or memory.
- Something learned and then contradicted → **delete the old rule** in the same
  change.

Route it through `/brain:capture`, which enforces where it goes.

**Record the source and the date** with the rule. A rule sourced from a
documentation page can be re-verified when the library upgrades; a rule with no
provenance can only be trusted or deleted.

## When not to search

- The answer is in the repository.
- The question is about this project's own conventions — that is a skill or a
  question for the user, not a web search.
- You are searching for reassurance about a decision already made. That is
  procrastination with a search bar.
- The user asked for an opinion. Give one.
