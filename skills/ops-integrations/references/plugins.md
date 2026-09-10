# Plugins

Extends `ops-integrations`.

## A plugin cannot contain plugins

There is no folder to drop them into. A plugin is installed from a **marketplace**
and loads independently:

```bash
/plugin marketplace add anthropics/claude-plugins-community
/plugin install <name>@claude-community
/plugin                       # what is installed, and any load errors
```

`claude-plugins-official` (curated by Anthropic) registers itself on first
interactive start. So this file is a **list with reasons**, not a bundle.

## The budget applies here too

An installed plugin's skill descriptions and agent descriptions sit in context in
**every session**, exactly like the brain's own. A plugin with twenty skills is
twenty more descriptions, permanently.

So: install what replaces work actually done repeatedly, check `scripts/budget.py`
thinking afterwards, and **uninstall what has not been used in a month.**

## Worth having

| Plugin | Why | Watch out for |
|---|---|---|
| **LSP plugins** (Python, TypeScript, …) | Real code intelligence — go-to-definition, real types, real errors instead of grep and inference. The highest ratio of usefulness to context cost available. | Needs the language server binary installed locally. Check `/plugin` → Errors if it does not start. |
| **Anthropic official skills** — `skill-creator`, document skills (`docx`, `xlsx`, `pptx`, `pdf`) | `skill-creator` is worth reading as a reference for how Anthropic itself structures a skill. The document skills matter if you generate reports or spreadsheets for clients. | Only install the document ones if you actually produce those files. |

## Worth knowing about, but read this first

**Superpowers** is the largest community skills framework, and its architecture is
the same one used here: a folder of markdown skills plus a session hook. Its
discipline is *clarify → design → plan → code → verify* across fourteen skills.

**Installing it alongside this brain would give you two sets of rules covering
the same ground**, which is precisely what the constitution forbids — two copies
drift and then contradict each other, and the reader cannot tell which is
current. Reading it for ideas is worthwhile; running both is not.

If its planning discipline is the appealing part, that belongs in this brain as a
`planning` skill, not as a second framework.

## Not worth it

- Anything wrapping a CLI you already have. A `git` plugin is worse than `git`.
- A plugin whose skills you would immediately have to override.
- Anything installed "to try" and left enabled. That is the whole cost model.

## Before installing

1. **What does it replace?** If the answer is "nothing I do repeatedly", skip it.
2. **Does it overlap the brain?** Overlap is not additive — it is contradiction
   waiting for the first disagreement.
3. **Read its skills.** They will shape behaviour in every session, and a project
   plugin's `allowed-tools` grants take effect once you trust the workspace.
4. Record it here with the reason, so a later audit can tell whether it earned
   its place.
