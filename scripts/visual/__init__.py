#!/usr/bin/env python3
"""The visual-diff engine. A PACKAGE, deliberately - read this before adding a
module or an import to it.

`capture.py` loads the target repository's auth driver IN THIS PROCESS (the repo
`driver.py` -> `django.setup()`), so every name the engine has bound in
`sys.modules`, and every directory it has left on `sys.path`, belongs to the
APP's import namespace while the app's own code runs.

The engine used to be a folder of flat modules that put its own directory on
`sys.path` and imported `config`, `pages`, `locate`, `store` under those bare
names. `sys.modules['config']` was then the engine's module, and every Django
project laid out the standard way (`config/settings/...` - the cookiecutter
layout) died on

    ModuleNotFoundError: No module named 'config.settings'; 'config' is not a package

which the engine reported as `auth_failed`, sending two agents after the wrong
problem. `pages` and `store` are the next collisions waiting to happen: they are
ordinary Django app names.

Two rules, and they are not style:

1. **Modules inside the engine import each other relatively** - `from . import
   config as vconfig`. The engine occupies exactly one top-level name: `visual`.
2. **Anything outside the engine is loaded BY PATH under a namespaced alias**
   (`isolate.load_module`), never by putting another brain directory on
   `sys.path`. That covers the repo's auth driver, the ticket store and the
   Gemini client.

**Every module here with an `if __name__ == "__main__"` block carries the
sys.path prologue** (copy it verbatim from `shoot.py`): run as a script, the
interpreter puts THIS directory on `sys.path[0]`, which re-opens the same hole
from the other side - the app asks for `config`, and finds `visual/config.py`
because the repo root was appended after it. The prologue swaps that entry for
the package's parent. A new entry point without it is the bug coming back.

Pinned by `test_shoot.py::test_the_engine_does_not_squat_the_app_s_config_or_its_event_loop`,
which runs the engine against a repo that has its own `config` package and a
driver that installs the Selector event-loop policy the way channels does.
"""
