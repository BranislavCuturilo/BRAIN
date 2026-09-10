---
name: ops-sync
description: >
  Keeping the brain in sync across machines, and resolving git conflicts —
  in the brain or in any project. Load when a pull or push fails, when a merge
  or rebase conflicts, when the brain was edited and needs pushing, or when two
  machines have diverged. Covers the pull-before-work rule, the conflict
  resolution protocol, and what must never be committed.
when_to_use: >
  "push the brain", "git conflict", "merge conflict", "cannot push", "diverged
  branches", "rejected non-fast-forward", after editing any brain file, setting
  up the brain on a new machine.
---

# Sync & conflicts

The brain lives in **one place per machine**: `~/.claude/skills/brain`, a git
clone of the shared repository. Personal-scope skills apply to every project on
that machine, so **there is no per-project clone and never should be.** A second
working copy is a second source of truth.

## Pull before work — automatic

A `SessionStart` hook runs `git pull --ff-only -q` in the brain at the start of
every session. It is `--ff-only` on purpose: if the machine has unpushed local
commits, the pull **fails cleanly** rather than inventing a merge commit behind
your back. Offline it fails silently and costs nothing.

If the brain looks stale, that is the signal there are unpushed local commits.
Push them, do not pull harder.

## Push after changing the brain

A brain edit that is not pushed exists on one machine only — which defeats the
entire point.

```bash
# Stage the paths you touched. `git add -A` is forbidden in this repo
# (CLAUDE.md): the tree routinely carries a live ticket sync and a regenerated
# dashboard at the same time, and `-A` commits both under your message.
git -C ~/.claude/skills/brain add skills/ops-scoring/SKILL.md scripts/brain/health.py
git -C ~/.claude/skills/brain commit -m "<what defect taught this rule>"
git -C ~/.claude/skills/brain push
```

`scripts/brain/sync.py` is the exception and the only one: it classifies paths
into groups and commits each group under its own message, so nothing is
described by a message about something else.

Push at the end of any session that changed a rule. Do not batch a week of rule
changes into one commit; the message should say what taught each rule.

## Conflict protocol

**Conflicts in the brain are almost always additive** — two machines added
different rules to the same file. That is a *merge*, not a contest, and the
correct resolution is nearly always **keep both sides**.

1. **Read both sides before touching anything.** `git log --oneline HEAD..origin/main`
   and `git diff` tell you what each side was trying to say.
2. **Additive conflict → keep both**, then merge them properly: if the two
   additions say the same thing in different words, **combine them into one
   rule** rather than leaving two near-duplicates. Two overlapping rules drift
   and then contradict each other.
3. **Genuine contradiction → the newer evidence wins**, and the loser is
   *deleted*, not commented out. Say in the commit message which one lost and
   why. A rule proven wrong is worse than a missing rule.
4. **Never resolve by picking a whole side blindly.** `-X ours` / `-X theirs` on
   a rules file silently discards a lesson someone earned on another machine.
5. **Never force-push.** Fix forward (`craft-git`). A force-push here destroys
   rules that exist nowhere else.

For code conflicts in a project repository the same order applies, with one
addition: **after resolving, re-read the merged function as a whole.** A
conflict resolved hunk-by-hunk very often produces code that is syntactically
clean and semantically incoherent — each side's logic half-applied. Run the
tests that cover it; a merge is a change like any other.

## Diverged branches

`rejected — non-fast-forward` means the remote moved. Never force. Instead:

```bash
git -C ~/.claude/skills/brain pull --rebase
# resolve per the protocol above
git -C ~/.claude/skills/brain push
```

Rebase (not merge) keeps the brain's history a readable list of "what we
learned, in order", which is worth preserving.

## Never commit to the brain

- Anything from `~/.claude/` outside the brain folder — `history.jsonl` holds
  every prompt you have ever typed, `projects/*/memory` holds client facts,
  `mcp.json` and `settings.json` can hold tokens, and `sessions/` holds whole
  conversations. **The repository is named `.claude` but contains only the
  brain.** Do not "helpfully" widen it.
- Credentials, tokens, connection strings, customer data, or a real person's
  details — in a rule, in an example, or in a journal entry.
- Machine-local paths as if they were universal.

## New machine

```bash
git clone https://github.com/BranislavCuturilo/BRAIN.git ~/.claude/skills/brain
```

That is the whole installation. Verify with `claude plugin list` — it should
report `brain@skills-dir … ✔ loaded`.
