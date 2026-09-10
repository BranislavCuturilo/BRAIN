# Session actions — start, end, skip, show

Extends `tickets`. The routine around the work rather than the work itself.

`start` and `end` bracket a working day; `skip` defers with a reason that survives; `<id>` just shows one.

### `start` — begin a session

1. Run `sync` first.
2. Reset every `skip_session` ticket back to `active` — new day, new chance.
   Leave `skip_consider` alone; that was a considered decision.
3. Show the board (`references/schema.md` for the layout): counts by status,
   critical + active first, then `skip_consider` with each one's `consider_for`.
4. **Recommend an order, with reasons.** Critical bugs before features; something
   blocking someone else before something blocking nobody; a small win first if
   the queue is demoralising. Say *why* each is where it is — the ordering is the
   value, not the list.
5. Ask what to work on (`AskUserQuestion`), and **accept free-form text** in the
   answer — a ticket number, a file path to analyse, an extra instruction. Parse
   the reply for a ticket id, image paths, and constraints.
6. If the user asks for a change to the tooling itself, put it on the todo list
   as a separate task and do it *after* the ticket work.

### `<id>` — show one ticket

Details, original description, notes, `consider_for`, attachment analysis. Then
offer the status actions.

### `skip <id>` — defer it

Ask which kind: `skip_session` (not today) or `skip_consider` (waiting on
something). For `skip_consider`, capture *what* it is waiting for into
`consider_for`. A deferral with no reason becomes a permanent one.

### `end` — close the session

1. Show what was done today with each ticket's notes, what was skipped, what is
   still active.
2. **Ask what is unfinished and why**, and record it — tomorrow's `start` is only
   as good as tonight's notes.
3. Run `/brain:capture` over the day: any defect fixed, any correction received,
   any decision made. This is the step that makes the next day cheaper, and it is
   the one most often skipped.
4. Offer to hand the session to `archivist` when the day involved real
   decisions worth reconstructing later.
