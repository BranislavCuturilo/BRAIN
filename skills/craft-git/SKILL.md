---
name: craft-git
description: >
  Version-control and deployment discipline that holds for any stack: what may
  never be done to a shared branch, how an automated deploy must fetch and fail,
  which steps are fatal and which are not, and how to diagnose a deploy that
  died silently. Load BEFORE editing any CI/CD pipeline, deploy script,
  container or compose file, or before rewriting history. Encodes two real
  production outages.
when_to_use: >
  editing a CI workflow or deploy script, force-push, rebase or history rewrite,
  "the deploy failed", "nothing shipped", writing a release step, container or
  compose changes, adding a conditional to a pipeline.
---

# Version control & deploy craft

## Never rewrite published history on a shared branch

A cosmetic fix is never worth it — make a follow-up commit instead.

> A `--force-with-lease` to correct a commit-message typo rewrote the main
> branch. The production server still held the pre-rewrite commits, so the
> deploy's `git pull` aborted on divergent branches — before build, before
> migrate. Nothing shipped, and the running container silently served the old
> build. The failure was in the *first* step, so no application error ever
> appeared.

**Rules:** no force-push, no rebase, no amend of anything already pushed to a
shared branch. If a broken commit is already public, fix forward.

## Deploy pulls by mirroring, not by merging

`git fetch` + `git reset --hard origin/<branch>`, never `git pull`. A deployment
target holds no local commits — it is a mirror. Mirroring self-heals against any
divergence, including one you caused by breaking the rule above. Merging asks the
server to reconcile, which it cannot do unattended.

## Build before you stop

`docker compose build` while the old container still serves, then `up -d`
(it recreates only what changed). Never `down` before `build`: the site is
then dark for the whole image build, minutes, when the swap itself costs
seconds. The git step is not where the time goes -- a `fetch` on an existing
mirror moves only the new objects -- so a shallow clone (`--depth 1`) is not a
downtime lever; it only matters on a first clone, and it costs `git describe`
and any log-based version stamp.

> `popis/deploy.sh`: `git pull` -> `docker-compose down` -> `build` -> `up -d`
> -> migrate. Every deploy takes the application down for the length of the
> build. The sibling script in MNS_HRIS already does `build` -> `up -d` with
> no `down`, and its outage is the container restart.

## Fail-fast pipelines and commands that legitimately exit non-zero

> A translation step was gated on `… | grep -q '\.po$'`. `grep -q` exits 1 when
> it finds nothing — that is the *normal* case. Under fail-fast, that exit-1 was
> treated as fatal and killed the deploy immediately after `migrate`: no
> collectstatic, no error message, no clue. Deploys failed on every push that did
> not touch a translation file, and succeeded on every push that did — which is
> about the most misleading symptom a pipeline can produce.

**Rules under fail-fast (`set -e`, `script_stop`, most CI defaults):**

- **No step may depend on a bare command whose "nothing found" result is
  non-zero** — `grep -q`, `test`, `diff`, `git diff | grep`. Either invert it so
  the expected outcome is exit 0, or append `|| true`.
- **Mark non-critical steps explicitly non-fatal** (`|| true`). Asset
  compilation, cache pruning, notifications: none of these should ever abort a
  release.
- **Keep critical steps fatal.** Migrations and static collection must abort the
  deploy when they genuinely fail. Do not blanket every line with `|| true` —
  that turns a fail-fast pipeline into a pipeline that always "succeeds".
- **Prefer always-run-idempotent over clever-conditional.** A cheap step that
  runs unconditionally cannot be broken by the conditional's edge case. Most
  "only run this when X changed" logic costs more than the step it skips.

## Make failure legible

- **Echo a marker before each step** (`[deploy] migrate`). Under fail-fast the
  last marker printed *is* the failing step — without markers you get an exit
  code and nothing else.
- Know your pipeline's signatures. "Migrations applied, then exit 1, no further
  output" means a post-migrate step aborted. A reconcile-divergent-branches error
  means history was rewritten.

## Record who wrote it, when agents did the writing

A commit produced with delegated agents carries trailers naming them:

```
Brain-Agents: dj-list-view, dj-templates, reviewer
Brain-Skills: stack-django, ui-bootstrap, craft-security
```

Generate them rather than recalling them — `scripts/brain/attribute.py` reads
the session's actual run records and prints both lines:

