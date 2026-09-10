# The rule everything else follows, and the crops

Extends `visual-diff`. Read BEFORE producing any pair. This is what the two pictures are allowed to claim, and how a changed REGION becomes a crop.

Nothing in the pair claims what changed; whether a screen changed at all is decided structurally, from the DOM.

## The rule that everything else follows

**The pair is PRE + POSLE + every changed region of the screen, enlarged + the
name of the screen. It claims nothing.**

The engine shows you WHERE TO LOOK. It never says what changed: no `novo:`
prefix, no percentage, no token in the caption, no rectangle on the full
pictures. The customer looks at the two pictures and judges — that is the whole
difference between a crop and a verdict.

This replaced a whole machine that pointed at things. `affected.py` read the
added lines of a hunk, pulled out the `id` / class / `{% trans %}` string,
`locate.py` turned it into a rectangle and `annotate.py` drew it with a caption
naming the element. Every stage worked, and the ticket that shipped said:

> `novo: Novi korisnik: analytics analitika: "location_city"`

`location_city` is a material-icon name. It genuinely came out of the diff — and
to the person reading the ticket it is noise wearing the authority of a
measurement. Ranking anchors better does not fix that, because **the customer is
not owed the engine's evidence about itself.** (Operator, 2026-08-21: "ne
prikazuju poredjenje sta je stvarno uradjeno vec ikonice i nebuloze.")

So the `novo`/`uklonjeno` label, the marked full picture and `--rephrase` are
gone and are not coming back. What DID come back, in a different shape, is the
zoom — because "here is the segment, enlarged" is not a claim (operator, next
day: "nije napravio poredjenje sta je izmenjeno uvecano ... ne zoom na ikonicu
vec taj segment ukviren i uvecan").

### The crops — one per changed REGION

`locate.py` resolves **every** hunk anchor, and every match of it, to **two**
rectangles each: the element the diff named, and **the nearest ancestor that
reads as a visual block** — the card, the list item, the form group, the table
row. The block is what gets cut. A change usually touches several places on one
screen (one commit added four fields to a form), so a pair carries a LIST of
regions, ordered top-to-bottom in page coordinates — reading order, not anchor
rank.

**Only anchors that name ONE element decide a region**: an `id`, a Django form
field, a string the diff added, a CSS selector (`SPECIFIC_ANCHOR_KINDS`). A bare
`class` is on every element that carries it — `form-label` is on every field of a
form, `hr-info-row` on every row of a card — so it says WHERE to look but never
WHICH element changed. **There is no fallback to classes.** When nothing specific
resolves, the pair is two full pictures and `regions: []`: a crop of an untouched
element is worse than no crop, and the operator has said so twice.

**A `{% trans %}` string renders as its TRANSLATION, and that broke everything
else** (`catalog.py`, 2026-08-21). The anchor a hunk yields is the MSGID —
`Postal code` — and the page says `Poštanski broj`, so the one specific anchor on
that row could never match. What was left were its classes, and the engine
outlined six untouched rows. It is also why icons kept getting boxed: a
material-icon ligature (`location_city`, `precision_manufacturing`) is the only
"text" in a translated template that gettext does not touch, so it was the only
text anchor that ever resolved. **Every project this brain serves is translated,
so this is the normal path, not an edge case.**

So an anchor carries `alternates` — every rendered form of it, read from the
repo's own catalogues (`<repo>/locale/<lang>/LC_MESSAGES/` and
`<repo>/<app>/locale/…`, via stdlib `gettext`; a `.po` NEWER than its `.mo` wins,
because a string translated today and not yet compiled is exactly the string
somebody is editing templates for). The matcher tries the msgid first, then each
translation; the first that hits anything decides. A repo with no catalogue loses
nothing — the anchor is just the msgid, as before.

**`{{ form.<name> }}` is an anchor** (`affected._RE_FORM_FIELD`). A Django form
field is rendered by the FORM, so the hunk that adds one carries nothing but
`form-label` and `form-text`; the template variable is the only thing in it that
names the element, and Django writes the element out as
`name="<name>" id="id_<name>"`. Without this a form change can only be pointed at
as "somewhere in this form".

