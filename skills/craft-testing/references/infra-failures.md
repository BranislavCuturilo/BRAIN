# The catalogue: what actually breaks a run

Extends `craft-testing/references/diagnosing.md`, which decides whether you are looking at an environment problem at all. This is the list of causes, roughly in the order they are worth checking.

**Do not debug any of these as bugs.** Start at the bottom of the list: the laptop going to sleep is mundane, invisible from inside the process, and produces every symptom the exotic explanations do.

## Infrastructure failures that look like code failures

**Separate the two in one command before reading a single traceback: count the
assertion failures.** If a run reports 45 errors and **zero** assertions failed,
then no test disagreed with the code and every one of those errors is
environmental. Measured: 90 connection-error lines, 0 `AssertionError`, on a
suite whose previous run had been fully green.

That count is worth more than the first traceback, because the first traceback
is whichever test happened to run when the environment gave out — it names an
innocent test and sends you to read innocent code.

**One shape defeats that count, and it is a shape you are likely to write
yourself.** A test that CATCHES exceptions into a list and then asserts the
list is empty — the usual way to check "all N screens render" in one method —
converts an infrastructure error into an `AssertionError`. The classifier then
reports a real code failure and points at your own test. Measured: a suite
whose only problem was the database host becoming unreachable mid-run
(`2002 Can't connect`) reported one assertion failure.

So when the count says one or two, read those specific ones before trusting
it. And when you write a collecting test, record WHY each entry failed, not
just that it did — `f'{url} raised {exc.__class__.__name__}'` keeps the
distinction visible in the assertion message itself, which is what saved this
one.

Do not debug these as bugs:

- **A wall of identical connection errors from unrelated tests mid-run** — the
  database connection dropped (idle timeout on a long multi-module run). Split
  into per-module invocations; they pass.
  - **Then MEASURE the timeout instead of working around it.** One query —
    `SELECT @@GLOBAL.wait_timeout` — turns weeks of "the suite is flaky" into a
    number. Measured on a shared host: **100 seconds.** Anything slower than
    that between two statements and the server has already hung up. Raise it
    for the session on the test connection (`init_command`, e.g. `SET SESSION
    wait_timeout=28800`); that touches no grant, no server config and nothing
    in production.
  - **Also bound the WAIT, or a dead socket hangs forever.** With no
    `read_timeout` the client blocks indefinitely reading a connection the
    server already closed — observed as a suite sitting 43 minutes on one test
    with the CPU idle and no error at all, which is worse than a red test
    because nothing signals that anything is wrong. Set it well past any
    legitimate statement; the point is turning an infinite hang into a
    diagnosable failure.
  - **A rolled-back suite AMPLIFIES a connection drop.** `TestCase` holds one
    transaction open across the whole class, so a drop kills the transaction
    and every remaining test in that class errors with "cannot execute queries
    until the end of the atomic block" — one blip, ten red tests, none naming
    the cause. Under a truncating base class each test stood alone and a drop
    cost exactly one. Worth knowing before concluding the rollback switch
    broke something.
