# Containers that re-base or resize their children

Extends `ui-bootstrap`. Read when something is positioned or sized wrongly and the markup looks right — a fixed overlay off-centre, a column that ignores the viewport, a tile twice the height of its neighbour.

These are all the same shape: a property on the PARENT changes what the child's own units mean. Nothing in the child's markup reads as conditional, so the child is where everyone looks.

**`filter`, `transform` and `backdrop-filter` turn the element into the
containing block for every `position:fixed` descendant.** A fixed child then
positions relative to *that ancestor's* box, not the viewport — invisible in
markup, because nothing about `position:fixed` reads as "conditional" at a
glance. Measured: `filter:blur()` on a zero-height wrapper whose only children
were `position:fixed` (`inset:0`/`top:50%`) collapsed them to the top of the
page instead of staying centred. Fix: put the filter/effect on the element that
is *itself* the full-viewport fixed layer (`inset:0`), so re-basing changes
nothing. Never filter/transform a zero-height wrapper that contains fixed
children.

**A placeholder with a `min-height` sets the height of its whole row.** In a
grid or flex row the default is `stretch`, so the one designed "nema slike" box
next to a real picture makes that picture's tile grow to the placeholder's
height — measured: a 90px bordered box around a 45px image, which reads as a
second broken tile, not as one image. Use `align-items:start` on rows of
side-by-side media so every tile is its own height with tops aligned. Check it by
**measuring, not looking**: for each cell log
`cell.getBoundingClientRect().height` next to `img.getBoundingClientRect()
.height` — equal-ish is right, double is the stretch.

**A grid row inside a flex container sizes itself to its CONTENT, so its
percentage columns stop tracking the viewport.** A `.row` placed as a child of a
flex parent is a flex item, and a flex item is shrink-to-fit — the 16.6% of a
"2 of 12" column then resolves against whatever the content happens to need, not
against the bar it sits in. The tell is that every column measures the SAME width
at 1920px and at 992px. It stays invisible for as long as the content is wide:
one screen only looked right because its `<select>` options carried long text,
and collapsed to 97px columns the day the control was replaced with something
that truncates. Give the row `width: 100%` (or stop nesting a grid in a flex
bar). Measure column widths at two viewport widths before believing a filter bar
is fine.

**A grid column class inside a CSS grid is a percentage of the TRACK, not of
the row.** `col-md-3` dropped into a `display: grid` container does not mean "a
quarter of the bar" — it means a quarter of the one track it landed in, so the
control renders at 25% of a cell and reads as a stub. Grid cells size
themselves; a component reused across a Bootstrap row and a CSS grid must let
the CALLER supply the wrapper class, and supply nothing itself.
