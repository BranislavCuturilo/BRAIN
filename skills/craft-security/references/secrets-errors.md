# Secrets & what errors reveal

Extends `craft-security`.

## Secrets

- **Encrypted at rest, in a separate record holding only ciphertext.** Never on
  the frequently-read parent row, never inside a config blob, never in a string
  representation, never in a log line.
- **Decrypt at the point of use**, and **fail closed to empty** on an
  undecryptable value rather than raising into a delivery path. A rotated key
  should degrade a notification, not crash a request.
- **One shared encryption helper in shared infrastructure**, so a new module
  storing a secret imports it rather than re-deriving the key handling or
  importing an unrelated feature module.
- Never in the repository, never in an image layer, never echoed by a pipeline
  step, never in a prompt or a memory note.

## What an error tells the caller

**Never return an exception's message across a boundary.** Database and ORM text
leaks table names, column types, and the values that were being written. Log the
traceback with a request id; return a generic message and that id.

This matters even when the caller is trusted infrastructure — it is an
information-disclosure path that does not need to exist, and "trusted" changes
the day someone points a new client at the endpoint.

## Malformed input is a 400, not a 500

An endpoint parsing a request body must handle the shapes that are *valid* in the
wire format but wrong for you:

- valid JSON that is **not an object** — `"RS"`, `["RS"]`, `5`, `null`, `true`.
  A `.get()` on any of those raises.
- a **number where text is expected** — `.strip()` on an int raises.

Both fall into a blanket handler and return a 500 with a stack trace for what is
plainly client input. Parse through one helper that returns either the object or
a 400, and read typed fields through coercing accessors. Then sweep every
endpoint — if one did it, they all did.

## Logging

Log enough to reconstruct what happened; never enough to replay it. No
credentials, no tokens, no full request bodies from authenticated endpoints, no
personal data beyond an identifier.

A failure-swallowed side effect must still log — otherwise "it silently didn't
send" is indistinguishable from "it wasn't supposed to send".
