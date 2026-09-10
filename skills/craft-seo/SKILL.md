---
name: craft-seo
description: >
  Making a sales or landing page findable and shareable — the checkable half of
  SEO. For the sales page OF an application (POPIS, HRIS, helpdesk each have
  one, in their own repo or folder) and for a client's marketing page. Invoke by
  hand with /brain:craft-seo.
disable-model-invocation: true
---

# SEO craft — the checkable half

**Measure first, always.** This skill exists to interpret a measurement, not to
replace one:

```bash
python ~/.claude/skills/brain/scripts/seo.py <file|dir|url>
```

## Where these rules come from — read this before trusting them

**This file breaks the brain's usual law, deliberately, and says so.** Every
other skill here carries rules earned from something that broke in this work.
There is no SEO incident history, so these were adopted from specification
rather than from experience.

That is only acceptable because of what was left out. **Everything here is
CHECKABLE**: a tag exists or it does not, a JSON-LD block parses or it does
not, an `og:image` is an absolute URL or it is a relative path that every
crawler will fail to fetch. `seo.py` verifies every one of them.

**What this file refuses to carry**, and the refusal is the point: keyword
density, "E-E-A-T signals", "AI citation readiness", how long the copy should
be. Those may even be right. They cannot be checked, they change with the
season, and a rule that cannot be checked is indistinguishable from a rule that
is wrong. `/brain:research` if one of them ever matters.

Earned rules will accumulate here as pages actually ship and something actually
goes wrong. Until then, the measured baseline below is the honest content.

## The reference implementation is yours, not mine

**`popis/templates/inventory/landing.html` scores zero missing items.** Read it
before reading the rest of this file: it is a self-contained template with its
own `<head>`, twelve tags, `prefix="og: http://ogp.me/ns#"` on `<html>`, and
complete Open Graph and JSON-LD. Where this skill's advice and that page
disagree, the page wins — it shipped.

It is also the answer to "does this work on a Django template". That one does
not extend `base.html`, so `seo.py` reads it directly. A landing page that DOES
extend a base would need the rendered URL instead, because the `<head>` would be
assembled from two files. Checked 2026-08-31: none of these three do that.

Measured the same day, so the gap is known rather than guessed:

| Page | Missing |
|---|---|
| `popis/templates/inventory/landing.html` | **0** — the reference |
| `TICKETING-PRODAJA/index.html` | 1 — `hreflang` |
| `MNS_HRIS/landing/index.html` | 4 — canonical, Open Graph, JSON-LD, `hreflang` |
| `palas sajt/index.html` | 4 — canonical, Open Graph, JSON-LD, `hreflang` |

### The one rule here that is EARNED

**Every landing page in this shop is multilingual, and not one of them declares
`hreflang`.** Four pages, four repos, four out of four. That is not a
specification adopted from elsewhere — it is a measured pattern in this work,
and it is the first rule in this file that meets the brain's usual standard.

The reason it keeps happening is structural rather than careless: these pages
switch language in JavaScript without changing the URL, and **a language switch
with one URL cannot have `hreflang` at all** — there is no second address to
point at. So the fix is never "add the tag"; it is a decision about whether the
other language deserves its own URL. Decide that first, and only then reach for
the tags.

## The order to fix things, and why that order

Measured on `palas sajt/index.html`, 2026-08-31 — a real page, already better
than most: `lang`, `viewport`, title, description, one `h1`, clean heading
order, and **6 images with 0 missing `alt`**. Four things were absent, and the
order below is by what each one costs.

### 1. Open Graph — the one that costs money the same day

No `og:` tags means **every link pasted into WhatsApp, Viber, LinkedIn or Slack
renders as a bare grey card.** On a sales page that is the difference between a
share that gets clicked and one that does not, and it is perhaps ten lines.

```html
<meta property="og:type"        content="website">
<meta property="og:url"         content="https://example.rs/">
<meta property="og:title"       content="...">
<meta property="og:description" content="...">
<meta property="og:image"       content="https://example.rs/assets/og.png">
<meta name="twitter:card"       content="summary_large_image">
```

- **`og:image` must be an ABSOLUTE https URL.** A crawler fetches it with no
  page context, so a relative path resolves against nothing. This is the single
  most common way the tags are present and the card is still blank.
- ~1200×630. Test the real link in the app you actually share in — the
  validators disagree with each other and with reality.

### 2. JSON-LD — what the page IS, rather than a guess

