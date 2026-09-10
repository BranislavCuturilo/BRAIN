---
name: onboard
description: >
  Bring a repository onto the brain, or bring one already on it back into shape:
  check the brain is installed and current, detect the stack and seam, reconcile
  CLAUDE.md against what the brain already covers, route project rules into a
  domain skill, and promote anything general back up. Re-runnable — never
  overwrites what it did not write.
disable-model-invocation: true
---

# Onboard a repository onto the brain

Run in the repository root. **This writes files.** It is re-runnable: the first
run wires a repo up, later runs reconcile it after the repo and the brain have
both moved.

## 0. The environment first — a stale brain onboards a repo wrongly

Before reading the project at all:

```bash
git -C ~/.claude/skills/brain pull --ff-only
python ~/.claude/skills/brain/scripts/brain/health.py
```

- If the pull brings changes, **the rules you are about to apply just changed** —
  work from the new ones.
- If `health.py` reports problems, fix those first. Onboarding onto a brain with
  stale generated docs or an unparseable workflow spreads the problem.
- If the brain directory does not exist, this is a fresh machine: `/brain:ops-sync`
  → "New machine" covers the clone and the settings wiring. Stop and do that.

Then check the repo you are standing in:

```bash
git status --porcelain
```

**If the tree is dirty, say so before writing anything.** Onboarding drops new
files into a working tree that already holds the user's in-flight work, and they
get swept into the next `git add -A`. Commit the framework files separately and
say which they are — never mix them silently.

## 1. Establish which of three situations this is

| What you find | What this run is |
|---|---|
| no `CLAUDE.md`, no `.claude/skills/` | **first wire** — sections 2, 4 |
| `CLAUDE.md` written without the brain | **reconcile** — section 3 is the real work |
| `CLAUDE.md` already points at brain skills | **refresh** — section 5 |

Detect the rest rather than interrogating. Report what you found and ask only
about what is genuinely ambiguous.

| Look for | Tells you |
|---|---|
| `manage.py`, `requirements*.txt`, `pyproject.toml` | Django / Python |
| `package.json` dependencies | which front end, if any |
| `*.jsx`/`*.tsx` present, `rest_framework` in settings | whether there is a SPA seam |
| `htmx` in templates or static | htmx seam |
| `.github/workflows/`, `Dockerfile`, `docker-compose*` | how it ships |
| existing `CLAUDE.md`, `.claude/` | already partly wired — **read it before writing** |

Confirm one thing in one question: **the seam** (`/brain:arch-seams`), because
that is what detection gets wrong most often.

## 2. `CLAUDE.md` — pointers, not copies

Everything the brain covers is **referenced, never restated**. Two copies drift,
and a drifted rule produces confidently wrong work.

```markdown
# <project> — <one line: what it is and for whom>

## Stack
<language/framework> · <database> · <how it ships>
**Seam:** <combination> (`/brain:arch-seams` → `references/<file>.md`)

## Layout
<app or module> — <one line each. Only what is not obvious from the tree.>

## Rules
General engineering rules come from the brain and load on demand:
`/brain:craft-code` · `/brain:craft-security` · `/brain:craft-testing` ·
`/brain:craft-git` · `/brain:stack-<x>` · `/brain:ui-<x>`
Do not restate them here.

Rules specific to THIS codebase live in `.claude/skills/<name>-domain/`.

## Always-in-context
<Only rules whose violation is a security incident and that cannot wait for a
skill trigger to fire. If there are none yet, leave this section out entirely.>

## Commands
<run, migrate, test, deploy — the exact command lines>
```

**Keep it short.** `CLAUDE.md` is read on every turn of every session; it is the
most expensive text in the project. Anything a skill trigger can deliver in time
belongs in a skill.

## 3. Reconciling an existing `CLAUDE.md` — never overwrite it

Its rules were earned, usually the hard way, and the incident behind each one is
rarely written down. **Read every line and sort it**, then propose:

| The line is | Do |
|---|---|
| already covered by a brain skill | delete here, point at the skill |
| true of this repo only | keep — or move to the domain skill if a trigger can deliver it in time |
| true in any repo — a real, general lesson | **promote it** (section 6) |
| no longer true of the code | flag it; verify against the code before deleting |

That last row is the dangerous one: a rule naming a file that no longer works
that way **reads as verified and is not**. Check it against the code rather than
assuming, and if you cannot confirm it either way, say so and leave it.

**Get approval before deleting anything from `CLAUDE.md`.** Shortening it is
valuable; deleting a rule whose incident you never saw is how a fixed bug returns.

## 4. The domain skill

`.claude/skills/<name>-domain/SKILL.md`, holding *only* what is true in this
codebase and nowhere else — domain vocabulary, invariants, workflow states and
what they mean. An empty stub with a good `description` is fine; it fills through
`/brain:capture`.

Do not copy brain content into it. If a rule would be true in another repo, it
belongs in the brain.

## 5. On a refresh run, look for drift

- **A project skill the brain now covers better** — duplication that will drift.
  One survives, and it is usually not the project copy.
- **Cited coordinates that moved** — a rule naming `x/views.py:412` when that
  function left months ago.
- **A domain skill grown past a router** — split it (`/brain:brain`,
  `brain/references/splitting.md`).
- **Commands in `CLAUDE.md` that no longer run.** Try them.

## 6. Promoting a rule up to the brain

A lesson found here that holds anywhere belongs in the brain, at the outermost
layer where it is still true. `/brain:capture` has the routing law — follow it,
do not restate it.

Promotion edits a shared repository from inside a project session, so:

- **Propose each promotion — the rule, and where it would land — and wait.**
  Unapproved promotion is how the brain fills with one project's specifics.
- Write it as the general consequence, not this repo's anecdote, but keep one
  indented line naming the incident or nobody can judge it later.
- Then `/brain:ops-sync` to push: a promotion that stays on this machine helps no
  other project.

## 7. Verify, then report

Prove it rather than assuming:

- a brain skill loads by name in this repo,
- `health.py` is clean,
- the commands you wrote into `CLAUDE.md` actually run.

Report: stack and seam detected, which brain skills now apply, what was written,
what was deleted and with whose approval, what was promoted, and what still needs
a human decision. Then note that `/brain:capture` grows this repo's domain skill
from here, and that this skill can be run again whenever the repo and the brain
have drifted apart.

## Optional — the Live Agent View launcher (Windows)

If the developer wants the live agent monitor one double-click away, offer to
create its desktop shortcut — it launches windowless (no terminal) and is
stopped from the page's stop button or Task Manager:

```
powershell -ExecutionPolicy Bypass -File ~/.claude/skills/brain/agent_view/install_shortcut.ps1
```

Offer, don't assume — it drops a file on their Desktop. Skip on non-Windows;
there the tool is `python agent_view/start.py`.
