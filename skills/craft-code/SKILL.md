---
name: craft-code
description: >
  Technology-agnostic code-writing discipline. Load BEFORE writing, refactoring
  or reviewing source code in any language. This router holds what applies to
  every coding task; references/ hold the detail per concern. Security is
  craft-security, reuse is craft-reuse, tests are craft-testing, framework
  mechanics are stack-*.
when_to_use: >
  writing or refactoring any code, a code review, "is this clean", a method that
  keeps growing, deciding where logic belongs.
---

# Code craft

## Applies to everything you write

- **Match the surrounding code.** Same layer boundaries, same naming, same
  comment density, same idiom. Code that reads differently from its neighbours
  was written by someone who did not look, and reviewers trust it less.
- **Never swallow an exception.** If a failure is genuinely non-fatal, log it
  with the traceback and continue. `except: pass` converts a bug into a permanent
  mystery. Catch specific types; a bare catch-all belongs only at the outermost
  request boundary. *(What an error reveals to a caller is `craft-security`.)*
- **The second occurrence is the trigger.** A literal, a status tuple, a
  permission list appearing twice becomes a named constant on sight. A constant
  costs nothing; two copies drifting costs an outage nobody traces.
- **Delete speculative work.** An interface with one implementation, a hook with
  no caller, error handling for a case that cannot occur. The requirement it
  anticipates will arrive in a different shape.
- **Only write a comment to state a constraint the code cannot show.** Not what
  the next line does, not why your change is correct — that is talking to the
  reviewer, and it is noise the moment the PR merges.

## SOLID, if that is the name you are looking for

Every letter is here, written as the consequence rather than the letter — a rule
you can check beats a principle you can agree with. The map exists so nobody
concludes it is missing and writes a second copy:

| | Lives in |
|---|---|
| **S** single responsibility | `references/responsibility.md` — a handler does four things; name services after what they return |
| **O** open/closed | `references/extension.md` — no per-customer branches, taxonomy is rows, a dispatch chain becomes a registry |
| **L** substitution | `references/patterns/strategy.md` — every subclass returns the declared shape and reads only declared fields |
| **I** interface segregation | `references/responsibility.md` — a mixin adds exactly one capability |
| **D** dependency inversion | `references/responsibility.md` — a module imports its own domain plus shared infrastructure, nothing else |

## Read on demand

| Working on | Read |
|---|---|
| a controller/view/handler, a service, deciding where logic belongs | `references/responsibility.md` |
| a growing `if/elif`, per-customer behaviour, configurable taxonomy | `references/extension.md` |
| more than one write, a partial-success loop, a base method that can bail | `references/transactions.md` |
| repeated literals, a config blob, two implementations of one calculation, **a state the UI can show and the store may not be able to hold** | `references/constants.md` |
| choosing a design pattern, writing a seed or fixture | `references/patterns.md` — an **index**; it names one file per pattern so you read only the one you need |
| **changing code that already works** | **`references/change-safety.md`** |
| a script that writes the sole copy of a file, `open(path, 'w')` | `references/file-io.md` |
| a choice a reasonable person would make differently and that is costly to reverse | `references/decisions.md` |
