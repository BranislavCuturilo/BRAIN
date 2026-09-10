---
name: ui-bootstrap
description: >
  Building server-rendered UI on Bootstrap 5 with a design-system layer: the
  rules that keep a new page looking like the rest of the app, and the recurring
  traps that make CSS silently do nothing. Load BEFORE adding or editing any
  page, card, table, form, tab bar or dashboard widget. The references/ files
  extend this one per screen type — read the matching one.
when_to_use: >
  building or editing a template or stylesheet, "this page doesn't look like the
  rest of the app", "the menu is cut off", "my font-size does nothing", adding a
  list, a form, a detail page, a dashboard, a tab strip.
---

# Bootstrap UI craft

Server-rendered Bootstrap 5 with a design-system layer on top: tokens for colour
and spacing, and component classes (`card`, `table`, header/body wrappers) that
already carry the house look.

**The recurring failure is not ugly CSS — it is CSS that silently does nothing**,
or a component used without its required wrapper. Both look like "the page is
just a bit off" and both waste an afternoon.

## Applies to every screen

**Colour comes from tokens, never from a hex literal.** A hex in a template is
invisible to theming, to dark mode and to per-customer branding. The only hex
allowed in code is a documented synthetic sentinel ("no data yet"), marked as
reserved so nobody assigns it as a real value.

**A card has no padding of its own — content goes in the body wrapper.** This is
the single most common "why is everything flattened against the edge". The card
supplies border, radius, shadow and clipping; the header and body wrappers supply
padding. `<div class="card"><div>fields</div></div>` is wrong every time.

**A card clips its contents (`overflow: hidden`), so any menu opened inside one
is cut off at the card edge.** Usually it looks like an empty dropdown appearing
right under the input. This is by design — the clipping is what rounds the
corners — so the menu has to escape it, not fight it. Details and the fix order
in `references/forma.md`. This has shipped as a client-visible bug more than
once; check it whenever a card contains a select, combo or popover.

**Never trust CSS you have not seen render.** Verify with a screenshot or
computed styles. Between template caching, `!important` sledgehammers and
specificity, "I wrote the rule" and "the rule applies" are genuinely different
statements here.

## The token VALUES are not in this skill, and must not be copied here

This file says *use a token, never a hex*. Which tokens exist, what they are
worth, and what each brand overrides lives in the stylesheet — and a second
copy of a palette is the copy that goes stale while still reading as
authoritative.

```bash
python ~/.claude/skills/brain/scripts/design.py <project>/static/css/tokens.css
```

That writes a `DESIGN.md` beside the stylesheet: every token with its value,
the per-tenant overrides, and — the part worth having — the prose already in
the CSS saying *why*. Which colours were chosen because a client asked to see
the difference, why a scale is deliberately clamped, and which globals are
deliberately left alone because overriding them broke the older templates. An
agent that cannot see those "fixes" them.

It is generated, never hand-edited; `--check` exits non-zero when it no longer
matches the stylesheet.

**A new project picks its own token prefix.** `acme-audit` is themed with
`--hr-*` and a `.hr-redesign` wrapper because its design was copied from an
HRIS app; the prefix says nothing about auditing and confuses everyone who
reads it. It stays there — renaming would touch a million lines to buy nothing
— but it is an accident of history, not a convention. Do not carry `hr-` into
the next project, and do not write a tool that looks for it by name.

## Read on demand

Two tables. The first is the screen you are building; the second is the symptom
you are looking at. Most wasted afternoons here start by reading neither.

| Building | Read |
|---|---|
| a list or table of records | `references/lista.md` |
| a form, filter bar, or anything with a dropdown | `references/forma.md` |
| a single-record detail page | `references/detalji.md` |
| a dashboard, metrics, charts | `references/dashboard.md` |
| a tab strip / section switcher | `references/tabovi.md` |
| a landing, marketing or presentation page — NOT a business screen | `references/presentation-pages.md` |

