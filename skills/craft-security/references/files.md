# Files & uploads

Extends `craft-security`.

## Refuse dangerous extensions on the way in

Media served from the application's own origin makes an uploaded `.html`,
`.svg`, `.js`, `.xhtml` or `.xml` **stored XSS with the victim's session
attached**. Someone opens the link, the file runs as first-party script.

**`X-Content-Type-Options: nosniff` does not help** — nothing is being sniffed.
The extension really is what it claims to be; the browser is behaving correctly.

- **Judge on the LAST extension.** `report.pdf.html` is html; `report.html.pdf`
  is a pdf. What the browser acts on is the last one.
- **A deny list, deliberately.** An allow list rejects a legitimate office format
  every week, and ends up deleted by whoever is on support that day.
- **Image fields are exempt** where the library refuses anything that is not a
  real raster image. A field that exists specifically to accept SVG is a
  documented carve-out and runs a sanitizer.
- **Second layer at the web server:** force `Content-Disposition: attachment` on
  those extensions for anything already stored. The validator protects new
  writes; legacy rows and writes that bypass validation need the server rule.
- **Pin it with a test that fails when a new file field ships without the
  validator.** Enumerate the fields at runtime rather than listing them — a list
  goes stale the day someone adds a model.

## Scope-prefix every upload path

A computed path, `<scope>/<bucket>/<year>/<month>/<name>`, never a static
directory. A fixed directory mixes every customer's files in one tree, where a
single misconfigured rule or one leaked link crosses the boundary.

Changing the path does **not** move existing files — their old paths stay in the
database. Relocation is a separate, deliberate migration.

## Never expose a raw filesystem path from a storage abstraction

Remote backends do not implement it. Depending on the caller, that surfaces as an
exception the framework does not absorb — a template rendering a `.path` on
remote storage takes down the whole page — or, worse, as a path that exists and
points at a file that is not there.

Read through the storage API. For images that must be embedded where the renderer
cannot fetch authenticated media, inline a data URI, failing closed to empty
rather than raising into the render.

Keep that conversion in **one** function, so the same builder feeds both the
screen (a URL) and the export (a data URI).
