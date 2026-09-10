# Testing the brain

This document is the ordered path from "it exists" to "it works", and each level
produces evidence you can point at. Do them in order — a failure at one level
makes the levels above it meaningless.

**Three of these levels are now automated and gate every push**
(`.github/workflows/brain.yml`):

```bash
python scripts/brain/tests.py --fast   # the scripts still work    (level 1)
python scripts/brain/evals.py          # the brain still fires     (level 2)
python scripts/brain/health.py         # nothing is broken or stale
```

`evals.py` is the one with no manual substitute: it drives the real hooks
through their real stdin/stdout contract and reads the real skill files, so a
skill that has been rewritten into something that no longer triggers fails here
instead of failing on a customer's ticket. See [`evals/`](../evals/README.md).
The paid tier (`--behaviour`) runs `claude -p` against a fixture and asserts on
what was actually written to disk; it is the only level that proves a rule
changed the outcome rather than merely firing.

---

## Level 0 — does it load

```bash
claude plugin list
claude plugin validate ~/.claude/skills/brain --strict
```

**Pass:** `brain@skills-dir ... loaded`, and validation passes with no warnings.
Also open `/plugin` → **Errors** once; a silently failing component appears only
there.

**If it fails:** the path must be exactly `~/.claude/skills/brain`, and
`.claude-plugin/plugin.json` must exist inside it.

---

## Level 1 — do the tools work

Each one answers a question. Run it and read the answer; a script that runs
without error but prints nothing useful has still failed.

```bash
cd ~/.claude/skills/brain
python scripts/brain/health.py            # should end with "No problems"
python scripts/budget.py                  # cost table; flags anything over budget
python scripts/brain/usage.py             # real counts from transcripts
python scripts/brain/docs.py --check      # "docs up to date"
python scripts/brain/dashboard.py --open  # opens the page
python scripts/tickets/board.py           # your real helpdesk queue
```

**The one that proves the most:**

```bash
cd <a Django repo>
python ~/.claude/skills/brain/scripts/map/concept.py <AModelName> --root .
```

**Pass:** it finds the model's definition at the right file and line, and the
reference counts are plausible. If it reports 3 references for a model you know
is used everywhere, the classifier or the root is wrong.

---

## Level 2 — do the hooks fire

1. **Start a new session.** The session-start hooks run a `git pull` and
   `health.py --quiet`. **Pass = you see nothing**, because nothing is wrong.
2. **Make health fail on purpose:** edit any `SKILL.md`, do not commit, start a
   new session. **Pass:** it reports the uncommitted change. Commit, start again,
   silence returns.
3. **Edit a `.py` file in any project.** **Pass:** a `BRAIN:` reminder appears
   before the edit.
4. **Edit a file under `tests/`.** **Pass:** the testing reminder appears.

If a hook never fires, check `claude --debug` — hook matches and exit codes are
logged there.

---

## Level 3 — does a skill load when it should

Skills load on description match, so this tests the descriptions, not the
content.

| Say this | Expect |
|---|---|
| "I need to add a file upload field to a model" | `craft-security` loads (files/uploads) |
| "why is my migration failing on the live table" | `stack-django` and/or `mysql-consultant` |
| "we keep doing this same sequence, automate it" | `ops-workflows` |
| "which model should I use for this subagent" | `ops-models` |

Check `/context` to see what actually loaded.

**Fail = the wrong skill loads, or none does.** That is a *description* problem,
not a content problem — fix the `description`/`when_to_use`, not the body. Record
it: `score.py record skill <name> hindered "did not trigger on X"`.

---

## Level 4 — does an agent actually work

This is the first level that tests anything real. **Start small and cheap.**

```
Use the scout agent to find every place Stocktake.objects is queried.
```

**Pass:** it returns file:line locations and a one-line conclusion, **not** a
dump of file contents. If it pastes whole files, its brief is not working.

Then a reader:

```
Use dj-model-reader on stocktaking/models_core.py for the Stocktake model.
```

**Pass:** a compact spec — fields with types, the scope FK, constraints, what
`clean()` enforces, suggested list columns. Short enough to hand to a writer.
**Fail:** it quotes the model back at you; it is copying, not extracting.

Then something with judgement:

```
Use the reviewer agent on my last commit.
```

**Pass:** findings ordered by severity, each with a concrete failure scenario, and
an explicit statement of what it did *not* reach. **Fail:** style nits, or
approval with no coverage statement.

**Record every one:**

```bash
python scripts/brain/score.py record agent scout helped "found all 9 call sites"
```

---

## Level 5 — does the orchestrator choose well

The real open question: **can anything pick correctly from 52 agents?**

```
Use the orchestrator agent: add a read-only list screen for RegistryItem
in the stocktaking app.
```

**Pass:** it sends a reader first, then `dj-list-view` (not `backend-junior`,
not itself), and it does not implement anything. **Fail:** it picks a general
agent, or starts writing code.

Then something that should *not* fan out:

```
Use the orchestrator agent: fix the typo in the heading of stocktake list.
```

**Pass:** it says this does not need delegation. **Fail:** it spawns an agent —
the "do not delegate what you could finish in a handful of tool calls" rule is
not landing.

---

## Level 6 — does a workflow run

```
Run the workflow at ~/.claude/skills/brain/workflows/authz-sweep.js
with args {"apps": ["stocktaking", "audits"]}
```

**Pass:** it maps each app's visibility helper, lists by-pk endpoints, and
returns confirmed findings plus an explicit `unverified` list. `/workflows` shows
the phases running.

**This is the highest-value test in the document** — it exercises `scout`,
`security`, `pipeline()`, adversarial verification and the explicit-coverage rule
at once, over the 538 bare `View` classes that are the real risk surface.

**Fail modes worth distinguishing:** a JS error (the script), an agent returning
the wrong shape (the schema), or plausible findings that do not survive
verification (working as designed — that is what the refute stage is for).

---

## Level 7 — does the measurement loop close

After levels 4–6 have actually run:

```bash
python scripts/brain/usage.py             # the agents you used now appear
python scripts/brain/score.py review      # your recorded outcomes
python scripts/brain/dashboard.py --open  # non-zero usage column
```

**Pass:** the dashboard is no longer all zeros, and "not seen in any transcript"
has shrunk.

**This is what makes every later decision evidence-based instead of a guess.**
Until it runs, the dashboard is a well-formatted empty page.

---

## What a failure means

| Where it fails | What is actually wrong |
|---|---|
| Level 0–1 | installation or a script bug — fix and move on |
| Level 3 | a **description**, never the body |
| Level 4 | the agent's brief — too broad, wrong tools, or wrong grade |
| Level 5 | too many agents, or the selection guidance is not enough. **This is the open question**; if it fails, the answer is fewer agents, not more instructions |
| Level 6 | the workflow script, or a schema mismatch |

Record failures as honestly as successes:

```bash
python scripts/brain/score.py record agent <name> hindered "<what went wrong>"
```

A registry where everything helped is a registry nobody is reading.

---

## Ongoing

Once through the levels, testing becomes routine use plus:

- **`health.py` at session start** — automatic, silent unless broken.
- **`score.py record`** at the moment an outcome is clear, either direction.
- **Monthly:** `/brain:ops-maintain` — structure (`brain-keeper`), usefulness
  (`score.py review` + dashboard), reasoning (`archivist`).

The kill criteria are already written down and should be honoured: an agent that
keeps producing work you throw away is **retired**, and the consult chain is
**deleted** if three runs produce what a capable executor would have produced
alone.
