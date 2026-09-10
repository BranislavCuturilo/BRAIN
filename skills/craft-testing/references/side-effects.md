# Side-effect channels, and keeping them shut

Extends `craft-testing`. Read BEFORE running a suite for the first time in a project, before adding any outward-writing backend to a development config, and before writing a test that shells out or that deliberately mocks nothing.

The router carries the database rule because it is the one that destroys data. These are the channels that destroy other people's data quietly, for weeks, while every test passes.

## Side-effect channels — there are more than you think

Every outbound channel must be neutralised in the test configuration: **email,
push/chat/SMS notifications, outbound HTTP, and file storage.** Storage is the
one people forget, and it is the one that fails silently.

> A development configuration once gained a remote file backend behind an
> environment flag. The test configuration inherited it. The whole suite then
> uploaded every saved file to the production media host, for weeks, with no
> failure — it surfaced only through an unrelated path-separator bug.

**Rule: when you add any outward-writing backend to the development
configuration, neutralise it in the test configuration in the same commit.**
Storage, cache, queue, webhook, telemetry. Inheritance means "test inherits
production's side effects" unless you actively stop it.

Belt and braces: a `RUNNING_TESTS`-style flag the dispatch layer can check as a
last-line defence, independent of configuration.

### A test that SHELLS OUT inherits the operator's credentials

In-process neutralisation — a stubbed config, a fake adapter, a monkeypatched
module, a temp store — **does not cross a process boundary.** A test that runs
`subprocess.run([sys.executable, tool, ...])` hands the child the developer's
whole environment, including the real `*_URL` / `*_TOKEN` variables, and the
child then builds a real client and performs the real, irreversible action
against production.

> Measured, 2026-08-20: a test drove a `--close` CLI to prove it *warns* before
> posting. The branch that does not warn is one `if` away and shares the
> process — it walked on into the send path and posted a comment on a live
> customer ticket. Every assertion in the test passed, and the temp store showed
> nothing, because the mirror it wrote was the temp one.

**Rule: pass an explicit `env=` to every subprocess a test starts, with the
credential variables removed — and assert the child stopped where you expect**
(`returncode == 2`, "not reachable" on stderr). Then the safety property is
itself a test, not an assumption. "This test only exercises the refusing branch"
is not a guarantee: you are running the whole program, not the branch.

## Per-test mocking protects the tests that did not need protecting

**What broke** — the tests that mock the HTTP client are the ones exercising
the happy path. The tests that deliberately mock NOTHING are the *gate* tests —
"a GET is refused", "an empty body is a 400" — whose whole claim is that the
request never reaches a provider. So the exact regression they exist to catch
(a gate failing open) is the one that converts them into live third-party
traffic from every dev machine and every CI run.

**Why** — mocking per test only protects the test that remembered to add the
patch. A gate test is written specifically to prove nothing downstream fires,
so it is the one test in the file least likely to carry a mock — and the one
where a missing mock matters most.

**The rule** — mock the TRANSPORT once for the whole module in `setUp`, so no
test in the module can reach the network regardless of what it patches
individually:

```python
def setUp(self):
    patcher = patch('requests.adapters.HTTPAdapter.send',
                     side_effect=AssertionError('no network in tests'))
    patcher.start()
    self.addCleanup(patcher.stop)
```

Raise `AssertionError`, not the client's own error class — a handler under
test that catches the client's errors would swallow the guard silently and the
test would pass for the wrong reason. Then **prove the guard fires once**,
rather than trusting it exists: run one test through the gate you expect to
block and confirm the `AssertionError` surfaces.
