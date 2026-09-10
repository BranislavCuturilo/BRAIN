---
name: stack-django
description: >
  Django and Python specifics: the framework traps that cost real time, layered
  on top of craft-code and craft-security. Load BEFORE writing or reviewing any
  Django code — models, views, forms, querysets, templates, migrations,
  management commands, signals. This file holds the rules that apply to all
  Django work; the references/ files hold the per-artifact detail and are read on
  demand.
when_to_use: >
  editing a .py file in a Django project, a model or migration, a class-based
  view, a ModelForm, a queryset, a template, a management command, "why does
  full_clean fail", "the validation runs too early", Django project layout.
---

# Django craft

Read with `craft-code` (structure) and `craft-security` (isolation and authz).
This file holds what applies everywhere in Django; the detail is split so a task
loads only what it needs.

## Read on demand

| Working on | Read |
|---|---|
| a model, constraint, index, `clean()` | `references/models.md` |
| a view, mixin, CBV hook | `references/views.md` |
| a form or ModelForm | `references/forms.md` |
| a queryset, aggregation, N+1 | `references/queries.md` |
| a template or template tag | `references/templates.md` |
| a migration or schema change | `references/migrations.md` |

## Every Python invocation goes through the project's venv

Never bare `python`, `pip` or `manage.py`. The system interpreter has a
different set of packages, and the failure is misleading: a missing dependency
surfaces as an import error deep in middleware, or — worse — the command runs
against the *wrong* versions and appears to work.

```bash
venv/Scripts/python.exe manage.py test        # Windows
venv/bin/python manage.py test                # POSIX
venv/Scripts/python.exe -m pip install -r requirements/development.txt
```

Same for every script, every management command, every test run. If a venv does
not exist, create it before running anything — do not "just this once" use the
system interpreter, because that is how a package ends up installed in the wrong
place and the next person cannot reproduce the environment.

## Applies to all Django work

- **Layout is per-app: `models.py`, `services.py`, `views.py`, `forms.py`,
  `signals.py`, `templatetags/`.** Business logic goes in `services.py`. An app's
  service imports its own models plus shared infrastructure — nothing from a
  sibling feature app.
- **Register signal receivers in `AppConfig.ready()`**, importing the signals
  module there. Receivers defined but never imported simply never fire, and
  nothing warns you.
- **Cross-app side effects use signals with lazy imports inside the receiver**, so
  the emitting app stays importable when the consumer is not installed. Log and
  swallow failures in the receiver: a downstream side effect must not roll back
  the state change that triggered it.
- **Settings are split** (`base` / `development` / `production` / `test`) and the
  test module is never inherited-into by accident — see `craft-testing` for the
  side-effect-inheritance trap, which is a Django-config problem specifically.
- **A setting the framework removed does not raise — it stops meaning anything.**
  `DEFAULT_FILE_STORAGE` and `STATICFILES_STORAGE` were removed in 5.1: on 5.1+
  they parse fine and the backend they name is silently replaced by the default,
  so uploads land on the container's disk while `MEDIA_URL` still points at the
  remote host and every new file 404s for the user. Configure storage **only**
  through `STORAGES` — and never beside the old key: on 4.2/5.0 the pair raises
  `ImproperlyConfigured` at import, so "leave the old one for compatibility" is a
  boot failure, not a safety net. **Pin the resolved backend**
  (`storages['default']`) per settings module; asserting the setting *string*
  passes happily over a dead key and proves nothing. Generalise at every upgrade:
  grep the release notes' removed settings against the settings package, and
  remember the deployed version is `requirements.txt`, not the venv.
- **Management commands are entrypoints and need a smoke test.** Django imports a
  command module only when it is invoked, so a syntax error there ships silently.
- **A command that prints non-ASCII crashes on a Windows console** (`cp1252`
  cannot encode `č/ć/š/ž/đ`) and dies *mid-run*, leaving a half-applied seed. The
  database write is fine — UTF-8 all the way down — it is only the console
  encoder. Keep command output ASCII, or run with `PYTHONIOENCODING=utf-8`.
- **On Windows, `django.setup()` installs the Selector event-loop policy**
  (channels), and anything that spawns a subprocess through asyncio — Playwright
  above all — then dies with a bare `NotImplementedError` from
  `asyncio.base_events._make_subprocess_transport`, naming nothing. Assert
  `WindowsProactorEventLoopPolicy` **immediately before the call that needs it**,
  after every Django import: set it earlier and the next `django.setup()` quietly
  takes it back. This is why a browser driver that "worked yesterday" fails the
  moment it starts logging in through the ORM.
- **`makemigrations --check --dry-run` before pushing.** Migration drift is the
  cheapest deploy failure to prevent and one of the more annoying to diagnose
  after the fact.
