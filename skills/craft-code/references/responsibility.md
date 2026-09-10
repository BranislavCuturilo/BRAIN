# Responsibility & boundaries

Extends `craft-code`.

## A handler does four things

Authorize · parse the request · call a service · render a response.

Scoring, aggregation, file handling, notification dispatch, payload assembly —
all of it belongs in a service. **Past ~60 lines, a handler is telling you it
absorbed business logic.**

Request-parsing and presentation concerns (reading `?year=`, resolving a view
mode, building chart JSON) legitimately stay on the handler as small helpers.
Those are not data-model concerns.

**Write the service methods first and let the handler shrink to call them.**
Growing the handler and extracting later does not happen: the extraction is
always harder than it looked and always gets deferred again.

## A refusal still owes the shared resource its cleanup

A handler that returns early — 404, 403, 413, a guard rejection — usually skips
the "read the input" step, because why read what you are about to refuse. When
the input arrives over a resource the *next* caller inherits, that skip is a
defect, and it does not surface where it was made.

> A tool's HTTP panel showed `Unexpected token '<', "<!DOCTYPE "... is not valid
> JSON`. The POST it complained about had succeeded — as a 404. That 404 replied
> without reading the request body, so two bytes stayed in the socket, and under
> HTTP/1.1 keep-alive the server parsed the *next* request starting at them:
> `501 Unsupported method ('{}GET')`, answered with an HTML error page, which the
> page then tried to parse as JSON. The message named neither the route that
> broke nor the route that failed.

**The rule:** whatever the reply, the request's input leaves the stream. Put the
cleanup where every reply passes — the response writer — not in each early
return. The bug belongs to the *class* "answered without reading", which is every
guard present and future; fixing them one at a time leaves the next one to be
written broken. Bound the cleanup (a body too large to swallow politely closes
the connection instead) and track "already consumed" so it is never done twice.

Generalises past HTTP: a pooled connection with an undrained result set, a file
handle left mid-record, a lock released with the buffer half-written. **If the
resource outlives the request, the refusal path owes it the same tidying the
success path does.** And when one handler object serves several requests in
sequence, per-request flags must be cleared at the start of each — a stale
"already consumed" reintroduces the very bug the cleanup prevents.

## A safety refusal that runs unattended needs a named way out

A guard that would rather keep something than risk destroying it is right — and
if it runs on a timer, in a sweep, inside somebody else's request, it will
eventually refuse something the operator wanted gone. Then there are two
failures, not one: the artefact is immortal, and the person looking at it has no
idea why.

> A gallery deleted a screenshot run only after proving its pictures had reached
> the baseline. A run whose capture had produced no promotable picture failed
> that proof for ever. The operator pressed "nothing goes to the customer",
> every decision saved correctly, the customer was owed nothing — and the card
> stayed, indistinguishable from an untouched one. The only explanation the
> system ever produced lived in the sweep's return value, inside a request that
> had already ended.

**Three things, and the first two are not optional.** The refusal's reason must
be *persisted next to the thing it refused*, not returned to whoever happened to
trigger the sweep — that caller is gone and the next person to look starts from
nothing. The UI reads it back and says it, because a card that will not leave and
will not say why is read as a broken button. And the operator gets one explicit
action that overrides the guard, which still runs every check, still saves
everything savable, and reports what it gave up.

**The override is not the guard with the checks removed.** Keep the ones that are
about *where* and *what* (a path jail, an in-flight obligation to a third party)
and drop only the ones about *certainty*. The unattended caller never passes it —
that path keeps failing loudly, which is the entire safety property.

## Name services after what they return

`fetch_*` · `build_*` · `resolve_*` · `validate_*` · `serialize_*`

Not after where they are called from. A name tied to its caller blocks reuse and
starts lying at the second caller.

## The data layer holds data and invariants — nothing else

No presentation strings, no CSS class names, no email dispatch from a save hook.
A model property returning a UI class couples the schema to a rendering
framework: changing the framework, or adding a second renderer (PDF, email,
export), then means editing model files.

**This applies to anything crossing the boundary**, not only models. A service or
state machine returning `{status, label, css_class}` has the same defect. Return
semantics; let the presentation layer map them.

## Boundaries between modules

- **A mixin adds exactly one capability.** A fat mixin makes wrong inheritance
  silently fine — read-only views end up carrying write behaviour, and the next
  change to that behaviour hits code that never needed it.
- **A service imports its own domain plus shared infrastructure — nothing else.**
  Anything further means the boundary is in the wrong place. A function-local
  import that dodges a cycle is a smell marking the real problem, not a fix.
- **A shared helper lives in shared infrastructure, not in whichever feature
  needed it first.** Otherwise the second consumer imports a feature module and
  the dependency graph inverts.

## Never introduce

- **God object** — the module everything imports and nothing can be changed in.
- **Active-record overload** — models that send email, render, or notify.
- **Ambient request state in business logic** — pass it explicitly.
