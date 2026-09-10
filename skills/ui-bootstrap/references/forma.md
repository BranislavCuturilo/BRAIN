# Forma — forms, filters, anything with a dropdown

Extends `ui-bootstrap`.

## The clipping trap — read this before adding any select

**A card clips its contents, so a dropdown opened inside one is cut off at the
card edge.** The symptom is usually "the menu opens inside its own box" or "the
list is empty" — because what you see is the top few pixels of a menu that
extends below the clip. Root cause is by design: the clipping is what rounds the
card corners.

This has shipped as a client-visible bug on separate pages, twice, days apart.
It is not obscure — it hits **every** searchable select, combo, autocomplete,
date picker and action menu placed inside a card, which is most of them.

Fix, in order of preference:

1. **Use the shared searchable-select widget** if the project has one. It already
   positions with a fixed strategy, which escapes *any* clipping ancestor, and
   syncs the menu width to the trigger (width classes are meaningless once the
   menu is positioned fixed). Do not hand-roll a second combo.
2. **A plain framework dropdown inside a card:** initialise it with a fixed
   positioning strategy.
3. **Last resort** (static menu, no positioning library): make that one card
   overflow-visible, accepting that its corners no longer clip.

**A native `<select>` is NOT clipped by the card.** The browser renders its
option list in the top layer, above every `overflow:hidden` ancestor — so the
trap above is about *custom* combos, comboboxes and action menus only. If a
native select's options appear "outside the section", the select *element* is
sitting outside the card's visible/scrolled region (typically a wide table
scrolled sideways, so the last column hangs off the card) — fix the containment
(`lista.md` → table scrolls inside its card), not the select. Reaching for a
positioning library on a native select does nothing.

## Form scaffolding

- **Content inside the body wrapper.** A form pasted directly into a card is the
  classic flattened-to-the-edges page.
- **Label every field.** Placeholder-as-label disappears the moment the user
  types, and fails accessibility outright.
- **Errors render next to their field**, not only as a banner at the top. A
  page-level "please correct the errors below" with no per-field marker makes the
  user hunt.
- **Help text belongs on the field**, not in a manual. Any non-obvious rule
  (format, allowed range, why a value is refused) is help text.
- **Required vs optional marked consistently** — pick one convention per app and
  hold it.

## A generic field loop must not style a checkbox like a text input

`form-control` is sized for a text box: applied to `<input type="checkbox">` it
renders a full-width empty rectangle with the tick invisible at one edge. It
looks like a broken, unclickable control, and users report it as one — "the
button does not work". The checkbox is fine; only the class is wrong.

This is a TEMPLATE-LOOP bug, not a one-field bug. A generic form renderer that
does the obvious thing for every field —

```
{% for field in form %}{{ field|add_class:"form-control" }}{% endfor %}
```

— ships that dead rectangle on every boolean of every form it renders. One such
loop reached eleven settings screens before a customer photographed it.

**The rule:** any loop that applies a class to every field branches on the
widget, and checkboxes get the check treatment — `form-check-input` inside a
`.form-check`, label AFTER the box. Branch on the WIDGET TYPE
(`field.field.widget.input_type == 'checkbox'` in Django), never on the field's
NAME: a name test is invisible to the next boolean somebody adds, and if the
same rule is spelled three different ways in three templates, two of them are
already wrong. Radio widgets have their own `input_type` and are unaffected;
verify a multi-checkbox widget separately, since it reports the same type.

Screenshot it. This particular defect is invisible in the markup — the class
name reads perfectly reasonable — and obvious the moment the page renders.

## "Not there" and "there but empty" are TWO states — never render them alike

A record that carries both *whether a thing applies* and *what its value is*
has three states, not two: **absent**, **present but blank**, **present with a
value**. The blank one is the one that gets collapsed, because a template that
prints the value renders absent and blank identically — an empty cell, or the
same em dash — and the two mean opposite things. "This supplier is not approved
for us" and "this supplier is approved, nobody has typed their reference yet"
become one grey dash, and the operator cannot tell which they are looking at,
let alone which one they just saved.

**The rule:** wherever a value can be both absent and empty, render three
distinct things, and use the same three on every screen that shows them — the
list, the detail and the editor. Colour alone is not enough (it dies in a
grayscale print and in the "is that grey or that grey" question); each state
gets its own WORDS.

In an editor the two facts need two controls: a toggle for "does it apply here"
and a field for the value. Then:

- the toggle drives a state attribute on the row, **rendered server-side** so
  the states are right before any script runs, and updated by script only to
  keep it in step while the user types;
