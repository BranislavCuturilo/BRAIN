# Assertions that cannot pass vacuously

Extends `craft-testing`. Read BEFORE writing an assertion, and before trusting a check, a lint or a validator you have just adopted.

**The only way to know an assertion can fail is to make it fail.** Reasoning about it is exactly the step that produced the vacuous one.

## Assertions that cannot pass vacuously

**The only way to know an assertion can fail is to make it fail.** Reasoning
about it is exactly the step that produced the vacuous one. After writing a
test, break the thing it claims to pin — revert the branch, restore the filter,
delete the column — run that one test, and require RED. Restore in a `finally`
so a crash cannot leave the mutation behind, and run the mutations as a batch so
the check is cheap enough to actually do. Two assertions in one recent change
survived their own mutation and were rewritten because of it; every rule in this
section is a shape that a mutation check would have caught the first time.

- **A mutation that stays GREEN has a THIRD explanation, and it is about the
  FIXTURE.** Before concluding the test is weak or the code is dead, check
  whether the data contains a row the mutation would actually change. A test
  can be well written, over live code, and still be incapable of failing —
  because the case it describes is absent from what it built. Two shapes,
  caught in one session on one module:
  - *An agreement test* — "screen A shows exactly what screen B shows" — is
    vacuous unless the fixture holds a row the drift would move. A narrowing
    (`filter(is_active=True)`) added to one side removed NOTHING, because every
    row in the fixture was active. One closed row turned the same mutation red.
  - *A cost test* — "the query count does not follow the row count" — is
    vacuous unless the fixture is deep or wide enough to defeat whatever
    already optimises it. A parent chain one hop deep is fully covered by
    `select_related('parent')`, so deleting the cache-warming changed nothing;
    at two hops the same deletion went from 5 queries to 29.

  The tell is that both passed *the first time they were written*, which reads
  as confirmation and is actually the warning: a test written to describe a
  regression should have to be shown failing. **Run the mutation before
  trusting the green**, and when it stays green ask which row is missing
  before rewriting the assertion.
- **A mutation that stays GREEN has two explanations, and the second one is
  about the CODE.** The reflex is "my test is weak", and usually it is. But the
  other possibility is that the line you deleted NEVER RAN — that the guard is
  unreachable by construction — and no amount of rewriting the test will show
  you that. Decide which before touching the test: put a `raise` where the
  mutation went and see whether anything reaches it. A guard that cannot be
  made to fire is not a guard; it is dead code that tells every future reader
  the case is handled. Delete it, write down why it could not fire, and pin the
  thing that ACTUALLY handles the case instead. Caught on a tree walk whose
  cycle guard survived deletion: with a single-parent FK, every member of a
  cycle has its parent inside the cycle, so no member is a root and a walk
  entered only at roots can never reach one. The real protection was a
  reachable sweep — "anything the walk did not render is reported" — and once
  that was what the test pinned, the mutation went red.
- **An "X is absent" assertion passes for free when you are looking at the wrong
  place.** If a refactor moves the thing you are checking, a presence assertion
  fails loudly and an absence assertion silently keeps passing forever. **Pair
  every absence assertion with a presence assertion** against the same target.
- **Drive the real entrypoint, and the real actor.** A test that authenticates as
  a privileged user the middleware treats specially, or requests a route the
  framework redirects, asserts against a response the feature never produced.
- **A shared fixture that omits a section the production path reads makes every
  test in the file vacuous — and they all still pass.** The fixture is written
  once, early, from what the assertions of the day needed; a later feature reads
  a section nobody put in it, takes its "nothing here" branch, and dozens of
  assertions go on measuring the step BEFORE the one that matters. Nothing fails,
  so nothing points at it. What finds it: for every terminal effect the feature
  is supposed to produce — a row deleted, a folder gone, a file written — assert
  THE EFFECT at least once, not only the predicate that decides it. A file where
  every case stops at `is_finished() is True` and none checks the thing is
  actually gone has probably never reached the delete.
- **Assert against the smallest thing that carries the claim, never the whole
  page.** A short or common string checked against a full rendered response is
  matched by chrome you never thought about: a test claiming "an empty cell
  renders a dash" passed on the em dash the page layout puts in its own
  `<title>`, and went on passing with the column deleted. The sibling failure
  is a fixed character WINDOW around a field name — it reaches the previous
  field's markup, so it goes red on a whitespace change while the code is
  still right (measured once at ten characters of slack). Slice to the row, the
  element, or the matched tag itself, then assert inside that.
