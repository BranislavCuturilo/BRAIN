---
name: arch-seams
description: >
  How a backend and a front end are joined — the small set of decisions that
  actually differ between Django+SSR, Django+htmx, and Django+SPA, and that cause
  most cross-boundary bugs. Load when starting a project, when adding a front end
  to an existing backend, or when a bug lives at the boundary (auth, CSRF,
  validation, errors, i18n, build). Read the references/ file matching this
  project's combination.
when_to_use: >
  "we're adding React/Vue to this", "should this be an API or a template",
  "CSRF fails from the front end", "validation is duplicated", starting a new
  project, "which seam does this project use", a bug at the client/server line.
---

# Seams — how two stacks are joined

A "stack combination" is not a thing to be documented as a whole. Django is
Django whether the front end renders server-side or in a browser; React is React
whichever backend feeds it. **What differs is the seam**, and it is small.

That is why there is no `django-react` skill. There is `stack-django`, there will
one day be `stack-react`, and between them there is one reference file answering
the questions below. Adding a front end is one file, not three skills.

## The seven questions every seam answers

Any boundary bug is almost always one of these being answered inconsistently on
the two sides:

1. **Where does state live?** Server session, URL, or a client store — and what
   is the single source of truth when they disagree.
2. **How does identity cross?** Session cookie, token, or both. Who refreshes it,
   and what happens on expiry mid-action.
3. **How is CSRF handled?** With a rendered form the framework does it. The
   moment a client makes the request, it becomes the client's job, and this is
   the single most common seam bug.
4. **Where does validation happen, and is it duplicated?** If both sides
   validate, which one is authoritative and how do the rules stay in sync.
5. **How does an error reach the user?** A form re-render, a JSON error shape, a
   status code, a toast. Field-level errors must survive the crossing or they
   become a useless banner.
6. **How is language resolved?** Server-side locale, a header, a client bundle —
   and where a translated *value* (not label) comes from.
7. **What does the build and deploy have to do differently?** Extra build step,
   extra artifact, extra process, extra web-server route.

## Available seams

| Combination | File | Status |
|---|---|---|
| Django + server-rendered templates | `references/django-ssr.md` | in use |
| Django + htmx (+ a JSON API alongside) | `references/django-htmx.md` | in use |
| Django + SPA over a JSON API | `references/django-spa.md` | **not filled in — no project uses it yet** |

## Declaring a project's seam

One line in the project's `CLAUDE.md`:

> **Seam:** Django + server-rendered templates (`arch-seams/references/django-ssr.md`)

That is all the routing needed. Do not copy the seam's contents into the project.

## More than two technologies

A stack is rarely two things. Django + Vue + nginx + Redis + Celery is normal.
The cross-product of those is unmanageable and does not need to exist, because
**most of them are not on the same seam.**

A seam is a **pair of layers that have to agree about the seven questions above.**
Django and Vue argue about state, auth and validation — that is a seam. nginx
does not: it argues about routing, TLS, headers and static files, and it argues
about them the same way regardless of what the front end is written in.

So decompose by *concern*, not by counting technologies:

| Concern | Owner |
|---|---|
| client ↔ server (state, auth, CSRF, validation, errors, i18n) | one `references/<backend>-<frontend>.md` |
| serving, TLS, routing, static files, uploads | the infrastructure layer (`craft-git`, `devops`) |
| background work, queues, scheduling | its own reference when it earns one |
| caching, sessions, real-time transport | usually a line in the seam that uses it |

`django + vue + nginx` is therefore `django-vue` **plus** the nginx concerns —
two files that compose, not one file per combination. Adding Redis adds nothing
to the seam; adding Svelte adds one reference.

**A new seam file is justified only when two layers genuinely have to agree
about the seven questions and no existing file covers that pair.** If the answer
to all seven is "same as an existing seam, plus one detail", it is a paragraph in
that file, not a new one.

## Noticing when a new seam has arrived

Nobody plans this — it shows up as the same boundary question being answered from
scratch a third time.

Watch for: the same class of bug recurring at one layer boundary; a decision
about state or auth being re-litigated; two projects solving the same crossing
differently. Any of those means the seam exists and is undocumented.

`archivist`'s journal review is where this surfaces (a decision re-litigated
twice) and `brain-keeper` is where the duplication surfaces. When either finds
one, the fix is a new `references/` file answering the seven questions — written
from the real code of a project that actually uses it, never from general
knowledge.

## Adding a new seam

Answer the seven questions for the new combination in one `references/` file, and
**write nothing you have not verified against real code.** A speculative seam
file is worse than an absent one: absent makes Claude ask, speculative makes it
act. The unfilled `django-spa.md` is deliberately a list of open questions rather
than invented answers.
