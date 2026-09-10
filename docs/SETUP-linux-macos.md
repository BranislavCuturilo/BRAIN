# Setup — Linux & macOS

Companion to [`SETUP.md`](SETUP.md), which is written from a Windows machine.
The brain is the *same* git repo on every OS; only the shell, the path
separators and one Python-interpreter detail differ. This page is the POSIX
translation.

> **TL;DR** — the whole install is one `git clone` into `~/.claude/skills/brain`,
> then `python3 ~/.claude/skills/brain/scripts/brain/bootstrap.py` to find out
> what is missing. There is no venv, no requirements file, no marketplace and no
> build step. Everything is standard library **except the visual-diff engine,
> which needs Pillow** — this page claimed otherwise until 2026-08-31, because
> `PIL` is imported inside the functions that use it, so the modules import
> cleanly and only the calls fail.
>
> **Setting Claude up to do this itself: [§0](#0-for-claude--setting-yourself-up-on-this-machine).**

---

## 0. For Claude — setting yourself up on this machine

*Everything below §0 is written for a human reading top to bottom. This section
is the executable version, and it is the one to follow when the operator says
"set yourself up" or points you at this file.*

**One command answers everything:**

```bash
python3 ~/.claude/skills/brain/scripts/brain/bootstrap.py
```

It prints one line per check with a verdict, and every failure prints the exact
command that fixes it. Re-run it after each fix; it is idempotent, so running it
ten times is the same as running it once.

If the brain is not cloned yet, that command does not exist. Start with:

```bash
git clone https://github.com/BranislavCuturilo/BRAIN.git ~/.claude/skills/brain
```

**The path must be exactly `~/.claude/skills/brain`** — that is how the plugin
is discovered. A clone anywhere else loads nothing and reports no error.

### The verdicts, and what each one means for you

| Verdict | What you do |
|---|---|
| `ok` | nothing |
| `warn` | it works; note it and carry on |
| `FAIL` | **blocking.** Run the printed command, re-run bootstrap |
| `ASK` | **stop and ask the operator.** Never guess these |

`bootstrap.py --fix` additionally performs the fixes that are user-level and
reversible: the `~/.local/bin/python` symlink, `pip install --user`, creating a
directory. Prefer it once you have read what it would do.

### What you must NEVER do while setting this up

- **Never run `sudo`.** A system-wide package install is the operator's
  decision, not yours. `bootstrap.py` prints the command and stops; hand it
  over rather than running it.
- **Never invent a value marked `ASK`.** `PROJECTS_ROOT` is the directory the
  operator's repositories live in and only they know it; `GEMINI_API_KEY` and
  the helpdesk credentials are secrets. A guessed `PROJECTS_ROOT` does not
  error — it silently resolves every module to a directory that does not
  exist, and the ticket store then reports an empty queue as if that were the
  truth.
- **Never `git clone` over an existing directory**, and never `git reset
  --hard` or force-push to fix a dirty clone. If `~/.claude/skills/brain`
  already exists and is not the brain, say so and stop.
- **Never commit anything during setup.** This repository holds real customer
  data (see `CLAUDE.md`); a setup session has no business writing to it.

### The three that actually break an install

Two of them fail **silently**, which is why they are worth knowing by name.

1. **Bare `python` is not on PATH.** Every hook in `hooks.json` invokes
   `python`, not `python3`, and every one of them is `|| true` or fail-open by
   design. On a distro that ships only `python3` the brain therefore looks
   installed and enforces nothing at all. This is the single most likely reason
   a fresh Linux install "works" and does nothing. §4 has the fix.
2. **`PYTHONIOENCODING` is unset.** The ticket store is full of č/ć/š/ž/đ; a
   script writing to a pipe picks ASCII and dies on the first Serbian ticket
   title. §4.
3. **`PROJECTS_ROOT` is unset.** `store.repo_path` falls back to the legacy
   Windows root, and on POSIX `"C:/projects"` is not even an absolute path — so
   every module resolves to a directory that cannot exist. This one is `ASK`.

### When you are done

```bash
python3 ~/.claude/skills/brain/scripts/brain/bootstrap.py   # expect: "Ready."
python3 ~/.claude/skills/brain/scripts/brain/tests.py --fast
python3 ~/.claude/skills/brain/scripts/brain/evals.py
```

Report what is still `warn` or `ASK` rather than declaring success. On Linux the
test suite has a known pre-existing gap — read the section immediately below
before treating a red suite as something you caused.

## Known gap — the test suite has never been green on Linux

**Measured 2026-08-31, the first time the suite ever ran on Linux (GitHub
Actions, ubuntu-latest): 16 of 21 files passed, 5 failed.** Every failure is
pre-existing and none was caused by the change under test. They had been
invisible because the suite had only ever run on the author's Windows desktops.

    scripts/tickets/test_sync.py            repo_path / legacy re-rooting
    scripts/tickets/test_triage_gate.py     far-side comment re-analysis
    scripts/tickets/test_writeback_sentlog.py
    scripts/visual/test_annotate.py
    scripts/visual/test_compare.py

The root cause of at least the first is not a test bug: `LEGACY_PROJECTS_ROOT`
is the literal `"C:/projects"`, and `Path("C:/projects/x").is_absolute()` is **False**
on POSIX. The whole legacy-re-rooting branch of `store.repo_path` is therefore
unreachable on Linux, so a store written on Windows resolves differently here.
That is a portability defect in the code, not in the assertion.

**So: the brain is usable on Linux, but it is not verified there.** CI gates on
`windows-latest` because that is what is actually in use, and a gate that is red
for reasons unrelated to the change is one people learn to ignore. If Linux
becomes a real target, these five are the work, and the gate should move to a
matrix.

## 1. Prerequisites

| Tool | Needed for | Notes |
|---|---|---|
| **Claude Code** | everything | the brain is a Claude Code *plugin*; nothing here runs standalone |
| **git** | the install itself + the `SessionStart` auto-pull hook | any recent version |
| **Python 3** | the hook scripts, `health.py`, `docs.py`, the dashboard, `agent_view` | stdlib only for everything in this row. The repo pins nothing and has no requirements file. Author runs 3.14; any modern Python 3 works |
| **Pillow** | *required by the visual-diff engine* — `annotate.py`, `compare.py`, `shoot.py` | `pip install --user pillow`. It is imported INSIDE the functions that use it, so the modules import cleanly and only the calls fail; this table said "stdlib only" for weeks because of that, and two test files went red the first time CI ran. Not needed unless you use `/brain:visual-diff` |
| **Node.js** | *optional* — only the workflow syntax-checker (`scripts/brain/check-workflows.js`, an ES module) that `health.py` runs | if `node` is absent, `health.py` skips that one check on purpose ("node absent is not a brain problem") |
| **PyYAML** | *optional* — lets `health.py` validate agent/skill frontmatter | `health.py` guards `import yaml` in a `try/except` and skips the check if it is missing. `pip install --user pyyaml` if you want it |

Nothing above except Claude Code, git and Python is required to *use* the brain.
Node and PyYAML only make `health.py` more thorough.

Confirm the three that matter:

```bash
claude --version
git --version
python3 --version
```

---

## 2. The Claude base folder

Claude Code keeps everything under a per-user base folder. It is the **same
relative location on all three OSes** — only the absolute prefix differs:

| OS | Base folder |
|---|---|
| Linux | `~/.claude/` (= `/home/<user>/.claude/`) |
| macOS | `~/.claude/` (= `/Users/<user>/.claude/`) |
| Windows | `C:\Users\<user>\.claude\` |

Because it is `~/.claude` everywhere, every path in the brain's own hooks is
written as `~/.claude/...` and needs **no per-OS editing** (see §4).

What lives inside it:

| Path | What |
|---|---|
| `~/.claude/skills/` | personal-scope skills. **The brain installs here**, at `~/.claude/skills/brain`. Personal skills apply to *every project on the machine* — there is never a per-project clone |
| `~/.claude/agents/` | personal-scope agents (may not exist; the brain ships its *own* agents inside the plugin, namespaced `brain:…`, so this folder can be empty) |
| `~/.claude/settings.json` | your machine-level Claude Code settings (model, env, your own hooks). **This is yours, not the brain's** — see the note in §4 |
| `~/.claude/projects/<project-slug>/memory/` | per-project long-term memory (`MEMORY.md` and friends) |
| `~/.claude/plugins/` | Claude Code's plugin bookkeeping |

> Everything under `~/.claude/` *except* `skills/brain` is machine-local state
> — `sessions/`, `projects/*/memory`, `mcp.json`, `history`. The brain repo is
> deliberately confined to `skills/brain` and must never swallow the rest.

---

## 3. Installing the brain

One command. Clone the repo to exactly `~/.claude/skills/brain`:

```bash
git clone https://github.com/BranislavCuturilo/BRAIN.git ~/.claude/skills/brain
```

That is the entire installation. The folder contains
`.claude-plugin/plugin.json`, which makes Claude Code load it as a
**skills-directory plugin** (`brain@skills-dir`): its `skills/`, `agents/` and
`hooks/hooks.json` are discovered by directory convention. **No marketplace, no
`/plugin install`, no install script.**

The path must be **exactly** `~/.claude/skills/brain`. If you clone it somewhere
else (or into a nested folder), `brain@skills-dir` will not appear.

> **Copying instead of cloning?** If you are migrating from the Windows machine
> and copy the folder across rather than cloning, copy the working tree
> *including* its `.git` directory so `git -C ~/.claude/skills/brain pull` (the
> `SessionStart` hook) keeps working. Do **not** copy machine-local secrets that
> are gitignored and Windows-bound — see §7 (DPAPI).

Start Claude Code, then verify (see §6).

---

## 4. Path & shell differences from Windows

The good news: **the brain's own hooks are already POSIX-clean.** They live in
`hooks/hooks.json` inside the plugin and every path is `~`-relative, e.g.:

```jsonc
// hooks/hooks.json — SessionStart
"command": "git -C ~/.claude/skills/brain pull --ff-only -q 2>/dev/null || true"
"command": "python ~/.claude/skills/brain/scripts/brain/health.py --quiet 2>/dev/null || true"
```

So there is **nothing to rewrite** in the brain for Linux/macOS. The differences
you will actually meet:

| Concern | Windows | Linux / macOS |
|---|---|---|
| Home directory | `C:\Users\you\` | `~` = `/home/<user>` (Linux) / `/Users/<user>` (macOS) |
| Path separator | `\` | `/` |
| Base folder | `C:\Users\you\.claude\` | `~/.claude/` |
| Brain location | `C:\Users\you\.claude\skills\brain\` | `~/.claude/skills/brain/` |
| venv interpreter (only if you make one — see §5) | `venv\Scripts\python.exe` | `venv/bin/python` |
| Run a project's tests | `venv\Scripts\python.exe manage.py test` | `venv/bin/python manage.py test` |
| Persist an env var | `setx NAME "value"` (new terminals only) | `export NAME="value"` in `~/.bashrc` / `~/.zshrc` |
| Hook script paths | `~/.claude/skills/brain/...` (already `~`-based — no change) | identical |

### The one real snag: bare `python` vs `python3`

Every brain hook and health script is invoked as **`python …`**, not `python3`:

```
python ~/.claude/skills/brain/scripts/brain/health.py --quiet
python ~/.claude/skills/brain/scripts/brain/prompt_router.py
python ~/.claude/skills/brain/scripts/brain/require_skill.py
python ~/.claude/skills/brain/agent_view/hook.py start
```

On Windows the launcher provides `python`. On many Linux distros and on a stock
macOS there is only `python3` — a bare `python` is missing. Because every hook
ends in `|| true` (or swallows errors), a missing `python` fails **silently**:
the brain still loads its skills and agents, but the `SessionStart` health
check, the `UserPromptSubmit` router, the `require_skill` reminder and the
`agent_view` telemetry all quietly do nothing.

Fix it by making `python` resolve to Python 3 — pick one:

```bash
# Debian/Ubuntu — the packaged shim
sudo apt install python-is-python3

# Any distro / macOS — a user-level symlink on your PATH
mkdir -p ~/.local/bin
ln -s "$(command -v python3)" ~/.local/bin/python
#   ensure ~/.local/bin is on PATH (usually already is on modern distros)

# macOS with Homebrew Python also exposes `python3`; the symlink above is the
# simplest way to give the hooks a bare `python`.
```

A shell `alias python=python3` is **not** enough — hooks run as
non-interactive subprocesses that do not read your aliases. Use a shim/symlink
on `PATH`, or `python-is-python3`.

### `PYTHONIOENCODING=utf-8` — the č/ć/š/ž/đ gotcha

This is a **real** trap, carried by the skills (`stack-django`,
`dj-command.md`): a script that prints Serbian Latin (`č ć š ž đ`) to a console
whose encoding is not UTF-8 raises `UnicodeEncodeError` and dies **mid-run**.
On Windows the offender is the cp1252 console; on Linux/macOS the default is
already UTF-8, so you will usually *not* hit it — but a stripped locale
(`LC_ALL=C`, a bare cron/systemd environment, a minimal Docker image) reintroduces
it. For parity and safety, export it once:

```bash
# ~/.bashrc or ~/.zshrc
export PYTHONIOENCODING=utf-8
```

### About `~/.claude/settings.json` on this Windows machine

If you inspect the Windows author's `~/.claude/settings.json` you will find
hooks with hard-coded `C:\Users\you\.pixel-agents\hooks\claude-hook.js` paths.
**Those are not the brain.** They belong to a separate tool ("pixel-agents").
The brain registers its hooks through the plugin's own `hooks/hooks.json`
(shown above), which is already portable. On a fresh Linux/macOS machine you
simply will not have that pixel-agents block, and the brain does not need it —
do **not** copy those Windows paths into your POSIX `settings.json`.

---

## 5. Python venv + dependencies

**There is none to create, and nothing to install.** Confirmed by inspection of
the repo:

- no `requirements*.txt`, no `pyproject.toml`, no `setup.py`/`setup.cfg`
- no committed `venv/` or `.venv/`
- every hook script imports only the standard library

So the brain runs against **whatever `python` is on your PATH** (§4). The only
optional extra is PyYAML for a richer `health.py` (§1):

```bash
python3 -m pip install --user pyyaml     # optional, health-check nicety only
```

### The single optional venv: `agent_view/.venv`

`agent_view/start.py` will *use* a venv **if one already exists** next to it,
and otherwise runs on the current interpreter — the server is stdlib-only, so a
venv is never required. It is gitignored (regenerated per machine). Create it
**only** if you later add extras to the live view:

```bash
cd ~/.claude/skills/brain/agent_view
python3 -m venv .venv
source .venv/bin/activate          # Windows would be: .venv\Scripts\activate
# (no requirements file — install extras by hand only if you add any)
```

`start.py` looks for `.venv/bin/python` on POSIX and `.venv/Scripts/python.exe`
on Windows automatically.

> **Project code is different.** When the brain drives *your project's* Django
> app or tests, that always runs through **the project's** venv, never the
> brain's: `venv/bin/python manage.py test` (POSIX) vs
> `venv\Scripts\python.exe manage.py test` (Windows). The brain itself has no
> venv.

---

## 6. Verifying the install

**Skills / agents / plugin loaded** — start Claude Code, then:

```bash
claude plugin list
#   expect: brain@skills-dir ... loaded
claude plugin validate ~/.claude/skills/brain --strict
```

In a session, `/brain:` should autocomplete the skills (e.g. `/brain:craft-code`)
and the agents should appear in the agent picker (e.g. `brain:scout`,
`brain:reviewer`). `/plugin` has an **Errors** tab — glance at it once after the
first start.

**A hook actually fires** — the cheapest proof is the `SessionStart` health
check. Run it by hand exactly as the hook does:

```bash
python ~/.claude/skills/brain/scripts/brain/health.py
#   clean install prints little or nothing; problems are listed explicitly.
#   If this errors with "command not found: python", fix §4 first — the hooks
#   are failing silently for the same reason.
```

Then edit any `.py` file in a project during a session: the `PreToolUse`
reminder ("BRAIN: writing code — INVOKE /brain:craft-code …") should appear.
That proves `require_skill.py` and the inline hooks are wired.

**Regenerate the catalogue / dashboard** (also confirms the scripts run):

```bash
python ~/.claude/skills/brain/scripts/brain/docs.py       # rebuilds docs/AGENTS.md, SKILLS.md
python ~/.claude/skills/brain/scripts/brain/dashboard.py  # rebuilds docs/dashboard.html
```

**The Live Agent View server** (optional, stdlib-only) — binds `0.0.0.0:7666`:

```bash
cd ~/.claude/skills/brain/agent_view
python start.py                 # start + open browser at http://127.0.0.1:7666/
python start.py --no-open       # start only
python server.py --port 8080    # server directly, custom port
python server.py --host 127.0.0.1   # loopback only (no LAN exposure)
```

Because it binds `0.0.0.0`, you can open it from a phone or another machine on
the same LAN at `http://<this-machine-ip>:7666/`. Start Claude Code and work —
a "Sesija" tab appears the moment an agent runs.

---

## 7. Troubleshooting (Linux/macOS-specific)

| Symptom | Cause & fix |
|---|---|
| `brain@skills-dir` missing from `claude plugin list` | Wrong clone path. It must be **exactly** `~/.claude/skills/brain`. Re-clone there. |
| Skills/agents work but the health check, prompt router and agent_view telemetry are all silent | Bare **`python` is not on PATH** (only `python3`). The hooks call `python` and swallow errors, so it fails invisibly. Fix per §4 (`python-is-python3` or a `~/.local/bin/python` symlink). |
| A `python: command not found` when you run a script manually, but hooks look fine | Same root cause as above — you have `python3` but not `python`. |
| A brain script dies mid-run on Serbian text (`UnicodeEncodeError`) | A non-UTF-8 locale (`LC_ALL=C`, cron, minimal container). `export PYTHONIOENCODING=utf-8` (§4). Rare on a normal desktop locale. |
| `health.py` warns a workflow "will not parse" is missing, or you want workflow checks | `node` is not installed. It is optional; install Node.js if you want the workflow syntax-check, otherwise ignore — the brain treats a missing `node` as fine. |
| `health.py` never validates frontmatter | PyYAML absent (optional). `pip install --user pyyaml` if you want that check. |
| An agent is missing after you edited `agents/` or `hooks/` | Live edits to a `SKILL.md` are hot; changes to `agents/`, `hooks/` or `.claude-plugin/` need `/reload-plugins` or a new session. |
| `SessionStart` auto-pull does nothing | You have unpushed local commits, or you are offline — `git pull --ff-only` fails cleanly and silently by design. Pull/rebase by hand when you can. |
| `git -C ~/.claude/skills/brain pull` fails with "not a git repository" | You copied the working tree without its `.git/` folder. Re-clone, or restore `.git`. |
| Mail / Gemini features of `agent_view` do not work | Those are **Windows-bound extras.** The mail password is stored with Windows **DPAPI**, whose ciphertext is tied to the original Windows account and **will not decrypt on Linux/macOS** — you would re-enter credentials there. The core Live Agent View (agents, sounds, HUD) is fully cross-platform; only the mail/DPAPI add-ons are Windows-specific. |
| A secret ended up in git after editing `.mcp.json` | Never run `claude mcp add` against a file containing `${VAR}` — it expands and writes the resolved secret back. Set env vars in `~/.bashrc`/`~/.zshrc` (`export …`) and keep only `${VAR}` placeholders in `.mcp.json`. |

---

### One-line recap

```bash
git clone https://github.com/BranislavCuturilo/BRAIN.git ~/.claude/skills/brain
# ensure `python` (not just python3) is on PATH; start Claude Code; done.
```
