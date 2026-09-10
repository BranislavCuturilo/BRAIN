# The standing agents, and what they cost

Extends `ops-delegation`. Read when picking WHICH agent, rather than whether to delegate at all. Model and effort per task: `ops-models`.

## The standing agents

Defined in the brain, available everywhere. Each is briefed for one shape of work
and carries its own model and effort so cost tracks the task.

| Agent | Model / effort | For |
|---|---|---|
| `scout` | haiku · low | Read-only reconnaissance: where does X live, which files match, list the call sites. Returns locations and a conclusion, never a file dump. |
| `reviewer` | opus · xhigh | Adversarial review of a change or a claim. Its job is to *refute*. |
| `planner` | opus · max | Design and sequencing for something hard, before any code is written. |
| `scribe` | sonnet · low | Writes a captured rule into the right skill file, following the `capture` routing law. |

Reach for a purpose-built agent over a general one — a narrower brief with fewer
tools produces a tighter answer at a lower tier.

## Model tuning note

Current models reach for subagents more readily than older ones did. Guidance
written to *encourage* delegation ("when in doubt, fan out") was calibrated
against models that under-delegated, and now over-fires. If a project's CLAUDE.md
still carries that wording, it is worth revisiting — the correct instruction
today is the cap above, not the encouragement.

## Choosing from the roster

**The full roster is `docs/AGENTS.md`** -- generated from the agent files, so it
is never out of date. Read it when the right choice is not obvious; do not work
from a list in your head.

**Read before write.** When the input is a large file, send a reader first and
give the writer its spec -- the reader's context is thrown away, so the writer
starts from thirty lines instead of fifteen hundred. `dj-model-reader`,
`dj-view-reader`, `repo-reader`, `scout`.

## Grade: junior or senior

Measured: `backend-senior` and `frontend-senior` carry 111 recorded runs between
them and were named NOWHERE in this file until now, so both were being selected
by reading a description among 56 rather than from a rule. `coverage.py --weak`
is what found that, and it is the check that keeps this table honest.

| Send | When |
|---|---|
| `backend-junior` / `frontend-junior` | the answer is unambiguous and the brief is complete: a service method, a form, a management command, a template inside the existing design system. |
| `backend-senior` / `frontend-senior` | isolation, schema, money, concurrency, workflow state — anything where being silently wrong is expensive. Also a design still open, and reviewing a junior's output. |

**When the grade is genuinely unclear, send the senior.** A junior's wrong
answer in this list costs a migration or a leak; a senior's cost is tokens.

## Changing something that already exists

The slice agents build a thing whole. These two edit one that is already there,
and picking the slice agent for an edit rewrites more than was asked.

| Send | Not |
|---|---|
| `dj-models` — add a field, tighten `clean()`, add a constraint or index to an EXISTING model | `dj-model-slice` (that is a NEW model with its migration, admin and tests together) |
| `dj-views` — a `TemplateView` dashboard, a `FormView`, a shared view helper or mixin | `dj-list-view` / `dj-detail-view` (plain CRUD) or `dj-action-view` (a POST that changes state) |

These are the choices that get made wrong:

| Situation | Send | Not |
|---|---|---|
| approve / reject / close / assign / convert | **`dj-action-view`** (senior -- no template, so nobody reviews these) | a junior view agent |
| a delete | **`dj-delete-view`** (senior -- decides deletable-at-all, then cascade) | `dj-update-view` |
| a model **plus** its migration, manager and tests | **`dj-model-slice`** -- they must agree | four agents in parallel |
| one screen: view + url + template + test | **`dj-screen-slice`** -- the names must match | four agents |
| five CRUD surfaces at once | the **`crud-scaffold` workflow** | one agent, serially |
| business logic spanning models | **`dj-service`** | a view agent |
| "make it faster" | **`optimizer`** (measures first) | `refactorer` |
| "clean this up" | **`refactorer`** (behaviour unchanged) | `optimizer` |
| a shared mixin or base class | **`dj-mixin`** (senior -- every consumer changes at once) | a file agent |
| what a screen *should be* | **`ui-ux`** | a template agent |

**The same step over many items, or several independent contexts, is a workflow,
not a fan-out you improvise** (`ops-workflows`). Check `workflows/` before
hand-rolling one -- and if the pattern is new but will recur, say so.
