# Consuming someone else's API

Extends `ops-integrations`. Read BEFORE calling any external HTTP API --
before writing the client, not while debugging it.

Six thousand characters of this sat in the router, where it cost every
session that loaded the skill for any reason -- a secret question, an MCP
server, a plugin. It applies to one task, so it lives here.

## Consuming someone else's API — three things that are true more often than not

These are measured, from a national tax API, but none of them is specific to it.

**1. The published spec is documentation, not a contract.** Verified live: the
official OpenAPI declared camelCase; v1 actually returns PascalCase; v2 returns
camelCase, so the two versions disagree with each other as well as with the spec.
The same logical field was a string enum in one version and a bare integer in
the other.

The consequence is the dangerous part: **a client generated from the official
spec deserialises to all-nulls and raises nothing.** Not a parse error — a
populated object full of `None`. That is the silent-zero class again
(`/brain:ops-scoring`), arriving through the front door.

So: **do not generate a client from a spec you do not control.** Map by hand,
read keys case-insensitively, and write a test that **fails** when a
known-populated field parses as empty against a captured production sample. The
test is the contract; the spec is a hint.

**2. A remote `GET` may write.** Your own rule is that GET must never mutate —
that rule binds *you*, not them. Measured: reading an inbound document flipped
its status from *new* to *seen*, irreversibly and by design, with no read-only
alternative offered.

When a channel behaves that way, mark it (`mutates_on_read`) and let the flag
change behaviour, because the normal reflexes are all wrong: **no retries** (the
first call already changed the world), no health probe against that endpoint,
and an idempotency claim taken **before** the call, not after. A polling loop
against a read-that-writes silently consumes the very signal it is polling for.

**3. Fields documented as populated arrive empty, at scale.** 91% empty on one
field, in production, on a field the documentation describes as present. Design
the parser so an absent value is an outcome with a name, not an exception and
not a default. `/brain:craft-testing` covers why the sandbox will not show you
this.

**4. A cap on their side is a CHUNKING requirement on yours — and a refusal
that happens before the request is a DEFINITE failure.** Measured: a helpdesk
comment accepts 5 attachments; an approved set of before/after screenshots was
13, the client refused the whole comment before sending, and every approval on a
screen with more than a couple of regions was silently undeliverable — the
customer received nothing, and the log said the send *might* have landed.

- **Split the payload into as many calls as their cap needs. Never trim it, and
  never let their limit refuse the user's request.** Chunk against BOTH caps
  (count and bytes), keep logically-paired items in the same call wherever they
  fit, and write each call's text so it stands alone — the recipient sees N
  separate messages in a thread, not one message you happened to split.
- **One logical item that becomes N irreversible calls needs N claims.** Claim
  each chunk under the lock before its call and record the outcome after it,
  keyed by what that chunk contained, so a retry after a partial failure resends
  exactly the ones that did not land and none of the ones that did. A single
  claim over the whole item can only say "something may have gone out".
- **Stop at the first chunk that fails, and report `k of n`** — never as success
  and never as total failure. Continuing past a hole hands the recipient a set
  numbered "1 of 3" and "3 of 3", which reads as a bug rather than as an
  interrupted send; stopping lets the re-run fill it in order.
- **Only a transport failure AFTER the send is ambiguous. Everything refused
  before the socket opens is definite.** Put that distinction on the exception
  itself (one flag, set at the raise sites that can mean it) and never infer it
  from the message text — the text is a description, not a state. Both mistakes
  cost: a definite failure logged as ambiguous sends someone hunting for a
  message that cannot exist, and an ambiguous one logged as definite invites a
  duplicate send of something irreversible.
- **Group by a key the PRODUCER states, never by a string the recipient reads.**
  Chunking needs to know which items belong together; recovering that by parsing
  the display name works right up until the wording changes — measured at less
  than a day, because the first thing the customer said about the delivery was
  that the names were unreadable. The producer already knows the grouping: it
  costs one field on the item. If you genuinely cannot add one, the parse needs a
  test that FAILS when the format changes, and you say so out loud.
- **What the recipient must read goes at the FRONT of the label.** Every list
  truncates from the tail, so a file called `<screen>-<very long>-PRE.png` loses
  exactly the word that made it useful. Put the load-bearing token first, and
  keep the label in the recipient's language with none of your internal
  vocabulary in it — ids, states, "before/after", "crop" mean nothing to them.
  Then make the message text name the same tokens in the same order, so text and
  attachment can be matched by reading rather than by opening each one.

**5. A client library's exception hierarchy covers only the transport — not
what happens before it.** An outbound adapter caught `requests.RequestException`
around its call, with a docstring claiming the swallowing was "deliberate and
total". A JSON request body containing an unpaired surrogate escape (which
`json.loads` accepts) made `requests` raise `UnicodeEncodeError` while
*building the URL* — before a socket ever opened. That subclasses `ValueError`,
not `RequestException`, so it escaped the catch and returned a 500 with a
traceback on plain client input.

URL preparation, parameter encoding and serialisation all happen before the
client's own exception hierarchy takes over, and they raise the language's own
exceptions. **An adapter that promises to degrade gracefully catches
`(<client>.BaseError, ValueError, TypeError)`, not the client's base class
alone** — and its test suite must include a request the client itself refuses
to build, not only a server that fails to answer.