- the value input goes `readonly`, **never `disabled`** — a disabled input
  posts nothing, so untick-then-retick silently discards what the user typed;
- and the row that means "does not apply" gets a visual treatment the other two
  do not, on top of the words.

**Check it by loading one record in all three states at once** — not the state
you designed for. Screenshot it, then toggle each row and confirm the state
follows. A screen that only ever showed you the happy state has not been
checked.

## A generic field loop needs a way to SKIP the fields rendered elsewhere

The moment one form renders part of itself in a block of its own — a table of
per-entity rows, a matrix, a repeated group — the shared "loop over every
field" body will ALSO render those fields, once each, in the plain stack above.
The page then shows the same inputs twice with the same `name`, and the last
one wins on submit. It looks like a styling accident and is a data bug.

Give the form a list of the field names its own block owns (empty by default on
the base form, so every other form is unaffected) and have the loop skip them.
Put the default on the SHARED base class, not on the one form that needs it —
a template testing an attribute that exists on one form out of seven is a
template that silently does the wrong thing on the other six the day someone
changes how missing variables resolve.

## Behaviour

- **Disable the submit button on submit** or the user double-posts. If the action
  is not idempotent, that is a duplicate record (`craft-security` → races).
- **Preserve entered values on a validation error.** Re-rendering a blank form
  after a failed submit loses the user's work and is the fastest way to make a
  form hated.
- **Destructive actions confirm**, and irreversible ones require typing something
  — a bare `confirm()` on "delete everything" is not a safeguard.
- **A filter bar is a `GET` form**; a data-changing form is `POST`. Never a write
  behind a query flag.

## Layout

- Group related fields; a flat column of twenty inputs is a wall. Section
  headings or a card per group.
- Field width should suggest the expected input — a postcode field as wide as a
  description field tells the user the wrong thing.
- Actions go at the end, primary action visually dominant, cancel as a plain
  link. Never two competing primary buttons.

## Native inputs on a dark surface

- **A native `<input type="date">` (or `time`/`color`) styled with a
  *translucent* background token renders LIGHT on a dark surface**, even with
  `color-scheme:dark` and a light `color:` set. The UA composites the control's
  own field over the translucent value and it reads white — while
  `getComputedStyle().backgroundColor` reports the translucent token as applied,
  so the diff and even a computed-style probe say "it's dark" and only the
  **render** shows white. Fix: give it a **solid** dark background token (not an
  `rgba(...,.8)` one) plus `color-scheme:dark`, and theme the picker glyph with
  `::-webkit-calendar-picker-indicator{filter:invert(.7) …hue-rotate(…)}`. This
  is another case of the ui-bootstrap headline: *verify by rendering, not by the
  CSS you wrote or even the computed value.*
- A `margin-left:auto` filter/toolbar group whose last child is a `white-space:
  nowrap` note clips past the toolbar's own box (the note overflows the flex
  line). Bound the group (`flex:1 1 auto; min-width:0; justify-content:flex-end`)
  so its children WRAP within the available width instead of being pushed to
  overflow.
- **A textarea or input cannot style part of its own value**, so live/provisional
  text — speech-recognition interim words, a streaming answer, an inline
  suggestion — must NOT be written into the field to be shown "greyed". Put it in
  a separate element next to the field and move it in only once it is committed.
  Besides being the only way to style it, this is what stops provisional text from
  being submitted: what is not in the field cannot be sent by the button that
  reads the field.
- **When a capability is unavailable, disable every control that only steers it —
  not just the primary one.** The obvious button gets the disabled state and its
  satellites (a language picker, a mode toggle, a "retry with…" chip) get
  forgotten, leaving controls that look live and do nothing. Check by listing the
  controls that would be meaningless if the capability never arrives, and asserting
  each one's `disabled` in the unsupported state.

## Overlay panels & floating editors (compose, side panels, drawers)

- **An editor panel fills its pane only with a DEFINED height, a flex body, and a
  `flex:1` field.** A `position:fixed` compose/editor panel given only
  `max-height` (no `height`) collapses to its content, and a body textarea with a
  small `min-height` then reads as a thin strip — the "the writing area is
  miniature, nothing visible" bug. Fix: give the panel a real `height`
  (`min(85vh,780px)`), make the scroll body `flex:1;display:flex;flex-direction:column;min-height:0`,
  and the main textarea `flex:1` with a generous `min-height`. Verify the rendered
  textarea height, don't infer it.

