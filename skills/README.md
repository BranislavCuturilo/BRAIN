# skills/ — for the reader

*This file is for you, not for Claude. The full table is
[`../docs/SKILLS.md`](../docs/SKILLS.md), generated from the files themselves.*

## The one number that explains the layout

A skill's **description is in context in every session**, whether it is used or
not — about 150 tokens each. Its **body** costs only when the skill loads. A
`references/*.md` file costs **nothing** until something reads it.

So a thousand small skills would cost 150,000 tokens before you typed anything,
while a thousand small *references* cost nothing. That is the entire reason the
layout looks the way it does:

```
craft-security/
├── SKILL.md              one description, always on. A router: the rules that
│                         apply to everything here, plus a table pointing onward
└── references/
    ├── isolation.md      1-3 KB each, free until read
    ├── authorization.md
    └── ...
```

**Few routers, unlimited references.** Something becomes its own skill only when
a task would load *it* without loading a parent.

## The four layers

A rule belongs to the **outermost layer where it is still true**:

| Layer | True for | Example |
|---|---|---|
| `craft-*` | any language, forever | a guard on one path must be on every path |
| `stack-*` · `ui-*` | one technology | field validation runs before `clean()` |
| `arch-seams` | how two technologies meet | with a SPA, CSRF moves to the fetch layer |
| a project's own skills | one repo only | what "closing a stocktake" means here |

The test: *would this be true in a different repo?*

## Navigating

Open `/brain:brain` first — it is the constitution: the routing law, the
ownership table saying which skill owns which concept, and the size budgets.

## Adding or changing one

Read [`brain/references/splitting.md`](brain/references/splitting.md). The short
version: put it in an existing router's `references/` unless it must trigger on
its own, keep the description under ~450 characters, and never restate a rule
that already has an owner — cross-reference it.

**Every rule traces to an incident, a measurement, or a marked hypothesis.**
A rule written from general knowledge looks identical to an earned one once it
is in the file, and that is how the whole thing rots. The three doors and what
each one costs are in `CLAUDE.md` — stated there once, because this said
"should" while that said "must" and a reader looking for the lenient version
would have found it here.

## Checking your work

```
python scripts/budget.py          what it all costs, and what is over budget
python scripts/brain/score.py     which skills have earned their place
python scripts/brain/docs.py      regenerate the tables
```
