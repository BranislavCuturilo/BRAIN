# Page scripts, and blocks the template can hide

Extends `ui-bootstrap`. Read when a template renders conditionally (`{% if %}`, a feature flag, a collapsible, a tab), when a page script addresses elements by id or class, and when markup moves into a shared partial.

The common failure is silence: one throw in an IIFE kills every later handler in the file, and the page shows no error at all.

**Assigning `className` wholesale drops the class you found the element by.**
`el.className = "note err"` replaces the whole list, so a marker class used as
the selector is gone after the first write — the element is still on the page and
no longer matches its own selector, so every later lookup silently finds nothing
and the feature works exactly once. Use `classList.add/remove/toggle`, or
re-state the marker in the assignment. An element found by `id` survives this,
which is why the bug appears only after someone switches a lookup from id to
class — and it will look like the element was deleted.

**Feedback rendered into a collapsed container is feedback nobody gets.** When
one action gains a second entry point — a button on a collapsed header as well as
in the expanded body — its answer has to appear at whichever one was pressed.
Writing the outcome into a single fixed node means the control that is reachable
while collapsed reports into a hidden one, and a button that answers invisibly is
indistinguishable from a button that does nothing.

**A JS hook whose block the template can hide must be looked up defensively AND
loaded in the hidden state.** Conditional rendering (`{% if %}`, a feature flag,
a variant of the same page) removes elements that page scripts still address by
id. `document.getElementById('x').addEventListener(...)` throws on the variant
that omits `x`, and because these scripts are usually one IIFE, the throw kills
every later handler in the file — the GPS button, the photo preview, the submit
hook — with no visible error on the page. Guarding is half the fix; the other
half is checkable: **load the page in the state where the block is ABSENT and
assert zero console/page errors.** A guard you have not exercised in the absent
state is unverified.

**Collapsing a section hides its inputs, not its data — and hides your
indicators too.** Two consequences, both checkable:

- **Never read a typed value back out of the DOM at submit time.** `getElementById(
  'cap-'+id).value` works right up until the first collapsible/tab/accordion, and
  then it silently sends nothing for every field the user typed into a section
  they later closed. Hold the value in state on `input` and read the state. Check
  it: type, collapse, submit, assert the value arrived.
- **Whatever tells the user "this thing is busy / N of M done / K left to review"
  belongs on the HEADER, not inside the body.** Put it in the body and collapsing
  the section deletes exactly the feedback the collapse was supposed to make
  reachable. Check it: screenshot the CLOSED state and read the header.

Measuring a `<details>` right after `.click()` also lies: Chrome defers the
content hide, so `getBoundingClientRect()` on a child of a closed `<details>`
still returns the open geometry. Measure the `<details>` element itself, one
frame later, against the height of its own `<summary>`.

**A shared include must not depend on an asset each page opts into.** Markup that
moves into a shared partial takes its JavaScript and CSS requirements with it, so
the script belongs in the layout every page already extends — not in a `<script>`
tag repeated per template. Otherwise the partial works on the pages that happened
to load the asset already and silently degrades everywhere else: an enhanced
control renders as its raw native element, which looks like a styling bug rather
than a missing file. Three screens shipped that way in one change. The check is
cheap — render every page that uses the include and assert the enhancement
actually mounted, not that the page returned 200.

## One markup, two renderers

A screen that renders the same row server-side AND builds it from JSON has two
copies of that row the moment the script contains any markup — and the copy in
the script is the one that misses the next fix, because nobody greps `.js` for a
table cell.

**Render the row ONCE, server-side, into an inert `<template>`, and let the
script clone it and fill named slots.** The template is produced by the same
partial the page renders, so a change to the row reaches both callers; the
script only sets `textContent` on `[data-field]` elements and `href` on
`[data-link]` ones. Two things follow for free: every user-facing string stays a
translatable tag in a template (a script cannot call gettext), and the "no value"
case keeps whatever default the server rendered — so a row with no actor still
says "System" without the script knowing that word.

Check it by grepping the script for `<` and for class names: a tag or a
`className =` in there is the second copy.

