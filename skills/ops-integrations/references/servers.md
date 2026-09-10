# MCP servers in use

Extends `ops-integrations`. What is actually configured across these projects,
what each is for, and whether it is worth its context cost.

**Read this BEFORE searching the internet for a tool.** When the question is
"what should we use for X", the evaluated answer is usually already here, with
the reasoning — including the rejections, which are the half that stops the same
tool being proposed again every few months.

**And then search anyway.** This list is a starting point, never a closed set:
it is a snapshot of what had been evaluated by its last-edited date, and this
space moves faster than the file does. Say what the list already holds, then say
what is worth looking for that it does not.

## In use

| Server | Transport | Scope | Worth it because |
|---|---|---|---|
| `acme-helpdesk` | local Python | per project | Reads and updates the ticket queue directly instead of syncing JSON by hand. Needs `HELPDESK_URL` + `HELPDESK_TOKEN`. |
| `context7` | `npx` | project | Current library documentation on demand — the one thing a model genuinely cannot know past its cutoff. |
| `MySQL` | `npx` | project | Schema and data inspection without writing a throwaway script. **Read-only credentials, always.** |
| `docker` | local | project | Container state during a deploy problem. |
| `memory` | `npx` | project | Overlaps agent `memory:` and the brain's own journal — pick one, do not run three memories that disagree. |
| **`chrome-devtools`** | `npx` | **project — the app repos only** | The console, the network tab and a headed browser. Fills what `visual-diff` structurally cannot; the boundary is below. |

### The `visual-diff` ↔ `chrome-devtools` boundary

Two browser tools without a stated boundary become two sources of truth for
"what the customer sees", which is the defect this brain exists to prevent.

| | `visual-diff` / Playwright | `chrome-devtools` MCP |
|---|---|---|
| **fires** | automatically, on a template/CSS change | when you ask a question |
| **mode** | batch pipeline, headless, seeded local server | interactive, on demand |
| **sees** | rendered DOM + geometry | **console, network, runtime state, Lighthouse** |
| **produces** | pictures **for the customer** | an answer **for you** |

The gaps it fills are ones this brain already has rules for and no tool to serve:

- `ui-bootstrap/references/js-hooks.md` demands *"assert zero console/page
  errors"* in the variant that HIDES a block. Nothing could read the console.
- `ui-bootstrap/references/toolbars-sticky.md` and `.../layout-sizing.md` demand live geometry
  (`getBoundingClientRect`) **while you are writing the CSS**. `visual-diff`
  measures during a sweep, which is afterwards.
- `stack-django/references/views.md`: *"headless Chromium has no PDF viewer
  — check PDF embeds in a headed browser."* `visual-diff` is headless.
- Which request returned 500, and what the API actually said.

**Two rules that ship with it:**

1. **It never produces a customer-facing picture.** That is `visual-diff`'s job
   and it owns the approval and `outbox` flow. A screenshot taken here is for
   diagnosis and stays in the session.
2. **Never against production credentials.** `visual-diff` already refuses a
   driver that logs in as a real production user; that refusal is a property of
   the work, not of one engine, so it binds here too.

Adding it, per the project-scope rule below — the brain repo has no UI and must
not carry it:

```bash
cd <app repo>
claude mcp add chrome-devtools -- npx chrome-devtools-mcp@latest
```

26 tools. Whether they land in every session's context or are deferred behind
tool search depends on the harness — check the next session and remove it if the
cost is real and the use is not.

## Worth considering

| Server | Why | Cost |
|---|---|---|
| **GitHub** | PRs, issues, Actions logs from inside a session. Removes "paste the failing run's log" entirely, which is currently how a failed deploy gets diagnosed. | Large tool surface; enable per project. |
| ~~Playwright / browser~~ | **Decided 2026-08-31**: adopted as `chrome-devtools`, above. The screenshot half was already covered by `visual-diff`; what was missing was the console and the network. | — |
| **Firecrawl** | Crawls a site to markdown. Overlaps `scripts/reach/reach.py get` (Jina Reader, keyless) for a single page; its edge is crawling a whole site. **Wanted for lead generation, not for engineering.** | Paid past a free tier. Add ONLY in the project where lead-gen happens — never in an ERP repo or here. |
| **Glif** | Generates images, video and audio, and renders HTML. **Wanted for design and marketing work — a landing-page image — not for programming or the helpdesk.** OAuth, no API key. | Add ONLY in the design/marketing project. It has no business in a session that touches customer data. |
| **Sentry / error tracker** | If one is in use, the traceback comes to you instead of being pasted. | Small. |

## Not worth it here

- **Filesystem MCP** — Read/Glob/Grep already do this, better.
- **A second memory** — see above.
- **Anything wrapping a CLI you already have.** A `git` MCP is worse than `git`.
- **Perplexity** — paid search-with-citations. Rejected 2026-08-31 on the same
  grounds as Exa was dropped from `reach.py`: `scripts/reach/reach.py` already
  covers search, Stack Overflow, Hacker News, GitHub, PyPI and RSS **with no key
  at all**, and `research` requires verifying any answer against the repository
  anyway — which is exactly the step a synthesised answer tempts you to skip.

## Rules for this list

- **Project scope by default.** A server useful in one repo is context tax in the
  other twenty. Reserve user scope for something genuinely universal.
- **Read-only credentials wherever the server only reads.** The MySQL server has
  no business holding a user that can `DROP`.
- **A server that has not been used in a month is removed.** Its schemas were in
  every session's context the whole time.
- Add a row here when adding a server, and say what it replaced.
- **Record the ones you decided AGAINST, and why.** A rejection with no reason
  is re-proposed every few months, and re-evaluated from scratch every time.
- **Record what a tool is wanted FOR, not just that it is good.** "Firecrawl is
  good" cannot be scoped; "wanted for lead generation" places it in exactly one
  repo and keeps it out of the other twenty.

## Local Python servers

Run them through the **project venv**, referenced as a variable:

```json
"command": "${PROJECT_VENV}/python.exe"
```

An absolute path pointing at a specific user profile silently fails to start on
every other machine — and on the same machine after the profile changes. This
has already happened here.
