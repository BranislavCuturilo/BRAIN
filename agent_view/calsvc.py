#!/usr/bin/env python3
"""Calendar service — the store + occurrence logic behind the Agent View calendar.

Standard library only, same shape as the sibling stores (tickets/focus):

  * ONE JSON file, ``calendar_store.json`` next to this module (gitignored, like
    focus_state.json). ``load_store`` never raises — a missing file initialises,
    a bad file degrades to an empty store rather than taking the server down.
  * ONE writer: every mutation takes ``_lock`` around load-modify-``save_store``
    (atomic tmp + ``os.replace``), the same single-writer discipline focus.py and
    scripts/tickets/store use. The HTTP layer (server.py) never touches the file.

An EVENT is a master record::

  {id, title, start (ISO datetime, or date for all-day), end (ISO|null),
   allday (bool), cat, notes, rrule (str|null), remind_min (int minutes-before-
   start to notify; 0 = no reminder), exdates:[YYYY-MM-DD],
   overrides:{<occ_date>:{…partial fields…}}, gcal_id (null — reserved Phase 4),
   source (one of SOURCES, default "manual" — who/what created the event),
   ref (str|null, <=120 chars — an external pointer the source understands,
   e.g. "INV#94313")}

``source``/``ref`` are MASTER fields, like ``remind_min`` — not per-occurrence
overridable, but carried onto every occurrence. They exist so F7 (calendar
suggestions, ``calsug.py``) can tell an auto-created event apart from a manual
one (never touched by suggestion logic) and dedupe "already accepted" against
the source system it came from.

A natural-language command (``gemini_command``) turns free text into calendar
operations via the shared Gemini door and APPLIES each through the very same
create/update/delete functions below — no validation, rrule or override logic is
reimplemented for the AI path.

The calendar renders OCCURRENCES, not masters: ``list_occurrences`` expands each
event across a [from, to] window into a flat list. Recurrence expansion is
delegated to the sibling ``rrule`` module (built in parallel) through the VERBATIM
interface ``rrule.expand(dtstart, rrule_str, win_start, win_end, exdates=None) ->
[datetime]``. It is imported LAZILY (like the mail/git backends) so this module —
and therefore the server — imports cleanly even before rrule.py lands on disk; a
missing rrule simply yields no occurrences for recurring events.

Per-occurrence edits use two mechanisms, both keyed by ``occ_date`` (the
occurrence's start date, ``YYYY-MM-DD``):

  * edit-this  → ``overrides[occ_date]`` — a partial field patch layered over the
    computed occurrence. The instance stays attached to its master.
  * delete-this → an entry in ``exdates`` — that occurrence is subtracted.

Both apply to singles AND recurring events, so there is one occurrence pipeline
and no special-casing. This keys occurrences by DATE, so it assumes at most one
occurrence per calendar day for a given event (the normal case for a personal
calendar); a sub-daily rule would collide occ_dates.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: Runtime store, gitignored like focus_state.json. A module global (not a constant
#: baked into the functions) so a test can point it at a temp file.
STORE_PATH = HERE / "calendar_store.json"

#: One writer in this process → one plain lock around every read-modify-write, the
#: same discipline as focus.py (not the tickets store's cross-process lock: nothing
#: else writes this file).
_lock = threading.Lock()

STORE_VERSION = 1
#: Bound the expansion: a window wider than this is refused, so a crafted or fat-
#: fingered range can never fan a daily rule out to an unbounded occurrence list.
MAX_WINDOW_DAYS = 400

#: The occurrence display fields an override may replace (rrule is per-master only,
#: never per-occurrence; id/exdates/overrides/gcal_id are structural).
_OVERRIDE_FIELDS = ("title", "start", "end", "allday", "cat", "notes")

# Field caps — a mutating body is untrusted; keep one row from growing unbounded.
_TITLE_MAX = 500
_CAT_MAX = 100
_NOTES_MAX = 5000
_RRULE_MAX = 1000
#: A reminder fires this many minutes BEFORE the event's start; capped so one row
#: can never carry an absurd value. 4 weeks is past any real "remind me before"
#: horizon, and 0 (the default for events saved before this field) means none.
_REMIND_MAX = 40320                             # 28 days, in minutes

#: Who/what created an event. "manual" is every event before F7 and every event
#: an operator types themselves; the rest are calsug.py suggestion sources.
SOURCES = ("manual", "ticket", "worklog", "deploy", "mail", "chat")
_REF_MAX = 120


def _coerce_remind(v):
    """Minutes-before-start as a non-negative, capped int — else 0 (no reminder).
    Deliberately LENIENT (absent / non-numeric / negative all collapse to 0), so a
    hand-edited or pre-field store never crashes a reader and the AI path never
    fails an op over an odd value; 0 is also the migration default for old events."""
    if isinstance(v, bool):                     # bool is an int subclass — not a count
        return 0
    try:
        n = int(v)                              # int/float/'30' → 30; '' / junk → error
    except (TypeError, ValueError):
        return 0
    if n < 0:
        return 0
    return min(n, _REMIND_MAX)


# --------------------------------------------------------------------------- #
#  Datetime helpers — a date or datetime ISO string in, a datetime (or formatted
#  string) out. One seam for parsing so create/update/expand agree on the shape.
# --------------------------------------------------------------------------- #
def _to_datetime(s):
    """Parse an ISO date (``YYYY-MM-DD``) or datetime into a ``datetime`` (a bare
    date becomes that day's midnight). None on anything unparseable — callers turn
    that into a 400, never a traceback."""
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()
    try:
        if len(s) == 10:                       # YYYY-MM-DD → midnight
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day)
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _fmt(dt, allday):
    """Format a datetime back to storage/wire form: a date for an all-day event,
    a full ISO datetime otherwise. None passes through (an event with no end)."""
    if dt is None:
        return None
    return dt.date().isoformat() if allday else dt.isoformat()


def _parse_day(s):
    """A plain calendar day (``YYYY-MM-DD``) as a ``date``, or None. Used for the
    window bounds and for ``occ_date`` validation — a datetime string is rejected."""
    if not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s.strip())
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
#  Store — single JSON file, atomic single-writer, tolerant read.
# --------------------------------------------------------------------------- #
def _default_store() -> dict:
    return {"version": STORE_VERSION, "events": []}


def _normalize_event(e: dict) -> dict:
    """Coerce one stored event to the whole shape, so a hand-edited or older file
    never crashes a reader with a missing/mistyped field."""
    return {
        "id": str(e.get("id") or uuid.uuid4().hex[:12]),
        "title": str(e.get("title") or ""),
        "start": e["start"] if isinstance(e.get("start"), str) else "",
        "end": e["end"] if isinstance(e.get("end"), str) else None,
        "allday": bool(e.get("allday")),
        "cat": str(e.get("cat") or ""),
        "notes": str(e.get("notes") or ""),
        "rrule": e["rrule"] if isinstance(e.get("rrule"), str) and e["rrule"].strip() else None,
        "remind_min": _coerce_remind(e.get("remind_min")),   # absent → 0 (migration)
        "exdates": [x for x in (e.get("exdates") or []) if isinstance(x, str)],
        "overrides": e["overrides"] if isinstance(e.get("overrides"), dict) else {},
        "gcal_id": e["gcal_id"] if isinstance(e.get("gcal_id"), str) else None,
        # F7: absent/mistyped/unknown -> "manual" (every pre-F7 event, and any
        # hand-edited row), so a reader never has to branch on the field's absence.
        "source": e["source"] if isinstance(e.get("source"), str) and e["source"] in SOURCES else "manual",
        "ref": e["ref"][:_REF_MAX] if isinstance(e.get("ref"), str) and e["ref"].strip() else None,
    }


def _normalize_store(data) -> dict:
    """Whole-store shape guard: events is always a list of well-shaped events."""
    if not isinstance(data, dict):
        return _default_store()
    evs = data.get("events")
    if not isinstance(evs, list):
        evs = []
    return {
        "version": data["version"] if isinstance(data.get("version"), int) else STORE_VERSION,
        "events": [_normalize_event(e) for e in evs if isinstance(e, dict)],
    }


def load_store() -> dict:
    """Read the store, initialising on first run. NEVER raises — a bad file degrades
    to an empty in-memory store (WITHOUT overwriting it: it may be recoverable, and
    the next successful write replaces it). Callers hold ``_lock`` around
    load_store()+save_store() for a consistent read-modify-write."""
    try:
        raw = STORE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        st = _default_store()
        save_store(st)                         # best-effort initialise; failure swallowed
        return st
    except Exception:
        return _default_store()
    try:
        data = json.loads(raw)
    except Exception:
        return _default_store()
    return _normalize_store(data)


def _write(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_name(STORE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_PATH)                # atomic on Windows and POSIX


def save_store(store: dict) -> bool:
    """The ONE write path. Returns False on a failed persist instead of raising, so a
    disk hiccup can never crash the server over a calendar write."""
    try:
        _write(store)
        return True
    except Exception:
        return False


def _find(store: dict, eid: str):
    for e in store.get("events", []):
        if str(e.get("id")) == eid:
            return e
    return None


# --------------------------------------------------------------------------- #
#  Field validation — one validator shared by create and edit-all, so a full event
#  is validated the same way whichever route built it.
# --------------------------------------------------------------------------- #
def _validate_fields(get):
    """Validate the seven editable fields from ``get(key)`` (which returns the body
    value where present, else the current/default). Returns (fields, None) with
    start/end already formatted per allday, or (None, (message, code))."""
    title = str(get("title") or "").strip()
    if not title:
        return None, ("title required", 400)
    allday = bool(get("allday"))
    sdt = _to_datetime(get("start"))
    if sdt is None:
        return None, ("invalid start", 400)
    end_raw = get("end")
    edt = None
    if end_raw not in (None, ""):
        edt = _to_datetime(end_raw)
        if edt is None:
            return None, ("invalid end", 400)
        if edt < sdt:
            return None, ("end before start", 400)
    r = get("rrule")
    if r in (None, ""):
        rrule = None
    elif isinstance(r, str):
        rrule = r.strip()[:_RRULE_MAX] or None
    else:
        return None, ("rrule must be a string", 400)
    src = get("source")
    if src in (None, ""):
        source = "manual"
    elif isinstance(src, str) and src in SOURCES:
        source = src
    else:
        return None, ("invalid source", 400)
    ref_raw = get("ref")
    if ref_raw in (None, ""):
        ref = None
    elif isinstance(ref_raw, str):
        ref = ref_raw.strip()[:_REF_MAX] or None
    else:
        return None, ("ref must be a string", 400)
    return {
        "title": title[:_TITLE_MAX],
        "allday": allday,
        "start": _fmt(sdt, allday),
        "end": _fmt(edt, allday) if edt is not None else None,
        "cat": str(get("cat") or "")[:_CAT_MAX],
        "notes": str(get("notes") or "")[:_NOTES_MAX],
        "rrule": rrule,
        "remind_min": _coerce_remind(get("remind_min")),
        "source": source,
        "ref": ref,
    }, None


def _validate_override(body: dict, master: dict):
    """Build a partial override patch from the keys PRESENT in ``body`` — validated
    and formatted, but only those keys. allday defaults to the master's when the
    patch does not set it. Returns (patch, None) or (None, (message, code))."""
    allday = bool(body["allday"]) if "allday" in body else bool(master.get("allday"))
    patch: dict = {}
    if "title" in body:
        t = str(body.get("title") or "").strip()
        if not t:
            return None, ("title required", 400)
        patch["title"] = t[:_TITLE_MAX]
    if "allday" in body:
        patch["allday"] = allday
    if "cat" in body:
        patch["cat"] = str(body.get("cat") or "")[:_CAT_MAX]
    if "notes" in body:
        patch["notes"] = str(body.get("notes") or "")[:_NOTES_MAX]
    sdt = edt = None
    if "start" in body:
        sdt = _to_datetime(body.get("start"))
        if sdt is None:
            return None, ("invalid start", 400)
        patch["start"] = _fmt(sdt, allday)
    if "end" in body:
        e = body.get("end")
        if e in (None, ""):
            patch["end"] = None
        else:
            edt = _to_datetime(e)
            if edt is None:
                return None, ("invalid end", 400)
            patch["end"] = _fmt(edt, allday)
    if sdt is not None and edt is not None and edt < sdt:
        return None, ("end before start", 400)
    return patch, None


# --------------------------------------------------------------------------- #
#  Occurrence expansion — one master → its occurrences in a window.
# --------------------------------------------------------------------------- #
def _expand(dtstart, rrule_str, win_start, win_end):
    """Delegate to the sibling ``rrule`` module, imported LAZILY so the server boots
    before rrule.py lands. A missing module, or any error inside expansion, yields
    no occurrences for THIS event rather than breaking the whole calendar — the same
    per-item isolation the ticket scan uses for a bad file."""
    try:
        import rrule
    except Exception:
        return []
    try:
        occ = rrule.expand(dtstart, rrule_str, win_start, win_end)
    except Exception:
        return []
    return [d for d in occ if isinstance(d, datetime)]


def _occurrences_for(ev: dict, win_start: datetime, win_end: datetime) -> list:
    """Materialise one event's occurrences within [win_start, win_end]: expand the
    rule (or the single start), subtract exdates, layer overrides. Returns the flat
    occurrence dicts."""
    sdt = _to_datetime(ev.get("start"))
    if sdt is None:                            # unparseable start — skip, never raise
        return []
    allday = bool(ev.get("allday"))
    edt = _to_datetime(ev.get("end")) if ev.get("end") else None
    duration = (edt - sdt) if edt is not None else None
    exset = {x for x in (ev.get("exdates") or []) if isinstance(x, str)}
    overrides = ev.get("overrides") or {}
    rrule_str = ev.get("rrule")
    if rrule_str:
        starts = _expand(sdt, rrule_str, win_start, win_end)
        is_recurring = True
    else:
        starts = [sdt] if win_start <= sdt <= win_end else []
        is_recurring = False

    out = []
    for occ_start in starts:
        occ_date = occ_start.date().isoformat()
        if occ_date in exset:                  # delete-this subtracts the occurrence
            continue
        occ_end = (occ_start + duration) if duration is not None else None
        occ = {
            "master_id": ev["id"],
            "occ_id": (ev["id"] + "#" + occ_date) if is_recurring else ev["id"],
            "occ_date": occ_date,
            "title": ev.get("title") or "",
            "start": _fmt(occ_start, allday),
            "end": _fmt(occ_end, allday) if occ_end is not None else None,
            "allday": allday,
            "cat": ev.get("cat") or "",
            "notes": ev.get("notes") or "",
            # remind_min/source/ref are MASTER fields (not per-occurrence
            # overridable), so every occurrence carries the master's value.
            "remind_min": _coerce_remind(ev.get("remind_min")),
            "source": ev.get("source") or "manual",
            "ref": ev.get("ref"),
            "is_recurring": is_recurring,
        }
        ov = overrides.get(occ_date)
        if isinstance(ov, dict):               # edit-this layers a partial patch
            for k in _OVERRIDE_FIELDS:
                if k in ov:
                    occ[k] = ov[k]
        out.append(occ)
    return out


# --------------------------------------------------------------------------- #
#  Public API — (result_dict, http_code), mirroring server.py's other services.
# --------------------------------------------------------------------------- #
def list_occurrences(from_s, to_s):
    """A FLAT list of occurrences across [from, to] (inclusive, ``YYYY-MM-DD``).
    Bad/absent bounds → 400; a window wider than MAX_WINDOW_DAYS → 400 (bounded
    expansion). Returns ({"events": [...], "from", "to"}, code)."""
    fd = _parse_day(from_s)
    td = _parse_day(to_s)
    if fd is None or td is None:
        return {"error": "from/to must be YYYY-MM-DD"}, 400
    if td < fd:
        return {"error": "to is before from"}, 400
    if (td - fd).days > MAX_WINDOW_DAYS:
        return {"error": f"window too large (max {MAX_WINDOW_DAYS} days)"}, 400
    win_start = datetime(fd.year, fd.month, fd.day)
    # Cover the whole `to` day so an occurrence on the final date is included.
    win_end = datetime(td.year, td.month, td.day) + timedelta(days=1) - timedelta(microseconds=1)
    out = []
    with _lock:
        store = load_store()
        for ev in store.get("events", []):
            out.extend(_occurrences_for(ev, win_start, win_end))
    out.sort(key=lambda o: (o["start"] or "", o["occ_id"]))
    return {"events": out, "from": from_s, "to": to_s}, 200


def create_event(body):
    """Create a master event from a validated body. Returns ({"ok", "id", "event"},
    200) or ({"error"}, 400). Nothing is persisted on a validation failure."""
    if not isinstance(body, dict):
        return {"error": "body must be an object"}, 400
    fields, err = _validate_fields(lambda k: body.get(k))
    if err:
        return {"error": err[0]}, err[1]
    ev = {"id": uuid.uuid4().hex[:12], **fields,
          "exdates": [], "overrides": {}, "gcal_id": None}
    with _lock:
        store = load_store()
        store["events"].append(ev)
        if not save_store(store):
            return {"error": "could not persist"}, 500
    return {"ok": True, "id": ev["id"], "event": ev}, 200


def update_event(body):
    """Edit an event. ``scope='all'`` re-validates and rewrites the master's fields
    (id/exdates/overrides/gcal_id preserved). ``scope='this'`` records a partial
    ``overrides[occ_date]`` layered over that one occurrence. Unknown id → 404; a
    bad scope, missing occ_date, or invalid field → 400 with nothing persisted."""
    if not isinstance(body, dict):
        return {"error": "body must be an object"}, 400
    eid = str(body.get("id") or "")
    scope = body.get("scope")
    if scope not in ("this", "all"):
        return {"error": "scope must be 'this' or 'all'"}, 400
    with _lock:
        store = load_store()
        ev = _find(store, eid)
        if ev is None:
            return {"error": "event not found"}, 404
        if scope == "all":
            fields, err = _validate_fields(lambda k: body[k] if k in body else ev.get(k))
            if err:
                return {"error": err[0]}, err[1]
            ev.update(fields)                  # id/exdates/overrides/gcal_id untouched
        else:                                  # this
            occ_date = str(body.get("occ_date") or "")
            if _parse_day(occ_date) is None:
                return {"error": "occ_date (YYYY-MM-DD) required for scope 'this'"}, 400
            patch, err = _validate_override(body, ev)
            if err:
                return {"error": err[0]}, err[1]
            overrides = ev.setdefault("overrides", {})
            merged = dict(overrides.get(occ_date) or {})
            merged.update(patch)               # accumulate repeated edits to one occurrence
            overrides[occ_date] = merged
        if not save_store(store):
            return {"error": "could not persist"}, 500
    return {"ok": True, "id": eid, "scope": scope}, 200


def delete_event(body):
    """Delete an event. ``scope='all'`` removes the master; ``scope='this'`` adds an
    exdate so that one occurrence disappears (works for singles and recurring alike).
    Unknown id → 404; bad scope or missing occ_date → 400 with nothing persisted."""
    if not isinstance(body, dict):
        return {"error": "body must be an object"}, 400
    eid = str(body.get("id") or "")
    scope = body.get("scope")
    if scope not in ("this", "all"):
        return {"error": "scope must be 'this' or 'all'"}, 400
    with _lock:
        store = load_store()
        ev = _find(store, eid)
        if ev is None:
            return {"error": "event not found"}, 404
        if scope == "all":
            store["events"] = [e for e in store["events"] if str(e.get("id")) != eid]
        else:                                  # this
            occ_date = str(body.get("occ_date") or "")
            if _parse_day(occ_date) is None:
                return {"error": "occ_date (YYYY-MM-DD) required for scope 'this'"}, 400
            ex = ev.setdefault("exdates", [])
            if occ_date not in ex:
                ex.append(occ_date)
        if not save_store(store):
            return {"error": "could not persist"}, 500
    return {"ok": True, "id": eid, "scope": scope}, 200


# --------------------------------------------------------------------------- #
#  Natural-language command — Gemini turns free text into calendar operations,
#  which are APPLIED through the create/update/delete functions above. Nothing
#  about validation, recurrence or overrides is reimplemented here: the AI only
#  proposes ops; the same service path that guards the HTTP CRUD routes guards
#  every op, so one bad op is skipped-and-reported, never half-applied.
# --------------------------------------------------------------------------- #
#: Window of the user's own events handed to Gemini for context, so "pomeri
#: zubara" / "obriši sastanak" can resolve to a real event id.
GEMINI_CONTEXT_DAYS = 30
#: Cap on how many context events are listed in the prompt (keeps it bounded).
GEMINI_CONTEXT_MAX = 100
#: Cap on ops applied from ONE command — a runaway/hallucinated reply cannot fan
#: out into an unbounded batch of writes.
GEMINI_MAX_OPS = 20
#: The command text is capped before the prompt (the route caps too).
GEMINI_TEXT_MAX = 2000

_GEMINI_HEAD = (
    "You are a calendar assistant. Turn the user's command into calendar "
    "operations. Return ONLY a JSON object (no prose, no markdown fences) with "
    "exactly these keys:\n"
    '- "ops": an array of operation objects. Each op has:\n'
    '    "action": "create" | "update" | "delete"  (required)\n'
    '    "id": the event id to act on  (required for update/delete — copy it from '
    "the event list below; NEVER invent an id)\n"
    '    "scope": "all" | "this"  (update/delete only; "all" changes the whole '
    'event, "this" only one date — default "all")\n'
    '    "occ_date": "YYYY-MM-DD"  (required when scope is "this")\n'
    '    "title": string,  "start": ISO date or date-time,  "end": ISO or null,\n'
    '    "allday": true|false,  "cat": string,  "rrule": an iCalendar RRULE '
    "string or null,\n"
    '    "remind_min": integer minutes before start to remind (0 = no reminder)\n'
    '- "reply": a short confirmation for the user, in the user\'s own language '
    "(string)\n\n"
    "Rules: for a NEW event use action \"create\" and no id. Use all-day "
    "(allday=true with a date-only start) only when the command gives no time. "
    "Keep every date/time in the same timezone-free ISO form as the event list. "
    "If the command asks for nothing actionable, return an empty ops array and say "
    "so in reply.\n\n")


def _gemini():
    """The shared Gemini door (scripts/tickets/gemini_client), reached the SAME way
    mail_ai does so calendar NL commands and mail drafts share the one free key and
    the one budget/rate gate. Imported lazily and behind this indirection so the
    module imports cleanly with no key configured, and a test can swap in a stub."""
    import sys
    tickets = str(HERE.parent / "scripts" / "tickets")
    if tickets not in sys.path:
        sys.path.insert(0, tickets)
    import gemini_client
    return gemini_client


def _gemini_context_events():
    """A compact list of the user's events over the next GEMINI_CONTEXT_DAYS, for
    the prompt — enough (id + date + start + title) to resolve an update/delete to
    a real event. Reuses list_occurrences (its own lock/validation), so the AI sees
    exactly what the calendar renders. Empty on any error rather than raising."""
    today = date.today()
    listing, code = list_occurrences(today.isoformat(),
                                     (today + timedelta(days=GEMINI_CONTEXT_DAYS)).isoformat())
    if code != 200 or not isinstance(listing, dict):
        return today, []
    return today, (listing.get("events") or [])[:GEMINI_CONTEXT_MAX]


def _build_gemini_prompt(cmd):
    today, occ = _gemini_context_events()
    lines = [
        f'- id={o.get("master_id")} date={o.get("occ_date")} '
        f'start={o.get("start")} allday={bool(o.get("allday"))} '
        f'cat={o.get("cat") or ""} title="{o.get("title") or ""}"'
        for o in occ
    ]
    events_block = "\n".join(lines) if lines else "(no events in this window)"
    return (_GEMINI_HEAD
            + f"Today is {today.isoformat()}.\n\n"
            + f"The user's existing events for the next {GEMINI_CONTEXT_DAYS} days "
            + "(use these ids to update or delete):\n"
            + events_block
            + "\n\nUser command:\n" + cmd)


def _apply_gemini_op(op):
    """Apply ONE proposed op through the existing create/update/delete functions —
    never a reimplementation, so every field is validated and every rrule/override
    path matches the HTTP CRUD routes. Returns a small record {action, ok, id?/
    error?}; a non-object op, unknown action or validation failure is reported, not
    raised, so one bad op cannot abort the batch or half-apply the rest."""
    if not isinstance(op, dict):
        return {"action": None, "ok": False, "error": "op must be an object"}
    action = str(op.get("action") or "").strip().lower()
    if action == "create":
        res, code = create_event(op)
    elif action in ("update", "delete"):
        op = dict(op)                           # never mutate the model's own object
        if not str(op.get("scope") or "").strip():
            op["scope"] = "all"                 # a bare "move/delete X" means the event
        res, code = update_event(op) if action == "update" else delete_event(op)
    else:
        return {"action": action or None, "ok": False, "error": "unknown action"}
    ok = code == 200 and bool(res.get("ok"))
    rec = {"action": action, "ok": ok}
    if ok and res.get("id"):
        rec["id"] = res["id"]
    if not ok:
        rec["error"] = str(res.get("error") or "failed")
    return rec


def gemini_command(text):
    """Apply a natural-language calendar command. Returns ({"ok", "reply",
    "applied":[...], "events_changed"}, code).

    Builds a prompt with today's date and the user's near-term events, asks Gemini
    for STRICT JSON {ops, reply}, parses it DEFENSIVELY (a failed call or a
    non-object reply is a clean ok:false result, NEVER a 500 and NEVER a bare
    json.loads().get()), then applies at most GEMINI_MAX_OPS ops through the
    service functions above. Because each op is validated by that same path before
    it persists, an invalid op is skipped-and-reported rather than half-applied."""
    cmd = (text if isinstance(text, str) else "").strip()[:GEMINI_TEXT_MAX]
    if not cmd:
        return {"ok": False, "reply": "empty command", "applied": [],
                "events_changed": False}, 400
    prompt = _build_gemini_prompt(cmd)
    try:
        result = _gemini().call(prompt, json_out=True)
    except Exception:
        # A failed/quota'd call, or a reply that was not JSON (gemini_client raises
        # GeminiError there). A clean structured error — not a 500 — nothing applied.
        return {"ok": False, "reply": "AI request failed, please try again",
                "applied": [], "events_changed": False}, 200
    if not isinstance(result, dict):            # non-object JSON (list/number/string)
        return {"ok": False, "reply": "AI returned an unexpected response",
                "applied": [], "events_changed": False}, 200
    raw_ops = result.get("ops")
    raw_ops = raw_ops if isinstance(raw_ops, list) else []
    applied, changed = [], False
    for op in raw_ops[:GEMINI_MAX_OPS]:         # cap the batch (runaway guard)
        rec = _apply_gemini_op(op)
        applied.append(rec)
        if rec.get("ok"):
            changed = True
    return {"ok": True, "reply": str(result.get("reply") or ""),
            "applied": applied, "events_changed": changed}, 200