- **A shared top bar that `flex-wrap`s will grow to two lines the moment someone
  adds a button — and silently overlap every `position:fixed` view that offsets
  its content by a HARD-CODED top padding.** Fixed full-screen views (a mail
  client, dashboard, flow canvas) commonly do `position:fixed;inset:0;padding-top:58px`,
  where 58px assumes a ONE-line bar. Add two buttons, the bar wraps at a laptop
  width, and the view's own toolbar disappears under the wrapped second line —
  invisible in the diff, and only at narrower widths. Two defensible fixes:
  keep the bar one line (`flex-wrap:nowrap;overflow-x:auto`, children `flex:none`,
  spacer `flex:1`) so the offset stays valid, **or** drive the views' top offset
  from the bar's real height. Never leave a wrapping bar in front of a
  fixed-offset view. **Check this at a narrow width (≤1280), not just your own.**

- **A non-blocking activity overlay must set `pointer-events:none`** on the
  container AND every child, or it silently eats clicks over its footprint even
  though it looks passive. Prove it: `document.elementFromPoint(x,y)` over the
  overlay must return the element BEHIND it, not the overlay.

- **`el.hidden = true` (and the `[hidden]` attribute) does NOTHING to an element
  that carries an author `display` rule.** The `hidden` attribute is only the UA
  rule `[hidden]{display:none}`, and an AUTHOR rule of *any* specificity — even a
  bare `.actions{display:flex}` — beats it, because author origin outranks UA
  origin in the cascade. Bootstrap makes it worse: `.d-flex{display:flex!important}`
  beats `hidden` outright, and even beats an inline `style="display:none"`. So a
  toggle that hides a flex/grid row by flipping `hidden` (or by removing a `.d-flex`
  and hoping `[hidden]` takes over) leaves the row fully visible, silently. Three
  clean fixes, in order: **conditionally render** the block (omit it from the DOM
  when closed — also the safest for held-in-state form values); toggle `.d-none`
  (which is itself `!important` and, placed after `.d-flex` in Bootstrap's source,
  wins); or, for a non-Bootstrap author `display`, set inline `style.display="none"`
  (inline beats an author class of any specificity, but NOT an `!important` one).
  **Check it by reading `getComputedStyle(el).display` after the hide, not by
  trusting that `hidden` "should" work** — this fails invisibly in the diff.

- **A new docked panel that shares a CSS shell with siblings via a grouped
  selector must be added to BOTH the base rule AND the `.open{display:flex}`
  rule — and its show/hide must be wired in every place the siblings are.** The
  shell pattern is `#panel-a,#panel-b{position:fixed;…display:none}` +
  `#panel-a.open,#panel-b.open{display:flex}`. Add the id to the base selector
  only and the panel stays `display:none` forever — it "does nothing" on click,
  with no error and a diff that looks complete. Then wire it in ALL sibling
  touch-points, not just the one you're looking at: each `open*()` closes the
  others AND every other `open*()` closes this one (mutual exclusion is O(n) edits
  in *both* directions — the reverse direction is the one missed), the `Escape`
  handler's panel list, and any "hide all panels" path (e.g. an availability gate
  that hides AI panels when the key is absent). Verify by clicking the new toggle
  and screenshotting that it appears, then opening a sibling and confirming the
  new one closed.

- **A panel that caches a fetched result scoped to the current list (folder,
  account, active tab) must invalidate that cache when the scope changes.** If the
  cached rows carry ids (uid, pk) that are only valid within that scope, a stale
  cache makes a row-click act on the wrong record after the user switches folder/
  account — silently, because the panel still shows plausible-looking rows. Key
  the cache by the scope (`acct+"\n"+folder`) and refetch when the key differs, or
  reset it in the same handlers that change the scope. Row-click should also close
  the docked panel so the reader/detail underneath it is visible.

## Replacing a plain textarea with a rich-text editor

Three one-way traps, all of which corrupt data on the FIRST save rather than
failing visibly. Check each before shipping the swap.

**Existing rows are plain text, and the editor parses HTML.** A newline is
whitespace in HTML, so handing an old body straight to the editor shows it as
one collapsed paragraph — and saving writes the collapse back. Convert on the
way IN, on the server where it can be tested, not in the editor's JavaScript.
Deciding plain-from-rich has its own trap: see
`craft-security/references/untrusted-input.md`, "A sanitizer is a transform, so it cannot
double as a detector".

