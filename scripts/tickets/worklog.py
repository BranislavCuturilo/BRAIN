#!/usr/bin/env python3
"""The work log - how long a ticket ACTUALLY took, so estimates can be
calibrated against reality instead of against a feeling: `<root>/worklog/`,
one append-only `<device>.jsonl` per machine.

WHY PER DEVICE. The store is a git-tracked directory used from two machines. A
single shared `worklog.jsonl` would be appended to on both and conflict on every
pull - a line-level conflict in a file nobody can merge by hand, over data that
is pure history. One file per device never conflicts, because a machine only
ever writes its own; `read_events` merges them.

WHY EVENTS, NOT MUTABLE ROWS. An interval is a `start` line, zero or more
`touch` heartbeats and, later, an `end` line - never one row created and then
edited to carry its end. Editing means rewriting the file, which re-introduces
exactly the merge conflict per-device files removed, and loses the record when
the process dies between the read and the write. Appending is one small write; the pairing happens on read. An
interval whose `end` never arrived stays OPEN, which is the truth rather than a
guess.

WHY A HEARTBEAT. A Claude run that dies, or whose Stop hook never fires, would
otherwise leave an interval open forever - and closing it at "now" whenever
somebody noticed would book hours nobody worked. `touch` is one cheap line per
minute per run; `reap` closes an abandoned interval AT ITS LAST HEARTBEAT, and
caps any interval that outlives CAP_HOURS.

Measured time NEVER goes to the helpdesk (plan decision 2026-08-18) - it is
local calibration data for `build_estimates.py` and the estimate pass.

Timestamps are local wall clock (`%Y-%m-%dT%H:%M:%S`, the format the rest of the
store uses). An interval spanning a DST change, or two machines with skewed
clocks, is off by that skew; this is calibration data, not billing.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import store  # noqa: E402

DIR_NAME = "worklog"
KINDS = ("claude", "hud", "manual")     # who opened the interval
EVENTS = ("start", "touch", "end")      # the three line shapes, in pairing order

#: Auto-close thresholds. An interval with no heartbeat for IDLE_MINUTES was
#: abandoned (the operator moved on and no Stop hook arrived); one open longer
#: than CAP_HOURS is capped whatever the heartbeat says, because a machine left
#: running overnight would otherwise book a 14-hour "session". Both produce
#: `auto_closed` ends, which `hours_by_ticket` leaves out by default.
IDLE_MINUTES = 30
CAP_HOURS = 4

#: ONE definition of the per-device file name, in the store (sent_log uses the
#: same one) - two logs naming the same machine differently would split its
#: history in half.
device_id = store.device_id


def dir_path(root) -> Path:
    return Path(root) / DIR_NAME


def path(root, device=None) -> Path:
    """This device's log file (or another device's, for tests)."""
    return dir_path(root) / f"{device or device_id()}.jsonl"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _pairs(items) -> list:
    """[{module, ticket}, ...] -> [(module, ticket), ...], deduplicated, order
    preserved. Anything missing either half is dropped: an interval that cannot
    name its ticket cannot be priced."""
    out = []
    for it in items or []:
        if isinstance(it, dict):
            mod = str(it.get("module") or "").strip()
            tid = str(it.get("ticket") or "").strip()
        elif isinstance(it, (tuple, list)) and len(it) == 2:
            mod, tid = str(it[0] or "").strip(), str(it[1] or "").strip()
        else:
            continue
        if mod and tid and (mod, tid) not in out:
            out.append((mod, tid))
    return out


def _share(value) -> float:
    """A missing or nonsensical share counts as a whole session - silently
    pricing a measured interval at 0 would bias the calibration downwards with
    nothing to show for it."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    return v if v > 0 else 1.0


def append_event(root, ev: dict) -> bool:
    """Append ONE event line. `at` and `device` are stamped when absent. Returns
    False on any failure (never raises) - see store.append_jsonl."""
    if not isinstance(ev, dict):
        return False
    e = dict(ev)
    e.setdefault("at", _now())
    e.setdefault("device", device_id())
    return store.append_jsonl(path(root, e.get("device")), e)


def start(root, work_id, items, kind="claude", share=None, session=None) -> list:
    """Open one interval per (module, ticket) in `items`. Returns the events
    written.

    `share` splits ONE session across the tickets it covered: a merged prompt
    over three tickets gives each a third of the elapsed time (plan decision -
    "objedinjen upit za N tiketa: trajanje / N"). Pass it explicitly only when
    the caller genuinely knows a different split.
    """
    pairs = _pairs(items)
    if not pairs:
        return []
    sh = _share(share) if share is not None else 1.0 / len(pairs)
    at, dev = _now(), device_id()
    out = []
    for module, ticket in pairs:
        ev = {"ev": "start", "work_id": str(work_id), "module": module,
              "ticket": ticket, "at": at, "share": sh,
              "kind": str(kind or "manual"), "device": dev}
        if session:
            ev["session"] = str(session)      # prompt-detected work: which Claude session
        if append_event(root, ev):
            out.append(ev)
    return out


