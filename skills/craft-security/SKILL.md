---
name: craft-security
description: >
  Application-security rules earned from real breaches, in any language. Load
  BEFORE writing or reviewing code that reads or writes data belonging to
  someone. This router carries the three rules that must never be missed;
  references/ carry the rest per concern.
when_to_use: >
  a query, a by-id endpoint, a permission check, a model with an owner or user
  FK, a file upload, a stored credential, anything accepting an id or SQL from a
  client, a security review, "can tenant A see tenant B".
---

# Security craft

**"Scope"** below means whatever partitions your data — tenant, organisation,
workspace, account, user.

## The three that must never be missed

These produced actual breaches, and each is checkable before the code is written.
They are in the router rather than a reference because a reference you did not
read is a rule that did not apply.

1. **Filter by scope first, in every query.** `.filter(scope=x, other=y)` — never
   `.filter(other=y)` on the belief that the relation stays in-scope. Nothing in
   the schema enforces that belief.

2. **A detail, edit or action endpoint addressed by id authorizes through the
   SAME helper the list view uses** — never a bare scope filter plus a
   scope-wide permission. This is the single most-repeated real defect in every
   codebase this brain has seen. When the list narrows by own-records, sub-tree,
   archive or draft status, **every sibling endpoint on that model must apply the
   same narrowing** — and the POST/action ones, which have no template and nobody
   looks at, are the ones that get forgotten.

   **This applies to EVERY scoped id the request names, not just the object the
   endpoint is about.** A fulfilment endpoint that carefully authorizes the
   requisition and then reads `from_location` out of the same POST resolves the
   second id with a bare scope filter, and any user permitted to fulfil moves
   stock out of any location in the tenant. Two principal-grade audits walked
   past exactly this while catching four seeded defects — the source id, the
   target id, the parent id and the "copy from" id are all authorization
   surfaces, and only the first one looks like one.

   **A guard with zero call sites is worse than no guard**, because the codebase
   reads as protected. When you find an authorization helper, grep for its
   callers before believing it: in the case above, the correct helper existed and
   was called from nowhere in the repository.

   **The same trap catches whole FEATURES, not just guards, and configuration
   makes it worse.** A price-tolerance check was written against
   `order.lines` — a relation that did not exist on that model — so it silently
   returned nothing on every call. Two tenant-tunable thresholds sat in the
   settings screen guarding code that could never run, and the presence of the
   knobs made the feature look *more* real, not less. Whenever a check reads a
   relation or attribute you have not personally seen on the model, confirm it
   against the schema before believing the check exists.

3. **`GET` must never write.** CSRF protection does not cover safe methods, so a
   write behind `?archive=1` executes as whichever victim loads an image tag
   pointing at it. Split the verb; do not add a flag.

   **This rule binds you, not the services you call.** A third-party `GET` may
   well mutate — a national tax API's document read irreversibly marks the
   document as seen, by design, with no read-only alternative. Treat such a call
   as an action: claim it before making it, and never retry it.
   `/brain:ops-integrations` has the handling.

## Read on demand

| Working on | Read |
|---|---|
| a query, a scoped model, a denormalized owner FK, ambient scope | `references/isolation.md` |
| a permission check, an aggregate, a user FK or M2M, multiple lookup paths | `references/authorization.md` |
| an id from a client, SQL, a stored reference, a redirect target | `references/untrusted-input.md` |
| a file upload, a storage path, serving media | `references/files.md` |
| a credential, an API error response, logging | `references/secrets-errors.md` |
| idempotency, concurrency, a uniqueness guarantee, tree traversal | `references/races.md` |
| a security review, or mapping a change to OWASP Top 10:2025 | `references/owasp.md` |

## Reviewing

Ask in this order: *What partitions this data? Is every read filtered by it? Is
every by-id write authorized by the same helper as the corresponding read? Which
resolution paths reach this, and is the guard on all of them? What here is
attacker-controlled, and where is it first trusted?*

Enumerate the surfaces rather than browsing the code — reading files hoping
something looks wrong finds nothing.
