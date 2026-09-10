---
name: tickets
description: >
  Working from the Acme Helpdesk ticket queue in any project: the daily start
  and end routines, analysing a ticket, working one, and syncing. Operates on the
  central store ~/.claude/skills/brain/tickets_store/<MODULE>.json, NOT on a
  per-repo .claude/tiketi.json. Load when the task involves tickets, a
  ticket number, the daily routine, or planning what to work on.
when_to_use: >
  /brain:tickets start | analyze <id> | triage | work | <id> | done <id> |
  skip <id> | close <id> | end | sync — also "what should I work on",
  "tiket 94313", "sync the tickets", "triage the queue", "work the tickets in
  order", "close the ticket", start or end of a working session.
argument-hint: "start | analyze <id> | triage | work | <id> | done <id> | skip <id> | close <id> | end | sync"
---

# Tickets

Arguments: `$ARGUMENTS`

The queue lives in ONE central store, `~/.claude/skills/brain/tickets_store/`,
one `<MODULE>.json` per helpdesk module (`DEMO.json`, …) — **not** in each
project's own `.claude/tiketi.json` (schema, statuses and display formats:
`references/schema.md`). This skill is the single implementation — it replaced
five near-identical command files duplicated across five repositories.

> This line used to say "every project keeps its own queue in
> `.claude/tiketi.json`", contradicting `references/schema.md`, which had the
> location right all along. The router is what gets read every time and the
> reference only on demand, so the wrong copy won: a session looked for
> `acme-audit/.claude/tiketi.json`, found none, and told the operator the
> ticket had no local entry — while `tickets_store/DEMO.json` held it, active,
> with that session's own runs recorded in it (2026-09-06). Stale
> `.claude/tiketi.json` files DO still exist in a few repos; they are legacy
> and are not what `store.resolve()` reads.

## The doctrine — read this before touching any ticket

**Default assumption: the person who wrote this has no technical vocabulary, no
understanding of how the system works, and no model of what is or is not
possible.** Not a slight — they were never supposed to have one. But it means
**a ticket is evidence, not a specification.** Treat every word as unverified.

What follows from that:

- **"It doesn't work" carries zero information.** Neither does "the field is
  wrong", "it's slow", or "it disappeared". Establish what actually happened, on
  which record, in which step — from the system, not from the sentence.
- **They report their theory, not the event.** What reaches you has already been
  through an explanation the writer invented. "The update deleted my data" is
  usually "I did not find my data where I expected". Peel the theory off before
  you read the report.
- **Their words are not our words and do not map.** The same noun means a
  different thing to them; the screen they name is often not the screen they were
  on; the sequence they describe is reconstructed from memory after the fact.
- **A proposed solution is almost always wrong, and it is the most dangerous part
  of the ticket** — implement it and you ship something nobody needed while the
  real problem survives. Solve the problem. Say explicitly when what you are
  doing differs from what was asked, and why.
- **Never take a report at face value. Reproduce it.** If it cannot be
  reproduced, that is the finding, and it goes back as a question — not as a
  guess implemented anyway.
- **Attachments outrank text, every time — ALL of them.** Always open
  `original.attachment_url` AND every `comments[].attachments[]` before forming a
  view. A screenshot routinely turns a three-word ticket into an unambiguous
  specification, and routinely contradicts the words next to it; a spreadsheet or
  document in a comment routinely IS the specification (#18536: three vague lines
  + four `.xlsx` table dumps in the thread — the tables were the ticket). When
  they disagree, believe the attachment.
- **When it is genuinely ambiguous, ask before starting.** One question costs
  minutes; the wrong build costs a day and then still needs the question.
- **A badly written ticket is fixed by the OPERATOR NOTE, not by re-running the
  AI.** When the engineer knows what the customer meant (they talked to them, or
  they know the customer), that reading goes into the ticket's `triage.context`
  (HUD: "kontekst za AI" in the ticket modal, or `notes`) and the ticket is
  re-analysed. The note is AUTHORITATIVE for every AI pass (reader, Gemini
  triage, merger): it overrides the ticket text and the attachments where they
  conflict, and it can declare an attachment irrelevant or "only an example".
  #08597 (VEZ): three words + a screenshot of ANOTHER app (docs.example.com/changes/)
  → the reader built the whole plan around a `/changes/` route that does not
  exist in acme-audit; one operator note ("slika je primer iz ODS-a; treba
  prijava NEPODUDARANJA podataka: popis kaže 10 TV, ima ih 5") produced the
  right reading (`/faults/`, servis-segment) on the next pass. Re-analysing
  without the note only buys a different wrong answer.
- **A screenshot from another of the operator's applications is a reference,
  not the target page.** Every AI pass now sees the KNOWN APPLICATIONS block
  (module → app URL → repo from `tickets_store/*.json project.url`); keep
  `project.url` filled for each module so the model can tell the hosts apart.
- **A screen's own declaration outranks the ticket's wording.** "Ne mogu da
  obrišem na Excel pregledu" reads as a bug; the screen refuses deletion on
  purpose, and nothing in the ticket says so. That knowledge lives in the
  project's `docs/pages/<url_name>.md` (`limits:` with `why` and `since`),
  rendered into the page for the reporter and read by BugReporter;
  every AI pass here sees the same file as PAGE CONTEXT, plus the EXTENSION
  TRACE the reporter's session recorded (role, flags, screens, what they
  corrected). A request for something under NE DOZVOLJAVA is a limitation by
  design, to be answered with the reason and the ticket that set it -- never
  planned as a fix. A screen the trace marks as having NO context file is a
  file to write when the ticket settles what that screen allows
  (`scripts/tickets/project_context.py::pages_block`). Every 14 days
  `scripts/tickets/ebr_review.py` reads the tags back against the close notes:
  a limitation closed as fixed is a page that lied, a bug closed by design is a
  page that was silent, and the screens with no file are ranked by the tickets
  they generated -- proposals, applied with a numbered "da", never by a model.