def touch(root, work_id, at=None) -> bool:
    """Record that `work_id` is STILL ALIVE at `at` (default: now).

    A touch carries no module/ticket: it is one heartbeat for the whole work,
    and every interval the work opened reads it. That is what makes the
    heartbeat cheap enough to write from a hook path - one line per minute per
    run, whatever the run is doing.

    Its only consumer is `reap`: without a heartbeat, an abandoned interval
    could only be closed at "now", inventing hours nobody worked. With one, the
    interval ends where the work actually stopped.
    """
    wid = str(work_id or "").strip()
    if not wid:
        return False
    return append_event(root, {"ev": "touch", "work_id": wid,
                               "at": str(at) if at else _now()})


def end(root, work_id, auto_closed=False, tickets=None, at=None) -> list:
    """Close the open intervals of `work_id` - all of them, or only the given
    (module, ticket) pairs. Returns the events written.

    The end event repeats the start's `share` and `kind` so an interval can be
    priced from either line. An end for an interval this store has never seen
    (its start sits on a device not pulled yet) is NOT written: it would pair
    with nothing and read as an orphan forever.

    `auto_closed=True` marks an interval closed by the heuristic rather than by
    the operator - `hours_by_ticket` leaves those out by default.

    `at` ends the interval at an EXPLICIT timestamp instead of now - the last
    heartbeat for an abandoned run, or start+cap. Writing "now" there would book
    every minute between the moment the work stopped and the moment somebody
    noticed.
    """
    wid = str(work_id)
    want = set(_pairs(tickets)) if tickets else None
    at, dev = (str(at) if at else _now()), device_id()
    out = []
    for iv in open_intervals(root):
        if iv["work_id"] != wid:
            continue
        if want is not None and (iv["module"], iv["ticket"]) not in want:
            continue
        ev = {"ev": "end", "work_id": wid, "module": iv["module"],
              "ticket": iv["ticket"], "at": at, "share": iv["share"],
              "kind": iv["kind"], "device": dev, "auto_closed": bool(auto_closed)}
        if append_event(root, ev):
            out.append(ev)
    return out


def read_events(root) -> list:
    """Every device file merged, sorted by `at`. Corrupt lines are skipped
    (store.read_jsonl_dir), and so is anything that is not a start/touch/end
    event.

    Ties break in EVENTS order (start, touch, end), because the timestamp
    resolution is one second: a session shorter than that must still pair
    instead of leaving an open interval and an orphan end, and a heartbeat
    written in the same second as the start belongs to it.
    """
    out = [e for e in store.read_jsonl_dir(dir_path(root))
           if e.get("ev") in EVENTS]
    out.sort(key=lambda e: (str(e.get("at") or ""), EVENTS.index(e["ev"])))
    return out


def intervals(root) -> list:
    """Paired intervals, oldest first: {work_id, module, ticket, start,
    end|None, last_touch, share, kind, device, auto_closed}.

    Pairing is by (work_id, module, ticket), FIFO within that key, so repeated
    sessions on one ticket close in the order they opened. `device` is the
    machine that STARTED the interval (where the work happened), not whichever
    one closed it. An end with no start is dropped.

    `last_touch` is the newest heartbeat seen WHILE the interval was open, or
    its `start` when none arrived - never None, so `reap` and the HUD can treat
    it as "alive as of" without branching. A touch names only the work, so it
    lifts every interval that work opened; one that lands after an interval
    closed is ignored (the interval was no longer running).
    """
    open_by_key: dict = {}        # (work_id, module, ticket) -> FIFO of open ivs
    open_by_work: dict = {}       # work_id -> the same open ivs, for heartbeats
    out = []
    for e in read_events(root):
        wid = str(e.get("work_id") or "")
        at = str(e.get("at") or "")
        if e.get("ev") == "touch":
            for iv in open_by_work.get(wid) or []:
                if at > iv["last_touch"]:
                    iv["last_touch"] = at
            continue
        key = (wid, str(e.get("module") or ""), str(e.get("ticket") or ""))
        if e.get("ev") == "start":
            iv = {"work_id": wid, "module": key[1], "ticket": key[2],
                  "start": at, "end": None, "last_touch": at,
                  "share": _share(e.get("share")), "kind": str(e.get("kind") or ""),
                  "device": str(e.get("device") or ""), "auto_closed": False,
                  "session": str(e.get("session") or "") or None}
            open_by_key.setdefault(key, []).append(iv)
            open_by_work.setdefault(wid, []).append(iv)
            out.append(iv)
            continue
        pending = open_by_key.get(key) or []
        if not pending:
            continue
        iv = pending.pop(0)
        alive = open_by_work.get(wid) or []
        for i, other in enumerate(alive):
            if other is iv:               # identity: two intervals of one work can
                alive.pop(i)              # be equal by value, and only THIS one closed
                break
        iv["end"] = at
        iv["auto_closed"] = bool(e.get("auto_closed"))
    return out


