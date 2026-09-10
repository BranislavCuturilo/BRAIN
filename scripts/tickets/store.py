#!/usr/bin/env python3
"""tiketi.json store — the ONE read/modify/write protocol for the per-project
helpdesk queue file, shared by the live view (agent_view/server.py) and the
deterministic sync (sync.py).

Both a browser triage write and a `sync` run mutate the same
`<repo>/.claude/tiketi.json`, in SEPARATE PROCESSES. A threading.Lock cannot
coordinate across processes, so every writer takes the cross-process file lock
here, re-reads under it, and writes atomically. Keeping the jail, the lock and
the serialisation in one module is what stops a triage write and a sync from
silently clobbering each other (the race flagged on the Phase-2 write path).

It also owns the second store mechanic, at the bottom of the file: the
append-only per-device JSONL used by `worklog/` and `sent_log/`. Same reason —
one implementation of "write a line into the shared, git-tracked store", so the
two logs cannot drift apart.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
from contextlib import contextmanager
from pathlib import Path

_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")


def default_store() -> Path:
    """The central ticket store — one `<MODULE>.json` per module, in the brain,
    not scattered across each repo's `.claude/`. `scripts/tickets/store.py` →
    parents: tickets, scripts, brain."""
    return Path(__file__).resolve().parents[2] / "tickets_store"


# --------------------------------------------------------------------------- #
#  Repo paths — the store is TRACKED and shared across machines, but the
#  projects live at a different root on each one (C:/projects on the laptop,
#  E:/POSAO on the desktop). So a module's repo is recorded as a name RELATIVE
#  to PROJECTS_ROOT ("popis", "NSGAS-Telefon-app/ODS"), and resolved here at
#  read time. Absolute values still work (legacy files, one-off overrides).
# --------------------------------------------------------------------------- #
LEGACY_PROJECTS_ROOT = "C:/projects"      # where the store was born; the default
LOCAL_OVERRIDES = "modules.local.json"  # gitignored per-machine {module: repo}
NON_MODULE_FILES = ("modules.json", LOCAL_OVERRIDES, "ai_log.json",
                    "profiles.json")   # catalogs + the AI action log + the per-creator
                                        # profile store (F4) — none are ticket files


def is_module_file(fp) -> bool:
    """True for `<MODULE>.json` — not the catalogs and not dotfiles."""
    name = Path(fp).name
    return name.endswith(".json") and name not in NON_MODULE_FILES and not name.startswith(".")


def projects_root() -> Path:
    """`PROJECTS_ROOT` from the environment, else the legacy default — so a
    machine that never set the variable behaves exactly as before."""
    return Path(os.environ.get("PROJECTS_ROOT") or LEGACY_PROJECTS_ROOT)


def repo_path(value) -> str:
    """Resolve a recorded repo value to an absolute path string.

    * empty        → ""  (module not mapped)
    * absolute     → as-is if it exists; if it does NOT exist but sits under the
                     legacy root, re-rooted under PROJECTS_ROOT (a store written on
                     the other machine keeps working here)
    * relative     → PROJECTS_ROOT / value
    """
    v = str(value or "").strip()
    if not v:
        return ""
    p = Path(v)
    if p.is_absolute():
        if p.exists():
            return str(p)
        try:
            rel = p.resolve().relative_to(Path(LEGACY_PROJECTS_ROOT).resolve())
        except (ValueError, OSError):
            return str(p)
        return str(projects_root() / rel)
    return str(projects_root() / p)


def repo_name(value) -> str:
    """The inverse for WRITING: strip PROJECTS_ROOT (or the legacy root) so the
    tracked file carries a machine-independent name. Anything else is kept
    verbatim."""
    v = str(value or "").strip().replace("\\", "/")
    if not v:
        return ""
    p = Path(v)
    if not p.is_absolute():
        return v
    for root in (projects_root(), Path(LEGACY_PROJECTS_ROOT)):
        try:
            rel = p.resolve().relative_to(root.resolve())
            return rel.as_posix()
        except (ValueError, OSError):
            continue
    return v


def local_overrides(root) -> dict:
    """`<root>/modules.local.json` → {module: repo} — per-machine, gitignored,
    for the one project that has a different folder NAME on this machine (not
    just a different root). Missing/unreadable → {}."""
    try:
        d = json.loads((Path(root) / LOCAL_OVERRIDES).read_text(encoding="utf-8"))
        m = d.get("modules") if isinstance(d, dict) else None
        return {str(k): str(v) for k, v in (m or {}).items()} if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def module_repo(root, module: str, recorded="") -> str:
    """The ONE answer to "where is this module's repo on THIS machine":
    local override → the recorded value (project.repo) → resolved via repo_path."""
    ov = local_overrides(root).get(module)
    return repo_path(ov if ov else recorded)


def resolve(root, name: str):
    """A module's ticket file Path (`<root>/<name>.json`) from its KEY (the
    module name — never a client path). The name must be a plain slug and the
    resolved file must stay under the root. Returns the Path (which may not exist
    yet — sync creates it), or None if the name does not validate or escapes the
    jail. Callers that require an existing file (a triage write) check
    `.is_file()` themselves."""
    root = Path(root).resolve()
    if not _SLUG.match(name or "") or name in (".", ".."):
        return None
    if not is_module_file(f"{name}.json"):       # the catalogs are not modules
        return None
    fp = (root / f"{name}.json").resolve()
    if not fp.is_relative_to(root):
        return None
    return fp


@contextmanager
def filelock(target, timeout: float = 10.0, stale: float = 60.0):
    """Cross-process advisory lock via an atomically-created sibling `.lock`.
    A lock older than `stale` seconds is reclaimed (a crashed writer leaves one
    behind). Raises TimeoutError if it cannot be taken within `timeout`.

    Intra-process, pair this with a threading.Lock so co-running threads do not
    busy-wait on the file; across processes, this file is the only coordinator."""
    lock = Path(target).with_name(Path(target).name + ".lock")
    start = time.time()
    fd = None
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            # Windows raises PermissionError (sharing violation), NOT
            # FileExistsError, when another holder has the lock open O_EXCL —
            # both mean "locked, retry", so catch both or the caller crashes.
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue                       # released between attempts
            if age > stale:
                try:
                    lock.unlink()              # reclaim a crashed writer's lock
                except FileNotFoundError:
                    pass
                continue
            if time.time() - start > timeout:
                raise TimeoutError(f"tiketi.json is locked: {lock}")
            time.sleep(0.05)
    try:
        try:
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            pass
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


# The live view writes a rich `triage.state` (the six states the user asked for);
# the CLI board and the daily-routine skill read the coarse legacy `status`. One
# mapping, here, keeps the two from disagreeing about the same ticket.
_TRIAGE_TO_STATUS = {
    "working_today": "active",
    "ignore_today": "skip_session",
    "ignore_indefinitely": "skip_consider",
    "solved_manually": "done",
    "nonsense": "done",
    "done": "done",
    "": "active",                # cleared in the view → back in the queue
}


def queue_status(ticket: dict) -> str:
    """The coarse queue status (`active|skip_session|skip_consider|done`) the
    board and the skill read. The view's rich `triage.state` wins when present;
    otherwise the legacy top-level `status`."""
    tri = ticket.get("triage") if isinstance(ticket, dict) else None
    if isinstance(tri, dict) and tri.get("state") is not None:
        return _TRIAGE_TO_STATUS.get(tri.get("state"), "active")
    return (ticket or {}).get("status", "active")


def ticket_rating(ticket: dict) -> dict:
    """The rating summary read off ONE ticket's `helpdesk` block - the ONE
    place `rating`/`ratings`/`rating_avg` are read, so `/api/tickets`
    (agent_view/server.py) and the Procene summary (`estimate.summary`) can
    never disagree about what a ticket's rating is.

    F9: a ticket carries every rater who scored it (`helpdesk.ratings`, []
    when none) plus the mean (`helpdesk.rating_avg`). A ticket synced before
    F9 - or pulled through a helpdesk that has not shipped it yet - carries
    only the old single `helpdesk.rating`/`rating_comment`/`rated_at`;
    tolerated here by folding that into a one-item `items` list.

    Returns `{"value", "avg", "n", "items"}` - every key always present.
    `value` mirrors `adapters.acme_helpdesk.to_record`'s own choice: the
    customer's own rating when they rated, else the average, else the legacy
    single value - never a KeyError on a pre-F9 ticket.
    """
    hd = ticket.get("helpdesk") if isinstance(ticket, dict) and isinstance(ticket.get("helpdesk"), dict) else {}
    raw = hd.get("ratings")
    items = []
    if isinstance(raw, list):
        for r in raw:
            if not isinstance(r, dict):
                continue
            rating = r.get("rating")
            items.append({
                "role": str(r.get("role") or ""),
                "rater": str(r.get("rater") or ""),
                "rating": rating if isinstance(rating, (int, float)) and not isinstance(rating, bool) else None,
                "comment": str(r.get("comment") or ""),
                "at": r.get("rated_at") or r.get("at") or None,
            })
    elif isinstance(hd.get("rating"), (int, float)) and not isinstance(hd.get("rating"), bool):
        # pre-F9: no `ratings` list at all - the one legacy field IS the customer's.
        items = [{"role": "customer", "rater": "", "rating": hd["rating"],
                 "comment": str(hd.get("rating_comment") or ""), "at": hd.get("rated_at") or None}]
    avg = hd.get("rating_avg")
    avg = float(avg) if isinstance(avg, (int, float)) and not isinstance(avg, bool) else None
    customer = next((it["rating"] for it in items
                     if it["role"] == "customer" and it["rating"] is not None), None)
    value = customer if customer is not None else avg
    return {"value": value, "avg": avg, "n": len(items), "items": items}


def load(path):
    """Parse a tiketi.json, or None if it is missing or unreadable. Never raises
    — a bad file must not take the caller down."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def atomic_write_json(path, data) -> None:
    """Serialise + replace atomically (temp file then os.replace, atomic on
    Windows). ONE serialisation for every writer, so a sync write and a triage
    write never differ only in whitespace and churn the git diff. `indent=1`
    matches what the Phase-2 write path has always produced."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
#  Append-only per-device logs (`worklog/`, `sent_log/`)
#
#  The store is a git-tracked directory shared by two machines. A single shared
#  log file would be appended to on both and conflict on every pull, so each log
#  is a DIRECTORY of `<device>.jsonl` files — a machine only ever writes its own
#  — merged on read. The two mechanics both logs need (write one line, read a
#  whole directory back) live here, next to the lock and the atomic write, so
#  worklog.py and sent_log.py stay two shapes over ONE mechanism.
# --------------------------------------------------------------------------- #
_DEVICE_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def device_id() -> str:
    """This machine's name, sanitised — it becomes a FILE NAME in the tracked
    store, so anything a path cannot carry is folded to `-`. Falls back to
    "device" when the host name is empty or sanitises away entirely."""
    try:
        raw = socket.gethostname()
    except OSError:
        raw = ""
    slug = _DEVICE_UNSAFE.sub("-", str(raw or "").strip()).strip("-.")
    return slug[:64].lower() or "device"


def append_jsonl(fp, obj) -> bool:
    """Append ONE object as a single line to `fp`, creating the parent dir.
    Returns True/False and NEVER raises: a lost log line must not sink the run
    that produced it (same contract as ai_log.append).

    The file lock is taken even though only this machine writes this file —
    the server and a CLI are two PROCESSES on that one machine, and a torn line
    would be unreadable forever (the readers skip it, so the record is lost)."""
    fp = Path(fp)
    try:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        fp.parent.mkdir(parents=True, exist_ok=True)
        with filelock(fp, timeout=5.0):
            with open(fp, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
        return True
    except Exception:            # noqa: BLE001 — see the contract above
        return False


def read_jsonl_dir(directory) -> list:
    """Every `*.jsonl` in `directory`, parsed, in file order. Tolerates
    corruption: a truncated or half-written line (a machine that died
    mid-append) is skipped, never fatal — one bad line must not hide a month of
    records. `device` defaults to the file's name, so a line written before that
    field existed still says where it came from."""
    out = []
    try:
        files = sorted(Path(directory).glob("*.jsonl"))
    except OSError:
        return out
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            obj.setdefault("device", fp.stem)
            out.append(obj)
    return out
