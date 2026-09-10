# Images, galleries and their empty states

Extends `ui-bootstrap`. Read when a screen renders pictures from metadata — a gallery, a thumbnail strip, an attachment list, a before/after pair.

A broken-image glyph is indistinguishable from "there is nothing here", so the viewer cannot tell a capture that failed from one that legitimately has no image. Every empty state here has to be DESIGNED and has to say the right thing.

**A `src` the server advertises must be one the server can actually serve.** A
gallery, thumbnail strip or attachment list built from *metadata* — a manifest,
a JSON row, a filename column — shows the browser's broken-image glyph the
moment one file is missing, still being written, or unreadable. A broken glyph
is indistinguishable from "there is nothing here", so the viewer cannot tell a
capture that failed from one that legitimately has no second image. Build the
URL list from a resolve-and-stat pass on the SERVER, omit what does not resolve,
and give the missing slot a **designed placeholder that keeps the layout**
("POSLE — nema slike") rather than an empty box. **Verify by deleting one file
and reloading:** you must see the placeholder, not a broken icon, not a gap.

**And the placeholder must not say "missing" about something that is merely
somewhere else.** A detail view often shows a zoomed crop above the whole
picture. When the producer emits no crop but the whole picture IS there, "nema
slike" sends the user hunting for a file that exists one click below, and they
report the feature as broken. Branch the placeholder text on what the record
actually holds — "nema isečka — cela slika je ispod" vs "nije snimljeno".
Measured: 13 of 13 rows in a real gallery said "nema slike" while every one of
them had its full screenshot in the fold underneath, and the operator's bug
report was "I cannot see a single enlarged picture".

**A "back to the top" control in a grouped list must name where it goes.** When
the panel can hold several groups (one per ticket, per day, per customer), the
top of the panel is somebody else's group, so scrolling to it is the wrong
answer to "take me back to the start of the one I am reading". Target the group
under the scroller's top edge, print that group's own label on the button, and
hide the control when its target's start is already on screen. Check it with
**two groups open**: from the bottom, the label must name the SECOND group, and
the click must land that group's top on the scroller's top edge.
