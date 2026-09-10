# Naming, and adding a stack or a seam

Extends `brain`.

## Naming

| Prefix | Meaning |
|---|---|
| `craft-` | technology-agnostic engineering discipline |
| `stack-` | one technology (`stack-django`, later `stack-react`) |
| `arch-` | cross-cutting architecture; `arch-seams` holds stack combinations |
| `ops-` | how the work itself is run (models, delegation, cost) |
| *(bare verb)* | an action you invoke: `capture`, `onboard` |

Lowercase, hyphenated, no version numbers, no numeric sort prefixes — the folder
name becomes the command.

## Adding a new stack

1. `skills/stack-<name>/SKILL.md` — the router.
2. Split by artifact kind into `references/`, not by feature.
3. Only write rules you can point at real code for. **An unverified rule is worse
   than a missing one** — a missing rule makes Claude ask; a wrong rule makes it
   act confidently.
4. Move nothing out of `craft-*` that is still true elsewhere.

## Adding a new stack combination (a seam)

**Do not create a skill per combination.** Django+SSR, Django+React and
Django+Vue share ~90% of both sides; a skill per pair duplicates that and grows
multiplicatively — add one front end and you write N new skills.

What actually differs is the **seam**, and it is small: where state lives, how
auth and CSRF cross the boundary, where validation happens and whether it is
duplicated, how errors reach the user, how i18n is resolved, how the build is
wired, what the deploy has to do differently.

So: one `references/<backend>-<frontend>.md` inside `arch-seams`, answering those
questions and nothing else. Adding Vue is one file.
