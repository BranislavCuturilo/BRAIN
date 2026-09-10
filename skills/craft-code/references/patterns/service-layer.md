# Service layer

**Reach for it when** logic spans more than one model, has steps, or must run
identically from a request handler, a command-line entry point and a background
task.

**Not when** it is one model's own invariant. That belongs on the model.

## The shape

The handler does four things — authorize, parse, call a service, render. The
service owns the transaction and the business rule. The model owns its data and
its invariants. `../responsibility.md` has the boundary in full.

**Name services after what they return** (`fetch_`, `build_`, `resolve_`,
`validate_`, `serialize_`), never after where they are called from. A name like
`handle_dashboard_request` is a second copy of the caller's structure and rots
the day a second caller appears.

## The traps

- **Write the service first and let the handler shrink to call it.** Growing the
  handler and extracting later does not happen — "later" is a refactor nobody
  schedules.
- **One write path per fact.** If two services can both change stock, the
  invariant is enforced in neither. Make one of them the only writer and have the
  other call it.
- **The service must not take the request object.** The moment it does, the
  command and the task cannot use it — which was the whole point.
