# Paper skeleton — do not write the claims yet

**Status: skeleton only.** The methodology has not been validated in practice.
Everything below is structure plus the honest statement of what evidence each
section would need. **Nothing here should be written up until `docs/TESTING.md`
and `docs/TEST-PROMPT.md` have actually been run and the dashboard shows real
usage.**

Writing the paper before the validation would produce exactly the failure the
system itself is built to avoid: a confident, well-argued, unverified claim.

---

## What kind of paper this can honestly be

**An engineering experience report with a proposed methodology.** Not a
controlled study.

This distinction decides everything downstream:

| Can claim | Cannot claim |
|---|---|
| a design and the constraints that forced it | that it is better than not using it |
| measured costs (tokens, per-invocation, always-on) | a productivity improvement |
| defects the system caught, with coordinates | a defect *rate* reduction |
| what was deleted after failing its own criteria | generalisation to other teams |

A single practitioner, one codebase, no control condition. Say so in the
abstract, not in a limitations paragraph at the end.

---

## Working title

> *Harness engineering for LLM coding agents: a file-based rule system with
> measured context budgets and explicit deletion criteria*

The three distinguishing pieces, and they should be the contribution:
**the context-budget constraint that shapes the layout**, **teaching by editing
files because subagents cannot learn**, and **written kill criteria**.

---

## Structure

### 1. Introduction
The problem: an agent that is competent but has no memory of what this team
already decided, so every session repeats the same corrections. Existing answers
(a monolithic instructions file, prompt libraries, fine-tuning) and why each
fails at scale.

### 2. Background
Agent Skills as an open standard; progressive disclosure; subagents and their
context isolation; what "memory" does and does not mean for a subagent. Related
work: harness engineering, memory-augmented agent loops (read–act–reflect–write),
self-evolving skill research, and the community frameworks. **Cite properly and
verify each is current** — this area moved fast in 2025–2026.

### 3. The constraint that shapes the design
The measurement: a top-level skill's description costs context in every session;
a body costs on load; a reference costs nothing until read. **This is the
paper's spine** — it is a real, measurable constraint that produces a
non-obvious architecture (few routers, unlimited references) and it is testable
by anyone.

*Evidence needed:* the budget numbers before and after the splits, and the
per-invocation cost of an agent before and after. **Already collected** —
`scripts/budget.py`, and the split results (e.g. `craft-code` −47% per junior
invocation).

### 4. Architecture
Four layers (craft / stack / seam / domain) and the outermost-true rule. Why
stack *combinations* are references and not skills — the combinatorial argument.
The ownership table as the mechanism that prevents contradiction.

*Evidence needed:* the ownership table, and at least one case of a genuine
conflict resolved by it.

### 5. Agents as briefs, not as learners
The mechanism: a subagent starts fresh and carries only preloaded skills, so a
correction teaches nothing and **teaching happens by editing a file**. The
consequence: many narrow agents rather than fewer improving ones; readers whose
context is discarded so writers start from a spec.

*Evidence needed:* a comparison of a narrow versus a general agent on the same
task. **Not yet collected** — `docs/TEST-PROMPT.md` §1–4.

### 6. Measurement
Three separate signals — cost (from files), usage (**counted** from session
transcripts), outcomes (recorded by hand) — and why they mislead individually.
The honest limits: a transcript-derived zero means "not seen", and a hand-recorded
score is only as good as the discipline.

*Evidence needed:* a populated dashboard over a real period. **Not yet
collected.**

### 7. Deletion as the maintenance
Written kill criteria: an agent that keeps producing discarded work is retired;
the consult chain is deleted if three runs match a plain executor; a rule proven
wrong is deleted in the same change. The health check that stays silent, and why
silence is the design.

*Evidence needed:* **at least one thing actually deleted for failing its own
criterion.** Without that, this section is aspiration and should be cut. This is
the most important missing evidence in the whole paper.

### 8. What failed
Reserved, and it must not be empty. Candidates already visible: whether anything
can select correctly from ~50 agents; whether the consult chain beats a capable
executor; whether hand-recorded scoring survives contact with a working week.

### 9. Threats to validity
n=1. No control. The author is also the evaluator. Effects attributable to the
model rather than the harness. Measurement gaps (deleted transcripts,
self-reported outcomes). **Write this section first** — if it cannot be written
honestly, the paper is not ready.

### 10. Reproducibility
The repository, the install, the scripts that produce every number, and the test
prompts. A reader should be able to reproduce the *measurements* even if not the
outcomes.

---

## Data to collect before writing

| Needed | From | Status |
|---|---|---|
| context budget, before and after splits | `scripts/budget.py` | **have** |
| per-invocation cost per agent | `scripts/budget.py` | **have** |
| real usage over a period | `scripts/brain/usage.py` | missing |
| recorded outcomes with notes | `scripts/brain/score.py` | missing |
| defects caught, with coordinates | the journal | missing |
| things deleted for failing criteria | git history | missing |
| narrow vs general agent comparison | `TEST-PROMPT.md` | missing |
| a session log of the design conversation | this transcript | **have** |

**Six of eight are missing.** That is the gap between a skeleton and a paper.

---

## Before writing a word

1. Run `docs/TESTING.md` end to end.
2. Run `docs/TEST-PROMPT.md` on a real codebase and answer its four questions **in
   writing**.
3. Use the system for a normal working month with scoring recorded honestly.
4. Complete at least one maintenance cycle that **deletes** something.
5. Re-read §9 and check every claim against what was actually measured.

If step 3 shows the scoring discipline did not survive, **that is a finding and
it belongs in §8** — not something to quietly fix before publishing.
