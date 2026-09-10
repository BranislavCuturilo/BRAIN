# scripts/

Deterministic tools. **Anything a script can answer exactly, a script should
answer** — an agent reading five files and reasoning about them costs tokens,
takes seconds, and can be wrong. One command costs nothing and cannot be wrong.

The agent's job is to *decide what to do about* the output, not to compute it.

```
scripts/
├── budget.py           what the brain costs: always-on, per-skill, per-agent
├── brain/              tools for the brain itself
│   └── score.py        record and review skill/agent outcomes
├── tickets/            the helpdesk queue across every project
│   └── board.py        one cross-project board, no file reading
├── visual/             before/after screenshots for a VISUAL change
│   └── shoot.py        the one door: before | after | sweep | baseline
├── temp/               scratch — see below. gitignored.
└── archive/            superseded scripts, kept for reference
```

## Organisation is mandatory

A flat pile of scripts becomes a pile nobody reads, and then the same script gets
written a third time. **One subfolder per area**, named after the area, not after
who wrote it or when.

A script belongs in a subfolder from the moment it has a second user or a second
use — see graduation.

## temp/ and graduation

`scripts/temp/` is scratch. Anything written for one task starts there: a
one-off parser, a quick count, an experiment. It is **gitignored** — it does not
sync, it does not clutter, and nothing is expected to survive.

**Graduation rule: the second time you need a script, it leaves `temp/`.**

The clearest signal is a **repeated filename**. If you are about to write
`temp/parse_tickets.py` and something by that name is already there — or was
there last week — that is not a coincidence. It is a tool, it just has not been
put anywhere yet. Move it to the right subfolder, give it a docstring saying what
it answers, and commit it.

Graduating means:

1. Move it into the subfolder for its area (create one if needed).
2. Add a one-line docstring: **what question does this answer?**
3. Take out the hardcoded paths that made sense for one run.
4. Add its row to the tree above.
5. Commit it. A script that only exists on one machine is not a tool.

## archive/

A script replaced by a better one moves to `archive/` rather than being deleted,
with a line at the top saying what replaced it. Deleting loses the reason it was
written; keeping it in place means two scripts do the same job and nobody knows
which is current.

## Writing one

- **Answer one question.** A script with a mode flag is usually two scripts.
- **Read-only unless the name says otherwise.** Anything that writes says so in
  its name and prints what it changed.
- **ASCII output only.** The Windows console (`cp1252`) cannot encode `č ć š ž đ`
  or an em dash, and the crash happens **mid-run**, leaving whatever the script
  was doing half-done. The failure is the console encoder, never the data.
- **Exit non-zero on failure**, so a pipeline can gate on it.
- No dependencies beyond the standard library unless there is a real reason.

## map/

| Script | Answers |
|---|---|
| `concept.py <Name>` | Where does a concept live and what touches it. Every reference, classified: definition, foreign keys, ORM queries, imports, templates, URL names, settings, migrations, tests, plain strings. `--json` for another tool, `--context` to see the lines. |

This is the mechanical half of an impact analysis. `impact-mapper` reads its
output and explains the flow; the script does the finding, exactly and
completely, in seconds.

## visual/

| Script | Answers |
|---|---|
| `shoot.py before\|after --repo <p> --ticket <id> [--files ...]` | What did this visual change look like before, and what does it look like now — per page and per STATE (modals, dropdowns, tabs), one picture per distinct look. The pair is PRE + POSLE + the screen name; it never claims what changed. No ticket, no pictures. |
| `shoot.py sweep --repo <p>` | Did any screen change structurally since the last sweep (DOM + geometry, never pixels — the dev database is production). |
| `shoot.py baseline --repo <p>` | Refresh `<repo>/.visual-baseline/` so the other machine has the same "before" after a pull. |

Routine, exit codes and the config contract: `skills/visual-diff/SKILL.md`.
Playwright and Pillow are the two dependencies beyond the stdlib, and both are
load-bearing — there is no screenshot without a browser.

**A script that runs another project's code IN ITS OWN PROCESS shares that
project's import namespace and its asyncio state.** `visual/` loads the repo's
auth driver, which calls `django.setup()`, so:

* it is a **package** and its modules import each other relatively — a flat
  `sys.path.insert(0, HERE); import config` made `sys.modules['config']` the
  engine's module, and every Django project laid out as `config/settings/…` died
  with `'config' is not a package`, reported as an auth failure. `pages`,
  `store` and `sync` are the next names waiting to collide;
* anything outside the package is loaded **by path under a namespaced alias**
  (`visual/isolate.py`), never by putting another brain directory on `sys.path`;
* process-wide state that the app's code overwrites — the Windows event-loop
  policy is the one that bit — is asserted **immediately before the call that
  needs it**, not at the top of the run.

Write a new repo-driving script the same way, or copy `visual/__init__.py`'s
prologue and read its docstring first.

## The three measurements, and why they only mean something together

`budget.py` says what something costs, `brain/usage.py` counts how often it is
actually used (from session transcripts), `brain/score.py` records how well it
worked. A cheap agent nobody uses and an expensive one used constantly look
identical in any one of them.

`brain/dashboard.py` puts all three in one self-contained HTML page:
`docs/dashboard.html`. Open it with `--open`.
