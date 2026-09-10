# Django templates

Extends `stack-django`. Visual/design rules live in the `ui-*` skills.

## `{# … #}` is single-line ONLY

A `{#` whose closing `#}` is on another line is **not a comment** — the parser
treats it as literal text and it renders into the page. This is a documented
Django behaviour, not a bug, and it produces the most embarrassing possible
symptom: explanatory prose appearing in the middle of the navigation bar on every
page.

**Any comment spanning more than one line uses `{% comment %}…{% endcomment %}.**
Never start a `{# … #}` and break it for readability.

Worth automating: a check script over every `*.html` that fails on a `{#` without
a matching `#}` on the same line, wired to a pre-commit or a `PostToolUse` hook.

## `{% load %}` does not propagate into child blocks

A `{% load %}` in a parent template makes the tag available **only in the
parent's own markup** — not inside a child's `{% block %}` override. Every
template that *uses* a tag must load it itself, even when the parent already
loaded the same library.

The failure is `TemplateSyntaxError: Invalid block tag`, and it appears **only
when that block actually renders** — so `manage.py check` passes and the page
500s. A test that requests the page is what catches it.

## Presentation logic lives in template tags

Badge classes, formatted labels, human-readable durations: `templatetags/`, not
model properties (`craft-code`). A filter keeps the coupling to a CSS framework
inside the presentation layer, where changing frameworks is a contained edit.

When several apps grow near-identical badge filters, consolidating is *optional* —
small per-app dictionaries with a dispatch-key test are cheap, and premature
consolidation to a generic helper usually costs more clarity than it saves.
Consolidate when the third one appears and they genuinely agree.

## Template caching bites during development

Django caches templates even with `DEBUG=True` under some loader configurations,
so a template edit under `runserver --noreload` **silently does not render** —
and you then "fix" markup that was never reloaded, twice, before suspecting the
loader. Keep an uncached settings module for visual checks, and verify a visual
change with an actual screenshot rather than trusting the edit.

## `{% trans %}` cannot translate a literal containing `%`

`Variable.resolve` escapes `%` to `%%` on a translated literal before the
catalogue lookup (`django/template/base.py`), so `{% trans "%(count)s rows" %}`
looks up the msgid `%%(count)s rows`, finds nothing, and **silently falls back
to the English source** — no error, no warning. The `.po`/`.mo` are fine and
`gettext("%(count)s rows")` in a shell resolves correctly, which is what makes
this hard to diagnose: the catalogue is proven correct while the page is still
wrong. The tell is a page where *some* strings translate and others, all
containing `%`, do not.

**Rule:** a string with a `%(name)s` placeholder is translated **either** in
Python with `gettext()`/`gettext_lazy()`, **or** in a template with
`{% blocktrans %}` (which handles placeholders correctly) — never with
`{% trans %}` on a literal. Strings destined for JavaScript should be built in
the view with `gettext()` and shipped via `{{ strings|json_script:"id" }}`,
which sidesteps the `%` bug and also escapes `<`/`>`/`&` for free, removing any
need for `{% autoescape off %}` + `{% filter escapejs %}`. Grep for review:
`{% trans "[^"]*%(` in any template is a defect.

## `json_script` serialises what you hand it — give it the object, never JSON

**What broke** — a map page shipped its markers as
`{{ markers_json|json_script:"markers" }}` where the view had already built
`markers_json` with `json.dumps(...)`. The filter encoded the *string*, so
`JSON.parse(el.textContent)` returned a string rather than an array and the
first method call on it (`rows.map`) threw. The page rendered its container,
its controls and its zoom buttons and drew nothing — which reads as a data or
CSS problem, not a serialisation one.

**Why** — `json_script` is serialise + HTML-escape + wrap in a `<script>` tag.
It is not a pass-through for text that is already JSON, and nothing errors on
double-encoding because a JSON string is itself valid JSON. Every server-side
check keeps passing: the context holds the right list, the view tests are
green, the payload is in the HTML, and it is even readable in devtools — just
wrapped in quotes.

**The rule** — pass `json_script` the raw Python object (`list`/`dict`). Never
`json.dumps` on the way in; that is the filter's entire job, and doing it twice
is invisible from the server side. Same for serializer output already rendered
to text.

**Checkable** — assert against the RENDERED page, not the context: parse the
`<script id="…">` payload and require a `list`/`dict`, not a `str`. A
context-level assertion cannot see this class of bug at all, which is why it
survives a green suite.

**Where it bit** — acme-audit `settings_admin/views.py LocationMapView`
(#70103); found only by loading the page in a browser and reading the console.

## A number rendered as DATA, not prose, must be `|unlocalize`d

**What broke** — `<a href="https://www.google.com/maps/search/?api=1&query=
{{ loc.latitude }},{{ loc.longitude }}">` under a Serbian locale produced
`?query=42,441042,19,263648` — four comma-separated numbers instead of two —
and the map answered "can't find". The stored coordinates were correct all
along.

**Why** — `{{ value }}` applies L10N. A locale whose decimal separator is a
comma silently turns any number rendered through it into a different value the
moment it lands somewhere a machine parses: a URL, a query string, a `data-`
attribute, hand-built JSON, a CSV cell.

**The rule** — a number that leaves the page as DATA rather than prose must
never go through a bare `{{ value }}`. Use `{% load l10n %}` + `|unlocalize`,
wrap the block in `{% localize off %}`, or format it explicitly in Python
(`str(Decimal(...))` is locale-independent) before it reaches the template.
Counter-case, so it does not get "fixed" twice: a value already serialised
through `float()`/`json.dumps` in the view is already safe — the bug is only
in template-side interpolation.

**Where it bit** — acme-audit `locations` map-link template, a Google Maps
`query=` URL built from `{{ latitude }},{{ longitude }}`.

## `|default:` fires on FALSY, not on missing — an empty string is not a choice you can express

**What broke** — a shared filter-bar include wrote
`class="{{ col|default:'col-6 col-md-3' }}"` so a caller could override the
wrapper class. One caller's filter bar is a CSS grid, not a Bootstrap row, so it
passed `col=""` to get a plain cell — and got the Bootstrap default anyway,
because `default` substitutes for ANY falsy value and `""` is falsy. The class
then meant "25% of the grid track", and three controls rendered as 46px stubs
showing `S...`. The page returned 200, the control existed, and it was
unusable.

**Why** — `default` answers "is this falsy", not "did the caller say anything".
`""`, `0`, `False` and an empty list are all indistinguishable from absent.
`default_if_none` narrows it to `None`, but a variable that was never passed to
an `{% include %}` resolves to `""` and not to `None`, so it does not help
there either.

**The rule** — an include parameter whose EMPTY value is meaningful gets **no
default at all**: render `{{ col }}` and make every call site state its own
value. A default that a caller cannot opt out of is worse than no default,
because the override looks like it worked.

**Where it bit** — acme-audit `templates/includes/filter_multiselect.html`
(#32991), the Popis list's filter bar.

## A filter ARGUMENT that is missing RAISES — `{% firstof %}` does not

**What broke** — a shared partial resolved its active key with
`{% with active=explicit|default:request.GET.key %}`. Rendered through a normal
view it was fine; rendered by `render_to_string` without a request (a harness, a
mail body, a PDF, an export) the whole template raised
`VariableDoesNotExist: Failed lookup for key [request]`.

**Why** — a missing VARIABLE is swallowed and becomes `string_if_invalid`, so
`{{ nothing }}` is safely empty. A missing filter ARGUMENT is not: the argument
is resolved before the filter runs, and that resolution is allowed to fail
loudly. So `{{ a|default:b.c }}` and `{% with x=a|default:b.c %}` are landmines
in exactly the partials most likely to be reused headlessly, and the failure
does not show up on the page where the partial was written.

**The rule** — when the fallback is another VARIABLE (rather than a literal),
use `{% firstof a b "" as x %}`. `firstof` resolves each candidate with
failures ignored, takes the first truthy one, and supports `as`. Keep
`|default:` for literal fallbacks only.

**Check** — render the partial once with `render_to_string(name, {...})` and no
request. Anything that only works inside a request is a partial that cannot be
tested, previewed or exported.

## Escaping

Autoescaping is on; `|safe` and `mark_safe` turn it off. Every use is a decision
about whether the value can contain attacker-controlled text — treat each one as
a small security review, not a formatting fix.
