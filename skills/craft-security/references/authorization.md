# Authorization

Extends `craft-security`. The router carries the headline: a by-id endpoint
authorizes through the same helper the list view uses.

## Aggregates re-scope every member

A parent grouping children that **different users attached** must render
`visible_children(user, scope).filter(parent=obj)`, never the raw reverse
relation.

Reaching the parent legitimately does not entitle you to every child. And an
item-level "can this user reach it" filter is **not** the whole helper: the
*status* dimension — archive-only permissions, own-records, drafts — lives in the
helper alone, so a reach filter silently passes rows the by-id view would refuse.

**Every total shown must be computed over exactly the rows displayed.** A count
taken over the unscoped set beside a partially-scoped list leaks the size of what
the viewer cannot see, and is simply the wrong number for them.

When rows are hidden, say so — "N more outside your visibility" — rather than
silently shrinking the set.

## Read scope is not write authorization

A helper that answers *who may SEE this* is the right queryset for a detail page
and the wrong one for a create/update/delete endpoint. Point a viewset's
`get_queryset` at it and every reader silently gains that endpoint's write verbs
— invisible in review, because the endpoint's own code did not change.

So when a write endpoint reuses a visibility helper, close the verbs it does not
need in the same change (read-only viewset, explicit method list) or authorize
the write separately. **Scaffolding is the worst case**: a full CRUD route over a
`fields = '__all__'` serializer, guarded only by "is authenticated", wired in the
project's first week and called by nothing, still accepts a PATCH that reassigns
the record's owner or a DELETE. Grep for callers before assuming an endpoint is
harmless — zero callers means nobody will notice either the hole or its removal.

## Before narrowing a by-id view, enumerate the parties

Closing an open detail view by scoping it to "owner or assignee" 404s the third
party nobody listed — the tester, the reviewer, the delegate, the watcher —
whose *own* screen links to that page. The fix then reads as a permission bug to
a legitimate user, and it surfaces in production rather than in review.

Grep the templates for the URL name, and the model for role-carrying FKs to the
record (`assigned_to`, `reviewer`, `created_by`), before choosing the predicate.
If a role reaches the record through a related row, the predicate spans that
relation — and then the queryset needs `distinct()`, or a record with two such
rows appears twice in every list.

## Permission checks

- **Always take the scope explicitly**, and return false without one.
- **Never fall back to a denormalized convenience field** ("primary role",
  "default organisation") on a permission path. That is a cross-scope fail-open:
  an admin in one workspace passes a check for another because the convenience
  field matched. Those fields are for rendering, not for deciding.
- Resolve the role from the membership in the scope being checked, every time.

## User-attached fields

**Every user FK on a scoped record is validated against that record's scope.**

For many-to-many relations, note that model-level validation usually does **not**
fire — you need the collection-change hook. Add the validator in the same change
as the field; the migration alone leaves it unguarded, and forms only look safe
because they filter the dropdown.

The same applies to non-user M2Ms where both sides are scoped.

## Every resolution path

**A wall enforced on one lookup path must be enforced on every path.** Custom
domain vs subdomain, slug vs id, header vs body, API vs UI, fragment vs full
page.

An asymmetric guard *is* the leak, and it is invisible because the path everyone
tests is the guarded one. When you add or rely on such a guard, grep the sibling
lookup sites in the same change.

## Enumerate, do not browse

When auditing, list the surfaces first: views, endpoints, fragment handlers,
management commands, signal receivers, background jobs, webhooks. Then check each
one. The surfaces without templates are where the recurring defect actually
lives.

## Merging two forms merges their permission tiers — downward

**What broke** — one screen existed twice: a settings page gated on
`settings.edit` and a segment page gated on `location.edit`, each with its own
hand-written field list. The lists had drifted, one was missing a field users
needed, and the fix was the obviously correct one: delete both lists, share ONE
form. That silently handed `parent` and `level` — the two columns that shape the
permission tree, so re-parenting a record changes who can see it — to every
holder of the weaker permission.

**Why** — de-duplication reasons about the FIELDS and forgets the GATES. A form
carries no permission of its own; it inherits whichever view renders it. Merge
two forms and every field lands at the LOWEST gate among the views that use it,
because that is the weakest door the merged set is now reachable through. Nobody
reviews the merge as an authorization change, because it presents as cleanup.

**The rule** — before sharing one form between views, list the permission each
view requires. If they differ, every field that was previously only on the
stronger view needs an explicit gate in the shared form (drop the field when the
caller lacks the permission — POP it, do not merely disable it, since a field
absent from `self.fields` is neither accepted from a POST nor written back,
whereas a disabled widget still round-trips). Same rule for merging serializers,
schemas or admin fieldsets.

**Where it bit** — acme-audit #05513, `locations/forms.py LocationForm`
shared by `settings_admin` (`settings.edit`) and `locations` (`location.edit`);
`regional_manager` holds the latter and not the former. Caught in adversarial
review, not by the change's own tests.

**Where it bit (again)** — Ticketing-System2 #87083, `chat/views/channel_views.py
manage_members`: the member SEARCH was about to be scoped (customer sees own
company + staff) while the `add` action next to it took any `user_id` from the
body and `User.objects.get(id=…)`. The by-id write must resolve the target
through the same `addable_users(channel, actor)` queryset the search reads —
`.filter(pk=user_id).first()` on that queryset, not a bare `get`. Same class as
the fulfilment/`from_location` case in the router: the second id in the request
is the one nobody authorizes.
