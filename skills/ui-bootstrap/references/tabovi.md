# Tabovi — tab strips and section switchers

Extends `ui-bootstrap`.

## The recurring bug: white text on a white card

Tab labels are `.nav-link` elements — **the same class the dark top navbar uses.**
So a navbar rule written unscoped:

```css
.nav-link { color: #fff !important; }     /* WRONG — leaks everywhere */
```

repaints every tab label white. On a white card the inactive tabs become
invisible; they only appear on hover or when active. It looks like the tabs are
missing rather than mis-coloured, so the search usually starts in the wrong place.

**Rules:**

1. **Scope navbar link colours to the navbar** — `.navbar .nav-link { … }`. An
   unscoped `.nav-link` colour, especially with `!important`, is *the* bug.
2. **Never re-colour a bare `.nav-link` for a tab.** Target `.nav-tabs .nav-link`
   or a page-scoped wrapper.
3. **Check the INACTIVE tab, not the active one.** The active tab picks up the
   primary colour and always looks fine; the bug hides entirely in the inactive
   label. Read the inactive labels on a rendered page — do not infer them.

The design system normally styles tabs correctly on its own: muted for inactive,
primary plus an underline for active. If tabs look wrong, something else is
overriding `.nav-link` globally — find that rule rather than adding another.

## Markup

Use the framework's standard structure so the existing overrides apply:

```html
<ul class="nav nav-tabs" role="tablist">
  <li class="nav-item">
    <button class="nav-link active" data-bs-toggle="tab"
            data-bs-target="#tab-one" type="button">One</button>
  </li>
  …
</ul>
<div class="tab-content">
  <div class="tab-pane fade show active" id="tab-one">…</div>
</div>
```

Colours from tokens — muted for inactive, primary for active — never hex.

## Two states of the strip itself

**Load the screen with NO query string and count the active tabs.** A strip
whose active key comes from the URL will show none as active whenever the
default value is not one of the tabs it renders — the classic case is a
producer that defaults to a virtual "All" while the list contains only the real
areas. Six tabs, none highlighted, reads as broken. Either render the virtual
value as a real first tab, or fall back to the first tab when nothing is
selected; decide which, and check the default URL, not the one you clicked to.

**A tab that is present but not built yet must not be signalled by a paler
colour alone.** Dimming it far enough to read as "inactive" takes the label
under the contrast a label needs — a typical "dim" token measures about 2.6:1
on white, where 4.5:1 is the floor. Keep the ordinary muted colour and carry
the meaning in something non-colour: a small badge on the tab, and a dashed
rather than solid underline. The body it opens should look deliberately
unfinished too — a dashed frame and a sentence saying what will land there —
so it is never mistaken for an empty result, whose frame is solid.

**And whatever marks that KIND of tab must not out-specify what marks its
STATE.** A rule like `.tabs .nav-link.soon { color: …; border-bottom: … }`
reaches the same specificity as the framework's `.nav-link.active` and, loading
later, wins — so clicking that tab leaves it muted with no underline and only a
bolder weight to say it is selected. Ship the `.soon.active` pair in the same
rule block, and READ BOTH BACK: the active placeholder must come out with the
same colour and underline as any other active tab (measured, e.g. primary
colour + 2px solid, against muted + 1px dashed for its inactive self).

## Behaviour

- **The active tab survives a reload.** Put it in the URL (`?tab=history`) and
  restore it, or the user loses their place on every save.
- **A tab that submits a form must not lose the other tabs' input.** Either one
  form spans all tabs, or each tab is its own form with its own save — decide
  deliberately; the accidental middle ground silently discards data.
- **Do not lazy-load a tab's content without a loading state.** An empty pane for
  half a second reads as an empty section.
- Tabs are for sibling views of one record, not for a workflow. Sequential steps
  are a wizard with next/back and visible progress.
- More than about six tabs means the page is doing too much; split it.
