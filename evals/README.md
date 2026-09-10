# evals/ — for the reader

*This file is for you, not for Claude.*

`scripts/**/test_*.py` proves the **scripts** work. This directory proves the
**brain** works — the skills, the hooks, the routing. Nothing else did.

```bash
python scripts/brain/evals.py              # the free tier
python scripts/brain/evals.py --behaviour  # + the paid tier
```

## Two tiers, for the same reason the ticket queue has two brains

| Tier | Cost | Runs | Proves |
|---|---|---|---|
| **static** — `gate` `router` `hook` `rule` | free, no model | every commit, in CI | the machinery still fires |
| **behaviour** | tokens per case | on demand, on a schedule | a rule changed the OUTCOME |

Bulk triage goes to the free brain and the hard cases go to Claude — the same
split, applied to the brain's own regression suite. A gate that costs money on
every push is a gate that gets turned off.

## The five kinds

| kind | Drives | Asserts |
|---|---|---|
| `gate` | `require_skill.py`, real stdin/stdout | `expect: deny \| allow`, and which skills it named as the way out |
| `router` | `prompt_router.py`, real stdin/stdout | `expect_names` / `expect_absent` in the injected context |
| `hook` | the `if` clauses in `hooks/hooks.json` | which reminders fire for a path |
| `rule` | the skill files themselves | one source of truth: who owns a rule, and who merely cites it |
| `behaviour` | `claude -p` in a fixture | what was written to disk |

A pipeline that does not route through `claude -p` cannot be driven by the
behaviour tier at all: the ticket pipeline (`analyze_gemini.triage_prompt`,
`ticket_reader._build_prompt`) runs on Gemini, so a fixture proving that a
`docs/pages` file changes its verdict was structurally impossible here. Prove
such wiring with a deterministic test that renders the real prompt and asserts
the block is in it (`scripts/tickets/test_page_context.py` is the pattern), and
pin ownership of the rule with a `rule` case.

`gate` and `router` run the **real hook as a subprocess**, through the same
contract Claude Code uses. They are not tests of the internal functions, so a
refactor that keeps the functions and breaks the contract still fails.

## Every case must cite an origin

A case with no `origin:` fails validation. The brain's own law is that a rule
traces to something that actually broke; a case written from general knowledge
is a case that proves a preference.

```yaml
- name: a Django view cannot be written before craft-security is loaded
  kind: gate
  origin: >
    The most-repeated real defect in every codebase this brain has seen: a
    by-pk endpoint authorized with a bare scope filter instead of the helper
    the list view uses (craft-security rule 2).
  proves: craft-security
  case:
    path: apps/warehouse/views.py
    loaded: []
  expect: deny
  expect_skills: [craft-security]
```

## What these have already caught

Written 2026-08-31, on the first run:

- **`prompt_router.py` missed the plainest phrasing of a cross-tenant
  question.** "da li klijent A moze da vidi podatke klijenta B" matched only
  the domain skill — every word in the security pattern (`izolacij`, `tenant`,
  `authz`) is one an engineer reaches for *after* classifying the problem. The
  single most-repeated defect in this brain's history had no rule named for
  the prompt that describes it most directly.
- **`test_sync.py` passed everywhere except the machine that owned it.** It
  asserted "legacy absolute re-rooted **when missing**" using `C:/projects/popis`
  — a path that exists on the machine the store was born on.

## Two YAML traps, both hit twice while writing these

- **A `name:` that STARTS with a quote is read as the whole value**, and YAML
  then chokes on the rest of the line. `- name: "which tool" is answered from
  the register` fails; wrap the whole string in single quotes, or start it with
  a word.
- **Markdown emphasis inside a `rule` phrase breaks the literal match.** The
  file says `the **same** assistant message`, so a phrase of
  `"same assistant message"` finds nothing. Choose a span with no `**` in it.

Both were caught by the loader rather than by silently passing, which is the
loader working -- but both cost a cycle, twice.

## Adding one

Put it in the file for its kind. Run `evals.py -k <name>` while writing it, and
**make it fail once on purpose** before trusting the pass — an eval that has
never been red is in exactly the position `craft-testing` warns about.
