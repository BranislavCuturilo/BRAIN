# Change safety — the cost of a change includes everything that depends on it

Extends `craft-code`. Read before improving code that already works.

## The rule

**A change that makes one thing slightly better and breaks something that
worked is a net loss.** Every time. There is no quality improvement worth an
outage, and "it was cleaner" is not a defence anyone accepts afterwards.

## A review's findings are a SNAPSHOT, and you act on them later

A reviewer lists what was true when it read the tree. By the time you act,
minutes have passed, and something in that list may have changed underneath you.

> A security audit flagged three leftover log files in a new app directory.
> Acting on the finding, all three were deleted — and one was the **live output
> of a background test run**, which lost its results at the moment they were
> about to matter.

So: **re-check the state of anything a review tells you to delete, move or
overwrite, at the moment you do it.** Not because the review was wrong, but
because it was true *earlier*. For a deletion the check is seconds — is anything
writing to it, has it changed since — and skipping it costs something
irreplaceable rather than merely being wrong.

This applies to any finding naming a file the reviewer did not create: **the
reviewer saw a photograph; you are acting on the room.**

So before changing working code:

1. **Enumerate every consumer.** Not "the obvious callers" — *every* one.
2. **Check each one against the change.**
3. **Say how many there were** in the report. That number is the honest measure
   of what you just did, and it is the number that tells the reviewer whether to
   read carefully.

## Finding the consumers — grep is not enough

Static search misses the places that break most often, because a name can be
referenced without being imported:

| Where a reference hides | How it is written |
|---|---|
| templates | `{{ obj.method }}`, `{% url 'name' %}`, a filter by name |
| configuration | a dotted path in settings, a middleware entry, an app label |
| the database | a stored class key, a stored path, a template name in a row |
| migrations | historical references to a field or model name |
| serialised data | a JSON payload with the old field name, a cached blob |
| tests | fixtures, mocks and patch targets naming the path |
| **other repositories** | an API contract, a webhook payload, a shared file format |

Search the **string** as well as the symbol. Search the singular and the plural,
the snake and the camel. A rename verified only against imports is a rename that
breaks at runtime, on a page nobody opened during testing.

## Adding a discriminator column to a shared model is a change to EVERY reader

A new `kind` / `mode` / `category` on a model that already has consumers does
not break anything — and that is the problem. Every existing queryset keeps
compiling and keeps returning rows, now of BOTH kinds, and every number it
feeds (a count, an average, a map marker, a rule match) is silently wrong for
the new kind.

> Measured: a "data mismatch" report kind was added next to "fault". The list
> and the hub were written kind-aware. Four other consumers were not: the
> analytics ("most problematic equipment" ranked a PC whose record said
> Linux), the map (a location with a paperwork mismatch became an open-fault
> marker), the automation rule matcher (a rule whose type was "any" paged the
> external servicer for a paperwork mismatch), and the demo seed. An
> independent refute pass found them; the author's tests were all green.

The rule, checkable before the diff is opened: **in the same change, grep
`<Model>.objects` and every manager/helper that wraps it, and classify each
hit — list, detail, aggregate/analytics, map/dashboard counter, rule or
automation matcher, notification/chat template, seed/export. Each one states
which kinds it serves, either by filtering or by a comment saying "all kinds
on purpose".** A nullable "any" FK on a rule table means *any of the kinds
that existed when the rule was written*; it must never widen to a new kind —
behaviour for the new kind is opted into explicitly. The wording of any
message a consumer emits is part of this ("New fault reported" for a
non-fault is a consumer that was not classified).

## To add a field to a mature model, trace an existing field of the same shape

The two rules above are about the READERS of a condition. This is the third
sibling and it is about the SURFACES that accept and display a value — and it
fails the other way round: nothing breaks, the field simply is not there on
half the screens, and the operator reports it weeks later as "you only put it
on one page".

Reasoning about which files *should* mention a new field does not work on a
mature model, because the answer is a list nobody holds in their head: the
form, a second design over the same form, the entry template, the detail
template, the list, the paste/import grid and its downloadable template, a
serializer, an export, a map marker payload, a change-log registry.

**The rule: pick an EXISTING field of the same shape as the one being added —
an optional scalar for an optional scalar — and grep for it across the whole
repo. That grep is the checklist.** It is complete by construction: every
place that had to know about a field of that shape already names the tracer.
Reasoning produces a list of the surfaces you remember; the tracer produces
the ones you do not.

> Measured: adding an optional numeric column to a `Location` model. Tracing
> the existing optional `postal_code` returned nine surfaces across six files
> — including a bulk paste grid, its generated spreadsheet template, and a
> settings-panel twin of the same form — of which a from-memory list had
> named three.

Two refinements worth keeping:

- **Pick the tracer by SHAPE, not by meaning.** A required field or an FK
  appears in places an optional scalar does not (and vice versa), so a
  same-topic-but-different-shape tracer yields a checklist that is both
  over- and under-inclusive.
- **The grep hits are candidates, not obligations.** Some are deliberate
  non-changes — a generic template that loops the form picks a new field up
  for free, an audit registry that tracks the whole model needs nothing. Say
  which ones you skipped and why; an unexamined hit is the same defect as an
  unfound one.

## Retiring a flag from the UI is all-or-nothing

When a customer says a switch is useless and asks for it to go, removing the
control is the easy half. The dangerous state is the half-retirement: the flag
can no longer be SET by anyone, but code still HONOURS it. Every row that
already holds the off value is then hidden by a condition no screen can reach —
invisible and unfixable, and the longer it lasts the more it looks like data
loss.

**The rule:** retiring a flag means finding every READER, not only the writer
you removed. The writer is the one you are looking at; the readers are
scattered, and they are rarely one. Expect at least these shapes, each usually
in a different file:

