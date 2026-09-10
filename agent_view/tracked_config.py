#!/usr/bin/env python3
"""The ONE reader/writer for the tracked `agent_view.config.json`.

Since F5 that file is written by three parties (the HUD server's settings
routes, mailstore's account metadata, the one-time seed script) on two machines
and pulled through git — so it needs exactly ONE loader that tells "absent"
apart from "unreadable", and exactly ONE writer that (a) never merges into `{}`
when the file is unreadable (a merge-conflicted config would otherwise be
silently rewritten WITHOUT the Gemini key and the monitor token — reviewer
2026-08-19), (b) serialises every writer on one lock, (c) uses one temp name and
one indent so two writers never rewrite each other's lines.

  path()          -> Path (env AGENT_VIEW_TRACKED_CONFIG overrides, for tests)
  load()          -> dict; {} when the file is absent; raises TrackedConfigError
                     when it exists but cannot be parsed
  load_quiet()    -> dict; {} on absent OR unreadable (for READERS that must
                     degrade quietly: monitor tab, mail accounts, load_config)
  save(patch)     -> merged write; refuses (raises) on an unreadable file
  update(mutator) -> read -> mutator(cfg) -> write, all under the lock

Secrets ride through here (the Gemini key, the monitor token — both in the
tracked file BY DECISION, private repo, one user); this module never prints or
logs the config.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "agent_view.config.json"
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 60.0


class TrackedConfigError(Exception):
    """The tracked config exists but cannot be read — refuse to write over it."""


def path(explicit=None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("AGENT_VIEW_TRACKED_CONFIG") or DEFAULT_PATH)


def load(explicit=None) -> dict:
    fp = path(explicit)
    if not fp.exists():
        return {}
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as exc:                          # noqa: BLE001
        raise TrackedConfigError(f"unreadable ({exc.__class__.__name__})") from None
    if not isinstance(d, dict):
        raise TrackedConfigError("not a JSON object")
    return d


def load_quiet(explicit=None) -> dict:
    try:
        return load(explicit)
    except TrackedConfigError:
        return {}


class _Lock:
    """A tiny cross-process lock file next to the config (mkdir-atomic on every
    OS). Not store.filelock, so this module has no dependency on the tickets
    subsystem — the config is shared by every tab."""
    def __init__(self, fp: Path):
        self.dir = fp.with_name(fp.name + ".lock")

    def __enter__(self):
        deadline = time.time() + LOCK_TIMEOUT_S
        while True:
            try:
                self.dir.mkdir()
                return self
            except FileExistsError:
                try:
                    if time.time() - self.dir.stat().st_mtime > LOCK_STALE_S:
                        self.dir.rmdir()          # a crashed writer left it
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError("tracked config is locked")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self.dir.rmdir()
        except OSError:
            pass
        return False


def _write(fp: Path, cfg: dict) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_name(fp.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, fp)


def update(mutator, explicit=None) -> dict:
    """read -> mutator(cfg) -> write, under the lock. `mutator` edits the dict
    in place (or returns a new one). Raises TrackedConfigError instead of
    writing when the current file cannot be parsed."""
    fp = path(explicit)
    with _Lock(fp):
        cfg = load(fp)                                # raises on unreadable
        out = mutator(cfg)
        cfg = out if isinstance(out, dict) else cfg
        _write(fp, cfg)
        return cfg


def save(patch: dict, explicit=None) -> dict:
    """Merge `patch` (top-level keys) into the config and write it back."""
    if not isinstance(patch, dict):
        raise ValueError("patch must be a dict")

    def _m(cfg):
        cfg.update(patch)
        return cfg
    return update(_m, explicit)
