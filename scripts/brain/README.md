# scripts/brain/ — tools for the brain itself

*For the reader.*

| Script | Answers |
|---|---|
| `score.py` | Which skills and agents have earned their place. `record` an outcome; `review` for what to retire or improve. Nothing measures this automatically, so the data is only as good as the recording. |
| `site.py` | Renders every documentation file into one self-contained browsable page, `docs/index.html`. Pure standard library; the markdown subset is deliberately limited so anything outside it renders as plain text rather than silently wrong. |
| `docs.py` | Regenerates `docs/AGENTS.md` and `docs/SKILLS.md` from the frontmatter. `--check` exits 1 when they are stale. A hand-maintained table of 45 agents is wrong within a week. |

`../budget.py` (one level up) measures what everything costs: descriptions that
are always in context, skill bodies once loaded, and what each agent pays per
invocation.

Run them after any change to `skills/` or `agents/`.
