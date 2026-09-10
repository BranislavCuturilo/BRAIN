#!/usr/bin/env python3
"""The SENT log - everything this brain wrote to the helpdesk, in the order it
went out: `<root>/sent_log/`, one append-only `<device>.jsonl` per machine.

A helpdesk comment cannot be recalled and a close fires notifications, so the
question "what did we actually send on this ticket, and who approved it" must be
answerable months later from the tracked store, not from a terminal scrollback.
That is what this file is; the HUD's "Poslato" button reads it.

Per device and event-shaped for the same reasons as `worklog.py` - see that
module's docstring; the mechanics live in `store.append_jsonl` /
`store.read_jsonl_dir` so the two logs cannot drift apart.

Entry (every field always present):
    {"at", "module", "ticket", "kind": comment|close|estimate,
     "preview": first 120 chars, "approved_by", "http": ok|fail|unknown,
     "error": str|None, "pre_image": any|None, "device"}

`pre_image` is the value the write REPLACED (the old estimate, read fresh from
the helpdesk) - without it a later "why is this ticket estimated at 3 h" has no
answer. `preview` is deliberately a prefix: the full body already lives on the
ticket, and a log that carries whole comment bodies stops being skimmable.

The ONLY writer is `writeback.py` (the single helpdesk door). Nothing here calls
the network.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import store  # noqa: E402

DIR_NAME = "sent_log"
#: The write kinds, in the order the UI shows them. ONE definition - the
#: server's per-ticket counters are built from `zero()`, never from a literal.
#: "create"/"edit" (F10, voice) have no per-ticket counter meaning for "create"
#: (the ticket does not exist until the send lands) - `counts()` still keys on
#: (module, ticket) and simply never sees a row for a ticket id that failed.
KINDS = ("comment", "close", "estimate", "create", "edit")
PREVIEW_CHARS = 120

device_id = store.device_id             # same machine name as the worklog


def dir_path(root) -> Path:
    return Path(root) / DIR_NAME


def path(root, device=None) -> Path:
    return dir_path(root) / f"{device or device_id()}.jsonl"


def zero() -> dict:
    """A fresh {kind: 0} counter. Callers get their own dict - the row builder
    hands one to every ticket that has never been written to."""
    return {k: 0 for k in KINDS}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _preview(value) -> str:
    text = " ".join(str(value or "").split())
    return text[:PREVIEW_CHARS]


HTTP_VALUES = ("ok", "fail", "unknown")


def _http_value(v) -> str:
    v = str(v or "").lower()
    return v if v in HTTP_VALUES else "fail"


def append(root, entry: dict) -> bool:
    """Append one send record. Returns True/False and NEVER raises: the write to
    the helpdesk has already happened by the time this is called, so a logging
    failure must not turn a successful send into an exception at the call site.

    `http` is one of "ok" | "fail" | "unknown"; anything else -> "fail", the
    safe direction: `counts` only counts successes, so a mislabelled row
    understates rather than claims a send that may not have landed. "unknown" is
    the writeback module's AMBIGUOUS case (transport error after the request
    left: the helpdesk may have processed it) and must stay distinguishable from
    a definite "fail" (a real HTTP error status).
    """
    if not isinstance(entry, dict):
        return False
    e = {
        "at": str(entry.get("at") or _now()),
        "module": str(entry.get("module") or ""),
        "ticket": str(entry.get("ticket") or ""),
        "kind": str(entry.get("kind") or ""),
        "preview": _preview(entry.get("preview")),
        "approved_by": str(entry.get("approved_by") or ""),
        "http": _http_value(entry.get("http")),
        "error": str(entry.get("error")) if entry.get("error") else None,
        "pre_image": entry.get("pre_image"),
        "device": str(entry.get("device") or device_id()),
    }
    fp = path(root, e["device"])
    if store.append_jsonl(fp, e):
        return True
    # A pre_image straight off an API can be anything (Decimal, datetime); it is
    # the only free-form field here, so retry once with it stringified rather
    # than lose the record of an irreversible send.
    if e["pre_image"] is not None:
        e["pre_image"] = str(e["pre_image"])
        return store.append_jsonl(fp, e)
    return False


def read(root, limit: int = 200, module=None, ticket=None) -> list:
    """Newest first, optionally narrowed to one module or one ticket. Never
    raises: a missing directory or a corrupt line reads as fewer rows."""
    out = store.read_jsonl_dir(dir_path(root))
    if module:
        out = [e for e in out if str(e.get("module") or "") == str(module)]
    if ticket:
        out = [e for e in out if str(e.get("ticket") or "") == str(ticket)]
    out.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    n = max(int(limit or 0), 0)
    return out[:n] if n else out


def counts(root) -> dict:
    """{(module, ticket): {"comment": n, "close": n, "estimate": n}} over the
    whole log, counting ONLY `http == "ok"` - the badge means "this went out",
    and a failed attempt did not.

    One pass over every device file; the caller computes it ONCE per request and
    looks each ticket up, never once per ticket.
    """
    out: dict = {}
    for e in store.read_jsonl_dir(dir_path(root)):
        kind = str(e.get("kind") or "")
        if kind not in KINDS or str(e.get("http") or "") != "ok":
            continue
        key = (str(e.get("module") or ""), str(e.get("ticket") or ""))
        if not key[0] or not key[1]:
            continue
        out.setdefault(key, zero())[kind] += 1
    return out
