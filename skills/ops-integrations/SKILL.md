---
name: ops-integrations
description: >
  Connecting Claude to things outside the repo — MCP servers and HTTP APIs — and
  keeping their secrets out of every file. Load before adding or debugging an MCP
  server, before calling an external API, or when a token needs to live
  somewhere.
when_to_use: >
  "add an MCP server", "connect to X", an MCP server not loading or returning
  401, an API token, "where do I put this key", documenting an endpoint.
---

# Integrations

## The secret rule, before anything else

**A credential never appears as a literal in any file that git can see.** Not
temporarily, not "just to test it". Once a secret has been in a file it is in the
reflog, in every clone, and in whatever transcript was open at the time —
**writing it down means rotating it**, and rotating is the expensive part.

References only:

```json
{ "mcpServers": { "helpdesk": {
  "command": "${PROJECT_VENV}/python.exe",
  "args": ["scripts/helpdesk_mcp.py"],
  "env": { "HELPDESK_URL": "https://tiket.example.com",
           "HELPDESK_TOKEN": "${HELPDESK_TOKEN}" } } } }
```

`${VAR}` and `${VAR:-default}` expand in `command`, `args`, `env`, `url` and
`headers`.

## Where the value actually comes from — read this before designing around it

**Claude Code does not read a `.env` file for MCP configuration.** There is no
`envFile` option. `${VAR}` is resolved from the **process environment** at
startup, so the variable must already be set when Claude Code launches.

That leaves two honest options:

| | How | Trade-off |
|---|---|---|
| **User environment variables** | `setx HELPDESK_TOKEN "..."` on Windows; export in the shell profile on POSIX | Survives reboots, always present. Set once per machine. |
| **A shell-loaded env file** | source it in the profile, then start Claude from that shell | Keeps secrets in one gitignored file, but only works from that shell |

A project `.env` read by Django is **not** the same thing and will not reach MCP.
If you want one file for both, load it in the shell profile as well.

## Three failure modes worth recognising

1. **`claude mcp add` rewrites `${VAR}` as the resolved value.** A known bug: it
   expands placeholders and writes the *secret* back into the file. **Hand-edit
   `.mcp.json`; do not use `claude mcp add` on a file that already contains
   references.** Check the diff before committing, always.
2. **The Desktop app does not expand `${VAR}`** — the server receives the literal
   string and the API answers 401. If it works in the terminal and 401s in the
   app, this is why.
3. **A missing variable is a warning, not an error.** The config still loads and
   the server gets the literal `${VAR}`. `claude mcp list` shows the
   missing-variable warning — check there first when a server behaves as if
   unauthenticated.

Also check the obvious: an absolute path in `command` pointing at a **different
user profile** silently never starts. Paths belong in `${VAR}` too.

## The harness itself — quirks that cost a whole session if unknown

**Hooks (including a plugin's `hooks.json`) load only at session start.**
Adding or editing a hook mid-session does nothing until the session restarts —
this includes the Claude Code extension inside an IDE (Cursor etc.), where the
restart is a new chat or an extension reload, not just the CLI. Measured: sound
and `agent_view` hooks were added, "nothing happened" in Cursor, because the
running session had already loaded its hook set.

**On Windows/Git-Bash, `pkill -f <script>` does not reliably catch
python/node processes** — orphans survive, and it is possible to end up with
several servers bound to the same port. Kill by **port** instead:
`netstat -ano | grep :PORT | grep LISTENING` → take the PID (last column) →
`taskkill //F //PID <pid>`.

## Read on demand

| Working on | Read |
|---|---|
| calling an external HTTP API -- auth, pagination, retries, what the docs get wrong | `references/consuming-apis.md` |
| which MCP servers exist, what each costs, what was rejected | `references/servers.md` |
| installing a plugin | `references/plugins.md` |
| a tool that sits BESIDE the harness -- a gateway, a browser driver, a statusline | `references/tooling.md` |

## "Which tool should we use for X?" — read the register FIRST

`references/servers.md` and `references/tooling.md` hold what has already been
evaluated, **including the rejections and the reason for each**. Answer from
there before searching: the evaluation cost real time, and a rejection with no
reason gets re-proposed every few months.

**Then search anyway, and say you are doing both.** The register is a snapshot
as of its last edit, not a closed set, and this space moves faster than the file.
The honest answer is two sentences: what the register already decided, and what
is worth looking for that it does not cover.

## Choosing to add one at all

An MCP server's tool schemas cost context in **every** session it is enabled for.
Add one when it replaces work you actually do repeatedly — not because it exists.

Prefer **project scope** (`<repo>/.mcp.json`) over user scope: a server useful in
one project is noise in the other twenty. Reserve user scope for something
genuinely universal.

A deterministic script beats an MCP server for anything with an exact answer
(`ops-workflows`). MCP earns its place when the interaction is open-ended.

## Documenting an API

An integration nobody documented gets re-discovered, at the cost of an afternoon,
every time. One reference file per integration in `references/`, covering:

- **base URL and auth** — the scheme, and the **name** of the env var holding the
  credential. Never the credential.
- **the endpoints that matter**, with the shape of a request and a real response.
  Not the whole API — the parts actually used.
- **what goes wrong**: rate limits, pagination, which errors are retryable, which
  fields lie, what the docs get wrong.
- **who owns it** and where the real documentation lives.

Read `references/servers.md` for the MCP servers already configured and what
each costs, and `references/plugins.md` before installing any plugin -- a
plugin cannot contain plugins, and an installed one costs context in every
session exactly like the brain's own skills.

**Read `references/tooling.md` before adopting any tool that sits beside the
harness** -- a gateway, a memory plugin, a browser driver, a statusline. It
carries the three questions that decide them (where does the source code go,
what capability does it silently remove, what does it inject and is it bounded),
what has been adopted, and **what was rejected and why** -- the rejections being
the half that stops the same tool arriving again next quarter.

## Before you finish

- No literal secret anywhere in the diff. Re-read it.
- The env var is documented in `templates/env.example` by **name**.
- `claude mcp list` shows the server without warnings.
- If a secret was ever written to a file during the work: **rotate it**, and say
  so plainly rather than hoping.
