# brain — working on the brain itself

Read on every turn, so this holds **only what cannot wait for a skill trigger to
fire.** Everything else is a skill: `/brain:brain` is the constitution,
`/brain:ops-sync` the git workflow, `/brain:ops-maintain` the periodic review.

## This repository holds real customer data

`tickets_store/` is **tracked, not ignored** — that is deliberate, because the
repo is how the ticket queue reaches the other machine. The consequence is that
this repository contains real customer names, ticket bodies and comment threads
from the Acme helpdesk.

- **The remote is private and must stay private.** Before any public remote,
  any fork, any paste into an issue or a third-party service, run
  `/brain:release` — it exists to strip exactly this.
- **Gemini is the one deliberate exception, and it is not a contradiction.**
  `analyze_gemini.py`, `attachments.py` and `profile_learn.py` send ticket
  bodies, comment threads and ticket attachments to Gemini. That is a decision
  the owner made and reaffirmed; do not report it as a leak, and do not
  "fix" it. The rule above is about this repository never becoming readable
  by people who should not read it — it was never a no-third-party rule.
  **Where it stops:** source code, credentials and the repository itself do
  not go, to Gemini or anywhere else. A new feature that would send something
  outside ticket content is a new decision and needs asking, not extending.
- **Never `git add -A` here.** The working tree routinely carries a live ticket
  sync, a regenerated dashboard and a half-finished edit at the same time, and
  a commit message describing your work would then be describing theirs.
  Stage the paths you touched. (`/brain:craft-git` has the rest.)
- This repo is named `.claude` but contains **only** the brain. The rest of
  `~/.claude/` — `history.jsonl`, `sessions/`, `projects/*/memory`, `mcp.json` —
  must never be committed here.

## Verify before you believe

Three commands, none of which needs a model or a key:

```bash
python scripts/brain/tests.py --fast   # the scripts still work
python scripts/brain/evals.py          # the skills, hooks and routing still fire
python scripts/brain/health.py         # budgets, stale docs, broken frontmatter
```

`.github/workflows/brain.yml` runs all three on every push. A change to a
skill, a hook or `hooks.json` that does not keep `evals.py` green has changed
behaviour, whatever the diff looks like.

**The same rule for anything you are about to quote.** Twice this month a claim
was made from a source that was not actually read at the moment of claiming: a
test verdict taken from a stale log, and a rule citing `consult-chain.md` as
naming no kill criterion when line 85 names one — three lines had been read and
the rest asserted. In the finished text a fabricated citation and an earned one
are indistinguishable, which is why this one rots the whole system rather than
just being wrong once.

So: open the file, the line or the output **at the point it becomes evidence**,
and quote what is there. Having read it earlier is not reading it, and a summary
of it is not it — including a summary written by an agent you trust. If you
cannot open it, say the claim is unverified instead of making it.

## Two rules that are violated by being forgotten, not by being disagreed with

- **A rule goes in the OUTERMOST layer where it is still true**, and never in
  two places. A second copy is how two versions of a rule come to exist, and
  the stale one is the one someone acts on.
- **Every rule traces to something that actually broke.** A rule written from
  general knowledge reads identically to an earned one once it is in the file,
  and that is how the whole thing rots.

  **Three doors, not one:** an incident, a measurement you can make today, or
  a borrowed idea that carries its origin and a review date and is *not* stated
  as law until one of the first two arrives. What the three cost, and the
  domains where waiting for an incident is simply wrong, are in
  `/brain:brain` — needed while writing a rule, not on every turn.

## Where things are

| | |
|---|---|
| `skills/` | routers + `references/`. Descriptions cost every session; references are free until read |
| `agents/` | brief + grade. **An agent cannot learn** — teach it by editing a skill it preloads |
| `evals/` | the regression suite for the brain itself, static and behavioural |
| `scripts/brain/` | the measurement tools; `outcomes.py` reads what the work produced |
| `hooks/hooks.json` | `require_skill.py` refuses; `prompt_router.py` nudges before the first tool call |
| `journal/` | why work was done, written by `archivist` |
