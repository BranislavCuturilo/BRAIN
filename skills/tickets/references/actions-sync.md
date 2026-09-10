# Pulling from the helpdesk — sync

Extends `tickets`. Read when the store and the helpdesk disagree, or before trusting the queue you are looking at.

### `sync` — pull from the helpdesk

**Deterministic — a script, not hand-editing.** Run it and report what it printed:

```
python ~/.claude/skills/brain/scripts/tickets/sync.py --all            # every module, both directions
python ~/.claude/skills/brain/scripts/tickets/sync.py --module <MODULE> --repo <path>
# writes tickets_store/<MODULE>.json; --repo recorded once; --dry-run previews
```

In the live view, **Analiziraj + predlog upita** (bulk bar) runs the same reader
per selected ticket (`POST /api/tickets/analyze`, module
`scripts/tickets/ticket_reader.py`) and shows a **full prompt** per ticket:
context header, the model's structured Serbian prompt (Kontekst / Šta korisnik
traži / Šta stvarno treba / Dokazi iz priloga / Šta već postoji u repou / Plan
rada / Otvorena pitanja / Brain / Kako proveriti), a `Brain:` line with
roster-validated agents + skills, the fixed brain rules, and the EVIDENCE — the
raw ticket with every attachment named, plus the text digest of xlsx/docx/csv
files. The reader sees: every attachment (`scripts/tickets/attachments.py` —
images/PDFs inline, tables/documents rendered as text, cached by URL) and the
target repo's context (`scripts/tickets/project_context.py` — its Django apps,
its `.claude/skills` catalogue, CLAUDE.md overview, and the brain agent/skill
roster), so it names REAL agents/skills and never proposes building an app the
repo already has. "Otvori u Claude" spills a long prompt to a temp file and
launches Claude with a pointer to it (over the 8 000-char argv cap).
Each fresh reading is PERSISTED on the ticket (`reading` block, via
`write_analysis --key reading`); re-opening serves it from the store with no AI
call, and re-analysing asks before overwriting. Every AI pass (Rescan triage,
Analiziraj, Objedini, Reši) is appended to `tickets_store/ai_log.json` and shown
under the **🧠 AI analize** button — that is where "what did Rescan do" lives.
A **standing project note** (`project.ai_note`, repos panel → "stalna napomena
za AI") applies to every ticket of a project and is authoritative for the design
(acme-audit: multi-tenant → generalise, per-tenant code lists, feature flags).
Every generated prompt: open questions go to the OPERATOR interactively (the agent
never decides alone), verification is the AGENT's job (venv, tests, browser
drive + screenshot), and the prompt ends with **DOPUNA OPERATERA** — the
operator's last word, which overrides everything above.
Prompts are LEAN: the standing note is read on demand through
`/brain:project-rules <MODULE>` (only when the reader's `scope` is not cosmetic),
and the raw ticket + attachments are one command away —
`python ~/.claude/skills/brain/scripts/tickets/show_ticket.py <MODULE> <id> [--files]`
— named in the prompt's "Izvor tiketa" pointer together with the attachments the
reader judged irrelevant (`attachment_verdicts`).

**Objedini u 1 upit** (bulk bar) takes the selected tickets —
typically the easy ones from ONE application — analyses each (ticket-reader) and
writes ONE consolidated Claude prompt per repo, shown on screen only; the
operator copies it into Claude in that repo and runs it in auto mode. Endpoint
`POST /api/tickets/merge`, module `scripts/tickets/ticket_merger.py`.

`--all` is what the live view's **Rescan** button runs before its Gemini triage:
pull each module's active queue; a ticket in the queue here that the helpdesk
no longer lists as active was closed there → closed here too (`status: done`,
`triage.state: done`, `helpdesk.is_closed`); a ticket closed HERE in triage
(`solved_manually` / `nonsense` / `done`) that is still active there is closed
ON the helpdesk through `writeback.py` (outbox posted first, resolution =
`triage.report` → `triage.context` → "Rešeno."). That last step is
irreversible — do not mark a ticket done in the view unless it is done.

`project.repo` and `modules.json` hold the repo **relative to `PROJECTS_ROOT`**
(`popis`, not `C:/projects/popis`) so the tracked store works on every machine;
`setx PROJECTS_ROOT E:\POSAO` (default `C:/projects`). A project whose folder NAME
differs on one machine goes in the gitignored `tickets_store/modules.local.json`
(`{"modules": {"ODS": "NSGAS-Telefon-app/ODS"}}`).

`sync.py` uses `HELPDESK_URL` / `HELPDESK_TOKEN` from the environment (never a
file), pulls the engineer's active tickets and each one's comment thread, and
merges into `.claude/tiketi.json`: **the human layers (`title`, `status`,
`notes`, `consider_for`, `triage`, `outbox`) are preserved**; the source layers
(`original` on first sight, `comments`/`url`/`priority`/`helpdesk`) are
refreshed. It takes the same cross-process file lock the live view uses, so a
sync and a browser triage write never clobber each other. New tickets arrive as
`active`. It reports every add and update; relay that, do not rewrite the file by
hand.
