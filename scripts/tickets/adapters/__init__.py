"""Ticket source adapters.

A source type maps to one adapter that knows how to pull that helpdesk's tickets
and map them into the canonical `tiketi.json` shape. The registry exists because
the user asked to keep the door open for a second helpdesk without the sync code
changing — the seam is here; only `acme_helpdesk` is built. Add a new source
by writing a module in this package that calls `@register("name")` on its class.
"""
from __future__ import annotations

class TicketSourceError(RuntimeError):
    """A ticket-source call failed.

    Lives HERE, in the registry, not in one adapter. `sync.py` catches this to
    decide whether a pull failed, and it used to import the concrete
    `adapters.acme_helpdesk.HelpdeskError` to do it -- so the core of the
    ticket pipeline could not be imported at all without that one adapter
    present. Removing the adapter to build a release tree took five test files
    with it, none of which mentions an adapter.

    Never carries the token, the query string, or a response body that could
    contain either.
    """


_SOURCES = {}


def register(name: str):
    """Class decorator: make an adapter selectable as `--source <name>`."""
    def deco(cls):
        _SOURCES[name] = cls
        return cls
    return deco


def get_adapter(name: str):
    """The adapter class registered under `name` (raises KeyError if unknown)."""
    if name not in _SOURCES:
        raise KeyError(
            f"unknown ticket source {name!r} (have: {sorted(_SOURCES) or ['(none loaded)']})")
    return _SOURCES[name]


#: name -> why it did not load. Read by `get_adapter`'s error, so a missing
#: dependency reads as "this adapter failed to load", not "no such source".
_FAILED: dict = {}


def _discover() -> None:
    """Import every adapter module in this package so it self-registers.

    This used to be one hard `from . import acme_helpdesk`, in the same file
    whose docstring says the registry exists so a second helpdesk needs no
    change to the sync code. It was not a seam: removing that single adapter
    broke the import of the PACKAGE, and with it five test files that never
    mention an adapter at all. A seam whose only implementation is load-bearing
    is a seam in the comments -- and nothing noticed, because the one
    implementation was always present.

    Failure is per-module and non-fatal: an adapter needing a library nobody
    installed must not take the whole ticket queue down with it.
    """
    import importlib
    import pkgutil
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{mod.name}")
        except Exception as exc:                              # noqa: BLE001
            _FAILED[mod.name] = f"{type(exc).__name__}: {exc}"


_discover()
