# Strategy

**Reach for it when** one operation has several interchangeable algorithms and
the choice is data, not code — a scoring method per profile, a pricing rule per
contract, an export format per region.

**Not when** there are exactly two cases and there always will be. A boolean
parameter is clearer than two classes and an indirection.

## The shape

A base class declaring the contract, one subclass per algorithm, and a
**registry mapping a stored slug to the class**. The caller resolves the slug and
calls the contract; it never knows the concrete type.

## The traps

- **Never resolve the class from a stored string via dynamic import.** A
  database value that selects code is remote code execution. It is a registry
  key, and an unknown key is rejected in validation — see `../extension.md`.
- **Every subclass returns the declared shape.** A strategy that raises where its
  siblings return, or returns a different tuple arity, breaks every caller
  written against the base — and it surfaces far from the subclass that caused
  it. This is the substitution rule, and it is the one people skip.
- **A strategy must not read fields the contract does not declare.** The moment
  one of them reaches for a caller-specific attribute, the others are no longer
  interchangeable and the registry is a lie.