- the picker that offers the value on the main form
- the FILTER over the same data on its list screen — a different queryset,
  routinely missed
- any bulk / import screen's "allowed values" dropdown
- the downloadable template or export that advertises the same list
- reports, pickers in other modules, and API serialisers

Grep the field name across the whole repo and read every hit, because the
narrowing line often does not mention the concept at all — a grep for the
feature word finds nothing while `filter(is_active=True)` sits three files away.

Then keep the column and stop reading it, rather than dropping it: no migration,
no data destroyed, and the decision stays reversible. Say so in the comment, and
say WHERE the other readers are — the next person will otherwise re-add exactly
one of them.

**Where it bit:** a city code-list whose Active flag was retired; the first pass
widened the picker and left the list filter, the bulk dropdown and the xlsx
template still narrowing. An independent review found the other three. One
reader left behind is the whole defect, so this is a case where a second pair of
eyes earns its cost.

## When "every consumer" is too many to check

That is information, not an obstacle. Say it:

> This is called from 34 places across 6 modules. I verified the 8 that pass a
> non-default argument; the rest use the default path, which is unchanged.

**A partial check stated honestly is fine. A partial check reported as complete
is not.** If the count is large enough that checking is impractical, that itself
is the argument for not making the change — or for making it additively.

## Prefer additive over destructive

- **Add a variant; do not redefine an existing one.** Changing what an existing
  function, class, token or template block means silently changes every consumer
  that was never re-checked.
- **Deprecate before deleting** where consumers are numerous or outside your
  control.
- **Keep the old signature working** when adding a parameter — a default that
  preserves current behaviour costs one line and removes the entire blast radius.

## Clearing a value for another layer to refill inherits THAT layer's conditions

**What broke** — one rule ("the postal code follows the chosen city") was split
across two layers: the form CLEARED the field, the model REFILLED it from the
newly chosen source. The model's fill was conditional — it only wrote when the
source actually carried a value — while the clear was unconditional. Choosing a
source that carried nothing therefore deleted a value the user was looking at
when they pressed Save. No error, no message; the two halves each looked
correct in isolation.

**Why** — a clear-then-refill split is an implicit contract that the refill
ALWAYS fires. The refilling layer usually has guards (only when empty, only
when in scope, only when the source has a value), and every one of those guards
becomes a silent data-loss path for the layer that already destroyed the old
value. The upstream layer cannot see them; nothing links the two.

**The rule** — **assign the new value; do not clear and hope.** Compute what the
field should become and write it. If you genuinely must split, the clearing side
carries the SAME precondition as the filling side, stated in both places. And
state the invariant in a form that is checkable at the clear site: *a value is
only ever replaced by another value, never by nothing.*

**How to check it before writing the code** — read the refill and list every
condition under which it does NOT write. If that list is not empty, the clear is
wrong.

**Where it bit** — acme-audit `LocationForm.clean()` clearing `postal_code`
for `Location._reconcile_city_and_region` to refill (#17780 dopuna, 2026-08-28).
Caught by an adversarial review, NOT by the tests written alongside the change:
every test asserted one layer at a time, and the defect lived only in their
composition. When a rule spans two layers, at least one test must exercise the
whole path with the source in its degenerate state (empty, missing, out of
scope) — that is the case the split forgets.

## The edit you APPLIED may not be the edit you WROTE

Applying a change through a shell heredoc — `python - <<'EOF'` and friends —
**loses one level of backslash escaping, even with the delimiter quoted.** The
file then contains something that is not what you typed, and the damage is
invisible: a control character renders as nothing in every editor and in every
diff you are likely to read.

> Measured, 2026-08-31. A patch written as `re.sub(r"\b(office|doo|…)\b", …)`
> landed in the file as `re.sub(r"\x08(office|doo|…)\x08", …)` — the `\b` word
> boundary became a literal **backspace byte**. The regex required a backspace
> before and after the word, so it matched nothing, ever. `inspect.getsource`
> printed it back looking correct; running the same three steps by hand in the
> module's own namespace produced the right answer while calling the function
> produced the wrong one. It took a `repr()` of the raw line to see it.

The tell is that shape exactly: **the source reads correctly, a manual
reproduction of its steps works, and the function still returns the wrong
thing.** When those three are true, `repr()` the line — do not re-read it.

- **Use the file-editing tool for file edits.** It writes the bytes you gave it.
- **When a script must do the patching**, write the script to a file first and
  run it, rather than piping it through a heredoc. Same script, no shell in the
  path of the payload.
- **A `SyntaxWarning: invalid escape sequence` from a heredoc is not noise** —
  it is the shell telling you a level of escaping has already been eaten, and
  every other backslash in that payload is equally damaged.

Editing through the shell has a second, unrelated cost in this brain: it
bypasses the `Edit|Write` hooks entirely, so the visual-diff BEFORE gate never
fires (`visual-diff/references/gate.md`). Two independent reasons, one
conclusion.

## The trade, stated plainly

Before proposing an improvement to working code, answer:

- **What breaks if I am wrong?** and **who notices, how soon?** A silent break is
  worth several loud ones.
- **How much better does this actually get?** "Slightly cleaner" against 30
  consumers is not a case.
- **Can it be done additively instead?**

**A change to working code needs a reason beyond preference.** A defect, a
measured cost, a blocked feature. "I would have written it differently" is not
one — and it is the most common reason a working system acquires a regression.

## After the change

Run what covers it. If nothing does, **say so** — an unverified change to working
code is a proposal, not a result. And re-read every touched function whole, not
just the changed lines: a change applied hunk-by-hunk very often leaves logic
half-applied and syntactically perfect.
