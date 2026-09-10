# Understanding the queue — analyze, triage, work

Extends `tickets`. Read before starting work on anything. The doctrine in the router says a ticket is evidence, not a specification; this is how that is actually carried out, including the free Gemini path for bulk triage.

**Triage reads and proposes; it does not implement.**

### `analyze <id>` — understand before building

1. Load the ticket. Show the original title, category, priority and customer.
2. **Open every attachment** — the description's and each comment's — and say
   what each shows: which screen, what error, what the user was pointing at,
   which tables/columns a spreadsheet carries.
3. **Look for a ticket we have already answered.**

   ```
   python ~/.claude/skills/brain/scripts/tickets/similar.py <MODULE> <id>
   ```

   It ranks past tickets that have an OUTCOME and shows both halves: what the
   customer was told (`triage.resolution`) and what actually caused it
   (`triage.report`, usually with the commit). Deterministic, no model, no key
   -- IDF over the queue with diacritics folded and Serbian inflection stemmed,
   so "sifarnik" finds "sifarnik" and "lokacije" finds "lokaciju".

   **The score is text similarity, not proof.** Open the match before reusing
   its solution: the same screen does not mean the same cause. But a hit here
   turns a cold reading into "we did this in DEMO#05513, and the cause was a
   `fields` list that had drifted from the form".

   No hit is also a finding -- either the problem is genuinely new, or a
   similar ticket was closed with no resolution and no report, which
   `chain.py --gaps --done` lists.

3. **Load this project's domain skill** — a ticket is a domain statement, and
   analysing it without the domain rules produces a plausible wrong reading.
4. Separate three things explicitly:
   - **What the user asked for** (their words)
   - **What the problem actually is** (your reading)
   - **What you propose to do** — and note it when that differs from what they
     asked, with the reason.
5. Estimate the grade (`ops-seniority`): is this junior work with a clear spec,
   or does it touch isolation, schema, or money?
6. Write the analysis into the ticket's `notes` so it survives the session.

### `triage` — read the whole active queue and propose how to work it

The AI pass over the queue (this one spends model tokens — unlike `sync`, which
is a free script — and that is the point).

**Launch the readers as ONE batch, not a loop.** Each ticket is read
independently of every other; nothing in step 1 needs a previous ticket's
output. Run them in a loop and you pay the full round trip per ticket and every
intermediate reading lands in the main context on the way past. This is the
purest queue in the system, and it was measured as one: 172 of 235 agent
launches across 37 sessions were a single agent alone — 73% (`ops-delegation`,
which also records why the figure used to read 100%).

1. **One message, one `ticket-reader` per active ticket.** It reads `original` +
   the `comments` thread + the project's domain skill and returns Said / Means /
   Needs, a `PLAN`, and suggested `AGENTS` / `SKILLS`, ending with one
   `ANALYSIS_JSON:` line.

   Batch Claude readers for the tickets that are ambiguous or expensive to get
   wrong. For routine bulk, the free Gemini path below already covers it — and
   currently does 47 of 49 analyses, so a Claude batch is the exception you
   choose, not the default.
2. **Persist that line through the locked writer** — never hand-edit the file:

   ```
   echo '<the ANALYSIS_JSON object>' | \
     python ~/.claude/skills/brain/scripts/tickets/write_analysis.py --module <MODULE> --id <id>
   ```

   `write_analysis.py` takes the same store lock and bumps `rev`, so it can never
   clobber a browser triage edit or a `sync`. The live view then shows the
   analysis, the plan and the suggested agents on the ticket.
3. After all active tickets, **recommend an order** across the queue with reasons
   — dependencies first (a feature another ticket builds on outranks its own
   priority), a blocker before a nice-to-have, a quick win to unstick a heavy
   queue. That cross-ticket ordering is the value; the per-ticket reading is the
   input to it.

**Dual-AI — the free automatic path.** The above is Claude's on-command path.
There is also `scripts/tickets/analyze_gemini.py`: it asks Gemini
(gemini-flash-latest, free tier) to produce the SAME `analysis` block and writes
it through the same locked `write_analysis` — run automatically by the
visualizer's **Rescan** button and once on **server start**. It needs
`GEMINI_API_KEY` in the environment (or the gitignored agent_view config), never
a tracked file. `analysis.by` records which brain produced it. Use Gemini for
routine bulk triage (no Claude tokens); use Claude's `triage`/`work` for the hard
or ambiguous tickets.

Triage reads and proposes; it does not implement. Working a ticket is `<id>`
then the real agents the analysis named.

### `work` — work the queue in order, following the triage

Turn the triaged queue into execution. Requires that `triage` (or `analyze`) has
run, so each active ticket carries an `analysis` with a `plan` and
`suggested_agents`.

1. **Order the active tickets by what actually unblocks work, not by priority
   alone.** A feature another ticket depends on comes first even if its own
   priority is lower; a ticket whose `consider_for` is now satisfied re-enters;
   two tickets that are one job are done together. State the order and the reason
   before starting — this dependency-aware reorder is the whole point, because
   the helpdesk's own priority/estimate/deadline are frequently wrong.
2. **Seed a `TodoWrite`** with that order, one item per ticket (or work-unit).
3. **Work them one at a time.** For each: `analyze <id>` if not already clear,
   load the project's domain skill, then **delegate to the agents the analysis
   named** (`suggested_agents`) with a precise brief — not to a generic agent.
   Review risky work with `reviewer` before it is called done.
4. **After each ticket**, run `done <id>` (its three checks), then re-look at the
   order — finishing one ticket often unblocks or reprioritises the rest.
5. Stop and ask when a ticket turns out to need a decision only the user can make
   (an ambiguous requirement, a solution the ticket got wrong). One question now
   beats a wrong build.

`work` is orchestration: it decides order and delegates. It does not write
feature code itself — the named agents do, under their skills.

### Rescan and the Gemini quota

The Rescan button reconciles with the helpdesk IN FULL (pull, merge, close both
ways) and then triages **only what is new, changed or newly closed**. It used to
re-analyse every active ticket on every press — 20 Gemini calls with nothing new
to read.

"Changed" means a comment from the FAR SIDE landed after the analysis was
written. Our own comments never count: posting the before/after pictures does
not change what is being asked, and re-triaging on it spends a call to re-read
our own message. Same rule, same helper (`rounds.is_own_comment`) as the one
that stops our comment reopening a ticket.

A deliberate full re-analysis — when the PROMPT changed, not the tickets — is
still available and is now explicit:

```
analyze_gemini.py --module VEZ --force        # or --all --force
POST /api/tickets/rescan  {"force": true}
```

The boot pass is unchanged: it skips the helpdesk sync and the estimate pass,
and narrows the created-check to the last run's window.