- **Every command-line entrypoint needs a smoke test.** A command module is only
  imported when it is invoked, so a syntax error in one ships happily while the
  service it calls is fully unit-tested. One invocation with side effects
  suppressed proves the entrypoint parses, imports and wires arguments through.
- **Do not assert a database-level error the engine does not actually raise.**
  Some engines silently ignore conditional unique constraints. Gate such a test
  on the engine, and test the service-layer guard instead — that is the real one.
- **"This must NOT have been called" is worthless if the call is DEFERRED to a
  commit.** In a rolled-back test the commit never happens, so the callback
  never runs, so the mock is never called — and the assertion passes even when
  the feature it guards is completely broken. Its positive twin fails loudly
  and gets fixed; the negative one just goes quiet, which is the worse half.
  Run the callbacks explicitly (Django: `captureOnCommitCallbacks(execute=True)`)
  so the assertion means something again.
  - **Grepping the TESTS for the deferral mechanism does not find these.** The
    `on_commit` lives in production code; the test only mocks the function it
    eventually calls, and may be two hops away. What does find them: for every
    negative mock assertion, check whether the same file has a POSITIVE one
    that still passes. If it does, the path is synchronous and the negative is
    sound. A file with only negative assertions about a deferred call is the
    one to look at.

- **A fixture chosen from the CONCEPT instead of the failure mode passes while
  the bug ships.** A test named "angle brackets are escaped, not swallowed" used
  `a < b and 5 > 3` — and `<` followed by a space is the one shape an HTML
  parser does not read as a tag, so it survived every implementation including
  the broken one. `<ENTER>` was being deleted the whole time. The test's name
  described the risk correctly and its data dodged it. **Pick the input by
  asking "what exact value would break this?", not "what is an example of this
  concept?"** — and where the risk is a class of inputs, table-drive it over
  several members of the class rather than the one that came to mind first.
  A test that cannot distinguish the fixed code from the broken code is
  decoration.

## Prove the check can fail

**A verification command that has never failed has not been verified.** Before
trusting one, feed it something you know is broken and confirm it says so.

This is the sibling of the vacuous-assertion rule above, one level up: there the
assertion was pointed at the wrong page, here the *tool* is looking at the wrong
thing entirely, and both report success.

> `node --check file.js` reported "syntax OK" on a file with an unescaped quote,
> because it parsed as sloppy CommonJS rather than as the module the file
> actually is. Two commits were made on the strength of that pass. Parsing it
> strictly then failed for a *different* wrong reason -- it rejected the
> top-level `return` the runtime legitimately allows. Neither command was
> checking what the runtime does.

So: when you adopt a lint, a type check, a schema validator or a syntax check,
**break something on purpose once** and watch it complain. If it does not, you
have a command that runs, not a check that checks.

The same applies to a check you write yourself: ship it with the broken input
you used to prove it, or the next person cannot tell whether it works.

## A format your app both WRITES and READS is a PAIR — test the round trip

**What broke** — an app offered "download a template", "export to Excel" and
"import a file". The writer put each column's TRANSLATED LABEL in the header
row. The reader matched header cells against the column key and a frozen list
of aliases — and nobody had added the labels to that list. So three columns
were silently dropped from every re-upload of a file the app had produced
itself, reported only as a mild "these columns were not recognised".

Both halves had tests. The writer's tests asserted it wrote the right header.
The reader's tests fed it headers the reader already knew. Neither side ever
handed its output to the other, so the gap between them was invisible to a
green suite for as long as the feature existed.

**The rule** — wherever the same program is both producer and consumer of a
format, the test that matters asserts the CIRCLE:

```
written = export(data)
assert import_(written) == data      # not: assert header == "Naziv"
```

Export/import, serialise/deserialise, encode/decode, a template you generate
and later parse, a URL you build and later route, a cache key you write and
later look up. Testing the halves proves each side is self-consistent; only the
round trip proves they agree with EACH OTHER — which is the only property the
user experiences.

**Two things that make it bite harder:** a producer whose output varies by
locale, user or config (translate the header and the reader's fixed alias list
goes stale), and a reader that degrades POLITELY — dropping what it does not
recognise with a soft notice instead of failing. The polite degradation is why
this survives so long in production: nothing ever errors, data just quietly
goes missing.
