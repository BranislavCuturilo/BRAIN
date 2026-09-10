# `tiketi.json` — schema and display

Extends `tickets`. **One file per MODULE, centralised** in the brain at
`~/.claude/skills/brain/tickets_store/<MODULE>.json` (not scattered across each
repo's `.claude/`). A sibling `modules.json` maps each module to the repo where
its work happens. `project.repo` on each file records the mapped repo.

**The store is TRACKED, not gitignored** — this line said the opposite until
2026-08-31, and the opposite is the dangerous belief to hold. Committing it is
deliberate: the brain repo is how the queue reaches the other machine, and the
per-device `worklog/` and `sent_log/` files only make sense if they travel. The
consequence is that **this repository holds real customer names, ticket bodies
and comment threads**, which is why the remote is private and why
`/brain:release` exists. Only the volatile parts are ignored (`*.lock`,
`estimates.csv`, `modules.local.json`, the caches).

## Schema

```json
{
  "project": {
    "name": "POPIS",
    "repo": "popis",                    // relative to PROJECTS_ROOT (see store.repo_path)
    "url": "https://example.com",
    "helpdesk_module": "POPIS",
    "last_sync": "2026-03-04"
  },
  "statuses": { "...": "labels, see below" },
  "tickets": {
    "94313": {
      "title": "short working title (yours, not theirs)",
      "priority": "critical | major | minor",
      "status": "active | skip_session | skip_consider | done",
      "notes": "your analysis, decisions, and what was changed",
      "consider_for": "what this is waiting on (skip_consider only)",
      "original": {
        "title": "as written by the user",
        "description": "as written by the user",
        "category": "helpdesk category",
        "created": "when it was raised",
        "customer": "who wrote it",
        "attachment_url": "screenshot, if any"
      },
      "comments": [
        {
          "id": 12,
          "author": "who wrote it",
          "author_role": "customer | engineer | other",
          "at": "timestamp",
          "body": "the comment text",
          "attachments": [{ "name": "", "url": "link on the helpdesk" }]
        }
      ],
      "url": "https://<helpdesk>/tickets/94313",
      "analysis": {
        "said": "their words, quoted",
        "means": "your reading",
        "confidence": "high | medium | low",
        "needs": "the underlying problem",
        "missing": "what to ask, if anything",
        "plan": ["ordered steps to actually do it"],
        "suggested_agents": ["dj-service", "reviewer"],
        "suggested_skills": ["procurement-domain", "craft-testing"],
        "complexity": "S | M | L",
        "updated_at": "timestamp"
      },
      "helpdesk": {
        "priority": "critical | major | minor",
        "status": "raw helpdesk status",
        "is_closed": false,
        "module": "helpdesk module name",
        "module_id": 7
      },
      "triage": {
        "state": "working_today | ignore_today | ignore_indefinitely | solved_manually | nonsense | done",
        "order": 0,
        "context": "your added context (≤2000 chars)",
        "priority_override": "critical | major | minor | ''",
        "report": "draft close/resolution text (≤4000 chars)",
        "updated_at": "timestamp"
      },
      "outbox": [
        {
          "body": "a comment you drafted in the view, to post at write-back",
          "attachments": [{ "name": "file.png", "path": ".claude/tiketi_outbox/<proj>/<id>/file.png" }],
          "posted": false,
          "created_at": "timestamp"
        }
      ]
    }
  },
  "rev": 4
}
```

**`original.*` is never edited.** It is the evidence of what was actually asked;
your interpretation goes in `title` and `notes`. Losing the original wording
makes it impossible to tell later whether a misunderstanding was theirs or yours.
On a re-`sync`, `original` is set **once** (first sight) and never overwritten;
`comments` are always refreshed — a clarification the customer adds arrives as a
comment, which is exactly why the thread is pulled.

Some projects carry a reduced form without `original` — treat the missing fields
as unknown, not as empty.

## The layers, and who writes them

| Layer | Writer | On sync |
|---|---|---|
| `original`, `comments`, `url`, `helpdesk` | the deterministic `sync` (pull) | refreshed (original once) |
| `title`, `priority`, `status`, `notes`, `consider_for` | you / the daily routine | **preserved** (priority tracks the helpdesk) |
| `analysis` | the `triage` action (ticket-reader → write_analysis.py) | **preserved** |
| `triage`, `outbox` | the **live view** (agent_view), by the user | **preserved** |
| `rev` | every writer | bumped — optimistic-concurrency guard, do not hand-edit |

**Two status fields, on purpose.** The coarse top-level `status` is what the
board and this skill read. The view writes a richer `triage.state` (the six
states above). `store.queue_status(ticket)` maps `triage.state` down to the
coarse status when it is present, so the board and the view never disagree —
read a ticket's queue status through that helper, never the raw `status`.

`comments` and `outbox` are **unbounded**. Inbound attachments are links only —
never downloaded (git stays small); the helpdesk API cannot receive an uploaded
file, so an `outbox` attachment `path` points at the gitignored
`.claude/tiketi_outbox/` and is attached on the helpdesk web UI, not via the API.

## Status semantics

| Status | Meaning | Reset by `start`? |
|---|---|---|
| `active` | in the queue, should be worked | — |
| `skip_session` | not today, no other reason | **yes** → back to `active` |
| `skip_consider` | waiting on something, recorded in `consider_for` | **no** — it was a decision |
| `done` | finished and verified | no |

The difference between the two skips is the whole point: one is scheduling, the
other is a judgement. Resetting `skip_consider` on every new day would throw away
the judgement.

## Priority

`critical` · `major` · `minor`, as set by the helpdesk. **Priority is the
customer's urgency, not your ordering.** A `minor` that blocks another person's
work outranks a `critical` nobody is waiting on — say so when you recommend an
order, rather than sorting the list mechanically.

## Board layout

```
╔══════════════════════════════════════════════════════════════╗
║  <project name> — <start | end of session>                   ║
╠══════════════════════════════════════════════════════════════╣
║  Date: <today>        Last sync: <project.last_sync>         ║
╚══════════════════════════════════════════════════════════════╝

📊 QUEUE
─────────────────────────────────────────────────────────────────
● active           N        ◐ consider      N
○ skipped today    N        ✓ done          N

🔥 CRITICAL + ACTIVE
─────────────────────────────────────────────────────────────────
#94313  Update lokacije menja vrednost        — <one-line read>

◐ WAITING
─────────────────────────────────────────────────────────────────
#88201  <title>                               → <consider_for>

📋 SUGGESTED ORDER
─────────────────────────────────────────────────────────────────
1. #94313 — <why this one first>
2. …
```

Legend: `●` active · `✓` done · `○` skip_session · `◐` skip_consider

## Ticket list layout

Grouped by priority, most severe first:

```
═══ CRITICAL (N) ════════════════════════════════════════════════
 ID     │ Title                          │ Status     │ Note
────────┼────────────────────────────────┼────────────┼──────────
 94313  │ Update lokacije…               │ ● active   │ …
```

## Writing to the file

- Preserve key order and indentation; the file is read by humans and diffed in
  git.
- Update `project.last_sync` only on an actual `sync`.
- **Report every change made**, ticket by ticket. This file is the queue's
  working state, and a silent rewrite loses trust in it immediately.
