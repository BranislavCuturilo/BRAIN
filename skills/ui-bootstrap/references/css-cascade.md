# CSS that silently does nothing

Extends `ui-bootstrap`. Read when a rule you wrote has no visible effect, before adding any rule to a shared stylesheet, and before reusing a class name you found somewhere else.

Every failure here reports SUCCESS: the rule is in the file, the grep finds it, the review passes, and the browser ignores it. That is why they survive for months and come back as a tuning request rather than a bug.

**Two unrelated things must never share a class name — the later rule wins
silently, and the symptom lands on the OTHER element.** A rename that reuses a
name already in the stylesheet is the usual way in: the new element looks right
(it got the rules it asked for) while an old one, elsewhere on the page, quietly
inherits properties nobody wrote for it.

> A tool's gallery defined `.tksh-bar` twice — a 5px capture-progress meter
> inside each list header, and, three hundred lines later, a sticky bottom
> strip. The strip rendered fine. Every progress meter turned into a strip glued
> to the bottom edge of the dialog: a clipped slab with no readable text, on an
> element nobody had touched. It read as a layout problem for as long as nobody
> grepped the class name.

Before adding a rule, grep the selector. Structural properties are what carry —
`position`, `display`, `overflow`, `top/bottom` — so the damage is layout, not
colour, and it appears far from the edit. A cheap regression pin is worth it
where it bit: assert the class that must stay simple does not carry the
structural property (`.tksh-bar` is never `position:sticky`).

**A global `!important` typography rule beats anything you write, including
inline styles.** Legacy base stylesheets often pin most elements at a fixed size
with `!important`. An inline `style="font-size:40px"` *loses* to it, silently, so
a size change appears to have no effect and the natural next move — making the
change bigger — also does nothing. A deliberate size override must itself be
`!important`. Text that should follow the global scale should carry no rule at
all, so it also follows any per-user font-scale feature.

**A class you reuse may be scoped to somebody else's container.** Copying markup
from a neighbouring panel copies its class names, and design-system stylesheets
routinely scope rules to an id — `#tixmodal .tix-when { … }`. Pasted into a
different modal that class matches **nothing**: no error, no warning, just text
at the wrong size and colour that everyone reads past. **Before reusing a class,
grep its selector and check the prefix.** Then decide deliberately: add your
container to the existing selector list when the rule is the same *knowledge*
(one house caret, one house timestamp), or write a scoped variant when it is
not. Never widen a selector to something unscoped to make it "just work" — that
is how a `.nav-link` colour ends up in a footer. Measured: a shots gallery
rendered `.tix-when` for two rounds with no styling at all, because the rule
lived under `#tixmodal` and the gallery is `#tkanmodal`.

**A SHORTHAND overwrites the longhand you wrote, and it is invisible to the
grep you would run.** Design-system stylesheets set `padding`, `background` and
`font` as shorthands; a page rule setting `padding-left`, `background-color` or
`font-size` loses to any of them that wins on specificity — and searching the
stylesheet for `padding-left` finds nothing, so the override looks unopposed.
The failure mode is a rule that reads as obviously correct and does nothing,
which is why it survives review and can sit in a shipped screen for months.
Measured: a tree view indented itself with `padding-left: calc(depth * step)`
against a design-system `td { padding: 10px 12px }` one class-level higher —
every level rendered at the same x from the day it shipped, and the customer
eventually reported it as "please indent this more", which reads as a tuning
request and is not one. **When a value "does nothing", look for the shorthand
before you assume the value is wrong** — and prefer out-specifying it to
`!important`, which the next override then has to fight again.

**`:empty` does not match an element that contains whitespace.** A wrapper meant
to vanish when its loop produced nothing — `.strip:empty { display: none }` — is
a rule that never fires if the template pretty-printed the loop onto its own
line, because the newline and the indentation are a text node. The element then
keeps its margin and leaves a gap nobody can explain from reading the CSS, which
looks like a spacing bug in the neighbour. Put the loop on ONE line inside the
wrapper, and if a script also fills that wrapper, have the script remove it when
it appended nothing — one `appendChild` is all it takes to stop being empty
either way. Measured: a counts strip reported `display: grid` with zero tiles.


**A background you measure straight after a state change is the value the
transition STARTED from, not the one it is going to.** Design systems commonly
put `transition: background` on a table row but not on its cells, so one frame
after a JavaScript toggle flips a `data-` attribute the row still reports the
old colour while the cell already reports the new one — which reads exactly
like the sticky-last-cell mismatch you were looking for, and is not. The
pointer makes it worse: whatever you just clicked is now hovered, so a `:hover`
rule is in the measurement too. **Move the pointer off the element and wait out
the transition before reading `getComputedStyle`**, then assert row and cell
are equal. Measured: a row read `#FFEBEE` and its last cell `#F0F7FB` at the
moment of the click, and both read the same zebra token 400 ms later with the
mouse parked at (5,5).
