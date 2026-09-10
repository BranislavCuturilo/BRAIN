# The consult chain — on trial

Extends `ops-delegation`. Read when reaching for a consultant agent, and when deciding whether the chain earned its cost on the last run.

## The consult chain -- ON TRIAL

> **This is an experiment, not an institution.** It has not yet been shown to
> beat sending the work straight to a capable executor, and it may not. Score
> every run (`ops-scoring`) and read the kill criterion at the end of this
> section before defending it.

Four roles, when the *right approach* is genuinely unclear:

| Role | Answers | Source |
|---|---|---|
| a **consultant** | how should this work | the domain -- and this project's skills and docs |
| **`project-expert`** | what does this project already do and decide | the project's skills, `CLAUDE.md`, spot-checked against code |
| **`synthesizer`** | so what do we do, and what must not break | those two accounts |
| an **executor** | does it | the spec |

`planner` sits between synthesizer and executor **only** when the work is big
enough to need ordering and a named point of no return.

### The default chain is `project-expert` -> `synthesizer`. The consultant is optional.

**Measured, on a schema question in a real codebase:** the full chain cost
192k tokens and 17.6 minutes; one senior agent given the same question cost 108k
and 4.4 minutes. Both reached the **identical** headline decision and both
independently found the same four secondary defects. Everything the chain added
came from the `synthesizer` -- it ran a test and empirically confirmed a failure
the control had only inferred, and it *overruled its own consultant*, rejecting a
change that would have reached into two other segments. The consultant link
contributed nothing that survived. Meanwhile the single control agent found two
things the entire chain missed, including an authorization hole on a write path.

So: **`project-expert` -> `synthesizer` keeps everything that paid.** Add a
consultant only for a decision with no precedent in the codebase. When the model
already embodies an answer, the consultant restates it in confident register,
which reads like analysis and is not.

And whatever the chain concludes, remember what the control did: **one competent
agent reading the actual code is a strong baseline, and it is the thing your
chain has to beat, not the thing it is assumed to beat.**

### When NOT to run it -- which is most of the time

- **One line, or an obvious change.** No consultant. Straight to the executor.
- **A bug.** Send the executor first. It will usually reproduce it, find it and
  fix it for a fraction of the cost, and that is the better outcome. Only when it
  comes back unable to explain *why* does a consultant earn a turn -- and then it
  explains the cause; it does not take over the fix.
- **Anything where the right answer is obvious once stated.** Naming a consultant
  does not make a decision harder than it is.

### When it might be worth it

The decision is expensive to reverse -- schema, money, an external contract, a
security boundary -- **or** a first attempt already went wrong in a way nobody
could explain. Those are the only two cases where five contexts plausibly beat
one good one.

### The failure this arrangement is exposed to

A consultant whose main source is the project's skills will reason confidently
from a skill that **stopped matching the code**. Nothing downstream catches it:
the synthesis is coherent, the spec is clear, and it is wrong.

So: **a consultant states which skill it relied on and when that skill was last
confirmed against the code.** "I do not know" is a finding, and `project-expert`
spot-checks the load-bearing ones. This guard is the only thing standing between
this chain and well-argued nonsense.

### The default when the two disagree

**The project wins unless the synthesizer can name concrete harm.** A running
system carries constraints nobody wrote down -- a client requirement, a
regulation, a deliberate compromise. "This is not how it is normally done" is not
harm.

And any change to working code carries its consumers with it
(`craft-code/references/change-safety.md`): **an improvement that breaks
something that worked is a net loss**, and the consumer count belongs in the spec
before the work starts, not in the post-mortem.

### Kill criterion

Record the outcome of every run. **If three runs produce materially what a
capable executor would have produced alone, delete the chain** -- keep the
consultants for direct questions and drop the ceremony. Say so plainly when it
happens; a process defended past its evidence is worse than no process.
