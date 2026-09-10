---
name: craft-i18n
description: >
  How to translate a codebase's user-facing strings correctly — gettext
  catalogues (.po/.mo), plural forms, placeholders, lazy vs runtime
  translation, and what must NEVER be translated. Load BEFORE touching a
  catalogue, adding a language, wrapping a string for translation, or writing
  user-facing text that will be translated later. TRIGGER on: translation,
  prevod, i18n, l10n, gettext, makemessages, compilemessages, .po, .mo, msgid,
  msgstr, plural forms, locale, or a request to translate an application into
  another language.
---

# Translation craft

The project's own rules — which locales, which script, where the catalogue
lives, which strings are exempt — live in that project's i18n skill and always
win. This file is what is true everywhere.

## A translation is a contract with the source, not a rewrite

The `msgid` is a key. Change the English and the translation silently detaches:
gettext falls back to the msgid, so the user sees English and **nothing fails**.
That is why a source-string edit and a catalogue update are one change, never
two.

- **Never edit a `msgid` to make it read better.** Fix the source string, then
  re-extract. Editing the catalogue's msgid orphans the entry.
- **Never delete an entry you did not add.** An untranslated entry (`msgstr ""`)
  is a working state — it falls back to English. A deleted one loses the
  translator's history and the fuzzy match that would have recovered it next
  version.
- **A `#, fuzzy` flag means "a human must look at this."** Extraction sets it
  when it guesses a match after the source changed. Leave the flag; do not
  strip it to make the file look finished.

## Placeholders are part of the string, and reordering is the point

Use **named** placeholders — `%(count)s`, `{name}` — never positional `%s`.
Word order differs between languages, and a translator who must reorder cannot
do it with positional arguments without breaking the call.

An f-string can never be a `msgid`. It is evaluated before gettext sees it, so
extraction finds nothing and the string ships untranslated forever. This is the
most common way a string quietly escapes the catalogue.

Every placeholder in the msgid must appear in the msgstr, spelled identically.
A dropped placeholder is a runtime `KeyError` in a language nobody on the team
reads — so it surfaces from a user, not from CI.

## Plurals are not "one vs many"

The number of plural forms is a property of the LANGUAGE, declared in the
catalogue header:

    Plural-Forms: nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : ...);

English has 2. Serbian, Croatian, Bosnian, Russian, Ukrainian, Polish have 3.
Arabic has 6. Japanese and Chinese have 1.

- A plural entry needs **every** `msgstr[0..n-1]` the header declares. A missing
  index is a blank string shown to a user.
- **Never build a plural by concatenating** ("1 " + item + "s"). Suffix rules
  differ per form and per gender; outside English, concatenation is never right.
- **A count that is always large still needs the plural machinery**, because
  form selection in Slavic languages depends on the last digits — 21 takes the
  first form, 22-24 the second, 25 the third.

## Lazy vs runtime is not a style choice

- **Lazy** for anything bound at import time: model field labels, help text,
  choices, form field labels, class attributes, menu definitions. It resolves at
  render, in the reader's language.
- **Runtime** for anything produced per request: flash messages, validation
  errors, generated text.

Getting it backwards is silently wrong. A runtime string used as a class
attribute freezes whichever language was active when the module imported —
usually the server's, for every user. A lazy string handed to something that
expects a real `str` (JSON serialisation, concatenation, `hash()`) blows up far
from the cause.

## What must NOT be translated

Decide this before touching anything; each has produced a real defect:

- **Stored data a user can rename.** A default row written into the database is
  *data*, not a `msgid`. Translating it means the value changes when the
  reader's language does, and any code comparing it breaks. Ship the default in
  the base language and let the user rename it.
- **Tokens the code branches on.** Status codes, answer-option values feeding a
  scoring map, registry keys, enum values. If any comparison reads `== 'Yes'`,
  that string is an identifier wearing a label's clothes.
- **Prompts sent to a model.** Changing the wording changes the behaviour.
- **Log lines and developer-facing exceptions.** Translating them makes the one
  audience who reads them unable to search for the message.
- **Identifiers shown in the UI**: field paths, HTTP headers, file names,
  currency and country codes.

The test is the same for all of them: *would a reader in another language want
this to change?* A button label yes; a status token no.

## A wrong translation is a bug report waiting to happen — check the WORD

