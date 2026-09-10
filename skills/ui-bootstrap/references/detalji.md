# Detalji — single-record detail pages

Extends `ui-bootstrap`.

## Anatomy

A detail page answers, in this order: **what is this, what state is it in, what
can I do with it, what is attached to it.**

1. **Identity header** — the record's name, its key identifiers, and its status
   as a badge. Never make the reader hunt for the status; it changes what every
   other element means.
2. **Actions**, next to the identity, gated by what this user may actually do.
3. **Core fields**, grouped.
4. **Related collections** — attachments, history, children.

## Actions

- **Every state-changing action is a POST form**, not a link. A link that writes
  is a CSRF hole and will fire from any page that can embed a URL
  (`craft-security`).
- **Hide what the user cannot do; do not show it disabled without saying why.**
  A greyed button with no explanation reads as a broken page. Either omit it, or
  show it with the reason.
- **A control gated on an async capability probe (feature flag, key/quota count,
  entitlement) must re-evaluate when the probe RESOLVES, not only when the record
  opens.** If the record renders before the probe returns, decide a default and
  re-render on resolution; the alternative is a button that flashes visible then
  vanishes (or the reverse) on first paint. Fail the default OPEN when the probe
  is merely slow/absent, and CLOSED only on a definite "not allowed" — a failed
  probe should not silently disable a whole feature. Verify with a fixture for
  each branch: probe-allows, probe-denies, probe-absent.
- **Show the action's consequence when it is irreversible** — closing, locking,
  posting, archiving. "This cannot be undone" belongs in the confirmation, not in
  the manual.
- The available actions come from the same state machine the backend enforces
  (`craft-code`). A button the server will refuse is a bug, not a UI nicety.

## Related collections are re-scoped

**The most dangerous part of a detail page.** A parent record groups children
that different people attached — the visible set must be computed with the same
visibility helper the standalone list uses, filtered to this parent. Reaching the
parent does not entitle the viewer to every child, and an item-level reach filter
does not carry status dimensions like archived or draft
(`craft-security` → "an aggregate re-scopes every member").

When the viewer sees only part of a collection, **say so** — "showing 4 of your
accessible records" beats a silently short list that reads as complete. And every
total on the page must be computed over exactly the rows shown.

## Read-only records

A record in a terminal state (closed, locked, signed, posted) shows that
prominently and drops its edit affordances entirely, rather than letting the user
type into fields that will be rejected on submit. If reopening is possible, that
is a distinct, permissioned, logged action — not an edit button.

## Rendering fields the API hands you

- **A structured field — a list, or an object like `{name, email}` — must be
  *formatted*, never string-concatenated into markup.** `''+obj` renders
  `[object Object]`; `''+[obj,obj]` renders `[object Object], [object Object]`.
  Format each element explicitly (`Name <email>`, or the bare part when the other
  is blank), drop the blanks, join, *then* escape. This bites To/Cc/recipient,
  author, tag and any "list of people" field. The trap is that the **diagnostic
  record often carries the scalar shape** (a plain string), so the object case
  ships unseen — check against a record whose field is actually the list-of-objects.

- **When a record has a thin and a rich rendering of the same content, default to
  whichever is primary — measured, not assumed — and never weaken the rich one's
  sandbox to do it.** A marketing email with a 4 KB text part and an 18 KB HTML
  part reads as "cut off" if you show the thin text by default; open the HTML view
  by default (HTML present and clearly larger, e.g. `html.len > text.len*1.3`, or
  text empty), keep the text/HTML toggle, and keep the untrusted-HTML iframe
  exactly as locked as before (no `allow-scripts`, no `allow-same-origin`, CSP
  intact). The default is a display choice; it is not a licence to relax isolation.

- **A collection/attachment strip is verified against a record that HAS items.**
  Empty is a false pass — "no chips rendered" looks identical whether the code is
  correct or missing entirely. Build the fixture with two or more items. Each
  chip carries a human-readable size and a download href **whose every query
  value is individually URL-encoded** — a filename with a space or `&` otherwise
  corrupts the link. This is a same-origin, server-built URL, so the http(s)-only
  `safeUrl()` guard does not apply; encode the values instead.

