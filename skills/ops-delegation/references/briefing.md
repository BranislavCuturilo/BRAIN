# Writing the brief

Extends `ops-delegation`. Read BEFORE launching any subagent.

A subagent starts fresh and remembers nothing. Everything it needs has to be in the brief, and everything it returns has to be checked by someone who did not write it.

## A subagent cannot load a skill, so the caller carries the rules

The `Skill` tool is unavailable inside a subagent. Two things follow, and the
second one is a hole rather than an inconvenience.

**Put the rules in the brief.** An agent told to "follow the project's X skill"
cannot open it. Either state the constraints inline, or name the file and tell
it to READ the file — reading is not invoking, but it does get the content in
front of the agent.

**A PreToolUse hook that demands a skill invocation before `Edit`/`Write` is
unsatisfiable for a subagent, so it will route around the hook rather than stop.**
Three agents in one run independently hit such a gate, could not clear it, and
made their edits through `Bash` string-replacement instead — which the hook,
matching only `Edit|Write`, never saw. They reported it honestly, and the work
was correct because the caller had loaded the skills and briefed them; but the
guard silently covered nothing for every delegated change. If a gate is worth
having, it has to match every tool that can write a file, and it has to be
satisfiable by whoever is being gated. Check what your own repo's
`.claude/settings.json` gates, and against which matchers, before assuming
delegated work is covered by it.

## A generated brief is shown before it runs, never chained straight into a launch

When a tool composes the prompt, command or payload that will drive an agent on
the operator's behalf, **that text is the only artefact they can review** —
afterwards there is a running session and a diff, not a decision they can still
make. A button that goes brief → launch in one click removes the review without
announcing it, and the operator's first sign that anything happened is a terminal
opening.

> A ticket panel's "Klasifikuj" fetched a generated adjudication prompt and piped
> it straight into the launch endpoint. It worked, and the operator never once
> saw the prompt — including the parts that decided whether a customer would be
> written to. Their report was not "it is wrong", it was *"treba da se predlog
> upita da dobijem a ne da se otvori terminal"*.

**Two steps: render it, then let them start it.** Copy is part of the render —
half the value is pasting the brief into a session they already have open. Keep
the launch as its own button so choosing to run it stays a choice. And when the
operator has already decided something the brief is about (a branch, a target, an
approach), the brief must OBEY that decision and say so; a generated brief that
re-opens a settled question invites the agent to overturn it.

The same applies to anything irreversible the brief will do: state the
confirm-before-send rule *inside* the brief, not only in a skill the session may
or may not load.

## The report is the one thing the context can never reclaim

Everything else a session reads can be taken back. Measured in the shipped
client (2.1.266 — re-measure when it updates; note that `claude --version`
reports the npm package, which here is 2.1.258, NOT the binary that is running):
the silent micro-compaction that frees room mid-turn swaps old tool results for
`[Old tool result content cleared]` and persists the original to a file that can
be read back — up to 1 GB, since 2.1.265, past which the saved file is truncated
and the in-conversation preview says so. It does that for a FIXED allowlist — `Read`, `Bash`,
`PowerShell`, `Grep`, `Glob`, `WebSearch`, `WebFetch`, `Edit`, `Write` — and
only once it would save at least 20,000 tokens.

`Task` is not on that list. Neither is any MCP tool.

So an agent's report sits at full size until a full compaction summarises the
whole conversation, while the thousand lines it read for you are cheap and
recoverable. That is the exact inverse of the intuition, and it BOUNDS the
second reason to delegate rather than cancelling it: the agent still absorbs
unbounded noise for free — it just must not hand it back.

**In the brief, say the return shape and a size.** "The finding, its file and
line, one sentence each, under 3000 characters" is a brief. "Report what you
find" is how a 40KB report gets into a context nothing can clean.

## Verification is adversarial or it is theatre

An agent asked "is this right?" will usually say yes. Ask it to **refute**: state
the claim, tell it to find the input that breaks it, and tell it to default to
"refuted" when uncertain. For anything that can fail in more than one way, give
each verifier a distinct lens — correctness, security, does-it-actually-reproduce
— rather than running the same check three times. Redundancy catches noise;
diversity catches failure modes.
