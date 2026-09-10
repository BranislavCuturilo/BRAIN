# Telling an infrastructure failure from a code failure

Extends `craft-testing`. Read when a suite is failing, flaky, hung, or reporting far more errors than you changed anything to cause.

**Count the assertion failures first.** If a run reports 45 errors and zero assertions failed, no test disagreed with the code and every one of those is environmental. That count is worth more than the first traceback, which names whichever innocent test happened to be running when the environment gave out.

Once it is environmental, the catalogue of actual causes — in the order they are worth checking — is `infra-failures.md`.

## `suite | tail` throws the exit code away

A pipeline's exit status is the **last** command's. `manage.py test … | tail -20`
exits 0 because `tail` succeeded — the suite's own failure is gone, and any
wrapper that reports "exit code 0" then reports success for a run that failed.

Watched it happen: a background suite reported **exit code 0** while its own last
line read `FAILED (errors=14)`.

Read the output, not the status, whenever a command is piped — or keep the status
by piping the *file* instead: run with `> out.txt 2>&1`, then `tail` the file
separately. This is the silent-success class one layer out from the code: not a
check that cannot fail, but a **failure that cannot be seen**.

## A timeout near the measured runtime fails as a function of machine load

A per-test timeout is meant to catch a hang. Set it close to how long the test
actually takes and it stops doing that job and starts doing a worse one: the
test passes when the machine is idle and "times out" when it is not, so the
suite's result depends on what else you happened to be running.

**The report is what makes it expensive.** It says `timed out`, which reads as a
broken or hung test — so the reflex is to debug the test, or to delete it as
flaky. Neither is the problem. The budget was never big enough.

> Measured, 2026-08-31: a browser-driven capture test took **102.7s** and
> **103.0s** against a **120s** default. Two runs of the same suite minutes
> apart, same machine, no code change between them: **20 passed / 0 failed**,
> then **19 passed / 1 failed**. The only variable was what else was running.

- **Measure before setting one.** Two runs, and read the seconds.
- **Give an integration test several times its measured runtime.** The number
  only has to be small enough to catch a genuine hang, and a hang is unbounded
  — there is nothing to trade off against.
- **A slow integration test is not a unit test.** Tier it explicitly so it can
  be skipped in a fast lane and named when it is, rather than silently
  inflating the timeout for everything.

Its sibling is the concurrency rule below: running two suites at once is one of
the loads that pushes a tight timeout over the edge, so the two failures arrive
together and look like one mysterious problem.

## A precondition you must REMEMBER before every run is a defect in the process

A rule of the form "clean X before running the suite" fails the way all
memory-based rules fail: not once, but every time attention is elsewhere —
which is exactly when a long run is most expensive.

> Measured in one session: a screenshot fixture wrote a row the suite's own
> fixtures also create. Every test in the app then died in setUp with a
> duplicate-key error — **N errors, zero assertion failures**, four separate
> times, each costing a full run. The rule "remember to clean first" was
> written down after the second one and broken twice more.

**The fix is never a firmer resolution; it is one command that cannot be run
the wrong way.** A script that performs the precondition and then the suite
removes the opportunity to skip it. Same shape as the concurrency rule above:
a loop in one shell beats four disciplined invocations.

Two things worth building into that wrapper while you are there:

- **Print the assertion-failure count next to the result.** "N errors, 0
  assertions" is the cheapest signal that separates an environment problem
  from a code problem, and it is the first question worth asking.
- **Read the result from the log, not from the exit code**, because the
  pipeline that writes the log has already thrown the suite's status away
  (see `suite | tail` above).
