# States — the first view is not enough

Extends `visual-diff`. Read when the change is only visible after a click, a filter, an expand, or in an empty/error state.

## States: the first view is not enough — and one picture per LOOK

A page can look identical and have a changed modal. After the base shot, every
`data-bs-toggle="modal|dropdown|tab|offcanvas|collapse"`, `details > summary`
and `.modal-trigger` is clicked **in isolation, on a fresh page load**, and the
state is kept only if something actually opened. `states.manual` in the config
adds named states with explicit steps.

Two filters then decide what the customer is offered, and they answer different
questions. **They are not interchangeable and neither can do the other's job.**

### 1. Is this a state of THIS SCREEN, or of the application?

**A trigger inside the site's chrome does not produce a state at all**
(`capture.CHROME_LANDMARKS` / `CONTENT_LANDMARKS`, `_drop_site_chrome`). A menu
in the navbar is the same menu on all 232 screens: photographing it says nothing
about the change, and it was costing a page load, a picture and a pair on every
one of them — `dashboard:location_quality` shipped six pictures of one screen
with a different application menu open in each.

It is keyed on **HTML landmarks, not on anyone's class names**: `nav`, `header`,
`aside`, `footer` and their ARIA roles are the site's furniture, `main` /
`[role=main]` is the screen. The nearest landmark ancestor decides, with one
refinement that makes the rule safe to have: **a chrome landmark that is itself
inside the content is local navigation** — a `<nav class="nav-tabs">` inside
`<main>` is this page's tab strip and keeps producing states. A trigger under no
landmark at all counts as the screen's own, so **the rule may only ever remove a
state it can positively prove is chrome**, and a layout with no landmarks
behaves exactly as it did before.

Every dropped trigger is recorded in `skipped` with `chrome: true`, its
selector, and the landmark that decided it. That row is the only way anybody
finds out when the rule misjudges — read it first when a state you expected is
missing.

**Where it misjudges, and what it costs:** a page whose only interesting action
lives in the site navbar (a global "Dodaj" button in the header) loses that
state, and so does an app that marks its content with `<div id="content">` while
wrapping a page-level toolbar in `<header>`. **`states.manual` is the way back
in** — a named state with explicit steps is never filtered, because it is the
operator's decision rather than a guess about a layout. This risk is accepted
(operator, 2026-08-21).

### A change that is only visible AFTER A CLICK must be given a manual state

**Operator rule, 2026-08-29, and it is on the author, not on the engine:** when
what you changed lives inside a dropdown, a modal, a tab or any other thing that
does not exist until somebody opens it, you MUST add a `states.manual` entry
that opens it. Otherwise the pair is two pictures of a closed control — visually
identical — and the customer is looking at a screen whose change is not in the
frame.

> *"kada izmenis nesto sto se ne prikazuje default na stranici vec nakon sto se
> klikne kao sto je dropdown ili slicno ... ovako ima 2 iste stranice koje imaju
> vizualnu izmenu ali se ne vidi jer nije otvoren dropdown"*

Do not rely on auto-discovery for this. It finds the trigger, but the picture it
takes then had to survive the same-picture collapse below, and an open menu over
a page sits a handful of phash bits from the base shot. **Manual states are now
exempt from that collapse** (`_keep_or_collapse(..., always_keep=True)`) —
precisely because a named state is the operator's decision and a perceptual hash
is not entitled to overrule it. Auto-discovered states are still subject to it.

Write the selector so it matches **both generations of the markup**. The before
side runs the OLD code: an id or a class the change introduced does not exist
there, so the step fails and the pair comes back one-sided. Key off something
stable — `select[name="status"] + div.dropdown > button` matched a widget both
before and after a rewrite where the new markup had added an id.

And know when a before is genuinely impossible: a **native `<select>`** has its
option list drawn by the OS, so it never appears in a screenshot at all. A
control that was a native select before and a JS menu after has no like-for-like
open pair; say so rather than shipping a one-sided picture that reads as new.

### 2. Is this a picture I already have?

**A state that opened and produced the same PICTURE as a state already kept for
that page is collapsed** (`compare.same_picture`, a phash distance of at most 7).
Never silently: the manifest's `skipped` carries the state, the state it repeats
(`same_as`) and the distance measured, and an unpaired row on the other side
quotes that reason.

**The threshold is calibrated, and its margin is one bit** — see
`compare.SAME_PICTURE_MAX_HAMMING`, which carries the measurements. A phash
compares DCT coefficients against their own median, so it is blind to a uniform
dimming of the page: an open modal *with* its backdrop is 8 bits from the base
shot while a navbar dropdown can be 24. **No threshold separates "a menu opened
over the page" from "this is a different tab"** — that is exactly why filter 1
exists and asks a structural question instead.

Two numbers govern the cost and they are not the same number:
`states.max_per_page` caps the PICTURES of a page, `STATE_ATTEMPT_FACTOR` (×2)
caps the page loads spent looking for them. Letting a collapsed state spend the
picture budget cost a real one immediately — two navbar dropdowns collapsed into
the base shot and the run never reached the role form's "lokacije" tab.

**Read-only, enforced:** every non-GET/HEAD request is aborted by a route guard
in the browser context. No screenshot is worth a write against the production
schema.

**Developer overlays are hidden before the shot** (`capture.DEV_OVERLAY_SELECTORS`):
a Django debug toolbar covered the right ~15% of every capture, on both sides of
every pair. Version 4 renders it behind a declarative SHADOW ROOT, so only the
host (`#djDebugRoot`) can be hidden from a page stylesheet — a rule for
`#djDebug` reaches nothing at all. The app's own navbar and menus STAY: they are
part of the screen. A project with no overlay loses nothing, because a rule that
matches no element is a no-op.
