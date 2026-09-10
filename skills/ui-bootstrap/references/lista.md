# Lista / tabela — lists and tables

Extends `ui-bootstrap`.

## Every multi-row list is striped

A flat white list with hairline separators is the wrong look in a design-system
app, and it is what you get by default. Use the design system's table component
so the striping comes for free and you write **no row-colour CSS**:

```html
<div class="table-wrap">
  <table class="hr-table mb-0">
    <thead><tr><th>…</th></tr></thead>
    <tbody><tr>…</tr></tbody>   {# odd/even coloured by the system #}
  </table>
</div>
```

**Both row states are tinted** — two close shades chosen so the difference is
visible. Never white alternating with a tint; that reads as a rendering bug, not
as striping. Do not reinvent it with `table-striped` or a hand-rolled
`:nth-child`; use the house component so a token change reaches every table.

**If it genuinely must be a `<div>` list** (clickable rows, cards-as-rows), apply
the same two row tokens by hand in the same alternating pattern, plus the same
hover. It has to read as the same system.

Prefer the table whenever the data is tabular. "It needs to be clickable" is not
a reason — a table row can be a link.

## Structure

- **Header row is real `<th>` in a `<thead>`.** Sorting, accessibility and sticky
  headers all depend on it.
- **A full-bleed table inside a card** is the one case where content skips the
  body wrapper: the rows supply their own padding and the card's clipping rounds
  them off. The heading still uses the header wrapper.
- **A table that can be wider than its card scrolls INSIDE the card, never off
  the page.** The card *wrapper* is the bounded box: put `overflow-x:auto` on it
  (it carries the border, radius and clip) and give the table a `min-width` so
  many columns don't crush to unreadable slivers. `width:100%` on the table alone
  does **not** contain it — the table squeezes columns and, where nowrap headers
  force it past 100 %, the whole card spills past the viewport, carrying the last
  column's control with it. Guard the screen's own container with
  `overflow-x:clip` (not `hidden` — `hidden` forces the other axis to `auto` and
  adds a stray vertical scrollbar). Symptoms when missing: "the card leaves the
  screen" and a per-row control (select, menu) sits "half outside the section".
  Adding columns is exactly when this regresses — re-check it whenever the column
  count grows.
- **That horizontal-scroll wrapper is a scroll container on BOTH axes.**
  `overflow-x:auto` forces the *computed* `overflow-y` to `auto` — CSS couples the
  axes, there is no "scroll X, leave Y visible". Being auto-height, the wrapper's
  LAST row sits flush against its bottom edge — exactly where the horizontal
  scrollbar and the `border-radius` corners are painted — so the last row's
  controls (a per-row `<select>`, an action menu) get their **bottoms clipped**,
  and worse under a thick or overlay scrollbar (the visible-fine classic scrollbar
  in headless hides it). Give the wrapper a `padding-bottom` to lift the last row
  clear of that band. **Verify by measuring the last row's control rect is fully
  inside the wrapper's content box top AND bottom** — a horizontal `scrollWidth`
  vs `clientWidth` probe passes green while this vertical clip is still there, so
  the horizontal check alone is not enough.
- **Filter chips live in a `flex-wrap` row or a horizontal scroll strip — never a
  bare inline `<span>`.** Pills concatenated with no whitespace between them have
  no line-break opportunity, so an inline span of them renders as one unbreakable
  line that spills the card off-screen ("the categories leave the screen").
  Containing ONE filter group does not fix its siblings: grep every group in the
  bar when one overflows — the ones left as inline spans are the forgotten ones.
- **Align numbers right, text left.** Money and quantities get the same number of
  decimals in every row, or the column is unreadable.
- **Sort and filter state belongs in the URL**, so a filtered list can be shared,
  bookmarked and reloaded without losing it.
- **A null-tolerant sort comparator decides "null/empty sorts last" BEFORE
  applying the direction multiplier, and descending is expressed by that
  multiplier — never by swapping the comparator's two arguments.** Swapping
  the args to invert order also re-inverts the null-last rule, so descending
  (or any tie-break reusing the comparator) pushes empties to the top instead
  of the bottom. Shape: `cmp(A, B, dir)` resolves the null-last case first,
  independent of `dir`, then multiplies only the non-null comparison by `dir`.
  Bit a ticket table where an empty `deadline` sorted to the top in one
  direction only.

## Rows are data, and data is scoped

