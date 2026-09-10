# Scope isolation

Extends `craft-security`. The router carries the headline: filter by scope first,
in every query.

## Queries

- **Through every relation.** When the model reaches its scope only via a
  relation, filter through *each* such relation, not just the convenient one.
- **A transitive filter scopes its inner query too.** `parent__in=<inner>`
  requires `<inner>` to be scope-filtered. Leaning on the join "happening" to
  stay in-scope is how a role's legacy region list widened a permission query.
- **A function taking two scoped objects rejects a mismatched pair explicitly** —
  a distinct `scope_mismatch` reason, not a silent falsy result that reads like a
  normal negative answer.
- **Validators and helpers take scope as a required argument.** A default of
  `None` that quietly searches globally is a leak with a friendly signature.

## The model layer

- **Every scoped model carries its own scope FK**, even when its parent has one.
  Reaching scope through a transitive FK only means every call site must remember
  to join correctly, and you cannot index on `(scope, …)`.
- **Auto-fill that denormalized FK in BOTH the validation entrypoint AND save.**
  Frameworks commonly run field-level validation — which rejects the null FK —
  *before* the hook where people put the autofill. A model that fills it only on
  save passes a save-only test and breaks the instant a form, admin page or
  serializer validates it, with an error naming the wrong problem.
- **Validation rejects a row whose own scope disagrees with its parent's.** That
  is what catches rows created by an earlier leak, and it costs three lines.
- **Add `Meta.indexes` leading with the scope column.** That is the practical
  reason the denormalized FK exists.

## Ambient scope

**Never read an implicit scope** — thread-local, request-global, contextvar —
from a model or service. Pass it explicitly.

Ambient scope is correct until the first background job, shell command, queue
worker or management command, at which point it is either empty or, worse, left
over from an unrelated request. Both failures are silent.

## A comment asserting an invariant is a CLAIM, and often a load-bearing one

**What broke** — a recipient resolver said, in its own docstring, "`ancestors()`
never crosses orgs, so the walk stays in-tenant", and built its argument for
who gets notified on top of that. Nothing in `ancestors()` enforced it. The same
generator also backed the two display labels for a row's region and city, so a
parent id pointing at another scope rendered that scope's record name into the
page.

**Why** — a comment is written by someone who checked once, or who assumed. It
then becomes the reason the NEXT author does not check: the invariant reads as
established, so the guard is never written, and every later feature built on the
generator inherits a hole nobody re-examined. The more security-relevant the
claim, the more it gets quoted and the less it gets verified.

**The rule** — **when auditing, grep the code for comments that assert an
invariant about ANOTHER function, and verify each one at its source.** Phrases
like "never crosses", "is always scoped", "cannot be null here", "the caller has
already checked". Each is either true and should be enforced where it is
claimed, or false and is a finding. Do not let a docstring stand in for a check.
And when you write such a sentence yourself, make the function true first — or
write what actually holds.

**Where it bit** — acme-audit `escalation/recipients.py` on
`Location.ancestors()` (2026-08-28). Fixed by making the walk stop at the scope
boundary, which is what everything already believed it did; a mutation test
showed the leak was real — the label rendered the other tenant's name verbatim.

## The pattern is only applied when it is applied everywhere

A model added *without* validation at all is invisible to a "does validation
cover every FK?" grep — there is nothing to find. Periodically enumerate **every**
scoped model and confirm each one has the shape, not just the ones that already
have a validation hook.

The same goes for a rule that names a file as its reference implementation:
re-verify occasionally that the reference still implements the whole pattern.
Cited references drift silently and then teach the wrong shape.
