# Django views

Extends `stack-django`. Authorization rules are in `craft-security`.

## Work with the CBV template method, not against it

Override `get_queryset`, `get_context_data`, `form_valid`, `get_form_kwargs`.
Do not override `dispatch` to re-implement the chain — you lose the framework's
ordering guarantees and every mixin that relied on them.

A view method past ~60 lines has absorbed business logic. Move it to
`<app>/services.py` and name the service methods after what they return. Request
parsing and presentation concerns (parsing `?year=`, resolving a view mode,
building chart JSON) legitimately stay on the view as small static helpers —
those are not data-model concerns.

## Mixins: one capability each

Split a tenant mixin into `TenantQuerysetMixin` (filters `get_queryset`) and
`TenantFormMixin` (stamps `form.instance.organization` in `form_valid`).
Read-only views inherit only the first. A composite for the common case is fine,
but it must be a composition of the two, not a fat class.

Why it matters: a read-only view that inherits `form_valid` for no reason will be
silently affected by the next change to it.

**A mixin whose behaviour comes from a CLASS attribute cannot be stacked with a
sibling of itself.** Two gates were chained in one view's MRO —
`LocationFeatureMixin` (feature `locations`) and a second subclass of the same
`FeatureRequiredMixin` base guarding `locations.geocoding`. `FeatureRequiredMixin`
reads `self.required_feature`, a class attribute, so it resolves once, to
whichever subclass comes first in the MRO. The second `dispatch` then re-checks
the FIRST gate's key — the second gate is dead code while the file reads as
doubly protected. Before believing two guards are two guards, grep the MRO for
two subclasses of the same base. A mixin that must stack takes its parameter
per-instance — an `__init__` argument, a method the subclass overrides, a list
attribute the mixin iterates — never from a single class attribute shared by
every subclass.

## `form_valid` that can short-circuit

When a base `form_valid` can return `form_invalid` without creating the object,
**every subclass that calls `super().form_valid()` and then performs its own
write must detect that the base bailed.** Otherwise a blocked create still runs
the side effect — consuming a one-shot token, linking to a `self.object` that is
stale or `None`.

```python
# base
def form_valid(self, form):
    if not self._may_create():
        self._create_blocked = True
        return self.form_invalid(form)
    return super().form_valid(form)

# subclass
def form_valid(self, form):
    response = super().form_valid(form)
    if getattr(self, '_create_blocked', False):
        return response
    self.notification.mark_consumed(self.object)   # only on a real create
    return response
```

`super().form_valid()` returning does **not** mean the object was created.

## Authorization on by-id views

The rule is in `craft-security` and it is the most repeated defect there is:
a detail, edit or action view addressed by primary key authorizes through the
**same helper the list view uses** — never `Model.objects.filter(organization=
tenant)` plus a tenant-wide permission.

In Django this usually means overriding `get_queryset` on the detail view to call
the same scope helper, and — for a POST-only action view that does not inherit
from a generic detail view — calling that helper explicitly before acting.
**Action views are the ones that get forgotten**, because they have no template
and nobody looks at them.

## GET must not write

`CsrfViewMiddleware` does not protect safe methods, so a write behind
`?archive=1` executes for any logged-in user who loads a page containing
`<img src="…?archive=1">`.

The fix pattern when one endpoint must both render and write:

```python
class _ArchiveOnPostMixin:
    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)     # same rendering path

    @staticmethod
    def _wants_write(request):
        return request.method == 'POST' and request.POST.get('archive') == '1'


def param(request, name):                              # verb-aware read
    return (request.POST if request.method == 'POST' else request.GET).get(name)
```

In the template, one CSRF form with two submit buttons gives both actions with no
JavaScript.

## Errors and messages

`messages.success/error` belongs in the view (presentation). Email, webhooks and
outbound HTTP belong in a service. At any API boundary, never return `str(exc)` —
log the traceback, return a generic message plus a request id.

## A response you embed in an `<iframe>` must opt into being framed

**What broke** — a page gained a "Preview" that loads the app's own PDF export
into an `<iframe>`. It rendered on the dev box and would have been a blank
frame in production, because production sets `X_FRAME_OPTIONS = 'DENY'`
(Django's own default since 3.0 — and DENY blocks *same-origin* framing too).

**Why** — `XFrameOptionsMiddleware` stamps every response; nothing about an
iframe in a template tells you the embedded response refuses to be framed, and
the failure is a silent blank box, not an error.

**The rule** — when a template embeds one of the app's OWN responses (a PDF, a
print view, a widget), the embedded view gets
`@method_decorator(xframe_options_sameorigin, name='dispatch')` (function
views: `@xframe_options_sameorigin`). The decorator sets the header first, so
the middleware leaves it alone. Never drop the global DENY for one page. Pin it
with a test under `override_settings(X_FRAME_OPTIONS='DENY')` asserting
`response['X-Frame-Options'] == 'SAMEORIGIN'` — and prove the test fails
without the decorator. Only a body with nothing to clickjack (a PDF, a static
print page) qualifies; an interactive page framed by your own origin is still
a page somebody else's page could not frame, so SAMEORIGIN is the ceiling.

**Where it bit** — acme-audit `knowledge_base.views_editor.InstructionPDFView`
(2026-08-19, ticket #93164), caught before shipping by reading the settings.
Verification note: headless Chromium has no PDF viewer — a PDF `<iframe>` never
fires `load` there; check PDF embeds in a headed browser.
