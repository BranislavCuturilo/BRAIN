# Writing back to the helpdesk — close (IRREVERSIBLE)

Extends `tickets`. Read BEFORE any write-back. This is the only action here that a customer sees and that cannot be undone.

### `close [<id> …]` — write back to the helpdesk (IRREVERSIBLE)

The point of no return: this POSTS comments and CLOSES tickets **on the
helpdesk**, which fires their notifications, webhooks and audit log. Walk the
user through it one ticket at a time; never batch-close silently.

**The helpdesk is the CUSTOMER's screen. Everything that goes there — the
resolution and every comment — is written for the person who opened the ticket,
in their language, about what they see and how they use it.** Learned the hard
way on 2026-08-19: #93164 was closed with a developer changelog ("DONE (commit
adffbe5 …), xframe_options_sameorigin, msgctxt, OTVORENO (van obima)…") and
#51571 got an internal musing ("demo podaci, ne unositi") and a question to the
operator ("zahtev mi nije najjasniji") posted as customer comments, plus the
same instructions twice (comment + resolution). The writer now REFUSES a
resolution/draft that reads technical (exit 3, override `--allow-technical`)
and refuses to close while unposted outbox drafts exist until you say
`--post-outbox` or `--drop-outbox` (exit 4). Rules:

- **Commits, file names, test names, settings, libraries, "operater izabrao",
  open engineering items** never go to the helpdesk. They go into the ticket's
  `report` (the store) — that is what `done <id>` is for — or into a NEW ticket.
- **Questions and doubts are for the operator, in the conversation** — never a
  helpdesk comment. If the answer changes the work, ask first, close after.
- **ONE message to the customer.** The resolution IS the closing comment. Post a
  separate comment only when step-by-step instructions are too long for the
  resolution — and then the resolution says "uputstvo u komentaru", it does not
  repeat it.
- **Drafts in the outbox were written earlier, by someone, for some reason** —
  an old note is not automatically a customer message. Read each one aloud to
  the user and decide post/drop BEFORE closing.
- Write it in the customer's language (Serbian Latin here), plain, with the
  path to the screen ("Portal → Administracija → …") when it helps.

For each ticket the user chose to close:

1. Show its drafted `outbox` comments. The user marks which to **post** and which
   to **drop** (dropping is a `×` in the view, or say so and skip it). Then pass
   `--post-outbox` or `--drop-outbox` accordingly — the writer will not close
   with undecided drafts.
2. Draft a close/resolution message, and let the user choose its destination
   **per message**:
   - **post** → it becomes the helpdesk `resolution`.
   - **memory** → it stays local (the note / the reader's per-customer memory for
     budget triage) and is NOT sent. Then the resolution is the user's own close
     description if they gave one, else a minimal one.
   Always push the user's own close description when they gave one.
3. Confirm, then run the deterministic writer — it posts the kept comments
   (**claim-before-call**, so a crash never double-posts) and closes **only if
   every comment landed**:

   ```
   echo "<resolution text>" | \
     python ~/.claude/skills/brain/scripts/tickets/writeback.py --module <MODULE> --id <id> --close [--post-outbox | --drop-outbox]
   ```
   (stdin is read as UTF-8 so č/ć/š/ž/đ survive; write the text to a file and
   `cat` it when the shell mangles quotes.)

   Drop `--close` (and the stdin) to only post comments without closing. It
   reports posted / failed / ambiguous / closed — relay that verbatim; if it says
   *ambiguous*, a prior run may already have posted, so check the helpdesk rather
   than re-running.
4. A ticket you already resolved but is still open on the helpdesk: run
   `writeback --close` to close it there too. Already closed remotely → it
   reports HTTP 400; leave the local `done`. A ticket the token's engineer does
   not own → HTTP 404.

**A close also triggers the customer-profile AI summary (F4)**, best-effort and
silent without a key: `writeback.py`'s CLI entrypoint calls
`profile_learn.learn_on_close` right after a successful close, gated on
`GEMINI_API_KEY` being set in the environment (the HUD has no close ROUTE —
closing only ever happens through this CLI — so that is where the hook lives).
The operator's Rescan covers the OTHER close direction the same way: every
ticket the sync discovers was closed on the helpdesk (`closed_here`) is folded
through `profile_learn.learn_batch`, gated by the `customer_profiles` toggle.

**Every send is logged**, in `tickets_store/sent_log/<device>.jsonl` — one row
per `add_comment` / `close` / `set_estimate`, success or failure, with who
approved it, a 120-char preview and (for an estimate) the value it replaced. Per
device because the store is shared across two machines and a single file would
conflict on every pull; `scripts/tickets/sent_log.py` merges them on read, and
the HUD serves them at `GET /api/tickets/sentlog`. Estimates alone go through
`writeback.set_estimate_only()` (anything outside (0, 40] h is REFUSED, not
clamped — 0 is how the helpdesk spells "no estimate"; mirrored into
`triage.ai_estimate` only after the helpdesk accepted it). A transport error
after the request left is logged `http: unknown` (ambiguous — check the
helpdesk), a real HTTP status as `fail`; rows are keyed by the module FILE STEM.

