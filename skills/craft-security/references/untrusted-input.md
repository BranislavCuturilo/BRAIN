# Untrusted input

Extends `craft-security`. The router carries the headline: `GET` must never
write.

## A stored id is untrusted, even when a dropdown produced it

A field holding `"custom:<id>"`, a reference inside a JSON blob, a hidden form
value — all of it is free text from the client.

**Validate on write that the referenced row is in scope, AND scope the read.**
The editor showing only in-scope options does not make a crafted POST safe. Both
layers: the write check catches the crafted request, the scoped read catches rows
already in the database from before the check existed.

## A stored string that selects behaviour is a registry key

Never fed to a dynamic import. That is remote code execution with extra steps,
and it becomes exploitable the moment that field is editable anywhere — an admin
page, an API, a settings screen someone adds next year.

An unknown key is rejected at validation time.

## Never validate untrusted SQL with substring or regex checks

Parse it and walk the tree:

- single statement — no `;` stacking
- SELECT only
- no set operations (`UNION`/`INTERSECT`/`EXCEPT`)
- **no JOIN and no multi-table FROM**
- a scope predicate AND-combined (no top-level `OR`) on **every** SELECT,
  including subqueries

The join rule is the one people leave out, and it is the one that leaks
everything: `FROM audits a, locations l WHERE a.scope_id = 42` passes a
scope-predicate check while `locations` stays completely unconstrained. **Proving
one table is constrained proves nothing about the others** — you cannot establish
per-table isolation with a WHERE scan, so fail closed on joins entirely.

Single-table selects cover the common case. Real multi-table reporting needs a
pre-scoped parameterised template, never raw generated SQL.

## Escape at the sink, not by field name

A value is not safe because of what it's *supposed to be*. A "hex colour" field
arriving on an untrusted POST was written into a `style="…"` attribute by string
concatenation, unescaped, while its neighbours (name, group, tool) were all
escaped — the field's name, not its content, was what made it look safe. A
crafted value like `#x">` plus an `<img onerror=…>` closed the attribute and the
tag and ran as stored XSS with zero user interaction, on an endpoint that
rendered in the default view for every incoming request.

Defence in depth, both layers: **validate the shape at ingest** (a hex colour is
`^#[0-9a-fA-F]{3,8}$`, rejected otherwise) **and escape every interpolation at
the sink**, including ones outside a template's autoescaping — string
concatenation into an HTML attribute, an f-string building a `style=` value, a
manually assembled tag. An attribute-context injection needs only one unescaped
quote to break out.

## A sanitizer is a transform, so it cannot double as a detector

**What broke** — a field that had been a plain textarea gained a rich-text
editor. Old rows are plain text, new ones are HTML, and both have to render, so
the code asked "is this HTML?" by sanitizing the value and checking whether any
`<` survived. An HTML sanitizer in strip mode DELETES what it does not
recognise, so `Pritisni <ENTER>` came back as `Pritisni `, `sifra <broj kase>`
lost its placeholder, and `<https://example.com>` rendered as nothing at all.
Because the same field was sanitized on write, the first person to open and save
one of those records destroyed it in the database. No error, no warning.

**Why** — the detector was built out of a destructive transform. Round-tripping
a value through something whose job is to remove things answers "what survived",
never "what was it". The question is about the INPUT; the sanitizer only reports
on its own output.

**The rule** — decide what a value IS by inspecting it, never by transforming it
and examining the wreckage. Then treat each kind on its own terms: markup gets
sanitized, plain text gets **escaped** and must never be handed to the sanitizer
at all. Keep the classifier conservative — text that merely resembles markup
(`<ENTER>`, `<name@example.com>`) is plain text, and escaping it is both safe
and correct.

**And make the two consumers share one function.** Whatever decides this is read
in at least two places — what the editor opens, and what the page renders — and
two copies of the rule drift into showing the user different documents. One
`render(value)` used by both cannot disagree with itself.

**Where it bit** — a per-location runbook body; the same shape applies to any
notes/description/comment field that outlives a plain-text era.

## `GET` that writes — the fix pattern

Keep rendering on both verbs, gate the write on the method plus an explicit
field, and read parameters through a verb-aware helper so one code path serves
both:

```python
def post(self, request, *a, **kw):
    return self.get(request, *a, **kw)

def _wants_write(self, request):
    return request.method == 'POST' and request.POST.get('archive') == '1'
```

In the template, one CSRF form with two submit buttons gives both actions with no
JavaScript.

The same applies to any read endpoint that quietly acquired a write: "download
and log", "view and mark read", "open and claim". Split the verb; do not add a
query flag.

## Redirects and filenames

A redirect target from a parameter is validated against an allow-list of paths,
never used raw. A filename from an upload or an archive is reduced to its base
name before touching the filesystem — path traversal through a preserved
directory component is the oldest trick there is.
