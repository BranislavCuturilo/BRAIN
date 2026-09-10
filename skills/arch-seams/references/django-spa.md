# Seam: Django + SPA (React / Vue) — NOT YET FILLED IN

**Status: no project on this machine uses this seam.** Nothing below is a rule.
It is the list of questions this file must answer, written down so that the first
project to need it produces a verified reference instead of an invented one.

Per `/brain:brain`: a speculative rule is worse than a missing one. A missing rule
makes Claude ask; a wrong rule makes it act confidently. Do not fill this in from
general knowledge — fill it in from a real codebase, and delete these instructions
when you do.

## Questions to answer, from real code

**State.** Which store, and what is authoritative when the client cache and the
server disagree. How a mutation invalidates it. What survives a reload.

**Identity.** Session cookie or token. If token: where stored (and the XSS
consequence of that choice), who refreshes, what happens when it expires
mid-action. If cookie: SameSite, and whether the front end is same-origin.

**CSRF.** With cookie auth, the client must send the token — how it obtains it,
and whether every request path (including uploads and third-party clients) does.
With token auth in a header, CSRF does not apply — record *which* model this
project uses, because half of all boundary bugs come from a codebase that is
quietly using both.

**Validation.** Server is authoritative, always. Whether the client duplicates
rules for responsiveness, and if so how the two stay in sync — a shared schema, a
generated client, or manual duplication (the last one *will* drift; if the answer
is "manual", say so plainly).

**Errors.** The JSON error shape, including **field-level** errors, and how the
client maps them back onto inputs. A shape that flattens field errors into one
string is unrecoverable at the UI.

**i18n.** Where the catalogue lives, how the language is negotiated, and where
translated *data* (not labels) comes from.

**Build and deploy.** The bundler, the artifact, who serves it, cache-busting,
and whether the deploy pipeline gains a build step that can fail
(`craft-git` — is it fatal or non-fatal?).

**Routing authority.** Which side owns the URL, and how a deep link to a
client-side route survives a hard reload without 404ing.

## Before choosing this seam

Ask whether htmx solves the actual pressure (`django-htmx.md`). It keeps
server-side rendering and single-source validation, and adds no build step.
A SPA re-opens all seven questions permanently and doubles the validation
surface — worth it for genuinely app-like interaction, expensive for CRUD.