**Regions merge before they are cut** (`annotate.merge_regions`,
`MERGE_MIN_OVERLAP`). A field, its label and its help text are three anchors and
one thing to look at. Two rectangles that overlap by 60% of the smaller become
one, transitively, and the outlines of everything merged travel with it. The
number comes off the run: two sidebar items 36px apart share 86% of the smaller
cut (one crop), two form field groups 140px apart share 46% (two crops, which is
what the operator asked to see).

**At most `MAX_REGIONS` (4) regions per pair reach the customer**, and
`regions_found` records how many there were. Each region is two more images in
the comment on top of the two full pictures; four is already ten images for one
screen, and a change that touches more places than that is one the full pictures
show better than any set of crops. A silent truncation would read as "that was
all of it".

* **What counts as a block** is decided twice over: what the markup *says* it is
  (`section, article, fieldset, details, li, tr, .card, .list-group-item,
  .form-group, .alert, .modal-content, .tab-pane, …`) and what it *looks* like
  (a visible border, a background of its own, a shadow) — plus one structural
  test that earns its place on every Bootstrap project: **a container holding a
  `label` and its `input/select/textarea` is a form field.** Bootstrap writes
  that as `<div class="mb-3">`: no border, no background, no `.form-group`, and
  it is exactly the segment a customer needs to see.
* **The walk stops when the candidate covers more than 55% of the viewport**
  (`MAX_BLOCK_AREA_SHARE`) — a crop that is most of the page is the same as no
  crop.
* **The region has a floor** (`MIN_REGION_W/H`, 520×260). A `<label>` is 206×24;
  cut with a fixed margin and enlarged to fill a comment it is a giant word and
  half a select box. That is not a hypothetical — it is what the first run of
  this produced on `locations:location_update`.
* **Each side is cut where ITS OWN content is** — never at the same page
  coordinates. This engine mostly photographs insertions, and everything below an
  insertion moves down by hundreds of pixels: cutting both halves at one
  rectangle produced a pair showing the "Nivo" field on one side and
  "Šifra"/"Region" on the other, two different parts of one form side by side.
  The same anchor is looked up on the other shot and the rectangle FOLLOWS THE
  BLOCK to wherever it sits there (`annotate.shift_rect`), keeping its size so
  the two halves still compare.
* **The BEFORE pass measures every anchor it can read, not just hunk ones.**
  There is no hunk at `before` time — the edit is not written yet — so asking for
  diff anchors there measures NOTHING, and every region on the after side then
  looks new. That is how a field which had merely moved got its before half cut
  at the same coordinates. Measuring is not drawing: the after pass still crops
  hunk anchors only.
* **Three cases, and the third may not be faked.** The anchor is on both sides →
  the region moved, each side is cut around its own copy. The anchor is on one
  side only and the other side WAS measured → the element is new (or gone), and
  the other side is cut at the same coordinates, which is exactly the place it
  was inserted into. **The other side was never measured** (a before shot from an
  older engine, a picture adopted from the baseline) → "moved" and "new" cannot
  be told apart, so there is no honest before: the region ships with
  `before.crop: null` and the HUD renders the gap.
* **A region whose two cuts are identical is never written.** That is what makes
  it safe to resolve every match of an anchor. Identical, not "similar" — a phash
  is far too coarse for a crop, where a whole word added to a header moves it by
  two bits.
* **The outline inside the crop is the BLOCK**, thin, blue and haloed — enough to
  find, not a red box screaming a claim — and only on the side the region was
  measured on. Nothing is drawn on the other side, because we do not know where
  the element is there.
* **No region, no crop.** The pair is then the two full pictures, `regions: []`,
  exactly as it was, and nothing is invented.
* A run with `--no-baseline` gets no crop either: there is no second side to
  compare it against.

`annotate.py` is otherwise still only the caption (page title, plus the state
name when the state is not the base one — both facts the engine had before it
took the shot) and the baseline thumbnail.

**`affected.py` keeps its anchors**, because they answer a different question:
which PAGES does a changed stylesheet or script reach (`_pages_using_token`).
That is page selection, not evidence for a customer, and it stays.

**What still decides anything is `compare.sweep_changed`** — DOM signature +
block geometry — and it decides exactly one thing: whether this screen changed
enough to be worth including at all.
