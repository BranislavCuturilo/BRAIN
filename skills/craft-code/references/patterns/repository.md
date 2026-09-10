# Repository / scoped manager

**Reach for it when** every query on a model must carry the same constraint — a
tenant, an owner, a soft-delete filter, a visibility rule.

**Not when** the model is global reference data with no access boundary.

## Why it is worth the indirection

Centralising the constraint turns *"did every caller remember the filter?"* —
unanswerable, and wrong exactly once — into one function reviewed once and reused
everywhere. **The security boundary becomes a place instead of a habit.**

## The traps

- **The scoped accessor must be the ONLY way in.** A default manager returning
  everything, sitting next to a safe one, will be used by someone in a hurry, and
  the two look identical in review.
- **A by-id lookup goes through the same accessor as the list.** Fetching by
  primary key and then checking a permission is a different rule, and it is the
  most-repeated authorization defect there is — `/brain:craft-security`.
- **Take the scope as a required argument.** Reading it from ambient state
  couples the data layer to a request and makes the unsafe call the easy one.
