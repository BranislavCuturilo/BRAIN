#!/usr/bin/env python3
"""The per-app visual-diff config: `<repo>/.claude/visual-diff.json`.

The method is portable (this folder); everything app-specific — base URL, how to
log in, where the page inventory lives — is in the repo. This module is the ONE
reader of that file.

A missing or unusable config is NOT swallowed and NOT guessed. `load_config`
returns a structured `{"error": "needs_config", "missing": [...],
"questions": [...]}` which the caller turns into a question for the operator
(plan decision: "Server ne radi, login pukne, nema configa -> odmah pitanje
operateru, nikad tiho preskakanje"). Guessing here would mean screenshotting 232
login screens and calling it a baseline.

Two closed registries, never free strings resolved at run time:
  AUTH_KINDS   how a session is obtained
  PAGE_SOURCES where the page inventory comes from
An unknown value is a config error with a question, never an import.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# THE PROLOGUE (visual/__init__.py): run as a script, `sys.path[0]` is THIS
# directory, so the engine's module names answer for the app's - a repo asking
# for its own `config` package got `visual/config.py`. Swap that entry for the
# package's parent and import relatively. Copy this block verbatim into any new
# entry point (every module here with a `__main__` block carries it).
if __package__ in (None, ""):                                   # run as a script
    _DIR = os.path.dirname(os.path.abspath(__file__))
    _NC = os.path.normcase(_DIR)
    sys.path[:] = [p for p in sys.path
                   if p and os.path.normcase(os.path.abspath(p)) != _NC]
    sys.path.insert(0, os.path.dirname(_DIR))
    __package__ = os.path.basename(_DIR)

CONFIG_REL = ".claude/visual-diff.json"

#: How capture.py authenticates. A closed set: the value selects code, so it is a
#: registry key, never something handed to importlib.
AUTH_KINDS = ("none", "driver")

#: Where pages.py reads the inventory from.
PAGE_SOURCES = ("razvojna-mapa", "list")

#: Dynamic text that must never drive a diff. These are excluded from the DOM
#: signature and the geometry used by the SWEEP — they are NOT hidden in the
#: customer-facing PNG, which always shows the real page.
DEFAULT_IGNORE_SELECTORS = [
    "time",
    "[data-relative-time]",
    ".relative-time",
    ".timeago",
    ".timestamp",
    ".last-updated",
    ".server-time",
    ".notification-count",
    "#notif-count",
    ".unread-count",
    ".js-live-count",
]

DEFAULT_TEMPLATE_GLOBS = ["*/templates/**/*.html", "templates/**/*.html"]
DEFAULT_STATIC_GLOBS = ["static/**/*.css", "static/**/*.js"]
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_MAX_STATES = 6
DEFAULT_MAX_PAGES = 20


def config_path(repo_root) -> Path:
    return Path(repo_root) / CONFIG_REL


def _q(key, question, example=""):
    return {"key": key, "question": question, "example": example}


def _needs(path, missing, questions) -> dict:
    return {"error": "needs_config", "path": str(path),
            "missing": list(missing), "questions": list(questions)}


def is_error(cfg) -> bool:
    """True when `load_config` returned a structured error instead of a config."""
    return isinstance(cfg, dict) and bool(cfg.get("error"))


def load_config(repo_root) -> dict:
    """Read + validate `<repo>/.claude/visual-diff.json`.

    Returns the normalised config (defaults filled in, `_repo`/`_path` added), or
    `{"error": "needs_config", "missing": [...], "questions": [...]}` when the
    file is absent, unparseable, or missing something capture cannot invent.
    Never raises for a bad file — a broken config is an answerable question, not
    a traceback.
    """
    repo = Path(repo_root)
    p = config_path(repo)
    if not p.exists():
        return _needs(p, ["file"], [
            _q("base_url", "Na kojoj adresi radi ova aplikacija u dev-u?",
               "http://acme.lvh.me:8020"),
            _q("auth", "Kako se agent loguje (driver skripta, ili nema logina)?",
               ".claude/skills/run-.../driver.py: mint_session"),
            _q("pages", "Gde je spisak stranica (razvojna-mapa _data, ili rucna lista)?",
               "razvojna-mapa/_data"),
        ])
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _needs(p, ["parse"], [
            _q("parse", "Config se ne moze procitati (%s). Popraviti ili obrisati?"
               % type(exc).__name__, str(exc)[:200]),
        ])
    if not isinstance(raw, dict):
        return _needs(p, ["parse"], [
            _q("parse", "Config nije JSON objekat.", type(raw).__name__)])

    missing, questions = [], []

    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        missing.append("base_url")
        questions.append(_q("base_url", "Na kojoj adresi radi aplikacija u dev-u?",
                            "http://acme.lvh.me:8020"))

    auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    kind = str(auth.get("kind") or "").strip()
    if kind not in AUTH_KINDS:
        missing.append("auth.kind")
        questions.append(_q("auth.kind",
                            "Kako se pravi prijavljena sesija? Dozvoljeno: %s"
                            % ", ".join(AUTH_KINDS), "driver"))
    elif kind == "driver":
        mod = str(auth.get("module") or "").strip()
        func = str(auth.get("func") or "").strip()
        if not mod or not (repo / mod).exists():
            missing.append("auth.module")
            questions.append(_q("auth.module",
                                "Putanja do driver skripte (u odnosu na koren repoa) ne postoji.",
                                mod or ".claude/skills/run-<app>/driver.py"))
        if not func:
            missing.append("auth.func")
            questions.append(_q("auth.func",
                                "Koja funkcija u driveru pravi sesiju?", "mint_session"))

    pages = raw.get("pages") if isinstance(raw.get("pages"), dict) else {}
    pkind = str(pages.get("kind") or "").strip()
    if pkind not in PAGE_SOURCES:
        missing.append("pages.kind")
        questions.append(_q("pages.kind",
                            "Odakle spisak stranica? Dozvoljeno: %s" % ", ".join(PAGE_SOURCES),
                            "razvojna-mapa"))
    elif pkind == "razvojna-mapa":
        ppath = str(pages.get("path") or "").strip()
        if not ppath or not (repo / ppath).is_dir():
            missing.append("pages.path")
            questions.append(_q("pages.path",
                                "Direktorijum sa JSON inventarom stranica ne postoji.",
                                ppath or "razvojna-mapa/_data"))
    elif pkind == "list":
        if not isinstance(pages.get("pages"), list) or not pages.get("pages"):
            missing.append("pages.pages")
            questions.append(_q("pages.pages",
                                "Rucna lista stranica je prazna.",
                                '[{"page_id":"home","url":"/","title":"Pocetna"}]'))

    if missing:
        return _needs(p, missing, questions)

    states = raw.get("states") if isinstance(raw.get("states"), dict) else {}
    manual = states.get("manual") if isinstance(states.get("manual"), dict) else {}
    server = raw.get("server") if isinstance(raw.get("server"), dict) else {}
    viewport = raw.get("viewport") if isinstance(raw.get("viewport"), dict) else {}

    cfg = {
        "_repo": str(repo),
        "_path": str(p),
        "base_url": base_url,
        "tenant": str(raw.get("tenant") or ""),
        "role": str(raw.get("role") or ""),
        "auth": {
            "kind": kind,
            "module": str(auth.get("module") or ""),
            "func": str(auth.get("func") or ""),
            "args": auth.get("args") if isinstance(auth.get("args"), dict) else {},
            "cookie": {
                "name": str((auth.get("cookie") or {}).get("name") or "sessionid"),
                "domain": str((auth.get("cookie") or {}).get("domain") or ""),
                "path": str((auth.get("cookie") or {}).get("path") or "/"),
            },
        },
        "server": {
            "health": str(server.get("health") or base_url + "/"),
            "start": list(server.get("start") or []),
        },
        "pages": {
            "kind": pkind,
            "path": str(pages.get("path") or ""),
            "pages": list(pages.get("pages") or []),
        },
        # A one-word CSS class can legitimately reach a hundred screens; capturing
        # all of them at commit time costs minutes. The overflow is REPORTED, not
        # dropped (affected.from_diff -> "overflow").
        "max_pages": max(1, int(raw.get("max_pages") or DEFAULT_MAX_PAGES)),
        "sample_ids": raw.get("sample_ids") if isinstance(raw.get("sample_ids"), dict) else {},
        "sample_ids_by_page": (raw.get("sample_ids_by_page")
                               if isinstance(raw.get("sample_ids_by_page"), dict) else {}),
        "template_globs": list(raw.get("template_globs") or DEFAULT_TEMPLATE_GLOBS),
        "static_globs": list(raw.get("static_globs") or DEFAULT_STATIC_GLOBS),
        "ignore_selectors": list(raw.get("ignore_selectors")
                                 if raw.get("ignore_selectors") is not None
                                 else DEFAULT_IGNORE_SELECTORS),
        "states": {
            "auto": bool(states.get("auto", True)),
            "max_per_page": max(1, int(states.get("max_per_page") or DEFAULT_MAX_STATES)),
            "manual": manual,
        },
        "viewport": {
            "width": int(viewport.get("width") or DEFAULT_VIEWPORT["width"]),
            "height": int(viewport.get("height") or DEFAULT_VIEWPORT["height"]),
        },
    }
    return cfg


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Validate a repo's visual-diff config.")
    ap.add_argument("--repo", required=True)
    a = ap.parse_args()
    cfg = load_config(a.repo)
    print(json.dumps(cfg, ensure_ascii=True, indent=1))
    return 2 if is_error(cfg) else 0


if __name__ == "__main__":
    raise SystemExit(main())
