# Setup

## On a new machine — one command

```bash
git clone https://github.com/BranislavCuturilo/BRAIN.git ~/.claude/skills/brain
```

That is the whole installation. **There is no per-project clone and never should
be** — a personal-scope skill folder applies to every project on the machine, and
a second working copy would be a second source of truth that immediately drifts.

Start Claude Code and verify:

```bash
claude plugin list          # brain@skills-dir ... loaded
claude plugin validate ~/.claude/skills/brain --strict
```

`/plugin` shows an **Errors** tab — check it once after the first start.

## Why it loads without installing anything

The folder contains `.claude-plugin/plugin.json`, which makes Claude Code load it
as a *skills-directory plugin*: no marketplace, no `/plugin install`. Skills
appear as `/brain:<name>`, the agents appear in the agent list, and the hooks are
active.

**Edits to a `SKILL.md` are picked up live.** Changes to `agents/`, `hooks/` or
`.claude-plugin/` need `/reload-plugins`, or a new session.

## What the hooks do on every session

| Hook | What |
|---|---|
| `SessionStart` | `git pull --ff-only` in the brain — fails cleanly and silently if you have unpushed commits or are offline |
| `SessionStart` | `health.py --quiet` — **prints nothing unless something is actually wrong** |
| `PreToolUse` | reminders when you edit source, tests, CI, or the brain itself |

If a project already has its own hook covering the same ground, you will see two
reminders. Drop whichever is redundant.

## Environment variables

Claude Code does **not** read a `.env` file for MCP configuration — `${VAR}` in
`.mcp.json` resolves from the *process environment*, so the variable must be set
before Claude Code starts.

```bash
# Windows (new terminals only; survives reboot)
setx HELPDESK_URL "https://helpdesk.example.com"
setx HELPDESK_TOKEN "..."
setx GEMINI_API_KEY "..."          # optional: agent_view bridges its config key too
setx PROJECTS_ROOT "C:/projects"      # where the project repos live ON THIS MACHINE
setx PROJECT_VENV "C:/projects/<repo>/venv/Scripts"

# POSIX — in ~/.bashrc or ~/.zshrc
export HELPDESK_TOKEN="..."
```

Names and purposes: `skills/ops-integrations/templates/env.example`.
A starting `.mcp.json`: `skills/ops-integrations/templates/mcp.json.example`.

**Never put a secret value in `.mcp.json`.** And never run `claude mcp add`
against a file that already contains `${VAR}` — it expands the placeholder and
writes the *resolved secret* back into the file. That is a documented bug and it
is how a token reaches git.

## agent_view: Production tab + Mail tab on a new machine

Both credentials now migrate with `git pull` (decision 2026-08-19, F5) — the
monitor URL/token and every mail account's METADATA live in the tracked
`agent_view/agent_view.config.json`; only the mail PASSWORD stays local
(Windows DPAPI, bound to this Windows account).

On a fresh machine: `git pull`, then `python start.py` — the **Production**
tab works immediately (no setup). The **Mail** tab lists every known account
but cannot connect yet; opening one prompts for its password once (DPAPI
ciphertext does not travel between machines), after which it works like any
other tab and a later `start.py` never asks again.

Migrating an EXISTING machine that still has the old per-machine
`agent_view.monitor.config.json` / `agent_view.mail.config.json`: run
`python agent_view/seed_tracked_config.py` once — it copies the mail metadata
(no mail secret) **and the monitor URL + token** into the tracked config. Yes:
the monitor token then lives in the tracked repo, on purpose (decision
2026-08-19 — read-only credential, private repo, sole user); once committed it
is in history for good, so a later rotation means rewriting history, not just
editing the file. The old local files stay in place as a fallback.

## agent_view: glasovne komande (F6)

The voice button uses the browser's built-in Web Speech API, so **Chromium (or
Edge) only** - Firefox and Safari have no usable implementation, and in Chrome
the audio is sent to Google for recognition, which is why the transcript is
shown and editable **before** anything is sent anywhere. What comes back from
Gemini is a list of proposed steps with a checkbox each: **nothing runs without
your click** - the server signs each step, executes only the ones you ticked, and
re-checks every one of them at the moment it runs. Steps that touch the disk or
start a process are always refusable, and a command on the blacklist cannot be
approved at all.

## agent_view: kalendar — predlozi (F7)

The calendar's `mail`/`chat` suggestion sources (`GET`/`POST
/api/calendar/sources`) are **OFF by default** — only `tickets`, `worklog` and
`deploy` (deterministic, no AI) are on until the operator switches mail/chat on
themselves; that also means nothing reads the mail cache or the AI log for the
calendar unless you opt in.

## Python

Everything here is standard library only. Scripts are run with the interpreter
that is on your PATH; **project** code is always run through that project's venv:

```bash
venv/Scripts/python.exe manage.py test        # Windows
venv/bin/python manage.py test                # POSIX
```

## Wiring a repository onto the brain

In the repository, run `/brain:new-project`. It detects the stack, confirms the
seam, writes a `CLAUDE.md` that **points at** brain skills instead of restating
them, and stubs the project's first domain skill.

General rules come from the brain. A repository's `.claude/skills/` holds only
what is true in that repository and nowhere else.

## Daily use

Nothing to do. Skills load when their description matches the task; agents are
picked by `orchestrator` or by you.

**After changing anything in the brain, push it** — an unpushed rule exists on
one machine only:

```bash
cd ~/.claude/skills/brain
python scripts/brain/docs.py          # regenerate the tables
python scripts/brain/health.py        # confirm clean
# Stage the paths you touched. `git add -A` is forbidden here (CLAUDE.md):
# the tree carries a live ticket sync with real customer names.
git add skills/ scripts/ docs/ && git commit -m "..." && git push
```

## If something is not working

| Symptom | Cause |
|---|---|
| `brain@skills-dir` missing from `claude plugin list` | wrong path — it must be exactly `~/.claude/skills/brain` |
| an agent is missing | `/reload-plugins`; agent files only reload there |
| an MCP server answers 401 | the variable is not in the environment, or you are in the Desktop app, which does not expand `${VAR}` |
| a session-start message about the brain | `health.py` found something real — run it without `--quiet` |
| skills work but nothing is in the dashboard | nothing has been *invoked* yet; see `docs/TESTING.md` |
