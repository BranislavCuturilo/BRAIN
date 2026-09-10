# What must not happen while an agent is running

Extends `ops-delegation`. Read before starting anything in the background, and before any git operation while one is alive.

## Never run a whole-tree git operation while a background agent is writing

**What broke** — `git stash push -u` was run while a background subagent was
actively writing files into the same repo. The stash swept up the agent's
in-flight work; the agent immediately recreated some of it, and the later `git
stash pop` then ABORTED ("would be overwritten"), costing a careful
backup/compare/delete/pop recovery. **This recurred in the same session** — a
leftover `git stash` reused from an earlier command paid the same recovery cost
a second time.

**Why** — a background agent shares the working tree, and nothing coordinates
the two writers. `git stash`, `git checkout -- <path>`, `git reset` and a
worktree-wide `git clean` are not scoped to "my files" vs "its files"; they
mutate the whole tree.

**The rule** — while ANY background agent is running against the repo, never
run `git stash`, `git checkout -- <path>`, `git reset`, or a worktree-wide
`git clean`. To commit a subset of files while an agent runs, stage a
hand-built blob instead of touching the working tree at all:
`git hash-object -w <tmpfile>` + `git update-index --cacheinfo
100644,<sha>,<path>`. This is also how to split one ticket's files out of a
commit while another ticket's work sits uncommitted in the same files. If a
whole-tree operation is genuinely required, stop the agent first (`TaskStop`)
and restart it afterwards.