**A minority of requesters do know exactly what they are talking about**, and
treating one of those as a novice wastes their time, insults them, and throws
away the most precise report you will get all week. Telling the two apart is not
guesswork — it is what the per-requester profile in `ticket-reader` exists for.
`original.customer` names who wrote it; read their profile before reading their
words.

**The profile store is `tickets_store/profiles/<device>.json`** (F4: moved out
of the old per-machine gitignored `agent_view/ticket_profiles/` — TRACKED now,
ONE FILE PER MACHINE like worklog/ and sent_log/, merged on read per creator by
the newest `updated`/`ai_summary_at` with `history[]` unioned — so both
computers learn and neither conflicts on pull; every load→modify→save runs under
`ticket_reader.profiles_lock(root)` (cross-process — the CLI close hook writes
it too); a machine that upgrades migrates its old file in automatically on first
load, leaving the old one in place). The CLI close hook (`writeback.py`) obeys
the SAME `customer_profiles` toggle as the HUD and bridges the Gemini key from
`agent_view.config.json` when the env has none. Per creator it carries the v1 fields
(`tone`/`style`/`recurring_needs`/`patterns`/`sample_count`, folded in FREE on
every analysed ticket) plus v2: `history[]` (up to 30 newest
`{ticket, module, scope, outcome, hours, rating, at}`, deduped by ticket — also
free/deterministic), `vocabulary{}`, `habits[]`, `satisfaction{note,
rating_avg}` and `ai_summary_at` — the last four are AI-written, ONE Gemini call
(MODEL_LITE) at ticket CLOSE that REWRITES (never appends to) them, capped at
~1200 characters combined.

**A global toggle gates the paid half.** `agent_view.config.json`'s
`customer_profiles` (default true; `GET`/`POST /api/tickets/profiles-toggle`)
controls two independent things: whether the learned profile is INJECTED into
every reader prompt, and whether the close-time AI summary call runs at all.
Turn it off and the deterministic `history[]` folding keeps running for free —
only the injection and the one paid call stop. `analyze()`'s `with_profile=True`
overrides the toggle for a single call (the one-off "analiziraj sa profilom"),
regardless of its setting.

## Actions

`/brain:tickets <action>`. Each one is documented where the work is:

| Action | What it does | Read |
|---|---|---|
| `start` `end` `skip` `<id>` | bracket a session, defer with a reason, show one | `references/actions-session.md` |
| `analyze` `triage` `work` | understand before building; order the queue; delegate | `references/actions-analyze.md` |
| `done <id>` | close it HERE — resolution for the customer, report for us | `references/actions-done.md` |
| `close [<id>…]` | write back to the helpdesk — **irreversible** | `references/actions-close.md` |
| `sync` | pull from the helpdesk | `references/actions-sync.md` |
| — | the store's schema, statuses and display formats | `references/schema.md` |

## Rules

- **A ticket is not done until `triage.resolution` exists.** That field is the
  ONLY text the customer ever sees at close; `triage.report` is ours and never
  leaves. Marking a ticket done without it produces a state that looks like
  progress and goes nowhere: `close_out` refuses to close such a ticket on the
  helpdesk, so it reads done here and stays open for the customer, with any
  drafted reply and screenshots stuck unsent in the outbox.

  > Measured 2026-09-10: **four** tickets in exactly that state — 99974, 93774,
  > 69587, 44272 — each with a drafted reply plus screenshots. The refusal had
  > been computed correctly, transmitted to the HUD correctly, and then written
  > into the `title` attribute of the rescan button, where nobody hovers. The
  > last comment reached the helpdesk three days earlier.

  The HUD now refuses the write itself (422, with the reason on screen) for
  every state that means done — `done`, `solved_manually` and `nonsense` — so
  this cannot be forgotten again rather than merely being discouraged here.
  **Write the customer text while you still have the answer in your head**;
  that is the cheapest moment it will ever be written.

- **On Windows, `export PYTHONIOENCODING=utf-8` BEFORE piping anything into
  `rounds.py verdict` or `writeback.py`.** Python decodes stdin with the console
  codepage (cp1252), so a Serbian `č` — UTF-8 `C4 8D` — arrives as the lone
  surrogate `\udc8d`, and the crash lands far from the cause: not on the read,
  but inside `store.atomic_write_json`, as `UnicodeEncodeError: surrogates not
  allowed` at a byte offset deep in an otherwise clean store file. The store
  itself is fine (the write is atomic, so the real file is untouched), but the
  `<MODULE>.json.tmp` is left behind and must be deleted before retrying. Every
  reply this system writes is in Serbian, so this fires on essentially any real
  verdict. (2026-08-28, DEMO#17780.)

- **Never edit `tiketi.json` without saying what changed.** It is the working
  state of the whole queue.
- One ticket at a time. A half-finished ticket left `active` with no note is
  worse than one honestly marked `skip_session`.
- A ticket that turns out to be two tickets should be said so, not silently
  half-solved.
