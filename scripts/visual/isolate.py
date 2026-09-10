#!/usr/bin/env python3
"""Import a module the engine needs from OUTSIDE its own package, without
touching the namespace of the application under test.

The engine runs the app's code in its own process (`capture.py` -> the repo's
auth driver -> `django.setup()`), so `sys.path.insert(0, <a brain directory>)`
followed by `import store` hands the app the brain's module under a name the app
may well own - `store`, `pages`, `sync`, `config` are all ordinary Django app
names. `visual/__init__.py` has the incident this comes from.

So there is ONE way to reach a module outside the package, and it is this
function: load it by PATH under an alias nobody else can ask for. Three callers -
the repo's auth driver, the ticket store, the Gemini client.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_module(alias, path):
    """The module at `path`, registered in `sys.modules` as `alias`.

    `alias` must be a name the app cannot plausibly use (`brain_ticket_store`,
    not `store`). A second call with the same alias returns the module already
    loaded - the alias is the identity, exactly like a normal import - and a
    module that fails halfway through is removed again, so a retry re-executes
    it instead of handing back a half-initialised object.

    Raises `ImportError` when the file is missing or unloadable; the caller
    decides what that means (`capture._load_driver` turns it into the operator's
    `auth_failed` question).
    """
    name = str(alias)
    if not name or "." in name:
        raise ImportError("alias %r must be a plain top-level name" % (alias,))
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    fp = Path(path)
    spec = importlib.util.spec_from_file_location(name, fp)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s from %s" % (name, fp))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod
