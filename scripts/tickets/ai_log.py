#!/usr/bin/env python3
"""The AI action log — what every AI pass over the ticket queue did, in the order
the operator used it: `tickets_store/ai_log.json`.

Why: Rescan (Gemini triage), "Analiziraj + predlog upita", "Objedini u 1 upit"
and "Reši" all spend AI calls and write results into tickets, but nothing showed
the operator WHAT ran, over WHICH tickets, and with what outcome — the Rescan
result in particular was invisible. This file is that trail: one entry per run,
newest first when read, tracked in git like the rest of the store so it is the
same on every machine.

Entry shape (all optional beyond `at`/`action`):
    {"at": "...", "action": "analyze|merge|solve|rescan|triage", "by": "gemini|claude",
     "modules": [...], "ticket_ids": [...], "titles": {id: title}, "cached_ids": [...],
     "failed_ids": [...], "summary": "one line", "counts": {...}, "extra": {...}}

Same lock discipline as the module files (store.filelock + atomic write); capped
at MAX_ENTRIES so it never grows without bound. Never raises on read; a failed
append is reported to the caller as False (a log miss must not sink the run).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import store  # noqa: E402

FILE_NAME = "ai_log.json"
MAX_ENTRIES = 500


def path(root) -> Path:
    return Path(root) / FILE_NAME


def read(root, limit: int = 100) -> list:
    """Newest first. Never raises: a missing/corrupt file reads as []."""
    fp = path(root)
    d = store.load(fp) if fp.exists() else None
    entries = d.get("entries") if isinstance(d, dict) else None
    if not isinstance(entries, list):
        return []
    out = [e for e in entries if isinstance(e, dict)]
    out.reverse()
    return out[:max(int(limit or 0), 0)] if limit else out


def append(root, entry: dict) -> bool:
    """Append one entry (stamped `at` if missing) under the store lock. Returns
    True on success, False on any failure (never raises)."""
    if not isinstance(entry, dict):
        return False
    fp = path(root)
    e = dict(entry)
    e.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        with store.filelock(fp):
            d = store.load(fp) if fp.exists() else None
            if not isinstance(d, dict):
                d = {"entries": []}
            entries = d.get("entries")
            if not isinstance(entries, list):
                entries = []
            entries.append(e)
            if len(entries) > MAX_ENTRIES:
                entries = entries[-MAX_ENTRIES:]
            d["entries"] = entries
            store.atomic_write_json(fp, d)
        return True
    except Exception:            # noqa: BLE001 — a log miss must not sink the run
        return False
