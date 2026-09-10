# Lifecycle -- onboarding a repo, and keeping the brain true

Extends `brain`.

## Onboarding a repository

A repo joins the brain with `/brain:onboard` (user-invoked; it writes files).
The same skill is re-run later to reconcile a repo and the brain after both
have moved.
It detects the stack, confirms the seam, writes a `CLAUDE.md` that **points at**
brain skills instead of restating them, and stubs the project's first domain
skill. Suggest it in any repo that has no `CLAUDE.md` or no `.claude/skills/`.

## Maintaining the brain

- A rule proven wrong gets **deleted or corrected**, in the same change that
  discovers it. A stale rule is worse than no rule.
- When a rule cites a file as the "reference implementation", re-verify
  periodically that the reference still implements the pattern fully. Cited
  references drift silently and then teach the wrong shape.
- New rules arrive through `/brain:capture`, which enforces this routing law.
- The brain is a git repo. Commit rule changes with a message saying what defect
  taught the rule.