- **Non-deterministic constraint errors or deadlocks on framework-internal
  tables** — two test processes are running against the same schema. Never run a
  background suite and a foreground one at once.
  - **The usual way this happens is not carelessness, it is a timeout.** A
    foreground suite that exceeds the tool timeout gets *moved to the
    background* and keeps running; the natural next move — "re-run it" — starts
    a second process against the same database. Before re-running any suite,
    confirm the previous one actually exited. A schema-creating suite is slow
    enough that this is the common case, not the rare one.
  - **Remembering does not work — remove the opportunity.** This rule was
    written, then refined, then broken twice more within the same session,
    because the failure mode is *forgetting a background run is still alive*
    while doing something else. The fix that holds is structural: **one shell
    command that runs the modules in sequence**, so a second concurrent run
    cannot be started by accident. A loop in one shell command beats four
    disciplined invocations.
  - **"One process" means one SHELL driving short-lived per-module processes —
    not one runner process executing every module.** These two readings look
    identical and fail in opposite directions, and the wrong one was chosen
    here precisely *because* the rule above says "one process". Handing the
    whole suite to a single `test <app>` invocation satisfies the concurrency
    rule and then violates the connection-lifetime one: 71 minutes on one
    connection, dropped partway, **352 connection errors and 0 real failures**,
    every test after the drop reported as broken code. The two constraints pull
    opposite ways and the loop of short processes is what satisfies both.
  - **Grep for the whole family of lock/connection codes, not the one you last
    saw.** MySQL: `1213` deadlock, `2006` server gone away, `2013` lost
    connection. A classifier that checks only two of the three reports
    "no connection errors" on a run that was nothing but.
  - **Check the OPERATING SYSTEM's process list, not your task list.** A suite
    started in a previous session survives that session's end, keeps the
    database, and appears nowhere in any list the current session can see. It
    is discovered only as unexplained lock errors in a run that should be
    clean. One command before starting a suite settles it.
  - **And that is still not enough: check the DATABASE's session list too.** A
    client that was killed mid-transaction leaves its SERVER-side session
    alive, holding row locks, with **no process anywhere on the machine**.
    Every later test touching those rows waits `innodb_lock_wait_timeout` and
    dies with `1205 Lock wait timeout exceeded`. Measured: 31 errors in one app
    from a single session that had been idle for 80 minutes.
    `information_schema.PROCESSLIST` finds it; nothing on the OS side does.
    Reap idle sessions on the test schema before a run — only that schema,
    only `Sleep`, only old, never your own.
  - **Raising `wait_timeout` REMOVES the cleanup that used to hide this.** The
    two fixes pull against each other and you need both halves: a short server
    timeout reaps orphans for free but kills long runs mid-class; a long one
    keeps the run alive and lets an orphan hold locks all day. Fix the drop by
    raising the timeout, then fix what that broke by reaping explicitly.
  - **Kill the LOOP, not its children.** Stopping the wrapper shell of a
    `for module in …; do run tests; done` script leaves the loop itself alive,
    and killing the test process it spawned merely advances it to the next
    module — so the collisions continue while every individual kill reports
    success. Find the process whose command line is the script and kill that,
    with its tree.
- **THE LAPTOP WENT TO SLEEP.** Check this before any of the above, because it
  is mundane, it is invisible from inside the process, and it produces every
  symptom the exotic explanations do.

  Standby suspends the process — CPU drops to zero — while a REMOTE database,
  which knows nothing about it, closes the connection on its own timeout. On
  resume the client blocks reading a socket that no longer exists. Nothing
  errors, nothing logs, and the run simply stands there.

  > Measured on Windows: `Kernel-Power` event **507** (modern standby) **27
  > seconds** after a suite started, and **eight** standby entries in six
  > hours. It accounted for a 43-minute "hang" with an idle CPU, and a run that
  > appeared to take twelve hours. Both had been attributed to locks.

  The event log settles it in one query — on Windows, `Kernel-Power` ids 42
  (sleep), 107 (resume), 507 (modern standby); on macOS, `pmset -g log`. **Do
  this before reading a traceback**, exactly like counting assertion failures:
  it is the cheapest question and it invalidates every other line of enquiry.

  Then remove the possibility rather than remembering it. A test runner can
  hold a wake lock for its own duration — on Windows
  `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`, deliberately
  WITHOUT `ES_DISPLAY_REQUIRED` so the screen may still go dark. It is
  per-thread state that lapses when the process exits, so a killed run stops
  asking and nothing is left altered.

- **A missing dependency surfacing as a middleware import error** — an
  environment gap. Install it; do not strip the dependency from shared
  configuration to work around a local problem.

- **A `UnicodeDecodeError` raised INSIDE the test client on a binary upload** —
  a settings literal, not your file. Django's `force_bytes` passes `bytes`
  through untouched only when `settings.DEFAULT_CHARSET` is the exact string
  `'utf-8'`; spelled `'UTF-8'` it takes the else-branch and decodes every
  payload as UTF-8 first, so a real PNG signature (first byte 0x89) blows up in
  `django/test/client.py` before the view is ever reached. Measured on a
  helpdesk repo whose `base.py` carried `DEFAULT_CHARSET = 'UTF-8'`.
  **Checkable:** before writing any upload test, `grep -i default_charset` the
  settings and compare the literal, character for character. Until it is fixed,
  keep test payloads valid UTF-8 — extension and declared content type are what
  upload validation reads anyway, not the file signature.
