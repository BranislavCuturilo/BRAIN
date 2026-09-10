# scripts/tickets/ — the helpdesk queue

*For the reader.*

| Script | Answers |
|---|---|
| `sync.py --all` | Reconcile every module with the helpdesk, both directions (what the view's Rescan runs). `--module X` for one; `--dry-run` previews. |
| `board.py` | What is open across every project, in one command. Scans `<root>/*/.claude/tiketi.json`. `--all` includes done and skipped, `--project X` narrows, `--json` feeds another script. |

This exists so an agent does not open five files and reason about them to answer
"what should I work on". The script counts; the agent decides.

`--root` defaults to the brain's `tickets_store/` (or `TICKETS_STORE`). Repo
paths in the store are relative to `PROJECTS_ROOT` (default `C:/projects`).

Ticket titles are Serbian, so the script forces a tolerant stdout — the Windows
console cannot encode the diacritics and the encoder raises mid-print, which
would render half a board and then a traceback.

## Vraćeni / Dopune (reopened tickets)

Full phase-by-phase detail (locked decisions, risks, work breakdown): `PLAN_REOPENED.md`.

A ticket closed with a resolution can be REOPENED on the helpdesk (status flips
back to Active + a "dopuna" comment). `rounds.py` is the ONE owner of the data
contract and the sync-time detection; `reopen_ai.py` runs the AI pipeline;
`agent_view/server.py` exposes it; `tickets.js` renders the "Vraćeni / Dopune"
lane.

**Data contract** — three additive, `.get`-tolerant keys per ticket:
- `t["rounds"]` — durable, append-only ledger. Index 0 is the original close
  (`{closed_at, resolution, closed_by}`, seeded lazily at first reopen);
  every later element is a reopen round (`{reopened_at, trigger:{comment_id,
  author, author_role}, branch: null|"A"|"B"|"C", resolution, closed_at}`).
- `t["reopened"]` — the current-reopen marker (`{at, by_role,
  trigger_comment_id}`). Lane membership (`rounds.is_reopened_open`) = marker
  present AND the current round has no `closed_at`.
- `t["reopen"]` — the current round's AI working block (Gemini `predlog`;
  Claude's verdict fields). Ledger FACTS live in `rounds`; prose lives here.
  Reset whenever a round opens.
- The server additionally computes `dopuna_ids` per row (the dopuna comment
  ids of the open round, via `reopen_ai._dopuna_comments`) — a read-only
  convenience field, not one of the three ledger-owned keys, so the frontend
  never re-derives the window.

**Detection** (`rounds.detect_reopen`) — LOCKED predicate: the local `closed`
marker is present (a dict) AND a freshly-pulled helpdesk block says
`is_closed: False`. NOT bare `status:done` — in a full rescan, pull+merge runs
before `close_out` in the same pass, so a ticket just marked done (awaiting
write-back) would false-positive on the loose predicate. The trigger comment
is the newest fresh comment after the close anchor (`_close_anchor`, preferring
the API's `closed_at` over the naive local stamp); no comment postdating the
close still yields a valid marker with `trigger_comment_id: null` — detection
never depends on finding the trigger.

**A/B/C methodology and re-close semantics**:
- **A** — the customer didn't notice/find what was already delivered, or it
  was already done. Never auto-closes; the customer self-closes on the
  helpdesk, detected via `sync.close_missing` (a round-with-no-pull-match
  stamps `close_current_round(by="helpdesk")`).
- **B** — a genuine supplement/extension: real new work, then close. The
  ONLY branch that auto-re-closes, through the round-aware `sync.close_out`
  skip rule (fires only when `is_reopened_open` AND `rounds[cur].branch=="B"`
  AND `queue_status=="done"` AND a FRESH `triage.resolution` exists this
  round — never round-1's leftover resolution).
- **C** — cannot reproduce, or fixed. Never auto-closes on its own; once
  fixed the operator re-branches it to B, which then closes via the B path.

**Pipeline**: `reopen_ai.py` runs a Gemini `predlog` pass automatically on
every full Rescan (cheap, bounded by the small reopened count) — its
`suggested_branch` is ADVISORY only. The authoritative verdict comes from a
launched Claude adjudication (`reopen_ai.adjudication_prompt`), which
classifies A/B/C + confidence and writes back through the `rounds.py verdict`
CLI door (`python rounds.py verdict --module X --id N`, JSON verdict on
stdin) — the CLI works even with the server down, mirroring
`write_analysis.py`'s pattern. `rounds.record_verdict` stamps the ledger
branch and the working block together, under one lock.

**Learning pass (CONSULT-ONLY)**: after a round finishes, `reopen_ai.
learning_prompt` / `POST /reopen/learn` runs a best-effort Claude pass that
analyses WHY the ticket was reopened and PROPOSES skill/doc/memory updates by
the brain routing law. Hard rule: it only proposes — it must
`AskUserQuestion` the operator before writing anything, and writes nothing on
a decline.

**Endpoints** (`agent_view/server.py`, all four in `_MUT` — loopback +
same-origin):
- `POST /api/tickets/reopen/verdict` — manual branch set/override; validates
  `branch ∈ {A,B,C}`, calls `rounds.record_verdict`.
- `POST /api/tickets/reopen/reply` — posts ONE named outbox draft as a
  COMMENT ONLY, through `writeback.run`'s existing comment-only mode
  (`close_resolution=None, only_drafts=[id]`) via `_helpdesk_adapter()` —
  never a second close/comment path. Serves A's "zatvorite sami", C's
  "ne mogu da reprodukujem"+questions, and Ask.
- `POST /api/tickets/reopen/classify` — builds the adjudication prompt,
  returns `{prompt, cwd}`. **The panel SHOWS that prompt; it does not run it.**
  Until 2026-08-28 "Klasifikuj" piped the answer straight into
  `/api/claude/launch`, so one click opened a terminal and the operator never
  saw the prompt they were accountable for. It now renders in the shared prompt
  modal with Kopiraj, and the launch is a second, deliberate button — the same
  two-step "Objedini u 1 upit" has always taken.

  The prompt carries the whole job, not just the verdict: triage, **check what
  the customer actually reported** (in the code and in the running app), then
  deliver — comment-only via `writeback … --post-outbox` for A and C, or the
  work plus `… --close --post-outbox` for B. It names both exact invocations.
  Two rules are stated inside it: **nothing reaches the customer until the
  operator has seen the exact text** (AskUserQuestion), and **a branch the
  operator already picked is confirmed, never re-classified** — the session
  stops and asks if the branch looks wrong rather than switching it.
  `reopen.suggested_branch` (Gemini's advisory guess) is deliberately NOT
  treated as a decision.
- `POST /api/tickets/reopen/learn` — builds the learning prompt the same way,
  for a finished round.

**Operator step that is NOT automated — the point of no return**: the first
LIVE `/reopen/reply` fires a real, customer-visible helpdesk comment.
`BRAIN_HELPDESK_READONLY` is honoured automatically (the adapter's write
guard refuses the send under it), so nothing leaves the machine during
development or a dry run — but crossing into a real send is a deliberate
operator action: run once WITHOUT `BRAIN_HELPDESK_READONLY`, on a designated
test ticket, before trusting the lane against live traffic.