def open_intervals(root) -> list:
    """The intervals with no end yet - what an auto-close pass acts on."""
    return [iv for iv in intervals(root) if not iv["end"]]


def _elapsed_s(start_at: str, end_at: str):
    """Seconds between two store timestamps, or None if either is unparsable."""
    try:
        a = datetime.fromisoformat(start_at)
        b = datetime.fromisoformat(end_at)
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds()


def _shift(at: str, seconds: float):
    """`at` plus `seconds`, in the store's format, or None if unparsable."""
    try:
        return (datetime.fromisoformat(at)
                + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


def reap(root, idle_minutes=IDLE_MINUTES, cap_hours=CAP_HOURS, now=None) -> list:
    """Close every open interval that can no longer be alive. Returns the end
    events written (empty when nothing was stale). Never raises.

    Two rules, both marking the end `auto_closed` so calibration ignores it:

    * **idle** - no heartbeat for `idle_minutes`. Ends at `last_touch`, the last
      moment the run was demonstrably alive, NOT at now.
    * **cap** - open longer than `cap_hours` since start, whatever the
      heartbeat says. Ends at start+cap. This is the one that catches a session
      left open overnight, where the heartbeat keeps arriving.

    Idle is checked first: when both apply, the last heartbeat is the closer
    guess of the two, and an over-long interval must never be booked longer
    than the evidence supports.

    Called from a launch (the operator moved on), the Rescan button and the
    server tick - it is idempotent, so calling it often costs one directory
    read and writes nothing.
    """
    now_s = str(now) if now else _now()
    idle_s = max(float(idle_minutes), 0.0) * 60.0
    cap_s = max(float(cap_hours), 0.0) * 3600.0
    out = []
    for iv in open_intervals(root):
        idle_for = _elapsed_s(iv["last_touch"], now_s)
        open_for = _elapsed_s(iv["start"], now_s)
        if idle_for is not None and idle_for >= idle_s:
            at = iv["last_touch"]
        elif open_for is not None and open_for >= cap_s:
            at = _shift(iv["start"], cap_s)
        else:
            continue
        if not at:
            continue
        out.extend(end(root, iv["work_id"], auto_closed=True, at=at,
                       tickets=[{"module": iv["module"], "ticket": iv["ticket"]}]))
    return out


def span_hours(ivs) -> float:
    """The WALL-CLOCK hours a set of intervals spans (earliest start -> latest
    end). One work over N tickets opens N intervals that run in PARALLEL, so
    their lengths must never be summed: a 1 h session over 3 tickets is 1 h of
    the operator's day, not 3. An interval with no end yet counts up to its last
    heartbeat, never to now. 0.0 when nothing is parsable.
    """
    starts = [iv.get("start") for iv in ivs if iv.get("start")]
    ends = [iv.get("end") or iv.get("last_touch") or iv.get("start") for iv in ivs]
    ends = [e for e in ends if e]
    if not starts or not ends:
        return 0.0
    secs = _elapsed_s(min(starts), max(ends))
    return max(secs, 0.0) / 3600.0 if secs is not None else 0.0


def hours_by_ticket(root, include_auto_closed=False) -> dict:
    """{(module, ticket): hours} over COMPLETE intervals only - see
    `hours_from_intervals`, which this is the read-from-disk shortcut for."""
    return hours_from_intervals(intervals(root), include_auto_closed)


def hours_from_intervals(ivs, include_auto_closed=False) -> dict:
    """{(module, ticket): hours} summed over COMPLETE intervals only.

    Takes intervals rather than a root so a caller that already read them (the
    HUD row builder needs both the hours and the open ones, on one request)
    prices them without a second pass over every device file. ONE implementation
    of "what did this ticket cost".

    An open interval contributes nothing - it has no length yet, and counting
    "now minus start" would make the number grow while nobody is working.

    `auto_closed` intervals are excluded by default: they were ended by a
    heuristic (an idle threshold, the 4 h cap), so their length is a guess, and
    an estimate calibrated on guesses is wrong in a way nobody can see (plan
    risk 7).
    """
    out: dict = {}
    for iv in ivs:
        if not iv["end"]:
            continue
        if iv["auto_closed"] and not include_auto_closed:
            continue
        secs = _elapsed_s(iv["start"], iv["end"])
        if secs is None or secs < 0:
            continue
        key = (iv["module"], iv["ticket"])
        out[key] = out.get(key, 0.0) + (secs / 3600.0) * iv["share"]
    return out
