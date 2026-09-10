# Fixtures, doubles and the data they stand for

Extends `craft-testing`. Read when writing or changing a fixture, a factory, a stub or a captured response — and when a test fails only after some other test has run.

Every rule here is a way a test can be well written, over live code, and still incapable of failing: the data it built does not contain the case it describes.

## Fixtures in a shared database

- **A fixture that names something REAL on the developer's machine passes for
  everyone except its author.** The usual shape is a path, a hostname or a port
  chosen because it is the realistic example — and the assertion is about what
  happens when that thing is ABSENT. On the one machine where it is present the
  code correctly takes the other branch, and the test fails for its own author
  while being green in CI and on every colleague's laptop.

  > Measured, 2026-08-31: `repo_path: legacy absolute re-rooted when missing`
  > used `C:/projects/popis`, the real legacy project root. On the machine the
  > ticket store was born on that directory exists, so `p.exists()` won, the
  > real path came back, and the assertion failed — the first time anything had
  > ever run the suite.

  **Name something that cannot exist** (`.../__no_such_repo__`), and derive it
  from the constant under test rather than retyping it, so a change to the
  constant cannot leave the fixture pointing somewhere real.

  **The machine is not only its disk — it is also its clock and its OS.** The
  same shape appeared three times in two days, and only the first one looks
  like a path:

  - *a real path*, above;
  - *a timezone*. A fixture paired a naive timestamp at 12:00 with an aware one
    at 14:00+02:00. Where a parser reads a naive stamp as LOCAL and compares in
    UTC — which is correct, because that is how the store writes them — those
    are two hours apart in CET and **the same instant** on a UTC machine, so
    `later > earlier` is False. Two assertions passed on the author's desktop
    and failed on every CI runner. **Make the gap exceed any real UTC offset
    (+14 to −12): use a different DAY, not a different hour**, and prove it by
    running the file under `TZ=UTC` and `TZ=Etc/GMT+12`, not by reasoning about
    it.
  - *an OS*. `Path("C:/projects/x").is_absolute()` is **False** on POSIX, so a
    whole branch of the code under test is unreachable there and the assertion
    describes behaviour that cannot happen. Gate such a test on the platform
    and say why — the same move as gating on the database engine below.

  The generalisation worth keeping: **a fixture is an assumption about the
  environment, and the ones that hurt are the assumptions you did not know you
  were making.** The cheapest way to find them is to run the suite somewhere
  else once — a CI runner found all three in a single afternoon, after the
  suite had been green for weeks.

- **Randomise every column under a composite uniqueness constraint** — not just
  the obvious string ones. A fixture hardcoding a small enumerable value
  (`depth=0`, `order=1`, `position='primary'`) collides the moment another test,
  or a crashed test's orphan, already left that value. The failure is
  **order-dependent**: green alone, red after the other test runs first, which is
  the most expensive kind of failure to diagnose.
- **A script that mutates a SHARED fixture must restore FIELDS, not only delete
  ROWS.** Cleanup is usually written as "remove what I created", which misses
  what was *changed*: a seed that flipped the shared tenant's language to run a
  screenshot deleted its own rows afterwards and left the language set, so
  every later test that rendered a page got the wrong one — and the suite
  failed in a module the change never touched. Write the teardown from the list
  of things the setup TOUCHED, not the list of things it INSERTED, and restore
  each to the value it had (or the documented default). The tell is a failure
  in an app unrelated to the work in progress.

- **Data inserted by migrations does not survive a flush.** A test relying on
  seeded reference data sees an empty table and the code under test silently does
  nothing — surfacing as a confusing wrong-value assertion rather than a missing
  row. Create what you depend on in setup, every time. Treat the database as
  empty of seed data at the start of every test class.
- **Track what you create and tear down only that.** Never delete by wildcard;
  the next test's fixtures are in the same tables.
- **A test exercising a limit, override or default must set the layer the code
  actually reads — the top of the precedence chain.** Setting the fallback proves
  nothing when a higher-precedence override exists (possibly left by another
  test), and the assertion fails looking exactly like a code bug.

## A sandbox that does not reproduce production's DATA SHAPE proves nothing

Everyone checks that the sandbox has the same endpoints. Almost nobody checks
that it returns the same *shape* of data — and the shape is what your parsing
and null-handling are written against.

> Measured against a national tax API: on the demo environment one field came
> back `null` for **every** record; on production it was populated in **100%**.
> A different field was empty in **91%** of production records. Test your
> empty-value handling on that sandbox and you have proven the opposite of what
> you think — you exercised a case production never sends, and left untested the
> one it always sends.

**Fixtures come from captured PRODUCTION responses**, not from the sandbox and
not hand-written. A hand-written fixture encodes what you believed the contract
was, and that belief is exactly what is under test.

Where production data cannot be stored (personal data, secrets), keep the
**shape** — same keys, same casing, same emptiness pattern, same date formats —
and replace only the values. A fixture differing from production only in values
still tests the parser; one differing in shape tests nothing.

## A stub written FROM the implementation agrees with nothing else

A hand-built double is shaped by reading the code it stands in for. When that
code is wrong about the real schema, the double is wrong in exactly the same
way — so the test passes, and the two of them agree with each other and with
nothing in the database.

> A price check read `order.lines`. No such relation existed anywhere in the
> schema; the method returned nothing on every call, and two tunable
> thresholds in the settings screen guarded code that could never run. Its
> test stubbed `order.lines` — read straight off the method — and passed,
> including the negative cases, which passed for the wrong reason. The defect
> was found by someone reading the SCHEMA, not by the suite.

So: **build the double from the schema or the captured response, never from
the caller.** Cheap check that catches it — for every relation or attribute a
double provides, confirm the real model actually declares it. If a stub is the
only reason a code path is reachable, that path has no coverage at all.

And when the implementation is later corrected, **the stub is part of the
change**. A stub left mirroring the old shape turns a real fix into a red test
and invites someone to revert the fix instead.