**The editor's serialization is not necessarily semantic, and your sanitizer
will finish the job.** Quill 2 emits EVERY list as `<ol>` and puts the real kind
on the item (`<li data-list="bullet">`); an allow-list that does not carry
`data-list` strips it, and every bullet list a user writes renders NUMBERED.
Normalize the editor's output into semantic markup before sanitizing — and
resolve the kind PER ITEM, because a type-agnostic container holds runs of
different kinds and deciding from the first item silently turns a numbered
procedure into bullets. **Read the editor's actual output in a browser; do not
assume it round-trips to the markup you would have written.**

**A toolbar button whose result the sanitizer rejects fails silently.** Quill's
image button reads the picked file with `FileReader.readAsDataURL` and inserts a
`data:` URL; a protocol allow-list of http/https/mailto drops the `src`, so the
image disappears on save under a success message. Either allow the scheme
deliberately (a real decision — SVG is executable, base64 bloats the column) or
tell the user the workflow that does work. Do not ship an instruction the
allow-list contradicts.

**The field must survive the editor not loading.** The textarea is normally
hidden and the editor rendered in its place, so with JS off — or the CDN
unreachable — the user gets a label above an empty box and a form that silently
re-posts the old body. A `<noscript>` rule that hides the editor host and shows
the field costs four lines. Do not claim graceful degradation without it.

## Server side

The Django-specific rules — scope-filtered FK querysets, stamping the scope on
the instance in `__init__`, and why a forbidden-value guard must exempt the
instance's own current value — are in `stack-django/references/forms.md`. That
last one is what makes a row permanently uneditable, and it presents as a UI bug
("I can't save this page"), so check it when a form refuses a value the user did
not change.

## An autocomplete that fires on focus AND on typing races itself

**What broke** — a member-search box in a chat panel ran one search on focus
(empty query, the whole list) and a debounced one on input. Two fetches were in
flight; whichever answered LAST painted the list. In the browser drive the stale
full list was still showing under the typed query and the click added the wrong
person (Ticketing-System2 #87083, 2026-08-28).

**Why** — every fetch-and-render autocomplete assumes responses arrive in
request order. Nothing guarantees it, and the empty query is the slowest one.

**The rule** — number the requests and render only the newest: `const seq =
++counter; … .then(data => { if (seq !== counter) return; render(); })`, on the
error path too. Checkable: an autocomplete with a debounce but no sequence guard
is wrong. And a browser test must wait for the RESULT it expects
(`to_have_count(1)`, `to_have_text(...)`), never for "some result row" — the
stale list satisfies that selector instantly.

## Za testera i za ekstenziju

Isto pravilo, iz ugla onog ko prijavljuje. Blok ispod je izvor za
`BugReporter/src/skills/screen-form.md` (generiše `scripts/brain/extension_skills.py`);
menja se OVDE, nikad u kopiji.

<!-- ebr:skill id="screen-form" name="Forma — šta svaka forma mora" kind="create, update" scope="compose" -->
Forma mora da: obeleži svako polje (ne samo sivim tekstom u polju), prikaže grešku UZ polje a ne samo na vrhu, sačuva unete vrednosti posle neuspelog čuvanja, onemogući dugme dok se šalje (dupli klik = dupli zapis), traži potvrdu za brisanje, obavezna polja označi dosledno.
Kako da klasifikuješ:
- Posle greške forma prazna, sve uneto nestalo → bug.
- Dupli zapis posle dva klika → bug, prioritet Major (podaci).
- "Ne mogu da sačuvam", a korisnik NIJE menjao polje koje se odbija → bug, poznata klasa (forma odbija sopstvenu tekuću vrednost); traži tačnu poruku i koje polje.
- Kvadratić (checkbox) izgleda kao prazno široko polje, "dugme ne radi" → bug prikaza, poznata klasa; slika je dovoljna.
- Padajuća lista se otvara prazna ili sečena unutar kartice → bug prikaza, poznata klasa; navedi koje polje. Ako je NATIVNI select sa opcijama "van sekcije", greška je u tabeli koja izlazi iz kartice, ne u select-u.
- Polje za datum ili vreme belo na tamnoj pozadini → bug prikaza.
- Uređivanje zaključanog ili zatvorenog zapisa nije moguće → "limitation" ako kontekst ekrana to kaže, inače "question".
- Autocomplete ubaci pogrešnu osobu/stavku iako je korisnik kucao pravu → bug, poznata klasa (stari rezultat stigne posle novog); traži tačan redosled kucanja.
- Novo polje, drugačiji raspored, dodatna validacija → "change_request".
<!-- /ebr:skill -->
