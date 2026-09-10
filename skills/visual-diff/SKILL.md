---
name: visual-diff
description: >
  Before/after screenshots for a VISUAL change: two pictures of a screen, every
  changed region of it enlarged, and the screen's name — so the customer on the
  ticket sees what it looks like now instead of reading a sentence about it.
  Fires only for templates and static CSS/JS — never for models, services,
  migrations or tests. Nothing in the pair claims what changed; whether a screen
  changed at all is decided structurally, from the DOM.
when_to_use: >
  editing a template or a stylesheet on a ticket, "send the customer a
  screenshot", "before and after", "snimci", "slike u komentaru", a pre-commit
  visual check, refreshing the visual baseline, or a regression sweep over every
  screen.
argument-hint: "before | after | promote | sweep | baseline"
---

# Visual diff — pre/posle snimci

Plan of record: `docs/PLAN-VISUAL-DIFF-2026-08.md`. Its decision table is
binding; this file is how to run it. Engine: `scripts/visual/`.

## What a picture does not prove, and what to run instead

A screenshot shows the page did not explode. It does not show that the page
scrolls sideways on a phone, that a button is smaller than a fingertip, that a
sticky bar is sitting on the submit control, or that the fine print is 9px.
Those are **measurements** — they pass or fail with nobody looking:

```bash
python ~/.claude/skills/brain/scripts/visual/responsive.py --widths xs,md,xl
```

After a change, let the diff choose both the pages and the widths:

```bash
python ~/.claude/skills/brain/scripts/visual/responsive.py --from-diff
```

`affected.py` already knows which `@media` block each changed CSS line sits
inside — it computed that condition to find the element and then threw it away.
Kept, it says which widths a change can possibly alter: a rule under
`(max-width: 768px)` cannot change a desktop, and a rule in no media query
still needs all three, because a 40px padding that is fine at 1440 overflows a
375 viewport.

**Do not answer this by shooting every screen at every width.** 86 screens at
five widths is 430 images per change: half an hour nobody spends, and 430 more
baseline images somebody must approve. A noisy baseline gets rubber-stamped,
and a rubber-stamped baseline catches nothing. Assert what can be asserted;
photograph only what a `@media` rule says the change can reach.

The widths are Bootstrap's own breakpoints — 375/576/768/992/1200/1400 — not a
list of devices. "iPad" is a dozen widths and none of them is where the layout
changes.

It reports what it cannot decide: whether a gap fits a thumb, whether a
hover-only control has a touch equivalent, whether the keyboard covers the
field being typed into. Those stay with a person.

## When it fires — and when it must not

| Changed path | Action |
|---|---|
| `*/templates/**/*.html`, `templates/**/*.html` | **yes** |
| `static/**/*.css`, `static/**/*.js` | **yes** |
| models, services, views, migrations, tests, management commands | **no** |

An implementation with no UI produces no screenshot. Sending a customer a
picture of a screen that did not change costs their trust in the next one.

The paths above come from the repo's own config (`template_globs` /
`static_globs`), not from this file — a repo that keeps templates elsewhere says
so there.

**The browser MCP does not do this job.** `chrome-devtools` reads the console,
the network and a headed page, and answers questions for YOU; every picture a
CUSTOMER sees comes from here, through the approval and `outbox` flow. Two tools
producing customer-facing pictures would be two sources of truth for what the
customer sees. The boundary: `ops-integrations/references/servers.md`.

## Read on demand

| Doing | Read |
|---|---|
| producing a pair — what it may claim, and how a changed region becomes a crop | `references/doctrine.md` |
| driving a run by hand | `references/cli.md` |
| a run has finished, is stuck, or must not leave the gallery yet | `references/run-lifecycle.md` |
| about to send — the two files, and what each picture says | `references/output.md` |
| the change is only visible after a click, a filter, an expand, or in an empty state | `references/states.md` |
| the gate fired, was bypassed, or the run cannot happen at all | `references/gate.md` |
| choosing which pages to shoot, or running a sweep | `references/screens.md` |
| installing it on a project, the config, pointing it at a server | `references/setup.md` |

## Phases

V1 (this engine) is done. V2 adds the pre-edit and pre-commit hooks, V3 the HUD
gallery and approval, V4 multi-attachment comments on the helpdesk, V5 the sweep
and baseline at close/push. Do not wire a hook to `shoot.py` before reading the
plan's V2 section.
