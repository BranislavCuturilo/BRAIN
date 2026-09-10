# Django forms

Extends `stack-django`.

## Scope-filter every FK queryset

```python
def __init__(self, *args, tenant=None, **kwargs):
    super().__init__(*args, **kwargs)
    self.fields['location'].queryset = Location.objects.filter(organization=tenant)
```

This is a usability and defence-in-depth measure, **not** the security boundary —
a crafted POST does not go through your dropdown. The model's `clean()` is the
boundary (`craft-security`).

## A filter form that fails validation must not WIDEN the list

`cleaned_data` simply **omits** a field that failed validation. So a list view
that reads `cleaned_data.get('region')` and applies the filter only when it is
truthy renders the **unfiltered** set the moment the value is invalid — and an
FK filter whose queryset is scope-filtered treats *another tenant's pk* as
invalid. `?region=<other tenant's pk>` therefore flips from "no rows" to "every
row", which is the opposite of what a narrowing control may do, and no template
that renders only `{{ form.field }}` shows any reason why.

**Fail closed and say so — both halves in the same change:**

```python
form = self.get_filter_form()
if not form.is_valid():
    return qs.none()          # never the un-narrowed qs
cleaned = form.cleaned_data
```

and render `form.errors` in the template. `qs.none()` alone converts "silently
too wide" into "silently empty", which is the same defect wearing the other
mask. Note the form is **bound whenever you pass `request.GET`** (an empty
`QueryDict` is still data), so `is_bound` is not the discriminator — `is_valid()`
is, and it is `True` for a request with no filters at all.

## Stamp the scope on the instance in `__init__`, not in the view

`ModelForm.is_valid()` runs `_post_clean()` then `instance.full_clean()`
**before** the view ever assigns the tenant. So a model `clean()` that compares
FKs against `self.organization_id` sees `None` and reports a false "belongs to a
different organization" on every field, or raises `RelatedObjectDoesNotExist` if
it touches `self.organization` directly. Every create through a form fails
validation, and the error message points at the wrong thing entirely.

```python
class _OrgModelForm(forms.ModelForm):
    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization and self.instance.organization_id is None:
            self.instance.organization = organization
```

Pair it with a defensive early return in the model's `clean()` when
`organization_id is None` — the service write path always sets the scope before
`full_clean()`, so the invariant is still enforced there. **Both halves**: the
guard alone silently skips validation for form paths that never re-clean.

## A "forbidden value" guard must exempt the instance's current value

```python
def clean_slug(self):
    value = self.cleaned_data['slug']
    if self.instance.pk and value == self.instance.slug:
        return value                      # keeping its own value is always allowed
    if value in RESERVED_SLUGS:
        raise ValidationError('Reserved.')
    return value
```

Without the exemption the guarded row becomes **permanently uneditable**: every
update POST re-submits the unchanged field and re-trips the guard. Django's own
`validate_unique` gets this right by excluding `self.instance`; hand-rolled
guards have to replicate it.

Root-cause class: the guard was written thinking only about creation. Any
`clean_<field>` that rejects values should ask "what happens on update?"

## A uniqueness constraint over a field the form does not show is NOT checked

`ModelForm._post_clean()` calls `instance.validate_unique(exclude=...)`, and the
exclusion list contains **every field not on the form**. A `UniqueConstraint`
naming any excluded field is therefore skipped in its entirety.

This bites hardest exactly where it matters most — scoped uniqueness:

```python
class Meta:
    constraints = [UniqueConstraint(fields=['organization', 'code'], name='…')]
```

The form does not expose `organization` (the view stamps it), so Django checks
nothing, the duplicate reaches the database, and the constraint surfaces as an
**uncaught `IntegrityError` — a 500 — instead of a field error.** The user sees a
crash where they should have seen "that code is taken".

**So: for every constraint spanning a stamped field, write the check by hand in
`clean_<field>()` or `clean()`**, excluding the current instance:

```python
def clean_code(self):
    value = self.cleaned_data['code']
    qs = Regime.objects.filter(organization=self.organization, code=value)
    if self.instance.pk:
        qs = qs.exclude(pk=self.instance.pk)
    if qs.exists():
        raise ValidationError(_('A regime with this code already exists.'))
    return value
```

The same applies to a formset over rows sharing a scoped constraint: collisions
*within one submission* never reach the database individually, so the formset's
own `clean()` has to compare the rows against each other as well.

## DRF: a list field reads exactly ONE field name

`serializers.ListField.get_value()` calls `dictionary.getlist(self.field_name)`,
so a serializer declaring `files` sees **nothing** when the client posts
`files[]` — which is what HTML/PHP-style clients, most JS upload libraries and
half the `curl -F` examples emit. The request then *succeeds*: 201, an empty list
in the response, nothing stored, no error in any log. Accept every name the
contract implies, in the field rather than in the view, so the count and size
limits still apply to the sum:

```python
class MultiNameFileListField(serializers.ListField):
    def get_value(self, dictionary):
        if not hasattr(dictionary, 'getlist'):        # JSON body
            return super().get_value(dictionary)
        values = []
        for name in (self.field_name,) + self.extra_field_names:
            values.extend(dictionary.getlist(name))
        return values or empty                        # empty != []
```

**And test the body a real client sends, not the one your serializer declares.**
A test written from the serializer passes identically on the broken and the fixed
code — that is exactly how the defect ships.

## Formsets

A formset validates each form, so every rule above applies per row. When rows
depend on each other (unique-within-the-set, totals), validate in the formset's
`clean()` — a per-form `clean()` cannot see its siblings.

## HTML5 date inputs need an ISO `format`, whatever the locale

**What broke** — every `DateInput(attrs={'type': 'date'})` on an edit form
showed an empty field even though the row had a date, so users could not tell
whether the value was set or what it was (POPIS #08825, #98143).

**Why** — with `USE_L10N` and a non-English locale, `DateInput` formats the
initial value with the locale's first `DATE_INPUT_FORMATS` (`sr-latn` →
`31.01.2016.`). The browser's `<input type="date">` accepts only `YYYY-MM-DD`
and silently drops anything else. It looks fine in English, so it ships.

**The rule** — an HTML5 date/datetime widget always carries an explicit ISO
format: `forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})`
(`'%Y-%m-%dT%H:%M'` for `datetime-local`, `'%H:%M'` for `time`). Grep before
shipping a form: `DateInput(attrs=` with `type': 'date'` and no `format=` is
the defect. The field should also list the ISO format FIRST in
`input_formats`, so a POST is accepted whatever the locale — the `format=`
above only fixes rendering; without an ISO-first `input_formats` the browser
posts ISO but the field parses it against the locale's formats first and can
still reject it.

**Pin it under a non-English locale, not the default.** A test that renders
the widget under `en` passes vacuously — the render-format bug and the
default `input_formats` bug are both invisible in English. Render under a
locale whose decimal/date separators differ (`sr-latn` here) and assert the
rendered `value` is ISO.

**Where it bit** — POPIS `inventory/forms.py`, ten widgets across four forms;
sibling forms in the same app already had the `format=` and nobody had
noticed the inconsistency. Recurred on a different form that already had
`format=`: the box rendered empty and posting the untouched form silently
ERASED the stored date, with no error anywhere — the missing half was
`input_formats`, not `format=`.
