# What counts as a screen, and which ones to shoot

Extends `visual-diff`. Read when choosing pages for a run or a sweep.

## A page whose URL is PROSE is not a screen

**What broke** — a map records `"url": "(template, nije ruta)"` for a shared
template that has no route of its own. It is non-empty and carries no `<param>`,
so it passed every fetchability test, and every run that touched the base layout
spent a page load navigating to it and logged `Cannot navigate to invalid URL`.
On BOTH sides. It looked like the capture was broken.

**The rule** — `_entry` now marks a URL that is neither a rooted path nor an
absolute http(s) address as `not_a_route`, the same treatment `_template()`
already gave a template recorded as prose: kept in the inventory, marked, never
quietly dropped and never navigated to.

## An asymmetric page cap does not capture less — it makes a FALSE CLAIM

**What broke** — `max_pages: 45` while a change to `base.html` reached ~60
screens. The BEFORE pass stopped at 45; the AFTER pass produced pairs for all of
them. The six pages past the cap therefore had an after and no before, which the
gallery renders as **"Nova stranica"** — telling the customer six pages had been
created that had existed all along.

**Why** — the cap is applied per pass, so it truncates the two sides at
different points, and "no before" is indistinguishable from "new screen".

**The rule** — when a change reaches the shared layout, size `max_pages` to the
whole affected set before the BEFORE pass, and check the pair list for one-sided
rows before sending. A one-sided pair is either a real new screen or a capture
gap, and only you can tell which.

## The sample row must still exist at AFTER time — the engine will not say so

**What broke** — a run's `before` pass resolved `locations:location_runbook_edit`
to a concrete URL with a sample pk. Between the two passes the fixture behind
that pk was deleted and re-seeded, so it came back with a NEW pk. The `after`
pass re-used the URL the run had already resolved, photographed a Django 404
page, and reported `2 pair(s), 2 shown` — success. Only opening the PNG showed
it. Re-pointing the config at the new pk did not help either: the URL is fixed
in the run, not re-read from the config.

**Why** — a captured page is only checked for HTTP reachability, and a 404 page
is a 200-shaped, perfectly capturable screen. Nothing compares the shot against
"is this the screen I asked for".

**The rule** — the row a sample pk names must survive from `before` to `after`.
If a fixture has to be rebuilt between the passes, **recreate it at the same
pk** (`Model.objects.create(pk=…)`) rather than letting the sequence hand out a
new one. And **always open the AFTER png before believing the run**: the engine's
own summary cannot tell a screen from an error page.

**Where it bit** — a seeded `test`-tenant location, rebuilt to move it off a
colliding unique column mid-ticket.

## What the sweep is for

The dev database **is** production, so counters, timestamps and row counts move
on their own. A pixel sweep over 232 pages would flag everything and be ignored
by the third day. So the sweep compares:

* the **DOM signature** — tag + sorted classes, no text, no numbers;
* the **geometry** of the main blocks — position and size, 2px tolerance.

Same tree with different numbers is not a change. A block that moved is. The
`ignore_selectors` in the repo config are excluded from both — and never from
the customer's PNG, which always shows the real page.

This is the **same** comparison that decides whether a customer pair is kept.
One question, one implementation: a sweep that ignored data noise while the pair
loop reported it was two answers to "did this screen change", and the operator
got the wrong one.

## A new segment is invisible to the engine until the MAP and the SAMPLE IDS both know it

**What broke** — a whole new segment (29 by-pk pages) was finished, deployed and
about to be photographed. `before --files <its template>` answered
`0 page(s) known` and asked which screen the change shows on. The template WAS
the page (`template_name` pointed straight at it), and the page WAS a real
route.

**Why** — two separate gates, and failing either looks identical:
1. the page inventory comes from the repo's page map, and the new app simply had
   no file there — the map is per-app and nobody generates it automatically;
2. every URL carrying a parameter needs an entry in `sample_ids_by_page`, or the
   page is dropped as `unresolved_params` — the same silent skip the repo config
   already warns about for an EMPTY map.

**The rule** — before shooting a new segment: add its `<app>.json` to the page
map and rebuild, then add one `sample_ids_by_page` entry per parameterised page.
Both, in that order. A `0 page(s) known` on a file you can see rendering is this,
not a broken engine.

## A screenshot proves what the SERVER is running, not what the working tree says

**What broke** — a Python fix (a registry entry set to `None` so a dead link
would stop rendering) plus a template guard. The `after` shot came back showing
the dead link still there, and the engine happily reported `1 pair(s), 1 shown`.

**Why** — the dev server had been started earlier with `--noreload`. The
TEMPLATE edit was picked up (templates are re-read per request), the PYTHON edit
was not. So the page rendered a new template over an old registry: a state that
existed in neither the before nor the after of the actual change.

**The rule** — restart the server after ANY .py edit before capturing, and treat
`--noreload` as "this process is frozen at the code it started with". More
generally: the pair is a picture of a running process, so anything that makes
that process disagree with the tree (a stale server, a stale container, a
different settings module) silently produces a truthful picture of the wrong
thing. This is why the doctrine says to OPEN the after png — the summary line
was correct and the image was not.

## One url_name serving MANY screens — the inventory records one, and shoots the wrong one

**What broke** — a declarative CRUD served seven different code lists through a
single route, `/<segment>/config/<list>/new/`, and therefore a single
`url_name`. The page inventory keys on `namespace:url_name`, so it held ONE
entry with ONE concrete url — the list that happened to be written down. A
ticket that changed a DIFFERENT list's form produced a pair of the recorded
list instead. The run reported `2 pair(s), 2 shown`, the pictures were sharp,
the engine was right, and the screen was the wrong one. **The operator caught
it, not the process** (DEMO#01621, 2026-09-07).

**Why it survives review** — every automated signal is green. The pair is a
real before/after of a real screen that really did change (the two forms share
a template, so the recorded one changes too). Nothing anywhere compares "the
screen I shot" against "the screen the ticket is about", because nothing knows
what the ticket is about.

**The rule** — when the changed template is reached through a route with a
segment that SELECTS which screen renders (`<list>`, `<kind>`, `<type>`,
`<tab>`), the inventory needs one entry per value you care about, and the
ticket's value is the one that must be there. Check the URL in the manifest
against the URL in the ticket before sending anything:

```bash
python -c "import json,io;m=json.load(io.open(r'<run>/manifest.json',encoding='utf-8'));[print(p.get('page_id'),'->',p.get('url')) for p in m['pairs'] if not p.get('drop')]"
```

The entry's `id` is the inventory KEY and does not have to equal `url_name` —
`_entry()` reads `page_id or id` — so two entries may share one `url_name` and
differ in url. Give the extra one a name that says which value it pins.

**Generalises past the url:** the same blind spot exists wherever ONE template
serves several screens by a parameter the inventory does not vary — a shared
form body, a generic detail view, a wizard step. If the diff can only ever
shoot one of them, the others are unphotographable and nobody finds out until
a customer is sent the wrong picture.