A `msgstr` nobody on the team reads is never reviewed again after the day it
was written, and an invented term survives until a customer files a ticket
about it.

> A goods receipt note shipped as `Zapremnica` in Serbian. That is not a word —
> `zapremina` means volume. It had been pattern-matched off `otpremnica`
> (dispatch note, correct) and nobody who spoke the language read the screen
> until the client asked for it to be changed. Its document-number prefix had
> been derived from the invented word too, so the mistake had reached the
> DATA.

Two things follow, and the second is the one that gets missed:

- **Translate the domain term, not the English words.** Business documents,
  legal statuses and accounting artefacts have established names in the target
  language; find the real one rather than constructing a plausible-looking
  neighbour of a word you already used. Confirm the sibling terms while you are
  there — a wrong one usually sits next to right ones, which is what makes it
  look plausible.
- **A wrong translation can have leaked into identifiers.** Prefixes, codes,
  file names and slugs get derived from whatever the screen said at the time.
  Grep the wrong term across the WHOLE repo, not just the catalogue, before
  concluding the fix is one `msgstr`. Renaming those is a data decision with a
  history attached — existing records carry the old value — and it belongs to
  the operator, not to the translator.

Fixing it stays one change with the source: if the English `msgid` also carries
the bad term (glossed in parentheses for the local reader, say), the template
and the catalogue move together — see the contract rule above.

## Register and length

- **Pick a register and hold it.** Formal or informal address, imperative or
  infinitive for buttons — mixing them inside one screen reads as machine
  output. Most business software wants the neutral/formal form.
- **A translation is usually longer than the English.** German and Slavic
  strings run 20-35% longer, and a button sized for "Save" will not hold
  "Sačuvaj izmene". Prefer short verbs; check the longest string against the
  narrowest place it appears.
- **Build a glossary first and follow it mechanically.** One domain term gets
  one translation everywhere. Two plausible renderings of the same word on two
  screens is the most common complaint from real users, and it is invisible to
  whoever wrote them one at a time.
- **Translate the meaning of the SCREEN, not the sentence.** A `msgid` arrives
  without context; read where it is used before translating anything ambiguous.
  "Open" is a verb on a button and an adjective in a status column, and no
  catalogue tells you which.

## When the extraction tools are unavailable

`makemessages`/`compilemessages` shell out to GNU gettext (`xgettext`,
`msguniq`, `msgfmt`), which are frequently absent on a Windows workstation.
Both commands then fail outright rather than degrading.

The fallback is to edit the catalogue programmatically — in Python, `polib`
reads and writes `.po` and compiles `.mo`
(`polib.pofile(path).save_as_mofile(...)`). It is a real option, with one
warning worth stating plainly:

> **A hand-assembled catalogue that is subtly wrong is worse than an
> out-of-date one, because it looks finished.** Plural entries and fuzzy flags
> are where hand-assembly goes wrong. If you cannot produce those correctly,
> leave them out and say so rather than guessing.

Extract Python strings with `ast`, never a regex: implicit concatenation across
lines is joined by the parser and missed by every pattern.

## Verify, and say how you verified

Editing a catalogue is not evidence that a translation reaches the user. The
`.mo` is what gets read, so a `.po` saved without recompiling changes nothing at
all. Prove it end to end:

    gettext.translation('django', 'locale', ['sr_Latn']).gettext('Save document')

Round-trip a handful of real strings, including one plural. Confirm the entry
count went UP and never down — a count that dropped means entries were lost, and
that is the failure nobody notices until a translator asks where their work
went.

**And know which catalogue answered.** A framework merges its OWN catalogues
with the project's — Django looks through every installed app, `django.contrib`
included — so a string the project never translated can still render translated
in the browser, borrowed from the framework's admin catalogue. The screenshot
looks finished, the `.po` has no entry, and the wording changes or reverts to
English the day the framework is upgraded or the app is uninstalled.

The two checks disagree in exactly this case, and that disagreement is the
signal:

    # the PROJECT catalogue alone — this is the one that must answer
    gettext.translation('django', 'locale', ['sr_Latn']).gettext('Clear selection')

If the rendered page shows a translation and this returns the msgid unchanged,
the project is relying on someone else's catalogue. Add the entry.

Caught on a filter widget whose "Clear selection" came from Django's admin
catalogue; every other string on the screen was the project's own, so nothing
looked wrong.