**Never render a raw reverse relation in a list.** A list of children under a
parent must come from the same visibility helper the standalone list view uses,
filtered to that parent — reaching the parent does not entitle the viewer to
every child (`craft-security` → "an aggregate re-scopes every member").

**Every total shown must be computed over exactly the rows displayed.** A count
or sum taken over the unscoped set, next to a partially-scoped list, silently
leaks the size of what the user cannot see — and is simply wrong as a number.

## Actions in rows

- A destructive or state-changing action is a **POST form**, never a link with a
  query flag. A `GET` that writes is a CSRF hole (`craft-security`).
- Keep at most one primary action per row plus an overflow menu; a row with five
  buttons is unreadable at any width.
- An action menu inside a card gets clipped — see `forma.md`.
- **When a row has both an on/off state and a "which option" choice, the option's
  highlight shows ONLY while the row is on.** The two states are independent in
  the data and read as one thing on screen: a filled/accent chip is the loudest
  element on the row, so on a row that is switched OFF it says "this one is
  included" when it is not. Render the options muted until the row is on, and let
  picking an option turn the row on. **Check it by rendering the OFF row**, not
  the on one — the mistake is invisible in the state you designed for.

- **An `<a>` with no `href` still looks like a link.** Frameworks style the
  ELEMENT (`a { text-decoration: underline; color: … }`), not `a[href]`, so a
  row whose link target is optional — one partial serving a source that has a
  URL and one that does not — renders underlined blue text that nothing
  happens on. It reads as a broken link, not as plain text. Give the partial a
  `:not([href])` rule that puts the colour back to the body colour, drops the
  underline and sets `cursor: default`; do the same for a card/tile made of an
  `<a>`, whose hover lift otherwise still fires. **Check it by rendering a row
  whose URL is absent** — the state you did not design for is the whole point.
  Measured on an event log where every title read as a link and none was one.
- **A column whose value is identical on every rendered row is noise, and in a
  narrow container it is expensive noise.** A per-area tab repeating the area
  name on each row, or a single-location card repeating the location, costs the
  "what happened" column the width it needs — measured at 327px, the title
  column was squeezed to about 60px and the table went to horizontal scroll;
  dropping the one invariant column made it fit. The rule is not "does this
  field exist" but "does it VARY": count the distinct rendered values and drop
  the header AND its cells when there are fewer than two. Server-side, the same
  decision comes from whatever the page already knows (a roll-up flag, the
  active tab) — not from a second query.

## Empty, loading, and too many

- **Design the empty state.** "No records yet" plus the action that creates the
  first one. An empty table with headers and nothing under them looks broken.
- **Paginate before the list can grow unbounded**, and show the total. A list
  that silently caps at 100 rows is worse than one that says so.
- **A "Load more" button is shown ONLY when the last page came back full**
  (`page.length >= limit`) — a full page is the only evidence a next one exists.
  When the last page was short (fewer than `limit`, including the very first
  load), replace the button with the shown count ("18 messages"), never a button
  that fetches nothing. A folder/list that genuinely has fewer rows than one page
  gets a count and no button — otherwise the user clicks a dead "Load more" and
  reports it as broken (this is the literal "load more doesn't work" bug: INBOX
  had 18 messages, page size 50). "Load more" **appends the next page via
  `&offset=<rows already shown>`** and concatenates — it does not re-request from
  `offset=0` with a grown limit (that refetches everything and scales badly).
  Verify by rendering both states: a full-page list shows the button and a second
  click appends the next slice; a short list shows the count and no button.
- Filters that produce no results need a *different* message from a genuinely
  empty list, with a way to clear them.
- **A filter added to fix "too many rows" is verified against the REAL stored
  data, not against your fixture.** The fixture holds the canonical spelling of
  the key; the store holds whatever a human typed. Both fail silently and in
  opposite directions — matching too little ships an empty panel, which reads as
  "there is nothing here" and is a *worse* bug than the one you were sent to fix.
  Measured: a gallery filtered by ticket compared `manifest.ticket` to
  `"<module>#<id>"` while every real capture on disk held `VEZ05513`, typed by
  hand with no separator. String equality would have turned a 168-item panel into
  a permanently empty one, and every fixture-based test would still have passed.
  So: **load one real record and print what the field actually contains before
  you write the comparison**, and compare on a normalised key (case, separators,
  leading zeros) rather than on the raw string.
