# Toolbars, and bars that are supposed to stick

Extends `ui-bootstrap`. Read before adding any control to an existing header, filter bar or toolbar, and before reporting a sticky bar as done.

A control can be present in the DOM, correctly styled, and still not exist for the user. Every rule here is checked by MEASURING, not by looking at the diff.

**A control added to an existing toolbar is not visible just because it sits next
to the right sibling in the DOM.** Header/toolbar rows are routinely single-line
with `flex-wrap:nowrap` + `overflow-x:auto` — the nowrap is deliberate, because
letting such a bar wrap to two lines has broken layouts before. Adding one button
pushes the right-hand end past the edge, where it is reachable only by horizontal
scrolling, and a scrolling bar shows no scrollbar hint: the control simply does
not exist for the user. Measured on a real bar: it already overflowed by 173px at
1440px wide, and a new 54px button landed entirely off-screen while every DOM and
computed-style assertion passed. **Before adding to a toolbar, measure
`el.getBoundingClientRect().right <= bar.clientWidth` at the widths you support**
— and measure the bar's overflow with your control removed too, so you know
whether you caused it or inherited it. Inherited overflow is the bar's own fix,
not a licence to make it worse; either way, never let a NEW primary entry point
be the thing that falls off the end.

**A WRAPPING bar hides the same mistake behind a passing measurement.** When the
bar is a grid row that wraps (`flex-wrap:wrap`, or a Bootstrap `row` of
`col-*`), nothing overflows and `right <= clientWidth` passes for every control
— but the row silently reflows, and the trailing controls (usually the submit
button) drop to a second line. Adding one `col-lg-2` filter to a row whose
`col-lg-*` already sum to 12 does exactly this. **So measure `top`, not only
`right`: capture `getBoundingClientRect().top` of every control before and after
your change — a control whose `top` moved is the wrap.** Then decide
deliberately: either keep the units summing to 12, or accept the second line.
Never leave it undecided because the overflow check was green.

**A sticky action bar inside a scrolling panel must be opaque.** A
fade-to-transparent gradient looks right in the state you designed it in (short
list, nothing behind it) and becomes unreadable the moment content scrolls under
it — its own label ends up sitting on top of somebody's screenshot. Give it a
solid background from a token plus a top border or shadow, and **screenshot it
with the panel scrolled, not at the top.**

**Opaque is half of it — sticky only sticks inside a scrolling ANCESTOR.**
`position:sticky` is inert unless some ancestor is the element that actually
scrolls; in a modal that is usually the body wrapper (`overflow-y:auto`), not the
dialog and not the page. So do not report a sticky bar from the CSS diff. Two
measurements, both cheap:

- walk up from the bar to the first ancestor with `overflow-y` auto/scroll AND
  `scrollHeight > clientHeight` — that is the scroller, and it must be an
  ancestor of the bar;
- log the bar's `getBoundingClientRect().top` at `scrollTop` 0, mid and maximum.
  **Three equal numbers is sticking. Three different numbers is a bar that scrolls
  away**, which is exactly what the user is reporting.

Setting `scrollTop` in a headless run does not reliably emit `scroll`; dispatch
it, or the handler you are testing never runs and the control reads as broken.

**A check that the control EXISTS is not a check that it is USABLE.** A
structural smoke test — 200, right element, widget mounted, no JS errors —
passed on all six screens while three controls on one of them were 46px wide.
Width, overflow and clipping do not show up in the DOM; assert the rendered
`getBoundingClientRect().width` against its siblings, or look at the picture.
