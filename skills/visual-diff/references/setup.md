# Engine layout, repo config and the driver

Extends `visual-diff`. Read when installing it on a project, changing the config, or pointing it at a server.

**A driver that logs in as a REAL production user is refused.**

## The engine is a package — do not flatten it

`scripts/visual/` loads the repo's auth driver **in its own process**, so its
module names and its `sys.path` are the app's while `django.setup()` runs. It
therefore imports its own modules relatively (`from . import config as vconfig`)
and everything else by path under an alias (`isolate.load_module`). A flat
`import config` here is not a style choice: it made `sys.modules['config']` the
engine's module and killed every repo with a `config/settings/` package, while
reporting `auth_failed`. `visual/__init__.py` carries the rule and the prologue
that a new entry point must copy.

## The repo config

`<repo>/.claude/visual-diff.json`, git-tracked. Keys: `base_url`, `tenant`,
`role`, `auth` (`kind: none|driver`, `module`, `func`, `cookie`), `server`
(`health`, `start`), `pages` (`kind: razvojna-mapa|list`, `path` / `pages`),
`template_globs`, `static_globs`, `ignore_selectors`, `states`
(`auto`, `max_per_page`, `manual`), `max_pages`, `sample_ids`,
`sample_ids_by_page`, `viewport`.

`auth.kind` and `pages.kind` are **registry keys**, validated against a closed
set. A stored string never selects a class to import.

A page whose URL still holds a parameter (`/audits/<pk>/`) cannot be fetched —
113 of acme-audit's 232 are like that. They stay in the inventory carrying
`skipped_reason`, so what was not captured is visible. Give `sample_ids` a real
id to bring one into scope.

## A driver that logs in as a REAL production user is refused — serve a seeded local database instead

**What broke** — Ticketing-System2's dev settings point at the production MySQL.
The operator chose "dev server on the production DB, driver mints a session for
the real channel owner". The auto-mode classifier blocked writing that driver AND
the config that referenced it, twice, and the operator's own constraint ("ne
kreiraj ništa novo i ne briši ništa") was already at odds with the session row a
login writes. The visual pass was stuck for an hour (#87083, 2026-08-28).

**Why** — a no-password login for a real account against production data is the
one thing the guard exists to refuse, and it refuses it by content, not by tool.
Re-wording the file does not change what it does.

**The rule** — for a project whose development settings reach production, the
screenshot server is a LOCAL sqlite server: a `settings/visual.py` that inherits
the TEST settings (so it carries every production guard), a `.claude/visual/
seed.py` that migrates and creates the people and rows the screens need, and a
driver that mints sessions only for those seed users and refuses any non-sqlite
engine. Offer that first; the production-DB route is not a default and needs the
operator's explicit choice — and even then the driver may not be writable.