## Scrolling a reader/detail pane with a huge body

- **A key related collection (attachments) goes directly under the header, not
  after the body.** A long plain-text body or an embedded HTML preview pushes a
  below-the-body strip off-screen, and the reader concludes the record has none.
  Put attachments/actions right under the subject; let the *body* take the
  overflow.
- **Pin the header, the collection and the actions; scroll only the body.** Make
  the pane a flex column that CLIPS (`overflow:hidden`), give the fixed parts
  `flex:none`, and give the body its own region: `flex:1; min-height:0;
  overflow:auto`. Then the attachments stay visible while a 400-line body or a
  tall HTML part scrolls beneath them. `min-height:0` is what lets a flex child
  actually scroll instead of forcing its parent to grow.
- **An embedded preview (iframe / rendered block) is height-bounded and scrolls
  inside its own area — never left to grow unbounded.** Fill the scroll region
  (`height:100%`) with a small `min-height` floor so it stays usable on a short
  pane; an unbounded `min-height` (e.g. 440px) forces a second, nested scroll on a
  short region. An iframe always scrolls its own content, so bounding its *outer*
  box is the whole job.
- **Verify by RENDER, both states.** With a very long body AND with the preview
  on: the top attachments stay visible, everything is reachable by scrolling, and
  there is no horizontal overflow. Gotcha: a clipped-but-scrollable child's
  `getBoundingClientRect()` box can legitimately extend past the pane — assert the
  **scroll region** is contained and the **pane itself never scrolls**
  (`scrollHeight <= clientHeight`), not the child's geometric box.

## Practicalities

- **Deep-linkable.** One URL per record, and a tab or filter state that survives
  a reload (`?tab=history`).
- **Timestamps show both absolute and relative** — "2 days ago" alone is useless
  in an audit context, an ISO date alone is unreadable at a glance.
- **Empty related sections get a message**, not a blank area.
- Long collections paginate or link to a filtered list view rather than rendering
  five hundred rows into a detail page.

## Za testera i za ekstenziju

Isto pravilo, iz ugla onog ko prijavljuje. Blok ispod je izvor za
`BugReporter/src/skills/screen-detail.md` (generiše `scripts/brain/extension_skills.py`);
menja se OVDE, nikad u kopiji.

<!-- ebr:skill id="screen-detail" name="Detalj — šta svaki detalj zapisa mora" kind="detail" scope="compose" -->
Detalj zapisa mora da: prikaže naziv, ključne oznake i STATUS odmah u zaglavlju; prikaže samo akcije koje korisnik sme — sakriveno, ili sa razlogom, nikad sivo bez objašnjenja; povezane stavke (prilozi, istorija, deca) sa porukom kad ih nema; vreme apsolutno I relativno; URL koji se može podeliti (osvežavanje vraća isti tab).
Kako da klasifikuješ:
- Dugme ili akcija koja fali: prvo SESIJA (pravo, flag) i kontekst ekrana (NE DOZVOLJAVA) → "question" ili "limitation"; tek onda bug.
- Zapis u završnom stanju (zatvoren, zaključan, proknjižen) ne dozvoljava izmenu → "limitation", ne bug.
- Sivo dugme bez ikakvog objašnjenja → bug prikaza (mora ili da nestane ili da kaže zašto).
- Dugme koje se pojavi pa nestane pri otvaranju (ili obrnuto) → bug, poznata klasa (provera prava stiže posle prikaza).
- Povezana lista prikazuje stavke koje korisnik ne bi smeo da vidi (tuđe lokacije, tuđi tenant) → bug, prioritet Critical.
- Vidi se samo deo povezanih stavki, bez napomene "prikazano N od M" → bug.
- "[object Object]" ili slično u polju → bug prikaza.
- "Nema priloga", a ima — prilozi ispod dugog teksta van ekrana → bug rasporeda; traži sliku celog ekrana.
<!-- /ebr:skill -->