```bash
git commit -m "$(printf 'feat: thing\n\n%s' "$(python ~/.claude/skills/brain/scripts/brain/attribute.py)")"
```

**Why the commit and not a ledger:** months later a user reports that something
is broken or badly built, and the useful question is *which agent wrote it and
what was it working from* — because a bug fixed only in the code gets written
again by the same agent from the same brief. Transcripts are cleaned up and a
side ledger drifts from the code it describes. Git history is the only store
that survives and that `git blame` can already address at line level.
`scripts/brain/blame.py` reads it back; `/brain:ops-postmortem` is the loop.

**This is now a gate, not a request.** `scripts/brain/attribute_gate.py` denies
a `git commit` when agents ran since the last one and the message does not name
them, and prints the exact lines to paste. It was written the day it was
measured that the habit had stopped on 2026-08-21 -- 153 commits, none
attributed, while `attribute.py` worked the whole time. It is silent when
nothing ran, which is most commits.

**Attribute what actually ran, or not at all.** A trailer listing the agents you
*meant* to use is worse than none: the next person treats it as a record. If the
transcript is gone, omit the trailers and say so.

## Build artifacts and manual steps

- **Compiled artifacts are built on the target, not committed.** A missing local
  build is then not an error, and no one has to install a toolchain to contribute.
- **A config file in the repo that mirrors something living outside the
  deployment is a *reference*, not a deployment.** Web server configs, cron
  entries, DNS: editing the copy in the repo changes nothing on the host. Say so
  explicitly, in the file and in the report, whenever a change needs a manual
  step — and never report such a change as shipped until it has been applied.

## Write the message to a file and commit with `-F`, never `-m`

A commit message that explains anything worth explaining contains backticks —
around a symbol, a flag, a pattern. Inside `git commit -m "…"` the shell runs
everything between them as a command and substitutes the (empty) output, so the
message is committed with holes exactly where the specifics were.

**It does not fail.** The commit succeeds, `git` reports success, and the
damage is a sentence whose subject is missing — visible only if you read the
message back.

> Four times on 2026-09-10 alone: a regex mangled across two lines in
> `upstream.py`, an RTF fix in `attachments.py`, and two commit messages —
> `d11c917`, which lost the subject of the sentence describing a `\s*` defect,
> and `4eaf111`, which lost both examples from the rule they justified. Each was
> found by re-reading, never by an error.

```bash
# the message goes in a file, and the shell never sees its contents
git commit -F <path-to-message>
```

`heredoc_gate.py` refuses a heredoc piped into an interpreter — the same class
of defect through a different vector. `-m` is not yet gated, so this rule is
what stands between you and the fifth occurrence. **If a mangled message is
already pushed, fix forward** — the no-rewrite rule at the top of this file
outranks a tidy log.

## Stage what you changed, never `git add -A`

A working tree is rarely yours alone. Regenerated docs, runtime data files, a
half-finished edit from yesterday, a scratch file a script wrote to the repo
root — `git add -A` sweeps all of it into a commit whose message describes only
your work, and the misdescription is what does the damage: months later the log
is the only record of why a file changed, and for those files it now lies.

Stage the paths you actually touched. When a session starts with a warning like
"N uncommitted changes", that is not noise to scroll past — it is the list of
things `-A` is about to adopt.

Two ways it lands, both seen in one session:

- **a tool wrote into the repo.** A measurement or debug script defaulting to
  the current directory drops its output next to the source. Point scratch
  output at a scratch directory, and check `git status` before staging rather
  than after pushing.
- **someone else's edit was already sitting there.** Here it was a docs
  regeneration flagged at session start; harmless in content, wrong in
  attribution.

If it is already pushed to a shared branch, leave it — the rule above outranks
tidiness — and say so plainly rather than quietly rewriting.

## Working rules

- **Commit or push only when asked.** If work lands on the default branch and the
  user has not said otherwise, branch first.
- **Never skip hooks or bypass signing** unless explicitly asked. A failing hook
  is information; investigate it rather than routing around it.
- One logical change per commit, and a message that says *why*. When the commit
  fixes a defect that produced a rule, name the rule.
- **Verify what shipped, by behaviour.** A green pipeline says the steps exited
  zero, not that the new code is serving traffic. Check something only the new
  version does.
