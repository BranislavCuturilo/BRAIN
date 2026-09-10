# Closing a ticket locally — done

Extends `tickets`. Read before setting any ticket to done.

`done` writes the customer's resolution and the internal report into two separate fields, and writes NOTHING else — in particular never the top-level `closed` marker, which belongs to `close`.

### `done <id>` — close it

Before setting `done`, confirm the four things that are forgotten most:

- Does it actually work — verified against the running app, not just the diff?
- Is there a test for the specific thing that was broken (`craft-testing`)?
- **Did the change touch a template, a stylesheet or front-end JS? Then it owes
  the customer a before/after pair (`visual-diff`) — not a screenshot you took
  to satisfy yourself.** The two get confused because both produce a PNG: a
  driver screenshot proves to YOU that the page renders, while the pair is the
  deliverable that goes on the ticket and is the only one the customer ever
  sees. Shipping the first and calling it the second is how a ticket closes
  with no picture at all, and the operator is the one who notices (twice, on
  consecutive tickets, 2026-09-06). If the work is already committed, the pair
  is still recoverable: `before` reads the stored baseline and `after` takes
  `--diff-from-commit <sha>`.
- Did anything here produce a reusable rule (`/brain:capture`)?

Then set `done` and write TWO things, in two fields — they have different
readers and the writer refuses to mix them (2026-08-19, #08597):

- `triage.resolution` — **for the CUSTOMER**, in their language: what they will
  see and how to use it. This is the ONLY text `close_out` / `writeback --close`
  sends to the helpdesk. No commits, files, tests, settings, open engineering
  items, no "operater izabrao". A `done` ticket without a resolution stays OPEN
  on the helpdesk (the sync reports it as `needs_resolution`) — never closed
  with a default and never with the report.
- `triage.report` — the **internal** record (commits, files, tests, open items,
  decisions), read months later by someone reconstructing what happened. Never
  sent anywhere.

And `notes` for anything else worth keeping about the ticket.

**`done` writes `status`, `triage.state` and those two fields — and NOTHING
else. In particular it must never write the top-level `closed` marker.** That
field belongs to `writeback`, which stamps it only after the helpdesk close
actually lands. `rounds.detect_reopen` keys off exactly that marker plus a
fresh pull saying the ticket is open — so a hand-written `closed` on a ticket
the helpdesk still has open is a guaranteed false reopen on the very next
rescan: the round opens, `triage.resolution` is CLEARED, the ticket resets to
active, the close phase then refuses to close it (an open round only
auto-closes on branch B), and `reopen_ai` spends a Gemini call adjudicating
it. Cost when it happened (DEMO#76082, 2026-08-30): all five, and the operator
saw a finished ticket sitting in "Vraćeni / Dopune". Leave `closed` alone and
let the close phase do its job.
