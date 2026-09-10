# Composition -- several skills loaded at once

Extends `brain`.

**One concept, one owner.** Every rule has exactly one authoritative home. Other
skills that touch the concept **name the owner and stop** — they never restate
it, not even "briefly for convenience". A convenience copy is how two versions of
a rule come to exist, and the reader cannot tell which is current.

| Concept | Owner |
|---|---|
| responsibility boundaries, registries, transactions, magic values, patterns | `craft-code` |
| scope isolation, by-id authorization, untrusted input, uploads, secrets, races, `GET`-that-writes, error disclosure | `craft-security` |
| finding what exists, when to extract, where shared code lives | `craft-reuse` |
| test isolation, side-effect channels, fixtures, assertions | `craft-testing` |
| history, pipelines, deploy, the manual-step trap | `craft-git` |
| framework mechanics (validation order, ORM, migrations, templates) | `stack-*` |
| how two stacks meet | `arch-seams/references/*` |
| screens, components, visual traps | `ui-*` |
| model/effort choice, escalation | `ops-models` |
| whether to delegate, and to whom | `ops-delegation` |
| grades, the teach loop, splitting | `ops-seniority` |
| pull/push, conflicts | `ops-sync` |
| what is true only in one repo | that repo's `*-domain` skill |

**Specific beats general on its own concept.** When `stack-django` says the
autofill must also run in `full_clean()`, that is the Django mechanics of a
`craft-security` rule — not a second rule and not a contradiction. Apply the
specific one; do not re-derive the general one.

**Ignore the parts that do not apply.** A loaded skill is a reference, not a
checklist to satisfy. Reading `ui-bootstrap` while fixing a query does not mean
the query needs an empty state. Take what the task needs and leave the rest —
forcing irrelevant rules onto a task is as damaging as missing the relevant ones.

**A rule that would fit two owners belongs to the one whose *failure mode* it
is.** `GET` that writes looks like a routing convention, but the failure is a
CSRF hole, so `craft-security` owns it and `stack-django` shows the Django fix.
Ask "what breaks?" not "what is it about?"

When you add a skill, add its row to this table. When two skills start arguing,
that is a missing row.
