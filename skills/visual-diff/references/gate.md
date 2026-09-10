# The BEFORE gate, and what it reverts

Extends `visual-diff`. Read when the gate fires, when it is bypassed, and when a run cannot happen at all.

**When it cannot run: ask, never skip.** A silently skipped pair is a claim that nothing changed visually.

## The BEFORE gate is anchored to the Edit/Write tool, and that is bypassable

**What broke** — the PreToolUse gate that requires a BEFORE capture fires on
the Edit/Write tools. Templates edited through Bash + a Python heredoc instead
never reached it, so the block did not surface until commit time — by which
point the original screen no longer existed in the working tree, and the
BEFORE had to be reconstructed.

**Why** — the guard is anchored to a tool, and any edit made through a
different tool is invisible to it.

**It has now happened twice, and the second time the harness CAUSED it.**
Claude Code's auto mode opens the session with "do your work through the Bash
tool wherever it can accomplish the job — make file changes with sed,
heredocs, or short scripts, rather than using the dedicated Edit or Write
tools." Follow that instruction and **every** template edit in the session
misses the gate — not by accident, by policy. The gate is not "bypassable in
principle"; in a whole class of sessions it is guaranteed off, and those are
exactly the autonomous ticket sessions where nobody is watching for it.

> DEMO#80808, 2026-08-30: two location templates edited via Bash heredocs. The
> gate never fired, the work was tested, browser-checked, committed and
> reported — and the customer got no pictures. The operator asked "zasto
> nemam slike pre i posle napravljene????". Recovery cost a worktree of the
> parent commit, a second server, and a re-shoot; it only worked because the
> change was already committed. Uncommitted, the BEFORE was gone.

**So the trigger may not be a hook.** Decide visual-diff from the CHANGE, at
the moment the work is planned:

- **When a ticket's plan names a template or a stylesheet, `before` is the
  first step of the work, ahead of the first edit** — not something checked
  for at commit time. Put it on the todo list with the implementation, not
  after it.
- **A repo-relative path ending `.html`, `.css` or `.js` in the diff you are
  about to make IS the trigger.** No tool is involved in that test, so no
  tool policy can switch it off.
- Passing the app in a browser is NOT the same check and does not substitute
  for it. A run-and-screenshot pass proves the change works and yields only
  "after" pictures; the pair is what the customer is owed, and having one
  half is what makes the miss invisible — the session looks verified.

**The rule** — when a template or stylesheet is about to change through a
shell command rather than the Edit tool, run the BEFORE capture explicitly
first, the same as you would before using Edit.

**Recovery, when the BEFORE was missed anyway and the pre-change state is
gone from the tree:** `git worktree add --detach <tmp> HEAD`, copy in any
untracked files the old code needs to run, serve *that* worktree on the
configured port, shoot `before`, tear the worktree down, serve the real tree
again, shoot `after`.

Two further traps hit doing this recovery:

- **A background `runserver` piped into `| head -20` dies of SIGPIPE** once it
  logs its 20th line, which looks exactly like the shoot hanging rather than
  like the server having exited. Do not pipe a long-running server's output
  through a bounded reader.
- **A changed line with no id/class/selector maps to no screen at all** — an
  `href` value, for instance. That is what `--accept-unlocated` is for, and it
  is the honest answer when the change genuinely has no pixels to show.

## The BEFORE reverts EVERYTHING the change touches — not only the template the gate named

**What broke** — the `before` for #87083 was shot after the sr_Latn `.po`/`.mo`
had already been recompiled with the change's new panel labels. The "before"
showed `Članovi` / `Napravi pozivni link` — translations that were part of the
change — and had to be re-shot with `--force` after stashing the catalogue too.

**Why** — the gate fires on the template edit, but a change reaches the screen
through every file the page reads at render time: templates, static CSS/JS, and
compiled message catalogues (loaded once per server process).

**The rule** — before a live `before` capture, `git stash push -- <every
changed template, static file AND locale .po/.mo>` (or work from a clean
worktree), RESTART the server so the old `.mo` is what gets loaded, shoot, then
pop. Check the picture: a label in the target language that the customer's
screenshot shows in English is the tell that a catalogue leaked in.

## When it cannot run — ask, never skip

Every stopping failure prints ONE json line on stdout and exits non-zero:

```json
{"error": "server_down", "detail": "...", "questions": [{"key": "...", "question": "...", "example": "..."}], "exit": 3}
```

| exit | error | what to ask |
|---|---|---|
| 2 | `needs_config` | the repo has no usable `.claude/visual-diff.json` — the questions name every missing key |
| 3 | `server_down` | dev server not answering; the question carries the configured start command |
| 4 | `auth_failed` | the driver could not mint a session — which user/role? |
| 5 | `playwright_missing` | `pip install playwright && python -m playwright install chromium` |
| 6 | `capture_failed` / `no_before` | nothing captured, or `after` with no `before`. For `no_before` the question is *can the previous state still be captured?* — if yes, revert and run `before`; if it is genuinely gone, `after --no-baseline --baseline-reason "…"` |
| 7 | `usage` | bad arguments or repo path |
| 9 | `no_ticket` | `before`/`after` with no ticket on the command line and none on the run's `state.json` — ask which ticket these pictures belong to, and re-run with `--ticket` |

Turn the questions into an `AskUserQuestion`. **Never continue silently** — a
ticket that ships without the pictures it promised is worse than one that asks.