**Rescan estimates the unestimated.** The operator's Rescan (never the boot
pass) sends ONE Gemini call for every ACTIVE ticket whose helpdesk
`estimated_time` is null or 0 and that has no `triage.ai_estimate` yet — max 40
per pass — and writes the hours straight to the helpdesk. **It never
overwrites**: immediately before each write the ticket is re-read FROM THE
HELPDESK, and a value there (or a failed read) means skip, never a write from
the local mirror. The number is the model's, multiplied by the median
`actual/estimated` of that `(module, scope)` bucket in `estimates.csv` (falling
back to the module, then 1.0; clamped to 0.25–3.0), then clamped to 0.25–40 h
and rounded to a quarter hour. The accuracy table is `GET
/api/tickets/estimates`.

**Time spent** is local-only (measured for HUD-launched Claude AND for any session whose first prompt lines name the ticket — `Tiket #NNNNN` / `tiket NNNNN`; a later prompt naming another ticket switches the measurement) and never written to the helpdesk:
`tickets_store/worklog/<device>.jsonl`, a `start`/`end` event pair per ticket
(`scripts/tickets/worklog.py`), one session split `1/N` across the tickets it
covered. `tickets_store/estimates.csv` is DERIVED from those two by
`python ~/.claude/skills/brain/scripts/tickets/build_estimates.py` — regenerable
at any time, never edited by hand, never a source of truth.

**Intervals open and close by themselves.** A Claude launched from the HUD *for
tickets* gets `BRAIN_WORK_ID` in its environment, which opens one interval per
ticket; every hook event of that run writes a `touch` heartbeat (at most one a
minute), and its SessionEnd closes the intervals. A run that never reports back
is closed by the reaper at its LAST HEARTBEAT (30 min idle, or 4 h since start),
marked `auto_closed` and left out of the calibration hours — so an abandoned run
is never priced as work. "Gotovo" in the ticket modal closes one on demand:
`POST /api/tickets/work/end {work_id}`.

**What the run did** is on the ticket as `runs[]` (newest 20), one entry per
`work_id`: `{started, ended, files, tests, commits, turns, summary}`, read back
from the session transcript by `agent_view/run_summary.py` after each Stop. Every
finished run also leaves ONE `ai_log` entry with `action: "run"`.

**Attachments:** a comment CAN carry files — `writeback` posts them as multipart
(the HUD gallery's approved before/after screenshots ride on the `kind:"shots"`
draft's `attachments`). The helpdesk accepts **5 files per comment**, so a draft
carrying more is delivered as **several comments**: `writeback.plan_chunks` fills
each one to the cap, keeps a screen's before/after/zoom pictures together
wherever they fit, puts the description on the FIRST comment and gives every
continuation its own "nastavak (i. od n)" lead so it reads on its own — the
customer sees them as separate comments. Each comment is claimed before it is
sent, so a retry after a partial delivery sends only the remainder and reposts
nothing (`writeback --module X --id Y` again is always safe). The report is
`posted k of n comment(s)`: a partly delivered set is never reported as success
and never as total failure. A file the helpdesk cannot take (wrong type,
unreadable, over 5 MB) refuses the WHOLE draft before the first comment goes out,
rather than quietly dropping one picture out of the set.

**The file name IS the label the customer reads** — the helpdesk prints it beside
the thumbnail, and nothing else says which picture is which. It is
`<n>-<PRE|POSLE>[-detalj-<k>]-<ekran>.png`: `<n>` is the numbered line in the
description ("1. Karton kvaliteta — slike: PRE, POSLE, POSLE detalj 1"), `<k>` is
the zoomed region's position on screen, and the ROLE comes before the screen name
because a long name gets truncated from the tail and PRE/POSLE is the word that
must survive. Serbian, transliterated to ASCII, no page ids, no "before/after",
no "crop", no state (2026-08-21: the first delivery went out as
`dashboard-location_quality-base-after-crop-1.png` and the operator could not
tell the before from the after). The screen name is the **caption** — the
operator's own text in the gallery, defaulting to the page title — so a page
whose `<title>` reads badly is fixed by editing the caption before approving.
Chunk grouping keys off the `pair` field on each attachment, never off this
name; the name is expected to change whenever the wording does.

**Ratings (F9)** are per RATER, not per ticket: a closed ticket can carry the
customer's, a tester's and an admin's rating independently (`helpdesk.ratings`,
`store.ticket_rating()` is the one reader); a rated ticket that also has a
comment feeds `project_context.ratings_block` into the next reading pass for
that module, so the AI sees what past customers actually said. `voice` can also
**create or edit a ticket** (F6 `create_ticket`/`edit_ticket` steps, through
`writeback.create_ticket_only`/`edit_ticket_only` — never a guessed ticket id).
