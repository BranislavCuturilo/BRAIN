# warehouse

Multi-tenant Django. Every row in `warehouse` belongs to exactly one tenant.

- `warehouse/selectors.py` is the scoped repository. Reads of tenant-owned rows
  go through it.
- The list view already does this. Anything addressed by pk is the same
  authorization surface.