Without it, a machine infers your business from prose. With it you state it.
One block in `<head>`, and it must be valid JSON — **an invalid block is
ignored entirely, which is the same as absent**, and that is exactly why
`seo.py` parses it rather than looking for the tag.

```html
<script type="application/ld+json">
{ "@context": "https://schema.org", "@type": "Organization",
  "name": "...", "url": "https://example.rs/",
  "logo": "https://example.rs/assets/logo.png",
  "address": { "@type": "PostalAddress", "addressCountry": "RS" } }
</script>
```

Pick the `@type` that is true: `Organization`, `LocalBusiness` (if there is a
physical address and hours), `Service`, `SoftwareApplication`. A wrong type is
worse than none.

### 3. `hreflang` — only when the page really is multilingual

`palas sajt` ships `translations.js` and declared no alternates, so its language
versions compete with each other instead of being offered to the right reader.

```html
<link rel="alternate" hreflang="sr" href="https://example.rs/">
<link rel="alternate" hreflang="en" href="https://example.rs/en/">
<link rel="alternate" hreflang="x-default" href="https://example.rs/">
```

**A JavaScript language switch that does not change the URL cannot have
`hreflang` at all** — there is no second URL to point at. That is a structural
decision about the site, not a tag you can add, and it is worth knowing before
someone spends a day on the tags.

### 4. `canonical` — cheap insurance

One line. The same page reachable at `www` and non-`www`, or with a `?utm_...`
tail, otherwise counts as several pages each holding a fraction of the standing.

```html
<link rel="canonical" href="https://example.rs/">
```

## The half no static reader can see

`seo.py` reads the source. It cannot tell you what the page DOES when it loads
— and for a landing page that is most of the experience.

**Lighthouse, through the `chrome-devtools` MCP**
(`ops-integrations/references/servers.md`): Core Web Vitals, render-blocking resources, image
sizing, accessibility. The `palas` page loads Font Awesome, AOS and Google
Fonts from three different CDNs before its own stylesheet; whether that costs
anything is a measurement, not a guess, and `visual-diff` cannot answer it
either — it compares structure, not timing.

## When this is BILLABLE work for a client

A landing page for one of your own applications is polish. The same work on a
client's marketing page is a deliverable someone pays for, and that changes two
things.

**1. Record the baseline BEFORE touching anything.**

```bash
python ~/.claude/skills/brain/scripts/seo.py <page> --save     # before
# ... do the work ...
python ~/.claude/skills/brain/scripts/seo.py <page> --client   # after
```

`--client` then prints what actually CHANGED, in the customer's language.
Without a baseline there is no evidence — "four things were missing and are now
present" is a deliverable; "I did SEO" is a claim. This is the same reason
`visual-diff` shoots a BEFORE before the first edit.

**2. Two reports, two readers — the split the ticket store already makes.**
The default output is yours: every check, including the passing ones. `--client`
is what the customer reads and names only what moved. A customer handed the full
list reads twenty "ok" lines as padding on an invoice.

### What may be promised, and what may not

This is the one place SEO work goes wrong professionally, and it is worth being
plain about because the field's norm is the opposite.

| Promise | Because |
|---|---|
| **"The link now shows a proper card when shared"** | verifiable in thirty seconds, in front of them |
| **"Search engines are now told what the business is"** | the JSON-LD either parses or it does not |
| **"The site is no longer counted as several pages"** | one tag, checkable |
| ~~"You will rank higher"~~ | depends on competitors, content and time — none of which you control |
| ~~"You will get more traffic"~~ | same, plus seasonality and whatever else the client is doing |

**Bill for the work, not for the outcome.** Every line `seo.py` reports is
something you did and can show. The report's own footer says this to the
customer in their language, deliberately — so the boundary is stated by the
document rather than having to be defended in a conversation later.

## Working rules

- **A page nobody can share is not a marketing page.** Check the card in the
  app you actually send links in, before optimising anything else.
- **Write the description for the human**, and let the length fall where it
  falls. `seo.py` reports 110–165 characters as a convention, not a rule; a
  clear 90-character sentence beats a padded 160-character one.
- **Never add a tag you cannot fill honestly.** An `og:image` pointing at a
  missing file, or a `LocalBusiness` with no address, is worse than the absence
  — it is a claim that fails when checked.
- **`ui-bootstrap` still governs the page itself.** This skill is about being
  found and shared; how the page looks and holds together is that one.
