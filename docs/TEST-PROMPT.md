# Test prompts — exercising the brain on a large Django app

Ordered by cost. Run them in order; a failure early makes the later ones
meaningless. `docs/TESTING.md` covers installation and tooling — this file is
about whether the **agents and skills actually help on real work**.

Use a repository with real size: many apps, a real domain, existing conventions.
A toy project proves nothing, because the whole system exists to handle scale.

> **Record every run**, either direction:
> ```
> python ~/.claude/skills/brain/scripts/brain/score.py record agent <name> helped|hindered "<what happened>"
> ```
> A registry where everything helped is a registry nobody is reading.

---

## 1 — Reconnaissance (cheap, ~2 min)

```
Use the impact-mapper agent: where does the Stocktake model live, and what
would break if I renamed its `status` field?
```

**Good:** names the definition file and line; reports counts per reference kind;
distinguishes what it verified from what it inferred; **names the template, URL
and migration references** a symbol search would miss.

**Bad:** re-greps by hand instead of running `scripts/map/concept.py`; pastes
file contents; says "this should be refactored".

---

## 2 — Read → brief (cheap)

```
Use dj-model-reader on the largest models.py in this project, for its two
main models.
```

**Good:** a compact spec — fields with types and editability, the scope FK,
constraints, what `clean()` enforces, suggested list columns, and **anything
unusual** (soft delete, a state machine, a `save()` override).

**Bad:** quotes the model back. That is copying, not extracting, and the whole
point is that the writer should not have to read the file.

---

## 3 — One narrow write (moderate)

```
Use dj-list-view: add a list screen for <an existing model that has none>.
Filter by status and location, ordered by date, paginated.
```

**Good:** scopes `get_queryset` through **the same helper the app's other list
views use**; `select_related` what the template reads; filter and sort state in
the URL; the design system's striped table; an empty state; and it **says which
helper it used**.

**Bad:** a bare `filter(organization=tenant)`; a hand-rolled table; row actions
as links with query flags.

---

## 4 — The dangerous surface (moderate)

```
Use dj-action-view: add an endpoint that marks a stocktake as reviewed.
Only the responsible person may do it, and only on a closed list.
```

**This is the highest-value single test.** It is the surface where the real
defect lives, and the reason the agent is graded senior.

**Good:** POST only; authorizes through the app's helper *plus* the action
permission; guards on current state (`closed`), so a second submit is a message
rather than a second review; goes through the service or state machine; **and
reports every sibling action on that model that does it differently**.

**Bad:** `GET` with a query flag; a bare scope filter; `obj.status = 'reviewed';
obj.save()`.

---

## 5 — Orchestration (the open question)

```
Use the orchestrator agent: add a "supplier returns" feature to the
procurement app — a new record type, the screens to manage it, and the
stock movement it produces.
```

**This tests whether anything can choose correctly from 52 agents**, which is the
one genuinely unproven claim in the whole system.

**Good:**
- sends a **reader first** (`dj-model-reader`, `repo-reader` or `scout`) before
  any writer;
- recognises the stock movement as a **domain question** and consults
  `inventory-consultant` or hands it to `data-model-consultant` — because "what a
  return does to a ledger" is not a coding decision;
- picks `dj-model-slice` for the model + migration + tests (they must agree),
  not four parallel agents;
- **never implements anything itself**;
- names a point of no return (the migration) and sequences reversible work first;
- says what it did not cover.

**Bad:** picks `backend-junior` for everything; starts writing code; fans out
five writers with no reader; asks the user to choose the agents.

**If this fails, the answer is fewer agents, not more instructions.** Say so and
record it.

---

## 6 — The ticket path (moderate)

Take a real, badly-written ticket from `.claude/tiketi.json` — the vaguer the
better — and:

```
Use the ticket-reader agent on ticket #<id>, then tell me what you would
actually build.
```

**Good:** separates **SAID** (their words, quoted) from **MEANS** (the reading)
from **NEEDS** (the underlying problem); opens the attachment; states confidence;
proposes the one question that would resolve the ambiguity; flags when the
requested solution is not the right one.

**Bad:** treats the ticket as a specification and starts implementing.

---

## 7 — The sweep (expensive, highest information)

```
Run the workflow at ~/.claude/skills/brain/workflows/authz-sweep.js
with args {"apps": ["stocktaking", "audits", "procurement"]}
```

Exercises `scout`, `security`, `pipeline()`, code-side judging and adversarial
verification at once, over the by-id endpoints where the recurring defect
actually is.

**Good:** per app, the authoritative helper and the endpoint inventory; findings
that survived a refutation attempt; an explicit `unverified` list of what was
capped; `notReached` for apps it could not map.

**Bad:** a wall of plausible findings with no verification; silence about what it
skipped.

**Whatever it finds, verify one finding by hand before acting.** A first run of
an unproven tool earns exactly that much trust.

---

## 8 — Does the system improve itself

After a real defect gets fixed in a session:

```
/brain:capture
```

**Good:** states the lesson in one sentence; classifies by **root cause**, not
symptom; routes to exactly one destination and says why; **searches for an
existing rule before writing a new one**; extends rather than duplicating.

**Bad:** writes a near-duplicate beside an existing rule; puts a general
principle in a project skill or vice versa; records something the code already
says.

Then close the loop:

```bash
python ~/.claude/skills/brain/scripts/brain/dashboard.py --open
```

The usage column should no longer be all zeros. **That is the moment the system
starts making decisions from evidence instead of from argument.**

---

## The honest scorecard

After running these, answer four questions in writing:

1. **Did a narrow agent beat a general one?** If `dj-list-view` produced nothing
   better than a plain request would have, the specialisation is not earning its
   complexity.
2. **Did the orchestrator choose well?** If it needed correcting more than once,
   52 agents is too many.
3. **Did the consult chain beat going straight to an executor?** Its own kill
   criterion is three runs producing materially the same result.
4. **Did anything catch a defect that would otherwise have shipped?** This is the
   only question that ultimately matters.

Answers 1–3 being "no" is a **useful** result. It means deleting things, and this
system is explicitly designed to be deleted from.
