---
name: craft-testing
description: >
  Test discipline that holds in any language: keeping the suite away from real
  data and real side effects, writing fixtures that survive a shared database,
  assertions that cannot pass vacuously, and telling an infrastructure failure
  apart from a code failure. Load BEFORE writing, modifying or running any test.
  Includes the data-loss and silent-upload post-mortems that produced these rules.
when_to_use: >
  writing or editing a test, running a suite, "run the tests", a flaky or
  order-dependent failure, a wall of identical errors mid-run, setting up test
  configuration, deciding what to cover for a new feature.
---

# Testing craft

Test discipline that holds in any language. **The one rule that cannot wait for
a reference is below**; everything else is routed, because a rule you did not
read is a rule that did not apply.

## The suite must not be able to touch production

- **The test database is a physically separate database.** Not the same database
  with different rows, not "protected" by a scope column.
- **Why, concretely:** transactional test base classes truncate every table
  between test classes. Truncation is table-level — it ignores your scope column,
  your tracked-primary-key cleanup, and every other row-level protection you
  built. A suite pointed at production wipes it entirely, schema intact, in one
  run. Row-level isolation protects reads and targeted writes; it is *worthless*
  against a table-level flush.
- **Two independent guards, both pinned by a test:** configuration refuses to
  start when the test database name equals production's, and the base test class
  re-checks the *live connection* at setup. Never remove either. Config can be
  overridden by an environment variable; the live-connection check cannot.
- Scope isolation stays as defense in depth, not as the wall.

## Read on demand

| Doing | Read |
|---|---|
| running a suite here for the FIRST time; adding email/storage/queue/webhook to a dev config; a test that shells out; a gate test that mocks nothing | `references/side-effects.md` |
| writing a fixture, factory, stub or captured response; a test that fails only after another test runs | `references/fixtures.md` |
| writing any assertion; adopting a lint, type check or validator | `references/assertions.md` |
| a suite that wraps each test in a transaction; asserting that a write FAILS | `references/transactions.md` |
| the suite is failing, flaky or hung, and it may not be the code | `references/diagnosing.md` |
| it IS environmental — which cause, and in what order to check | `references/infra-failures.md` |

## Working rules

- **Write the test alongside the feature, not after.** Retrofitting during an
  audit costs several times more, and the audit only happens if someone schedules
  one. Ask which edge cases matter in the domain before writing them — the
  combinations that behave differently are rarely the ones you would guess.
- **When you delete or rename a test module, update its package index in the same
  change.** A dangling import breaks discovery for the *entire* suite, and the
  error names the missing module, not the feature you were working on.
- **Prefer running only the modules you changed** while iterating; run everything
  before shipping.
- **When the suite's FIXED cost dwarfs its marginal cost, batch the runs.**
  "Test after every step" silently assumes a cheap suite. Where the runner
  spends twenty minutes building a schema before the first assertion, running it
  per step buys one bit of information for an hour of wall-clock — and buys it
  for code that has no consumers yet, so nothing can be harmed in the meantime.
  Write the whole batch, run once, and bisect with git if it goes red.
  Static checks stay per-step: they are free, and they catch most of it. Never block on a long suite when one targeted run answers the
  question — and if a suite is slow enough that you are tempted to skip it, say
  so and fix the slowness rather than quietly skipping.
- **Run the suite proactively after high-blast-radius changes**: schema,
  permissions, scoring or money, workflow state, isolation. Skip it for
  documentation and copy.
- **A pipeline's exit code is the LAST command's, so never judge a suite by
  it.** `runner ... | grep ...` reports grep's status, and `| tail` reports
  tail's — both are 0 whenever the filter itself succeeded, which is almost
  always. Worse, the filter can hide the verdict too: a runner that prints
  `Ran N tests / FAILED` on stderr while the tests themselves print to stdout
  will have that line pushed out of a `tail -N` window by ordinary output.
  Two failing suites were reported as green this way in one session, and the
  second one had been pushed. **Redirect to a file, keep the real status, then
  read the file**: `runner ... > out.log 2>&1; echo "EXIT=$?"; grep -E
  '^Ran |^OK$|^FAILED' out.log`. If you must pipe, set `pipefail` — and either
  way, quote the verdict line, never the exit code alone.
  **And delete the log before the run.** A verdict read from a file the
  runner never rewrote is the previous run's verdict: in one chain a `grep`
  step before the runner exited 1, `&&` skipped the runner, the old log
  still said `fail 0`, and a commit with a red test was pushed. `rm -f
  out.log` first, or write the run's own marker into the log and assert it.
  **It recurred the same day**: the `rm -f` was placed INSIDE the gated
  chain (`grep -q ... && rm -f log && runner`), so when the gate failed the
  deletion was skipped too and the old log was read again. Delete the logs
  unconditionally, as the FIRST command (`rm -f a.log b.log; ...`), never
  behind `&&` or a conditional. The mechanism is `craft-git`'s fail-fast rule
  (a step whose "nothing found" exit is non-zero kills the chain) showing up
  in a test chain -- read it there; this bullet only adds the stale-log symptom.
- **A test that goes red after you delete code has THREE possible meanings, and
  "the code is gone, so delete the test" is the rarest of them.** The failure
  cannot tell them apart on its own, so triage before touching anything: read
  `git diff` / `git log -p` for what the deletion actually removed, then decide
  which of these it is.

  | What happened | What to do |
  |---|---|
  | The GUARANTEE went with the code | delete the test, and name in the commit which guarantee no longer exists |
  | The guarantee MOVED somewhere else | repoint the test at its new home |
  | The guarantee is alive; only the FIXTURE became unbuildable | rebuild the fixture, keep every assertion |

  The third case looks identical to the first — the test names the deleted
  symbols, so it reads as a test OF them. Measured case: retiring two code-list
  models turned nine tests red across three apps; all nine named the deleted
  models, and **all nine were the third case** — they guarded "filter the axis
  the screen displays", which was untouched, and merely built their fixture out
  of the retired models. One of the nine was the test that had blocked a
  widening measured at 3 users going from 0/23/5 records to all 80. A rule of
  "the only failures are tests for deleted code, so drop them" would have
  deleted it.

  A deleted test is only honestly deleted when you can say where its guarantee
  went. If the answer is "nowhere", say that out loud — it may still be right,
  but it is a decision, not cleanup.
- Report failures with the actual output. A test that fails is a result, not an
  embarrassment; a test reported as passing when it was skipped is a lie.