- **A filter argument that cannot be parsed returns NOTHING, never everything.**
  Falling back to the unfiltered list when `?x=` is missing, empty or malformed
  is how a "show me this one thing" request answers with the whole store — and
  it is invisible, because the page renders perfectly. Distinguish "no filter
  asked for" (list all) from "a filter was asked for and it matches nothing"
  (empty state); they are different inputs and must not share a branch.
- **A row that cannot be acted on does not belong in a list whose purpose is
  acting on it.** Hide it — never delete the underlying record to achieve that.
  Measured: capture runs with no ticket attached were listed in a per-ticket
  approval gallery as "bez tiketa", where approving them was impossible; the fix
  is to stop listing them while leaving every file on disk and still servable.

## Live-polling a long list

A list that refreshes on a timer must not re-fetch every row's *expensive*
per-row state every tick — with N rows that is N units of work every few seconds,
and it only gets worse as N grows. Poll narrow, not wide.

- **Poll only the rows that matter: the ones the user marked to watch
  (pinned/starred) ∪ the currently selected/active row.** The server returns full
  state for those and cheap base fields (id, name, flags) for the rest. On the
  wire this is `?watch=<ids>`; a bare tick must not run the expensive query on
  everything. Measured need: 25 repos polled every 4 s → poll only the 1–2 the
  user is watching.
- **The watch set is client-owned and persisted** (e.g. `localStorage`, a set of
  stable ids) so it survives reload. If the server also reports a "pinned/watched"
  flag, it is *informational* — it must not override the local set, or the two
  sources of truth drift.
- **Do ONE full fetch on screen/tab open** (all rows *with* state) for a complete
  initial picture; the interval uses the cheap watched-only fetch thereafter.
- **Merge each poll into the last full snapshot BY id, and KEEP the last-known
  state for any row the poll did not refresh** — never blank it. Detect "no fresh
  state in this row" by the *absence* of the state field (`row.status===undefined`),
  not by a truthiness test — a legitimately-zero/empty value (`ahead:0`, clean)
  fails a truthy test and would wrongly read as "missing".
- **The watch toggle lives on the row but must not select/open it** — the star's
  click stops propagation so pinning never navigates.
- **Make "live" legible:** a one-line hint naming which rows refresh, and pinned/
  active rows visibly live (filled star / glow) vs the dim idle toggle.
- Verify by driving the real poll (not the wall-clock timer — capture the interval
  callback and fire it): assert the request carries only the watched+selected ids,
  **not all N**; pin a row and assert it joins the next request; an unwatched row
  keeps its state across a poll; pins survive a reload.

## Za testera i za ekstenziju

Isto pravilo, iz ugla onog ko prijavljuje. Blok ispod je izvor za
`BugReporter/src/skills/screen-list.md` (generiše `scripts/brain/extension_skills.py`);
menja se OVDE, nikad u kopiji.

<!-- ebr:skill id="screen-list" name="Lista — šta svaka lista mora" kind="list" scope="compose" -->
Lista (tabela) mora da ima: naizmenično obojene redove, zaglavlje kolona, filter ili pretragu čije stanje ostaje u URL-u (osvežavanje ne briše filter), paginaciju ili "učitaj još" sa ukupnim brojem, prazno stanje sa porukom, i DRUGU poruku kad filter ne nađe ništa (sa načinom da se filter ukloni). Brojevi poravnati desno, isti broj decimala u koloni.
Kako da klasifikuješ:
- Izvoz, uvoz, kolona ili filter koji nikad nije postojao → "change_request", ne bug.
- Filter sa neispravnim ili praznim upitom vraća SVE umesto ništa → bug (prikazuje što ne treba).
- Ukupan zbir ili broj se ne slaže sa prikazanim redovima → bug, prioritet iznad Minor.
- Tabela izlazi van kartice ili ekrana, ili se poslednji red (njegov meni/izbor) seče na dnu → bug prikaza; traži širinu prozora i broj kolona.
- "Učitaj još" ne radi, a lista je kraća od jedne strane → bug: dugme uopšte ne sme da se prikaže.
- Red na kome ne može ništa da se uradi, a prikazan je u listi čija je svrha ta akcija → bug.
- Sortiranje: prazne vrednosti idu na dno u OBA smera; ako u jednom smeru idu na vrh → bug.
- Filter-čipovi izlaze iz kartice u jednom redu → bug prikaza, poznata klasa.
<!-- /ebr:skill -->
