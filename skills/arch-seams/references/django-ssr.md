# Seam: Django + server-rendered templates

The default and simplest seam: Django renders HTML, the browser posts forms back.
There is no client-side state layer, which removes most classes of boundary bug —
and the ones that remain are easy to misdiagnose because there is "no API" to
blame.

| Question | Answer in this seam |
|---|---|
| State | Server-side session plus the URL. **The URL is the only shareable state** — filters, sort, active tab, pagination all belong there. |
| Identity | Session cookie, handled by middleware. Nothing to do. |
| CSRF | Framework-handled, provided every write is a `POST` form carrying the token. |
| Validation | **Server only, once.** Any client-side check is a convenience, never authoritative. |
| Errors | Re-render the form with field-level errors and the user's input preserved. |
| i18n | Server-side locale; template translation tags; translated *values* come from per-language rows in the database. |
| Build | None. No bundler, no extra process, no extra deploy step. |

## What actually goes wrong here

**A write behind a `GET` link.** With no API layer, it is tempting to make an
action a plain link with `?archive=1`. Framework CSRF protection does not cover
safe methods, so that is a live CSRF hole (`craft-security`). Every state change
is a `POST` form — including "download and log", "mark as read", "claim".

**Losing the user's input on a validation error.** Re-rendering a blank form
after a failed submit is the fastest way to make a form hated, and it happens by
default whenever a view constructs a fresh form instead of re-rendering the bound
one.

**Double submit.** No client framework means nothing debounces the button. If the
action is not idempotent, that is a duplicate record.

**Interactivity smuggled in as page state.** A filter or tab kept only in
JavaScript is lost on every reload and cannot be shared. Put it in the URL.

**Template caching during development** makes an edit silently not render — you
then "fix" markup that was never reloaded (`stack-django/references/templates.md`).

## When this seam stops being enough

Move to htmx (not to a SPA) when the pressure is **partial page updates** —
inline edit, live filtering, a modal that saves without a full reload. htmx keeps
every property in the table above, including server-side validation and rendering,
and adds no build step. It is a strictly smaller jump than a SPA, and for a
CRUD-shaped application it is usually where the pressure actually stops.

Consider a SPA only for genuinely app-like interaction — offline, real-time
collaborative editing, heavy client-side computation. It re-opens all seven
questions and doubles the validation surface, permanently.