| Symptom, or what you are about to touch | Read |
|---|---|
| a rule you wrote has no effect; reusing a class name; editing a shared stylesheet | `references/css-cascade.md` |
| a fixed overlay off-centre; a column ignoring the viewport; one tile taller than its neighbour | `references/layout-sizing.md` |
| adding a control to a header/toolbar; a bar that should stick | `references/toolbars-sticky.md` |
| a page script by id/class; `{% if %}` around a block; a collapsible; moving markup into a partial | `references/js-hooks.md` |
| a gallery, thumbnail strip, attachment list, before/after pair | `references/media.md` |
| "N of M", "K left to review", a progress bar, rows the producer filtered out | `references/counts.md` |

## Before shipping any screen

- Content is inside the body wrapper, heading inside the header wrapper.
- Any dropdown inside a card escapes the clipping.
- Colours from tokens; no stray hex.
- Any size override is `!important` — and was verified rendered.
- The empty state is designed, not an accidental blank area.
- Any value that can be BOTH absent and empty renders three distinct states,
  in words and not only in colour (`references/forma.md`).
- Anything added to a toolbar was measured on screen at the supported widths —
  `right` on a nowrap bar, `top` on a wrapping one.
- Every image URL was proved servable; one file was deleted and the placeholder
  seen.
- Any sticky bar was screenshotted with content scrolled underneath it, and its
  `top` was logged at three scroll positions — equal, or it is not sticking.
- Every "N of M" / "K left" was counted over the rows actually rendered, and any
  row the producer filtered out is still reachable somewhere.
- Every page variant that HIDES a block was loaded with the console open, not
  only the variant that shows it.
- Every reused class had its selector grepped for an id prefix.
- Every collapsible was screenshotted CLOSED, and a value was typed into one,
  collapsed, and submitted.
- Every progress/status indicator names the artefact it reads, and can render
  "unknown".
- Rows of side-by-side media were measured cell-height against image-height, with
  one side missing.
- Any numbered list was checked with an unrenderable item in the MIDDLE.
- It was looked at in a browser, not only in the diff.

## Za testera i za ekstenziju

Isto pravilo, iz ugla onog ko prijavljuje. Blok ispod je izvor za
`BugReporter/src/skills/screen-any.md` (generiše `scripts/brain/extension_skills.py`);
menja se OVDE, nikad u kopiji.

<!-- ebr:skill id="screen-any" name="Svaki ekran — pre nego što je greška" kind="any" scope="compose" -->
Za svaki ekran, pre nego što nešto proglasiš greškom:
- Dugme ili akcija koja "ne postoji": prvo blok SESIJA — ako ekran traži pravo koje sesija nema, ili flag koji je OFF, to je "question" (pravo/modul), ne bug. Ako je pod NE DOZVOLJAVA u kontekstu ekrana, to je "limitation".
- Prazan prostor gde bi trebalo da bude sadržaj: prazno stanje mora da ima poruku i akciju ("Nema zapisa — dodaj prvi"). Tabela sa zaglavljem i ništa ispod, ili prazna kartica, jeste greška prikaza, ne "nema podataka".
- Padajući meni koji se "otvara prazan" ili se seče na ivici kartice: poznata klasa greške (kartica seče sadržaj). Opiši koji meni, u kojoj kartici, priloži sliku OTVORENOG menija.
- Sadržaj priljubljen uz ivicu kartice bez razmaka: poznata klasa (sadržaj van unutrašnjeg omotača) — bug prikaza.
- Boja ili veličina "drugačija nego drugde": bug samo ako isti element na drugom ekranu izgleda drugačije — navedi oba ekrana.
- Problem u rasporedu: traži širinu prozora (telefon / tablet / desktop) i sliku; isti ekran se ponaša različito po širini, i bez toga programer ne može da ponovi.
- Slika koja se ne prikazuje: traži da li se vidi zamena (placeholder) ili slomljena ikona — razlika odlučuje gde je greška.
<!-- /ebr:skill -->
