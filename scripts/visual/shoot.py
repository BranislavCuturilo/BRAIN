#!/usr/bin/env python3
"""The ONE door of the visual-diff engine.

    shoot.py before   --repo <path> [--files a b] [--ticket DEMO#08597] [--work-id W]
    shoot.py after    --repo <path> [--files a b] [--ticket ...] [--work-id ...]
                      [--no-baseline [--baseline-reason "..."]]
    shoot.py promote  --repo <path> [--ticket ... | --work-id ...] [--pairs id ...]
    shoot.py sweep    --repo <path>
    shoot.py baseline --repo <path>

`before` and `after` write into the brain scratchpad
`agent_view/.shots/<ticket-or-work-id>/{before,after}/` and keep ONE
`manifest.json` per run - both commands update it, so the state of a ticket's
screenshots is a single file the HUD can read.

Every failure that STOPS the run prints one JSON line on stdout and exits
non-zero, so the caller (a hook) can turn it into a question for the operator
instead of shipping a ticket with no pictures and no explanation:

    {"error": "server_down", "detail": "...", "questions": [...], "exit": 3}

Exit codes: 0 ok - 1 unexpected - 2 needs_config - 3 server_down - 4 auth_failed
- 5 playwright_missing / pillow_missing - 6 capture_failed (nothing captured) -
7 usage - 8 no_pages (a visual file that maps to no screen) - 9 no_ticket
(`before`/`after` with no ticket anywhere).

**No ticket, no pictures.** The pair exists to be sent to a customer on a
helpdesk ticket; a run that cannot name one produces images nobody may send and
the HUD then has to hide. So `before` and `after` REFUSE - with a question, and
before writing anything at all - when neither the command line nor the run's own
`state.json` names a ticket (`_require_ticket`).

`promote` is the exception to the server probe: it copies files that are already
on disk, and refusing to run because the dev server is down would strand pairs
the operator has already approved.

The server and Pillow are probed in `main()`, BEFORE the command runs. Both used
to be checked further in: the health check lived inside `open_session`, which a
run with nothing to capture never reached, and Pillow was never probed at all -
so an `after` pass on a machine without it failed inside every annotate call and
still exited 0 with "0 pairs".

`before` NEVER re-captures a page it already has: the whole value of the "before"
shot is that it predates the first edit. A second `before` for another file adds
pages to the same manifest and leaves the existing ones alone (`--force` to
override, which you want only after a manual reset).

**`before` prefers the ROLLING BASELINE.** `<repo>/.visual-baseline/` holds the
app's last approved picture per page+state; when it has the page, that picture
IS the before and nothing is captured live. That is what removes the stash /
restart / shoot / unstash dance a real session had to do twice, and it is why a
ticket can still have a baseline after the edit is already written. `promote`
makes an approved `after` the new baseline and moves the picture it replaces
into `.visual-baseline/archive/<stamp>/` - kept, never deleted.

The invariant that outranks every convenience: a post-change capture must never
become a pre-change one. Promotion is therefore EXPLICIT (its own command / its
own function, never a side effect of `after`), it refuses a page whose after
shot failed, and `before` refuses to adopt a baseline its own run promoted.

A visual file that maps to NO page stops the run with `no_pages` and its
questions, and is NOT recorded as covered - otherwise the V2 gate is satisfied by
a pass that captured nothing at all. `--accept-unlocated` is the operator's
answer ("no screen shows this") and records the coverage.

`after` with no `before` on record stops with `no_before` and asks. When the
before state is genuinely unobtainable - the data migration has already run, or
the engine itself was broken when the baseline was due - `--no-baseline` is the
operator's answer and the run produces STANDALONE shots:

    manifest["baseline"] = {"state": "none", "reason": "...", "declared_at": ...}
    manifest["pairs"][i]["baseline"] = "none"   # one picture, `before` is null

Absent or `"captured"` means a real pair. The two must never be confused in the
gallery: "no baseline captured" and "no visual change" look identical if nobody
writes down which one it was. The standalone path writes only into
`shots["after"]` and only into `state.json`'s `after` side, so a post-change
capture cannot become a pre-change one - that is what makes it honest, and it is
why the answer is a flag on `after` and not a second `before` run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
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

from . import affected as vaffected                             # noqa: E402
from . import annotate as vannotate                             # noqa: E402
from . import capture as vcapture                               # noqa: E402
from . import compare as vcompare                               # noqa: E402
from . import config as vconfig                                 # noqa: E402
from . import isolate as visolate                               # noqa: E402
from . import locate as vlocate                                 # noqa: E402
from . import pages as vpages                                   # noqa: E402

HERE = Path(__file__).resolve().parent
BRAIN = HERE.parents[1]
#: The brain's ticket store (filelock + atomic_write_json), loaded BY PATH under
#: a namespaced alias: a bare `import store` would leave it in the app's
#: sys.modules under a name a Django project is allowed to own.
store = visolate.load_module("brain_ticket_store",
                             BRAIN / "scripts" / "tickets" / "store.py")
#: THE ticket-reference rule, SHARED with the HUD gallery (`agent_view/
#: server.py`). Loaded, never copied: the two ends once applied different rules
#: to the same field, so a `--ticket 43417` run captured correctly, printed
#: success, and was then invisible in the gallery for ever.
ticket_ref = visolate.load_module("brain_ticket_ref",
                                  BRAIN / "scripts" / "brain" / "ticket_ref.py")

MANIFEST_VERSION = 1
#: 2: the baseline stores the full PNG per page+state and an archive of what it
#: replaced, keyed by pair id under `shots`. Version 1 kept a phash and a thumb
#: per PAGE under `pages` and nothing to show a customer; an index still at 1
#: simply has no shot to adopt, so `before` captures live as it always did.
BASELINE_VERSION = 2

EXIT = {"needs_config": 2, "server_down": 3, "auth_failed": 4,
        "playwright_missing": 5, "pillow_missing": 5, "capture_failed": 6,
        "usage": 7, "no_pages": 8, "no_ticket": 9}

#: Anchor kinds that name ONE element: an id, a Django form field, a string the
#: diff added, a CSS selector. A bare `class` names every element that carries
#: it - `form-label` is on every field of a form - so it can say WHERE to look
#: but never WHICH element changed. Regions come from the specific kinds when any
#: of them resolves, and only from classes when none does: outlining an untouched
#: field because it shares a class with a new one is the "ikonice i nebuloze"
#: complaint drawn in blue.
SPECIFIC_ANCHOR_KINDS = ("id", "field", "text", "css_selector")

#: How many regions of ONE screen a customer is shown. Each is two more images
#: in the comment, on top of the two full pictures: four regions is ten images
#: for one screen, which is already the point where a reader stops looking - and
#: a change that touches more than four places is one the full pictures show
#: better than any set of crops. `regions_found` records what the cap hid, so a
#: truncation never reads as "that was all of it".
MAX_REGIONS = 4

#: How a page got into the affected set. `affected.from_diff` writes exactly
#: these into each page's `why`, and they are the whole input to the
#: layout-only collapse - there is no second guess about it anywhere.
OWN_TEMPLATE_WHY = "template changed"
SHARED_TEMPLATE_WHY = "includes "

#: The commands that produce customer pictures, and therefore need a ticket to
#: produce them FOR. `sweep` and `baseline` are repo-wide and belong to no
#: ticket; `promote` acts on a run that already has one.
TICKET_COMMANDS = ("before", "after")

#: The ONE reason that means "this screen did not exist before the change".
#: `unpaired_rows` writes it and `new_page_pairs` selects on it, so the two can
#: never disagree about which unpaired record is a NEW PAGE - every other reason
#: on that side (the before capture errored, the baseline has no such state) is a
#: gap in OUR evidence and must never be sold to a customer as a new screen.
WHY_STATE_APPEARED = "state appeared after the change"


def shots_root() -> Path:
    """`agent_view/.shots/` - already gitignored, next to `attachment_cache/`:
    per machine, large, and only the APPROVED pair ever leaves it."""
    import os
    return Path(os.environ.get("BRAIN_SHOTS_DIR") or (BRAIN / "agent_view" / ".shots"))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ascii(text) -> str:
    """Console output only. The Windows console is cp1252 and dies MID-RUN on
    `c/c/s/z/d`, which would leave a half-written manifest."""
    return str(text).encode("ascii", "replace").decode("ascii")


def emit_error(code, detail="", questions=None, exit_code=None) -> int:
    print(json.dumps({"error": code, "detail": str(detail)[:400],
                      "questions": list(questions or []),
                      "exit": exit_code or EXIT.get(code, 1)}, ensure_ascii=True))
    return exit_code or EXIT.get(code, 1)


def run_key(args) -> str:
    """The folder name for this run: the WORK ID first, then the ticket, then
    the repo name - so a second pass for the same unit of work always finds the
    first one's shots.

    The work id outranks the ticket because the PreToolUse gate looks a run up
    by it and nothing else (`<shots_root>/<work_id>/state.json`,
    `scripts/brain/visual_gate.py: state_for`). Now that a capture must NAME its
    ticket, the gate's own command line carries both - and with the ticket
    winning, the state file would land in a folder the gate never reads, so
    every edit would be denied forever with the answer already on disk. One
    ticket worked on twice is two works and two folders, which is the unit the
    HUD approves in anyway (one outbox draft per work).
    """
    for raw in (getattr(args, "work_id", "") or "", getattr(args, "ticket", "") or ""):
        if str(raw).strip():
            return vcapture.slug(raw, "run")
    return "repo_" + vcapture.slug(Path(args.repo).name, "app")


def manifest_path(key) -> Path:
    return shots_root() / key / "manifest.json"


def load_manifest(key) -> dict:
    return store.load(manifest_path(key)) or {}


def update_json(path, mutate) -> dict:
    """Read-modify-write under the brain's cross-process lock.

    Two `before` runs can be in flight at once (the hook fires per edited file)
    and the HUD reads the same manifest, in three separate processes. Loading at
    the top of a command and saving at the bottom would lose whichever finished
    first — the exact race `tickets/store.py` was written to close. The CAPTURE
    happens outside the lock (20-40s would blow past the lock's stale window);
    only the merge is held.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with store.filelock(path):
        current = store.load(path) or {}
        merged = mutate(current)
        store.atomic_write_json(path, merged)
    return merged


def state_path(key) -> Path:
    return shots_root() / key / "state.json"


def _record_state(key, args, side, files) -> None:
    """`<shots_root>/<key>/state.json` - the small file the V2 PreToolUse gate
    reads (`scripts/brain/visual_gate.py`, which documents the contract):

        {"work_id", "repo", "ticket",
         "before": {"at": iso, "files": [repo-relative paths covered]},
         "after":  {"at": iso, "files": [...]}}

    Deliberately NOT the manifest: the gate runs on every Edit and must answer
    "is this file already covered?" without parsing a megabyte of geometry. A
    file counts as covered once a pass BASELINED it - never merely because a
    pass ran for it (`_covered_files`).
    """
    def merge(current):
        st = current or {}
        st["work_id"] = str(getattr(args, "work_id", "") or "") or st.get("work_id", "")
        st["ticket"] = str(getattr(args, "ticket", "") or "") or st.get("ticket", "")
        st["repo"] = str(Path(args.repo).resolve())
        prev = (st.get(side) or {}).get("files") or []
        st[side] = {"at": _now(), "files": sorted(set(prev) | {str(f) for f in files})}
        return st

    update_json(state_path(key), merge)


def _covered_files(aff, accept_unlocated=False) -> list:
    """The visual files this pass actually BASELINED - what the gate unlocks.

    A file that mapped to no page is deliberately NOT in here. It used to be:
    the pass printed a line, recorded the file as covered and exited 0, so the
    gate let the edit through believing a "before" picture existed when nothing
    had been captured and the server had not even been contacted. The operator
    answers the question with `--accept-unlocated`, and only then is it covered.
    """
    covered = set(aff.get("considered") or [])
    if not accept_unlocated:
        covered -= {u["file"] for u in aff.get("unlocated") or []}
    return sorted(covered)


def _unlocated_gate(aff, args) -> int:
    """Report the files that map to no screen, and STOP unless the operator has
    already answered. Returns an exit code, or 0 when there is nothing to ask.

    The plan is explicit that a case we cannot handle becomes a question
    ("nikad tiho preskakanje"), and this is the one that reads most like
    success: no pages, no error, exit 0.
    """
    rows = aff.get("unlocated") or []
    for u in rows:
        print(_ascii("  NO SCREEN %-36s %s" % (u["file"], u["reason"])))
    if not rows or getattr(args, "accept_unlocated", False):
        return 0
    return emit_error(
        "no_pages",
        "%d page(s) affected, %d file(s) map to no screen: %s"
        % (len(aff.get("pages") or []), len(rows),
           ", ".join(u["file"] for u in rows[:5])),
        [{"key": "pages",
          "question": "Izmena u %s ne pogadja nijedan ekran iz inventara (%s). "
                      "Na kom ekranu se to vidi?" % (rows[0]["file"], rows[0]["reason"]),
          "example": "dopuni inventar stranica, ili pokreni ponovo sa "
                     "--accept-unlocated ako se ta izmena nigde ne vidi"}])


def _state_ticket(key) -> str:
    """The ticket recorded on this run's state file, or "" - never raises.

    A recorded reference that names no ticket reads as NO ticket, so an older
    run written before the reference was validated cannot keep satisfying the
    guard and producing pairs the gallery will hide."""
    try:
        raw = json.loads(Path(state_path(key)).read_text(encoding="utf-8"))
        rec = str((raw or {}).get("ticket") or "")
        return rec if ticket_ref.names_a_ticket(rec) else ""
    except Exception:
        return ""


def _bad_ticket_question(args, raw):
    """The question for a `--ticket` that names no ticket.

    Names the candidate modules mapped to this repo when the store knows them,
    so the operator can see the spelling rather than guess it. We still refuse:
    a repo may serve several modules and picking one would be a guess about
    whose customer receives the pictures.
    """
    got = str(raw or "").strip()
    mods = []
    try:
        mods = ticket_ref.modules_for_repo(args.repo)
    except Exception:                                          # noqa: BLE001
        mods = []
    digits = "".join(ch for ch in got if ch.isdigit()) or "43417"
    # ONE mapped module is a fact and may be shown as the answer. SEVERAL is a
    # choice that is the operator's: naming one of them in the example reads as
    # the right one, and a confident wrong suggestion is worse than a
    # placeholder the operator has to fill in.
    if len(mods) == 1:
        example = "%s#%s" % (mods[0], digits)
    else:
        example = "<MODUL>#%s" % digits
    hint = (" Moduli mapirani na ovaj repo: %s." % ", ".join(mods)) if mods else ""
    return [{
        "key": "ticket",
        "question": ("--ticket %r ne imenuje tiket: nedostaje modul, pa se par "
                     "ne moze svrstati ni pod jedan tiket i galerija bi ga "
                     "sakrila. Koji je pun oblik?%s" % (got, hint)),
        "example": "shoot.py %s --repo %s --ticket %s%s"
                   % (args.command, Path(args.repo).as_posix(), example,
                      (" --work-id " + str(args.work_id))
                      if getattr(args, "work_id", "") else ""),
    }]

def _require_ticket(args) -> int:
    """0 when this run has a ticket, an exit code with a question when it does
    not. Called BEFORE anything is read or written, so a refused run leaves no
    folder, no manifest and no half-captured page behind.

    Two sources, both facts: the command line, and the ticket the run's own
    `state.json` already carries (a `--work-id` pass that a `--ticket` pass
    started). There is deliberately no third: a work id can cover several
    tickets (`worklog` opens one interval per ticket for a merged session), so
    resolving one from it would be a GUESS about whose customer receives the
    pictures. The engine's contract for an operator decision is a question.
    """
    if getattr(args, "command", "") not in TICKET_COMMANDS:
        return 0
    given = str(getattr(args, "ticket", "") or "").strip()
    if given:
        # A PRESENT `--ticket` still has to name a ticket. Non-empty is not the
        # test: `--ticket 43417` is non-empty and splits into ("", "43417"),
        # which the gallery's key rule rejects — so the run captured, reported
        # success, and no operator could ever see or send it (DEMO#43417,
        # 2026-08-28; it had happened more than once). Refuse HERE, before a
        # folder exists, using the SAME rule the gallery filters by.
        if not ticket_ref.names_a_ticket(given):
            return emit_error(
                "no_ticket",
                "--ticket %r names no ticket (module missing)" % given,
                _bad_ticket_question(args, given))
        return 0
    key = run_key(args)
    if _state_ticket(key).strip():
        return 0
    return emit_error(
        "no_ticket",
        "no --ticket, and %s carries none" % state_path(key),
        [{"key": "ticket",
          "question": "Za koji tiket se prave ove slike? Bez tiketa nema slika - "
                      "par PRE/POSLE postoji samo da bi bio poslat kupcu.",
          "example": "shoot.py %s --repo %s --ticket DEMO#08597%s"
                     % (args.command, Path(args.repo).as_posix(),
                        (" --work-id " + str(args.work_id))
                        if getattr(args, "work_id", "") else "")}])


def _diff_of_commit(repo, rev) -> str:
    """The unified diff a commit introduced, or "" when it cannot be read.

    `-U0` matches what `changed_files()` asks git for, so the hunks the anchors
    come from are identical whether the change is in the working tree or in
    history.
    """
    txt = vaffected._git(repo, "show", "--format=", "-U0", str(rev))
    return txt or ""

def _new_manifest(key, args, cfg) -> dict:
    # The ticket falls back to the one the RUN already carries. A `--work-id`
    # run with no `--ticket` (what the commit gate issues) otherwise produced a
    # manifest with `ticket: ""`, which the HUD lists as "bez tiketa" and the
    # approve path refuses as "not attached to a ticket" - so its pictures could
    # never reach the customer. `state_path` already inherits it; nothing read
    # it back. Reported by the HUD agent, whose fix had to live in this file.
    return {"version": MANIFEST_VERSION, "key": key,
            "ticket": str(getattr(args, "ticket", "") or _state_ticket(key)),
            "work_id": str(getattr(args, "work_id", "") or ""),
            "repo": str(Path(args.repo).resolve()), "base_url": cfg["base_url"],
            "captured_at": _now(), "before_at": "", "after_at": "",
            "affected": {}, "shots": {"before": {}, "after": {}},
            "pairs": [], "sweep": None, "errors": [], "skipped": [], "unpaired": []}


def pair_id(page_id, state, taken=None) -> str:
    """`<page>__<state>`, unique within a run. Two page ids can slug to the same
    string (the slug is ASCII-folded and cut at 40 chars); without the guard the
    second page would silently overwrite the first one's shot and the customer
    would get a picture of the wrong screen."""
    base = "%s__%s" % (vcapture.slug(page_id, "page"), vcapture.slug(state, "base"))
    if taken is None or taken.get(base, page_id) == page_id:
        if taken is not None:
            taken[base] = page_id
        return base
    n = 2
    while taken.get("%s_%d" % (base, n), page_id) != page_id:
        n += 1
    taken["%s_%d" % (base, n)] = page_id
    return "%s_%d" % (base, n)


# --------------------------------------------------------------------------- #
#  Shared capture step
# --------------------------------------------------------------------------- #
def _capture_side(cfg, args, key, side, page_rows, anchors_by_page=None, taken=None):
    """Capture `page_rows` into `<key>/<side>/`.

    Returns `(shots, errors, skipped, pages_done)` instead of mutating a
    manifest: the merge into the shared file happens later, under the lock, so a
    40-second capture never holds it. `skipped` carries every state that did not
    open, with the reason - a modal that stopped opening is a finding, and it
    used to be dropped on the floor by both passes.

    `taken` is the pair-id book. Pass the one the run already has (the adopted
    baseline shots, the before shots) so the two sides agree on the id of a page
    whose slug collides with another's; a book that starts empty on each side
    resolves the collision by iteration order, and the after shot then pairs
    with the wrong before.

    `skipped` also carries the states that opened and looked EXACTLY like a state
    already kept for that page (`collapsed`, `capture._keep_or_collapse`). They
    are counted separately in the console line: a collapsed state is a picture
    the customer is spared, a skipped one is a picture nobody could take.
    """
    out_dir = shots_root() / key / side
    out_dir.mkdir(parents=True, exist_ok=True)
    session = vcapture.open_session(cfg, args.repo)
    shots_out, errors, skipped, done = {}, [], [], 0
    taken = {} if taken is None else taken
    try:
        for row in page_rows:
            url = cfg["base_url"] + row["url"]
            try:
                shots, missed = vcapture.capture_page(
                    session, url, out_dir, page_id=row["page_id"],
                    anchors=(anchors_by_page or {}).get(row["page_id"]) or [])
            except Exception as exc:                            # noqa: BLE001
                errors.append({"stage": side, "page_id": row["page_id"], "url": url,
                               "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
                continue
            for m in missed:
                # The WHOLE row, not three of its keys: a collapsed state
                # carries `collapsed` and `same_as`, and rebuilding the row by
                # hand dropped both on the floor - so the manifest said 49
                # states were "skipped" with no way to tell which of them were
                # pictures the customer was spared.
                skipped.append({**m, "stage": side, "page_id": row["page_id"],
                                "url": url})
            if not shots:
                errors.append({"stage": side, "page_id": row["page_id"], "url": url,
                               "error": "no shots"})
                continue
            done += 1
            for shot in shots:
                shot["title"] = shot.get("title") or row.get("title") or ""
                shot["why"] = row.get("why") or []
                shots_out[pair_id(row["page_id"], shot["state"], taken)] = shot
            collapsed = sum(1 for m in missed if m.get("collapsed"))
            print(_ascii("%-6s %-40s %d state(s), %d collapsed, %d skipped"
                         % (side, row["page_id"], len(shots), collapsed,
                            len(missed) - collapsed)))
        if session.blocked:
            errors.append({"stage": side, "page_id": "", "url": "",
                           "error": "blocked %d non-GET request(s): %s"
                           % (len(session.blocked), session.blocked[0]["url"][:120])})
    finally:
        session.close()
    return shots_out, errors, skipped, done


def _side_reason(record, stage, shot) -> str:
    """What the OTHER pass recorded about this page and state, or ""."""
    page_id, state = shot.get("page_id"), shot.get("state")
    for row in record.get("skipped") or []:
        if (row.get("stage") == stage and row.get("page_id") == page_id
                and row.get("label") == state):
            return "the %s pass skipped this state: %s" % (stage, row.get("reason"))
    for row in record.get("errors") or []:
        if row.get("stage") == stage and row.get("page_id") == page_id:
            return "the %s capture failed: %s" % (stage, row.get("error"))
    return ""


def unpaired_rows(before_shots, after_shots, record) -> list:
    """The states that exist on ONE side only, each with the REAL reason.

    Every unmatched `after` shot used to be labelled "state appeared after the
    change" - which is false for a page whose BEFORE capture errored, and that
    is the case an operator most needs to see. The reason the failing pass
    recorded travels through instead; the optimistic sentence is only the
    fallback when nothing was recorded.

    It is false for a second reason once the before side comes from the stored
    baseline: the baseline holds the states somebody promoted, so a modal that
    is simply not in it has not "appeared", it was never photographed.
    """
    rows = []
    from_baseline = {s.get("page_id") for s in (before_shots or {}).values()
                     if (s or {}).get("source") == "baseline"}
    for pid in sorted(set(after_shots) - set(before_shots)):
        shot = after_shots[pid]
        if shot.get("page_id") in from_baseline:
            why = ("the stored baseline has no '%s' state for this screen"
                   % shot.get("state"))
        else:
            why = (_side_reason(record, "before", shot)
                   or WHY_STATE_APPEARED)
        rows.append({"pair_id": pid, "page_id": shot.get("page_id"),
                     "state": shot.get("state"), "side": "after_only",
                     "why": why})
    for pid in sorted(set(before_shots) - set(after_shots)):
        shot = before_shots[pid]
        rows.append({"pair_id": pid, "page_id": shot.get("page_id"),
                     "state": shot.get("state"), "side": "before_only",
                     "why": _side_reason(record, "after", shot)
                     or "state no longer opens after the change"})
    return rows


def _page_path(shot, page) -> str:
    """The screen's path as a CUSTOMER may read it: `/settings/cities/bulk/`.

    Never the captured URL as it stands - that is `http://acme.lvh.me:8020/...`,
    the operator's dev host, and it has no business in a customer's comment.
    """
    url = str((page or {}).get("url") or "").strip()
    if url.startswith("/"):
        return url
    raw = str((shot or {}).get("url") or "").strip()
    if "://" in raw:
        from urllib.parse import urlsplit
        parts = urlsplit(raw)
        return parts.path + (("?" + parts.query) if parts.query else "")
    return raw


def new_page_pairs(unpaired, after_shots, cfg, repo) -> tuple:
    """`(pairs, consumed_ids)` for the screens that DID NOT EXIST before.

    A page whose after shot has no before is not automatically news: the before
    capture may have failed, or the stored baseline may simply not hold that
    state. `unpaired_rows` already tells those cases apart and writes the real
    reason, so the selection here is exactly `why == WHY_STATE_APPEARED` and
    there is no second heuristic anywhere.

    Such a record IS a pair - one picture, no comparison - because everything
    downstream (approval, the mode pills, the draft, the attachments, promotion)
    already works on pairs. Leaving it in `unpaired` meant the HUD never showed
    it and the customer was never told the screen exists.

    The LABEL and the PATH come from the app's own map, not from the capture: the
    browser title of the new Gradovi grid is `Gradovi | Kontrola - Kontrola`,
    which names the wrong screen, and the captured URL carries the dev host.
    """
    rows = [r for r in (unpaired or [])
            if r.get("side") == "after_only" and r.get("why") == WHY_STATE_APPEARED]
    if not rows:
        return [], []
    inv = {}
    try:
        inv = {p["page_id"]: p for p in vpages.inventory(cfg, repo)}
    except (OSError, ValueError, KeyError, TypeError):
        inv = {}                     # no map: label from the shot, steps blank
    pairs, used = [], []
    for row in rows:
        pid = row.get("pair_id")
        shot = (after_shots or {}).get(pid)
        if not _has_picture(shot):
            continue                 # reported by `unpaired` exactly as before
        page = inv.get(row.get("page_id")) or {}
        label = str(page.get("title") or shot.get("title") or row.get("page_id") or "").strip()
        pairs.append({
            "pair_id": pid, "page_id": row.get("page_id"), "url": shot.get("url"),
            "title": label, "state": shot.get("state"),
            # There is no before picture and there could not have been one. The
            # HUD and the draft both read `new_page` for the REASON; `baseline`
            # keeps saying what it always said, so every older reader still gets
            # "one picture, no comparison" instead of an empty half.
            "baseline": "none",
            "new_page": True,
            # What the customer is told, as FACTS - the sentence itself is the
            # HUD's (server `shots_draft`), so the wording lives in one place.
            # `steps: []` is honest and designed: the comment prints an empty
            # "Do nje:" line for the operator to fill rather than a guess.
            "nav": {"label": label, "path": _page_path(shot, page),
                    "steps": vpages.nav_steps(page)},
            "caption": label or vannotate.caption_from_facts(
                shot.get("title"), shot.get("state")),
            "before": {"full": None},
            "after": {"full": shot.get("png_path")},
            "regions": [], "regions_found": 0, "sweep": None,
            "drop": False, "drop_reason": "",
            "why": [WHY_STATE_APPEARED]})
        used.append(pid)
    return pairs, used


def _crop_anchors(aff, only_from_diff=True) -> dict:
    """`{page_id: [anchor, ...]}`, best first.

    Two different jobs, and the difference is the old "measured, not drawn" rule:

    * the AFTER pass may only crop what came out of a HUNK, because a whole-file
      anchor names a line nobody touched (the default);
    * the BEFORE pass measures EVERYTHING it can read off the file. At that
      moment there is no hunk at all - the edit is not written yet - so asking
      for diff anchors there measures nothing, and every region on the after side
      then looks NEW. That is how a field that had merely moved got its before
      half cut at the same coordinates and showed a different part of the form.
      Measuring is not drawing; it costs one page-load budget and buys the
      alignment.
    """
    return {page["page_id"]: vaffected.anchors_for_page(
        aff, page["page_id"], only_from_diff=only_from_diff)
        for page in aff.get("pages") or []}


def _taken_from(shots) -> dict:
    """The pair-id book implied by shots already on record."""
    return {pid: (shot or {}).get("page_id") for pid, shot in (shots or {}).items()}


def _collapse_layout_only(pairs) -> int:
    """One pair per changed SHARED LAYOUT, not one per page that includes it.

    A sidebar item added to `base_settings.html` reaches all thirty screens that
    extend it, and thirty pictures of the same new menu item is what the operator
    got: "prikazuju se jer je na side bar dodat tab, a ne jer postoji izmena".
    The layout renders the SAME markup on every one of them, so one picture of it
    is the whole story and the rest are copies.

    The input is provenance, not a new heuristic: `affected.from_diff` already
    records WHY each page is in the set. A page whose own template changed is
    never collapsed - `OWN_TEMPLATE_WHY` wins over every shared reason, because
    something on that screen itself is different. A page reached any OTHER way is
    left alone as well: a CSS rule restyles each screen own markup differently,
    while an include renders the same block everywhere. That is the line, and it
    is why this collapses templates and not stylesheets.

    The representative is chosen by the DATA, never by discovery order:

    1. a page whose pair survived the structural sweep beats one that did not -
       otherwise a whole group could hide behind a screen that reported no change
       at all;
    2. then the SHORTEST url path - the entry screen of a segment is the one a
       person recognises;
    3. then the page id, so two equal candidates resolve the same way on every
       machine and in every run.

    Nothing is hidden silently: the representative lists every screen it stands
    for (`represents`) and says so in its caption, and each collapsed pair names
    the one that speaks for it (`represented_by`).
    """
    groups, by_page = {}, {}
    for pair in pairs:
        page_id = pair.get("page_id")
        by_page.setdefault(page_id, []).append(pair)
        why = list(pair.get("why") or [])
        shared = sorted(w for w in why if w.startswith(SHARED_TEMPLATE_WHY))
        # No provenance at all, its own template, or reached some other way too:
        # this page speaks for itself.
        if not why or OWN_TEMPLATE_WHY in why or len(shared) != len(why):
            continue
        groups.setdefault(tuple(shared), set()).add(page_id)

    collapsed = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        shown = {pid for pid in group
                 if any(not p["drop"] for p in by_page.get(pid) or [])}
        rep = sorted(group, key=lambda pid: (
            0 if pid in shown else 1,
            len(str((by_page[pid][0] or {}).get("url") or "")),
            str(pid)))[0]
        others = sorted(pid for pid in group if pid != rep)
        for pair in by_page.get(rep) or []:
            pair["represents"] = others
            pair["caption"] = vannotate.caption_from_facts(
                pair.get("title") or pair.get("page_id"), pair.get("state"),
                others=len(others))
        for pid in others:
            for pair in by_page.get(pid) or []:
                pair["represented_by"] = rep
                pair["drop"] = True
                pair["drop_reason"] = ("same shared layout as %s, which is the "
                                       "picture of it" % rep)
                collapsed += 1
    return collapsed


def _region_specs(after, bshot, anchors) -> list:
    """The regions of ONE pair, merged and in reading order.

    Every anchor of the diff is resolved, not the best one: a commit that adds
    four fields to a form is four places to look, and taking the first left three
    of them invisible.

    **Each region carries the rectangle for BOTH sides, aligned on content.** The
    same anchor is looked up on the other shot: when it is there, the region has
    simply MOVED, and the other side is cut around it where it actually sits
    (`annotate.shift_rect`). Cutting both sides at the same page coordinates is
    only right while the layout holds still, and this engine mostly photographs
    insertions - everything below one shifts by hundreds of pixels, and the crop
    pair then shows two different parts of the form side by side.

    Three cases, and the third is the one that must not be faked:

    * **the anchor is on both sides** - the region moved; each side is cut around
      its own copy of it;
    * **the anchor is on this side only, and the other side WAS measured** - the
      element is new (or gone). The other side is cut at the same coordinates,
      which is exactly the place it was inserted into or removed from;
    * **the other side was never measured** (a before shot from an older engine,
      a picture adopted from the baseline) - then "moved" and "new" cannot be
      told apart, so there is no honest before. The region ships with the after
      half alone rather than a rectangle nobody can justify.
    """
    specific = [a for a in (anchors or [])
                if a.get("kind") in SPECIFIC_ANCHOR_KINDS]
    # The KEY is the fact: a shot measured for these anchors has the map, one
    # from an older engine has nothing at all, and "measured and not found" is
    # what tells a new element from an unmeasured one.
    measured = {"after": isinstance(after.get("regions"), dict),
                "before": isinstance((bshot or {}).get("regions"), dict)}

    def collect(use):
        specs, seen = [], set()
        for shot, side in ((after, "after"), (bshot or {}, "before")):
            other = (bshot or {}) if side == "after" else after
            other_side = "before" if side == "after" else "after"
            for anchor in use:
                key = vlocate.anchor_key(anchor)
                here = vlocate.regions_of(shot).get(key) or []
                there = vlocate.regions_of(other).get(key) or []
                for i, region in enumerate(here):
                    rect = vannotate.region_rect(region)
                    box = vannotate.frame_box(region)
                    if not rect or not box:
                        continue
                    # The same element measured on both sides is ONE region; the
                    # after side already spoke for it.
                    ident = (box["x"], box["y"], box["w"], box["h"])
                    if ident in seen:
                        continue
                    seen.add(ident)
                    twin = there[i] if i < len(there) else (there[0] if there else None)
                    if twin:
                        rect_other = vannotate.shift_rect(
                            rect, box, vannotate.frame_box(twin))
                    elif measured[other_side]:
                        rect_other = rect      # new here: the place it went into
                    else:
                        rect_other = None      # unmeasured: no honest counterpart
                    specs.append({
                        "rect": rect if side == "after" else rect_other,
                        "rect_before": rect_other if side == "after" else rect,
                        "outline": [box], "side": side,
                        "kind": region.get("kind") or "fallback"})
        # A region measured only on the before side still needs a rectangle on
        # the after picture to be cut from; without one there is nothing to show.
        return [sp for sp in specs if sp["rect"]]

    # NO FALLBACK TO CLASS ANCHORS. `hr-info-row` is on every row of the card,
    # so cropping to "the first few elements carrying it" outlines six untouched
    # rows and misses the one that is new - which is what the operator saw twice
    # and called "ikonice i nebuloze". No region is better than a region on an
    # untouched element; the two full pictures are still there.
    specs = collect(specific)
    merged = vannotate.merge_regions(specs)
    merged.sort(key=lambda r: (r["rect"][1], r["rect"][0]))
    return merged


def _crop_regions(after, bshot, anchors, out_dir, pid, errors) -> tuple:
    """`(regions, found)` - the customer-facing crop list and how many regions
    were worth showing before the cap.

A region whose two cuts are identical never reaches
    the customer (`annotate.crop_regions` does not even write it), which is what
    makes it safe to resolve every match of a generic anchor. A region with no
    honest before is kept, with `before.crop` null - the missing half is
    information, and the HUD renders the gap.
    """
    specs = _region_specs(after, bshot, anchors)
    if not specs:
        return [], 0
    try:
        cut = vannotate.crop_regions(bshot.get("png_path"), after.get("png_path"),
                                     specs, out_dir, pid)
    except Exception as exc:                                    # noqa: BLE001
        errors.append({"stage": "pair", "page_id": after.get("page_id"),
                       "url": after.get("url"),
                       "error": "crop: %s: %s" % (type(exc).__name__, str(exc)[:120])})
        return [], 0

    regions = [{"before": {"crop": row.get("before")},
                "after": {"crop": row.get("after")},
                "crop_from": row.get("side") or "after",
                "crop_kind": row.get("kind") or "fallback"} for row in cut]
    return regions[:MAX_REGIONS], len(regions)


def _has_picture(shot) -> bool:
    """The pair IS the two pictures, so a record without one is not a pair. The
    HUD resolves an image straight out of this path now, and a manifest naming a
    file that is not there renders as a broken card."""
    png = str((shot or {}).get("png_path") or "")
    return bool(png) and Path(png).is_file()


# --------------------------------------------------------------------------- #
#  The rolling baseline: <repo>/.visual-baseline/
#
#      index.json                     one entry per page+state (below)
#      shots/<pair_id>.png            the picture - what `before` adopts
#      shots/<pair_id>.json           the capture record: signature, geometry,
#                                     measured boxes, title, url
#      thumbs/<pair_id>.webp          ~15 KB, for recognising a page at a glance
#      archive/<stamp>/<pair_id>.*    every picture a promotion replaced, kept
#
#  It is the app's LAST APPROVED look, which is exactly what the next ticket's
#  "before" is. Two commands write it and they share ONE writer (`_baseline_put`):
#  `baseline` seeds it over the whole inventory, `promote` moves one run's
#  approved AFTER shots into it.
#
#  It holds full PNGs now, so it is per machine and belongs in .gitignore - the
#  version-1 index (a phash and a thumb per page) was small enough to commit and
#  had nothing a customer could look at.
# --------------------------------------------------------------------------- #
def baseline_dir(repo) -> Path:
    return Path(repo) / ".visual-baseline"


def baseline_index_path(repo) -> Path:
    return baseline_dir(repo) / "index.json"


def load_baseline(repo) -> dict:
    idx = store.load(baseline_index_path(repo)) or {}
    idx.setdefault("shots", {})
    return idx


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def baseline_key(index, page_id, state) -> str:
    """The stable id of one page+state in the baseline.

    Looked up by (page_id, state) first, so promoting the same screen twice
    reuses its slot instead of growing a second one. The slug can collide
    between two different page ids (it is ASCII-folded and cut), and the loser
    gets a suffix rather than silently overwriting the winner's picture.
    """
    shots = (index or {}).get("shots") or {}
    for bkey, rec in shots.items():
        if rec.get("page_id") == page_id and rec.get("state") == state:
            return bkey
    base = pair_id(page_id, state)
    n, bkey = 1, base
    while bkey in shots:
        n += 1
        bkey = "%s_%d" % (base, n)
    return bkey


def _baseline_put(repo, index, shot, stamp, from_run="") -> dict:
    """Write ONE shot into the baseline store, archiving what it replaces.

    `{"key", "action": "written"|"unchanged", "archived": path|""}`. Identical
    content is a no-op: that is what makes promotion idempotent, and it keeps
    the archive a list of real previous looks rather than one copy per run.
    """
    root = baseline_dir(repo)
    png_in = Path(shot["png_path"])
    bkey = baseline_key(index, shot.get("page_id"), shot.get("state"))
    rec = dict(((index.get("shots") or {}).get(bkey)) or {})
    digest = _sha256(png_in)
    png_out = root / "shots" / ("%s.png" % bkey)
    meta_out = root / "shots" / ("%s.json" % bkey)

    if rec.get("sha256") == digest and png_out.is_file():
        return {"key": bkey, "action": "unchanged", "archived": ""}

    archived = ""
    if png_out.is_file():
        adir = root / "archive" / stamp
        adir.mkdir(parents=True, exist_ok=True)
        # Kept forever, by the operator's decision ("sve slike before ostavljas
        # u folderu zapamcene"). MOVE, never delete: the picture a customer was
        # shown as "before" must stay reachable after the next promotion.
        shutil.move(str(png_out), str(adir / png_out.name))
        if meta_out.is_file():
            shutil.move(str(meta_out), str(adir / meta_out.name))
        archived = str(adir / png_out.name)

    png_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(png_in), str(png_out))
    thumb_rel = "thumbs/%s.webp" % bkey
    try:
        vannotate.thumbnail(png_out, root / thumb_rel)
    except Exception as exc:                                    # noqa: BLE001
        # A thumb is a convenience; the picture and its hash are the artefact.
        print(_ascii("baseline: thumb failed for %s (%s)" % (bkey, str(exc)[:80])))
        thumb_rel = ""

    record = {k: v for k, v in (shot or {}).items() if k != "png_path"}
    record.update({"baseline_key": bkey, "from_run": str(from_run or ""),
                   "at": _now(), "sha256": digest})
    store.atomic_write_json(meta_out, record)

    index.setdefault("shots", {})[bkey] = {
        "page_id": shot.get("page_id"), "state": shot.get("state"),
        "url": shot.get("url") or "",
        "title": shot.get("title") or "", "png": "shots/%s.png" % bkey,
        "meta": "shots/%s.json" % bkey, "thumb": thumb_rel,
        "phash": vcompare.phash(png_out), "sha256": digest,
        "at": record["at"], "from_run": record["from_run"]}
    return {"key": bkey, "action": "written", "archived": archived}


def _write_baseline_index(repo, index, cfg=None) -> Path:
    def merge(current):
        rows = dict((current or {}).get("shots") or {})
        rows.update(index.get("shots") or {})
        return {"version": BASELINE_VERSION,
                "base_url": (cfg or {}).get("base_url") or index.get("base_url") or "",
                "shots": {k: {kk: rows[k][kk] for kk in sorted(rows[k])}
                          for k in sorted(rows)}}

    p = baseline_index_path(repo)
    update_json(p, merge)
    return p


def adopt_baseline_shots(repo, cfg, dest_dir, page_rows, run_key="", taken=None):
    """The stored baseline as this run's BEFORE shots.

    `(shots, page_ids, notes)`. The picture is COPIED into the run's own folder:
    a manifest that pointed into `.visual-baseline/` would show a different
    "before" the moment the next promotion overwrote it.

    Adoption is per PAGE, never per state. A page whose baseline has only the
    base state must not have its modal captured LIVE and filed as a before -
    that is a post-change picture on the pre-change side, and it is the one
    thing this engine may never do. The missing state shows up in `unpaired`
    with its real reason instead.
    """
    index = load_baseline(repo)
    shots_idx = index.get("shots") or {}
    notes = []
    if not shots_idx:
        return {}, set(), notes
    if index.get("base_url") and cfg.get("base_url") and \
            index["base_url"] != cfg["base_url"]:
        notes.append("baseline was taken against %s, this run is %s - not adopted"
                     % (index["base_url"], cfg["base_url"]))
        return {}, set(), notes

    root = baseline_dir(repo)
    dest = Path(dest_dir)
    taken = {} if taken is None else taken
    out, adopted = {}, set()
    for row in page_rows:
        rows = [(bkey, rec) for bkey, rec in sorted(shots_idx.items())
                if rec.get("page_id") == row["page_id"]]
        if not rows:
            continue
        # A baseline this very run promoted is this run's own AFTER shot. Handing
        # it back as the before would make the pair a picture of itself.
        own = [bkey for bkey, rec in rows if rec.get("from_run") == str(run_key or "")
               and str(run_key or "")]
        if own:
            notes.append("%s: baseline was promoted by this run, capturing live"
                         % row["page_id"])
            continue
        page_shots = {}
        for bkey, rec in rows:
            png = root / str(rec.get("png") or "")
            if not png.is_file():
                notes.append("%s: baseline entry %s has no picture on disk"
                             % (row["page_id"], bkey))
                page_shots = {}
                break
            meta = store.load(root / str(rec.get("meta") or "")) or {}
            dest.mkdir(parents=True, exist_ok=True)
            copy_to = dest / ("%s.png" % bkey)
            shutil.copyfile(str(png), str(copy_to))
            shot = {k: v for k, v in meta.items()
                    if k not in ("baseline_key", "sha256", "from_run", "at")}
            shot.update({"page_id": row["page_id"],
                         "state": rec.get("state") or meta.get("state") or "base",
                         "png_path": str(copy_to),
                         "title": shot.get("title") or row.get("title") or "",
                         "why": row.get("why") or [],
                         # The provenance the pair loop reads: on a baseline the
                         # measured boxes belong to whichever ticket promoted it,
                         # so an anchor that does not resolve was not measured -
                         # it is NOT evidence that the element was absent.
                         "source": "baseline",
                         "baseline_at": rec.get("at") or "",
                         "baseline_from_run": rec.get("from_run") or ""})
            page_shots[pair_id(row["page_id"], shot["state"], taken)] = shot
        if page_shots:
            out.update(page_shots)
            adopted.add(row["page_id"])
    return out, adopted, notes


def promote_baseline(key, pair_ids=None, repo=None) -> dict:
    """**The promotion API.** Make run `key`'s AFTER shots the repo's baseline.

    `key` is the run folder (what `run_key()` produced and what the HUD lists as
    `work_id`); `pair_ids` limits it to the pairs the operator approved; `repo`
    defaults to the repository the manifest was captured against.

    Returns `{"promoted": [...], "unchanged": [...], "skipped":
    [{pair_id, reason}], "archived": [...], "baseline": path, "at": stamp}`.

    Explicit and idempotent, both on purpose:

    * `after` never calls it. Promotion says "this look is now the truth", which
      is a decision about approved work, not about a capture succeeding.
    * a second call promotes nothing (same bytes, same baseline) and archives
      nothing, so the HUD may call it again after a retry without growing the
      archive or losing the previous picture.
    * a page whose after shot errored, was skipped, did not load (`status` 0 or
      >= 400) or has no file on disk is REFUSED with a reason. A broken screen
      must never become the picture the next ticket calls "before".
    """
    man = load_manifest(key)
    if not man:
        return {"error": "no manifest for %s" % key, "promoted": [], "skipped": [],
                "unchanged": [], "archived": []}
    repo = Path(repo or man.get("repo") or ".")
    after_shots = ((man.get("shots") or {}).get("after")) or {}
    wanted = set(pair_ids or [])
    failed_pages = {e.get("page_id") for e in (man.get("errors") or [])
                    if e.get("stage") in ("after", "capture")}

    index = load_baseline(repo)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = {"promoted": [], "unchanged": [], "skipped": [], "archived": [],
           "at": stamp, "baseline": str(baseline_index_path(repo))}

    for pid, shot in sorted(after_shots.items()):
        if wanted and pid not in wanted:
            continue
        reason = ""
        png = str((shot or {}).get("png_path") or "")
        status = (shot or {}).get("status")
        if not png or not Path(png).is_file():
            reason = "the after shot has no picture on disk"
        elif shot.get("page_id") in failed_pages:
            reason = "the after capture recorded an error for this page"
        elif isinstance(status, int) and (status == 0 or status >= 400):
            reason = "the page answered HTTP %s on the after pass" % status
        if reason:
            out["skipped"].append({"pair_id": pid, "reason": reason})
            continue
        try:
            res = _baseline_put(repo, index, shot, stamp, from_run=key)
        except (OSError, ValueError) as exc:
            out["skipped"].append({"pair_id": pid,
                                   "reason": "%s: %s" % (type(exc).__name__,
                                                         str(exc)[:120])})
            continue
        if res["action"] == "unchanged":
            out["unchanged"].append(pid)
            continue
        out["promoted"].append(pid)
        if res["archived"]:
            out["archived"].append(res["archived"])

    if out["promoted"]:
        _write_baseline_index(repo, index)
        update_json(manifest_path(key), lambda cur: {
            **(cur or man), "promoted": {"at": _now(), "pairs": out["promoted"],
                                         "baseline": out["baseline"]}})
    return out


def verify_promotion(key, repo=None, pair_ids=None) -> dict:
    """Prove the promotion LANDED, before anything is deleted.

    `{"ok": bool, "verified": [pair_id], "missing": [{pair_id, reason}]}`.

    Three things are checked per shot, and all three are needed: the index NAMES
    the page (a picture on disk that nothing points at is not a baseline), the
    file EXISTS, and its bytes are the run's bytes (both hashes, so a truncated
    copy or a stale slot from an earlier ticket cannot pass as this one).

    It reads the index off DISK rather than the in-memory one promotion built:
    the question is what a later `before` will find there, not what we intended
    to write.
    """
    man = load_manifest(key)
    if not man:
        return {"ok": False, "verified": [],
                "missing": [{"pair_id": "*", "reason": "no manifest for %s" % key}]}
    repo = Path(repo or man.get("repo") or ".")
    index = load_baseline(repo)
    shots = (index or {}).get("shots") or {}
    after_shots = ((man.get("shots") or {}).get("after")) or {}
    wanted = set(pair_ids or [])
    out = {"ok": True, "verified": [], "missing": []}
    for pid, shot in sorted(after_shots.items()):
        if wanted and pid not in wanted:
            continue
        png_in = str((shot or {}).get("png_path") or "")
        bkey = None
        for k, rec in shots.items():
            if (rec.get("page_id") == shot.get("page_id")
                    and rec.get("state") == shot.get("state")):
                bkey = k
                break
        reason = ""
        if bkey is None:
            reason = "the baseline index does not name this page and state"
        else:
            png_out = baseline_dir(repo) / str(shots[bkey].get("png") or "")
            if not png_out.is_file():
                reason = "the baseline index names %s, which is not on disk" % png_out.name
            else:
                try:
                    landed = _sha256(png_out)
                    source = _sha256(png_in) if png_in and Path(png_in).is_file() else ""
                except OSError as exc:
                    reason = "unreadable: %s" % type(exc).__name__
                else:
                    if landed != str(shots[bkey].get("sha256") or ""):
                        reason = "the baseline picture does not match its own index entry"
                    elif source and landed != source:
                        reason = "the baseline picture is not this run's after shot"
        if reason:
            out["ok"] = False
            out["missing"].append({"pair_id": pid, "reason": reason})
        else:
            out["verified"].append(pid)
    return out


def finalize_run(key, repo=None, force=False) -> dict:
    """A finished run: **promote, verify, and only then delete the folder.**

    `{"promoted": [...], "unchanged": [...], "verified": [...], "deleted": bool,
      "folder": path, "reason": "..."}`.

    `force=True` is THE OPERATOR'S ESCAPE HATCH, and nothing else's. It still
    promotes and still verifies — everything savable is saved first — but a
    promotion that refuses, or a verification that comes up short, no longer
    HOLDS the folder: it is removed anyway and `reason` records exactly what was
    given up. Without it a run whose after pass broke on one single page sits in
    the gallery for ever with no way for anybody to clear it (measured: a demo
    run with no `shots` section could not be dismissed by any button on screen,
    operator 2026-08-28). The automatic sweep must NEVER pass it: there nobody is
    looking, and failing loudly is the whole point of the order below.

    THE ORDER IS THE WHOLE POINT and is not negotiable. The run folder holds the
    only copies of the pictures; a delete that runs before a promotion that
    failed loses them permanently. So: promote first, then read the baseline back
    off disk and prove every picture is there, and only a run that clears both
    is removed. Anything short of that leaves the folder exactly where it is and
    says why - a run left behind is a nuisance, a lost picture is not
    recoverable.

    EVERY after shot is promoted, not just the approved ones. Rejecting a pair
    means "do not send this to the customer"; it is a decision about the message,
    never a claim that the screenshot is wrong. The picture is still the app's
    true current state and therefore the correct reference for the next
    comparison (operator's decision, 2026-08-23).

    WHEN this may run at all - every pair decided, and nothing of this work still
    waiting to be delivered - is the HUD's question, because it is the only side
    that can see the ticket's outbox. This function refuses nothing on that
    ground and must not be called without it.
    """
    man = load_manifest(key)
    out = {"promoted": [], "unchanged": [], "verified": [], "deleted": False,
           "folder": "", "reason": ""}
    # A run with no manifest is not a run: there is nothing to promote and
    # nothing that says which repo it belongs to. `force` does not delete it
    # either — a folder we cannot read is exactly the folder to leave alone.
    if not man:
        out["reason"] = "no manifest for %s" % key
        return out
    # Under `force` each check still runs and still records what it found; only
    # the `return` is dropped, and the reasons accumulate so the operator is told
    # everything that was given up, not just the first thing.
    lost = []

    def stop(reason):
        """Record `reason`. Returns True when it must also end the function."""
        if force:
            lost.append(reason)
            return False
        out["reason"] = reason
        return True

    repo = Path(repo or man.get("repo") or ".")
    res = promote_baseline(key, repo=repo)
    out["promoted"] = res.get("promoted") or []
    out["unchanged"] = res.get("unchanged") or []
    if res.get("error"):
        if stop(str(res["error"])):
            return out
    if res.get("skipped"):
        row = res["skipped"][0]
        if stop("promotion refused %d page(s), first: %s (%s)"
                % (len(res["skipped"]), row.get("pair_id"), row.get("reason"))):
            return out
    ver = verify_promotion(key, repo=repo)
    out["verified"] = ver.get("verified") or []
    if not ver.get("ok"):
        row = (ver.get("missing") or [{}])[0]
        if stop("the promotion did not land for %d page(s), first: %s (%s)"
                % (len(ver.get("missing") or []), row.get("pair_id"),
                   row.get("reason"))):
            return out
    if not out["verified"]:
        if stop("nothing was promoted, so nothing is proven - the run stays"):
            return out
    out["reason"] = "; ".join(lost)

    root = shots_root().resolve()
    folder = (root / str(key)).resolve()
    # The one irreversible step in the engine, so the jail is re-checked HERE
    # rather than trusted from the caller: a key that resolves outside the shots
    # root deletes nothing at all. `force` does not relax this one — it is about
    # WHERE we delete, never about whether the operator meant it.
    if not folder.is_relative_to(root) or folder == root or not folder.is_dir():
        out["reason"] = "the run folder does not resolve inside the shots root"
        return out
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        out["reason"] = "could not remove the run folder: %s" % type(exc).__name__
        return out
    out["deleted"] = True
    out["folder"] = str(folder)
    return out


def cmd_promote(args, cfg) -> int:
    key = run_key(args)
    man = load_manifest(key)
    if not man:
        return emit_error("usage", "no run on record for %s" % key, [{
            "key": "run", "question": "Za koji tiket/work-id da promovisem snimke?",
            "example": str(manifest_path(key))}])
    recorded = str(man.get("repo") or "")
    if recorded and Path(recorded) != Path(args.repo).resolve():
        return emit_error("usage",
                          "run %s was captured against %s, not %s"
                          % (key, recorded, Path(args.repo).resolve()),
                          [{"key": "repo",
                            "question": "Snimci su pravljeni nad drugim repozitorijumom. "
                                        "Koji je ispravan?",
                            "example": recorded}])
    res = promote_baseline(key, pair_ids=args.pairs, repo=args.repo)
    for row in res["skipped"]:
        print(_ascii("  NOT PROMOTED %-40s %s" % (row["pair_id"], row["reason"])))
    print(_ascii("promote: %d new baseline shot(s), %d unchanged, %d skipped, "
                 "%d archived -> %s"
                 % (len(res["promoted"]), len(res["unchanged"]), len(res["skipped"]),
                    len(res["archived"]), res["baseline"])))
    if args.json:
        print(json.dumps(res, ensure_ascii=True))
    return 0


# --------------------------------------------------------------------------- #
#  before
# --------------------------------------------------------------------------- #
def _merge_affected(current, aff) -> dict:
    """A second `before` for another file ADDS to what the run already knows.

    `from_diff` is OR-ed rather than first-write-wins: the same anchor recorded
    off the whole file by an early pass and out of a real hunk by a later one IS
    diff-derived, and the pass that could see the hunk is the one telling the
    truth. The other direction never happens - a hunk anchor cannot become less
    diff-derived later.
    """
    prev = current.get("affected") or {}
    pages = {p["page_id"]: p for p in (prev.get("pages") or [])}
    for p in aff["pages"]:
        pages.setdefault(p["page_id"], p)
    anchors = list(prev.get("anchors") or [])
    at = {(a["file"], a["kind"], a["value"]): a for a in anchors}
    for a in aff["anchors"]:
        known = at.get((a["file"], a["kind"], a["value"]))
        if known is None:
            at[(a["file"], a["kind"], a["value"])] = a
            anchors.append(a)
        else:
            if a.get("from_diff") and not known.get("from_diff"):
                known["from_diff"] = True
                known["page_ids"] = a.get("page_ids") or known.get("page_ids")
            # What a string RENDERS as is derived, not observed: a pass that
            # knows it is right, and an older record that does not simply
            # predates the catalogue being read. Without this the anchors of a
            # run whose `before` pass is older keep matching nothing at all.
            if a.get("alternates") and not known.get("alternates"):
                known["alternates"] = a["alternates"]
    provenance = dict(prev.get("provenance") or {})
    for rel, how in (aff.get("provenance") or {}).items():
        # HUNK wins whichever pass saw it: the file WAS diffable at some point
        # in this run, and that is the fact the anchors were read against.
        if provenance.get(rel) != vaffected.HUNK:
            provenance[rel] = how
    return {"pages": list(pages.values()), "anchors": anchors,
            "unlocated": (prev.get("unlocated") or []) + aff["unlocated"],
            "changed_files": sorted(set(prev.get("changed_files") or [])
                                    | set(aff["changed_files"])),
            "provenance": provenance,
            "overflow": aff["overflow"], "source": aff["source"]}


def cmd_before(args, cfg) -> int:
    key = run_key(args)
    aff = vaffected.from_diff(args.repo, files=args.files, config=cfg)
    if aff.get("error"):
        return emit_error(aff["error"], aff.get("path", ""), aff.get("questions"))
    # `before` means the state that PRECEDES the edit, and a LIVE capture cannot
    # enforce that - it can only say so. Every named file already having a hunk
    # means the edit is written, so a live shot of it is the best picture
    # available but it is NOT the original. The warning is printed further down,
    # and only for the pages actually captured live: with the rolling baseline
    # the same condition is the normal, correct flow, and a warning that fires
    # on every good run is a warning nobody reads.
    edit_already_made = bool(aff.get("considered")) and \
        "whole_file" not in (aff.get("source") or "")

    known = load_manifest(key)
    known_before = (known.get("shots", {}) or {}).get("before") or {}
    already = {v.get("page_id") for v in known_before.values()}
    todo = [p for p in aff["pages"] if args.force or p["page_id"] not in already]
    for pid in sorted({p["page_id"] for p in aff["pages"]} & already):
        if not args.force:
            print(_ascii("before %-40s already captured (kept)" % pid))

    taken = _taken_from(known_before)
    shots, errors, skipped, done = {}, [], [], 0
    adopted = {}
    if todo and not args.force:
        # The stored baseline IS the before when it has the page: no live
        # capture, nothing to revert, and it works after the edit is written.
        adopted, adopted_pages, notes = adopt_baseline_shots(
            args.repo, cfg, shots_root() / key / "before", todo,
            run_key=key, taken=taken)
        for note in notes:
            print(_ascii("before: %s" % note))
        for row in todo:
            if row["page_id"] in adopted_pages:
                print(_ascii("before %-40s from the stored baseline" % row["page_id"]))
        todo = [p for p in todo if p["page_id"] not in adopted_pages]
    if todo:
        if edit_already_made:
            print(_ascii("before: WARNING - %s already differ(s) from HEAD and "
                         "%d page(s) have no stored baseline, so their 'before' "
                         "is POST-edit; the original state is gone"
                         % (", ".join(sorted(aff["considered"])[:3]), len(todo))))
        shots, errors, skipped, done = _capture_side(
            cfg, args, key, "before", todo,
            anchors_by_page=_crop_anchors(_merge_affected(known, aff),
                                          only_from_diff=False), taken=taken)
    # `done` stays the LIVE count: a capture failure must still be a capture
    # failure when some other page came from the baseline.
    shots = {**adopted, **shots}

    def merge(current):
        man = current or _new_manifest(key, args, cfg)
        man.setdefault("shots", {}).setdefault("before", {})
        man["shots"].setdefault("after", {})
        man.setdefault("errors", [])
        man.setdefault("skipped", [])
        man.setdefault("pairs", [])
        man["affected"] = _merge_affected(man, aff)
        for pid, shot in shots.items():
            # setdefault, not assignment: a concurrent `before` may have captured
            # this page first, and the EARLIER shot is the one that predates the
            # edit. --force is the only way past it.
            if args.force:
                man["shots"]["before"][pid] = shot
            else:
                man["shots"]["before"].setdefault(pid, shot)
        man["errors"].extend(errors)
        man["skipped"].extend(skipped)
        man["before_at"] = man.get("before_at") or _now()
        man["captured_at"] = _now()
        return man

    manifest = update_json(manifest_path(key), merge)
    if todo and not done:
        return emit_error("capture_failed",
                          "no page captured; %d error(s)" % len(errors),
                          [{"key": "capture",
                            "question": "Nijedna stranica nije snimljena. Da li je "
                                        "tenant/rola ispravna za ove ekrane?",
                            "example": str(manifest_path(key))}])
    covered = _covered_files(aff, args.accept_unlocated)
    if covered:
        _record_state(key, args, "before", covered)
    n_adopted = len({s.get("page_id") for s in adopted.values()})
    if not todo and not n_adopted:
        print(_ascii("before: nothing new to capture (%d changed file(s), %d page(s) known)"
                     % (len(aff["changed_files"]), len(manifest["affected"]["pages"]))))
    else:
        print(_ascii("before: %d page(s) captured, %d from the baseline, "
                     "%d state(s) skipped -> %s"
                     % (done, n_adopted, len(skipped), manifest_path(key))))
    return _unlocated_gate(aff, args)


# --------------------------------------------------------------------------- #
#  after
# --------------------------------------------------------------------------- #
def cmd_after(args, cfg) -> int:
    key = run_key(args)
    manifest = load_manifest(key)
    before_shots = ((manifest or {}).get("shots") or {}).get("before") or {}
    # NO baseline is a question, not a default: the pair is what the customer is
    # owed. `--no-baseline` is the operator's ANSWER for the case where the
    # before state is genuinely unobtainable - the migration already ran, or the
    # capture engine was broken when the baseline was due. It produces a
    # standalone, LABELLED artefact and never writes into `shots.before`, so a
    # post-change capture still cannot become a pre-change one.
    no_baseline = not before_shots
    if no_baseline and not getattr(args, "no_baseline", False):
        return emit_error("no_before", "no before shots for %s" % key, [{
            "key": "before",
            "question": "Nema 'pre' snimaka za ovaj tiket. Da li 'pre' stanje jos "
                        "moze da se snimi (vrati izmene pa `before`), ili ga vise "
                        "nema (migracija je vec izvrsena, snimanje je bilo u kvaru)?",
            "example": "moze -> shoot.py before --repo %s   |   ne moze -> shoot.py "
                       "after --repo %s --no-baseline --baseline-reason 'zasto'"
                       % (args.repo, args.repo)}], exit_code=6)
    if no_baseline:
        manifest = manifest or _new_manifest(key, args, cfg)
    elif getattr(args, "no_baseline", False):
        print(_ascii("after: --no-baseline ignored, %d before shot(s) exist"
                     % len(before_shots)))

    recorded = manifest.get("affected") or {}
    files = args.files or recorded.get("changed_files") or None
    rev = str(getattr(args, "diff_from_commit", "") or "")
    dtext = _diff_of_commit(args.repo, rev) if rev else ""
    if rev and not dtext:
        return emit_error("bad_commit", str(rev), [{
            "key": "commit",
            "question": "Ne mogu da procitam izmenu iz commita %r." % rev,
            "example": "proveri rev: git show --stat %s" % rev}])
    aff = vaffected.from_diff(args.repo, files=None if dtext else files,
                              diff_text=dtext or None, config=cfg)
    if aff.get("error"):
        return emit_error(aff["error"], aff.get("path", ""), aff.get("questions"))

    merged = _merge_affected(manifest, aff)
    rows = {p["page_id"]: p for p in merged["pages"]}

    # The after pass inherits the before side's pair-id book, so the two sides
    # agree on the id of a page whose slug collides with another's.
    after_shots, errors, skipped, done = _capture_side(
        cfg, args, key, "after", list(rows.values()),
        anchors_by_page=_crop_anchors(merged), taken=_taken_from(before_shots))
    if not done:
        update_json(manifest_path(key), lambda cur: {**(cur or manifest),
                                                     "after_at": _now(),
                                                     "errors": (cur or {}).get("errors", [])
                                                     + errors})
        return emit_error("capture_failed", "no page captured on the after pass")

    crop_anchors = _crop_anchors(merged)
    crops_dir = shots_root() / key / "crops"
    pairs = []

    for pid, after in sorted(after_shots.items()):
        before = before_shots.get(pid)
        if before is None and not no_baseline:
            continue                                # reported by `unpaired_rows`
        bshot = before or {}
        page_id = after.get("page_id")
        # THE PAIR IS THE TWO PICTURES AND THE NAME OF THE SCREEN. No box, no
        # crop, no "novo", no token in the caption: every one of those is a claim
        # about what changed, and a claim the customer cannot check is worth less
        # than nothing (a real ticket shipped `novo: Novi korisnik: analytics
        # analitika: "location_city"`, a material-icon name). What decides
        # whether this screen is worth showing AT ALL is still `sweep`, below.
        if not _has_picture(after):
            errors.append({"stage": "pair", "page_id": page_id,
                           "url": after.get("url"),
                           "error": "the after shot has no picture on disk"})
            continue
        if before is not None and not _has_picture(bshot):
            errors.append({"stage": "pair", "page_id": page_id,
                           "url": after.get("url"),
                           "error": "the before shot has no picture on disk"})
            continue
        sweep = None if before is None else vcompare.sweep_changed(before, after)
        caption = vannotate.caption_from_facts(
            after.get("title") or bshot.get("title") or page_id, after.get("state"))
        # WHERE TO LOOK, never what to think - once per changed REGION, because
        # one change routinely touches several places on a screen. No region ->
        # no crop, and the pair is the two full pictures exactly as before.
        #
        # No crop without a before picture either: the crop exists to be
        # COMPARED, and one enlarged region beside nothing invites a comparison
        # that is not there. The standalone record stays the one full picture it
        # always was.
        regions, found = ([], 0) if before is None else _crop_regions(
            after, bshot, crop_anchors.get(page_id), crops_dir, pid, errors)
        # KEEP-vs-DROP IS STRUCTURAL, and only structural. The dev database is
        # the production one, so counters, chart values and row counts move on
        # their own; a pixel ratio made every such page read as "this changed".
        # Same DOM tree + same block geometry + different numbers = not a change.
        # `comparable` False (a signature missing on one side) is NOT a verdict:
        # the pair is kept, because a page we cannot compare is precisely the one
        # somebody has to look at.
        # ...AND STRUCTURE-BLINDNESS IS NOT EVIDENCE OF NO CHANGE WHEN THE
        # PAGE'S OWN TEMPLATE WAS EDITED. The comparison deliberately ignores
        # text, so a pure LABEL change — a corrected translation, a reworded
        # button — produces an identical tree and identical geometry and was
        # dropped as "nothing happened". That hid the only pair a wording
        # ticket has (DEMO#53464, "Zapremnica" -> "Primka": one screen, one
        # word, and the gallery showed the operator nothing).
        # `OWN_TEMPLATE_WHY` already marks this case and already outranks every
        # shared reason in the layout-only collapse below — the same fact has
        # to win here, or the two mechanisms disagree about the same page.
        own_template = OWN_TEMPLATE_WHY in (after.get("why") or [])
        drop = (bool(sweep) and sweep.get("comparable")
                and not sweep.get("structural") and not own_template)
        pairs.append({
            "pair_id": pid, "page_id": page_id, "url": after.get("url"),
            "title": after.get("title") or bshot.get("title"), "state": after.get("state"),
            # "none" -> there was no before shot to compare against, so this
            # record is ONE picture. Absent or "captured" -> a real pair. The HUD
            # renders the difference, so "no baseline" is never read as "nothing
            # changed".
            "baseline": "none" if before is None else "captured",
            "caption": caption,
            # The captures themselves, not copies of them: with nothing drawn on
            # top there is no second image to make, and the HUD addresses a
            # picture by (work, pair, side) anyway.
            "before": {"full": bshot.get("png_path") or None},
            "after": {"full": after.get("png_path")},
            # ONE ENTRY PER CHANGED REGION, top to bottom in page coordinates.
            # `crop_from` and `crop_kind` are per region and are diagnostics: the
            # customer never sees either, they are how a bad crop is found
            # without re-running the capture.
            "regions": regions,
            # Before the cap, and after dropping the regions whose two crops were
            # identical. A reader has to be able to tell a truncated list from a
            # complete one.
            "regions_found": found,
            "sweep": sweep,
            "drop": bool(drop),
            "drop_reason": (sweep or {}).get("why", "") if drop else "",
            "why": after.get("why") or bshot.get("why") or []})

    # Without a baseline every shot would be "after_only" carrying "state
    # appeared after the change" - false, and it would double every record.
    unpaired = [] if no_baseline else unpaired_rows(
        before_shots, after_shots,
        {"errors": (manifest.get("errors") or []) + errors,
         "skipped": (manifest.get("skipped") or []) + skipped})
    # A screen that DID NOT EXIST before is a pair with one picture, not a
    # diagnostic row. It is lifted OUT of `unpaired` (never listed twice) and
    # joins the pairs before the collapse, so a new page that also extends a
    # changed layout is judged by the same rule as every other pair.
    fresh, consumed = new_page_pairs(unpaired, after_shots, cfg, args.repo)
    pairs.extend(fresh)
    unpaired = [r for r in unpaired if r.get("pair_id") not in set(consumed)]

    collapsed = _collapse_layout_only(pairs)

    def merge(current):
        man = current or manifest
        man["shots"]["after"] = after_shots
        # The anchors on record gain what this pass could read out of the hunks
        # (`from_diff` is OR-ed), so a later reader - the HUD, a re-run - sees
        # the same provenance the box was chosen on.
        man["affected"] = _merge_affected(man, aff)
        # An `after` pass replaces its own previous result; the before shots and
        # anything another process recorded meanwhile are left alone.
        man["errors"] = [e for e in (man.get("errors") or [])
                         if e.get("stage") not in ("after", "compare", "pair")] + errors
        man["skipped"] = [s for s in (man.get("skipped") or [])
                          if s.get("stage") != "after"] + skipped
        man["pairs"] = pairs
        man["unpaired"] = unpaired
        # ONE place says whether this run has a baseline at all; the per-pair
        # flag is the same fact per record.
        man["baseline"] = ({"state": "none", "declared_at": _now(),
                            "reason": str(getattr(args, "baseline_reason", "") or "")[:200]}
                           if no_baseline else {"state": "captured"})
        man["after_at"] = _now()
        man["captured_at"] = _now()
        return man

    manifest = update_json(manifest_path(key), merge)
    covered = _covered_files(aff, args.accept_unlocated)
    if covered:
        _record_state(key, args, "after", covered)
    keep = [p for p in pairs if not p["drop"]]
    if no_baseline:
        print(_ascii("after: NO BASELINE - %d standalone shot(s), nothing to "
                     "compare against (%s) -> %s"
                     % (len(pairs),
                        str(getattr(args, "baseline_reason", "") or "razlog nije naveden"),
                        manifest_path(key))))
    else:
        print(_ascii("after: %d pair(s), %d shown, %d new screen(s), %d with no "
                     "structural change, %d layout-only behind a representative, "
                     "%d unpaired, %d state(s) skipped -> %s"
                     % (len(pairs), len(keep), len(fresh),
                        len(pairs) - len(keep) - collapsed,
                        collapsed, len(unpaired), len(skipped), manifest_path(key))))
    if args.json:
        print(json.dumps(manifest, ensure_ascii=True))
    return _unlocated_gate(aff, args)


# --------------------------------------------------------------------------- #
#  sweep
# --------------------------------------------------------------------------- #
def sweep_store_path(repo) -> Path:
    return shots_root() / "_sweep" / ("%s.json" % vcapture.slug(Path(repo).name, "app"))


def cmd_sweep(args, cfg) -> int:
    """Structure + geometry over every page, against the previous sweep. No
    screenshots at all - the signature comes from the DOM, so a full pass costs
    navigation time only."""
    inv = vpages.inventory(cfg, args.repo)
    rows = vpages.usable(inv)
    if args.limit:
        rows = rows[:int(args.limit)]
    prev = store.load(sweep_store_path(args.repo)) or {}
    prev_pages = prev.get("pages") or {}

    session = vcapture.open_session(cfg, args.repo)
    now, results, errors = {}, [], []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for row in rows:
                url = cfg["base_url"] + row["url"]
                try:
                    shots, _ = vcapture.capture_page(session, url, tmp,
                                                     page_id=row["page_id"],
                                                     states=[], screenshot=False)
                except Exception as exc:                        # noqa: BLE001
                    errors.append({"stage": "sweep", "page_id": row["page_id"], "url": url,
                                   "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
                    continue
                if not shots:
                    continue
                shot = shots[0]
                now[row["page_id"]] = {"dom_signature": shot["dom_signature"],
                                       "geometry": shot["geometry"], "url": row["url"],
                                       "title": row["title"], "at": _now()}
                old = prev_pages.get(row["page_id"])
                if old:
                    verdict = vcompare.sweep_changed(old, now[row["page_id"]])
                    if verdict["structural"]:
                        results.append({"page_id": row["page_id"], "url": row["url"],
                                        "title": row["title"], **verdict})
    finally:
        session.close()

    out = {"version": MANIFEST_VERSION, "repo": str(Path(args.repo).resolve()),
           "at": _now(), "compared_against": prev.get("at") or "",
           "pages": now, "needs_review": results, "errors": errors,
           "skipped": [{"page_id": p["page_id"], "url": p["url_raw"],
                        "reason": p["skipped_reason"]}
                       for p in inv if p.get("skipped_reason")]}
    p = sweep_store_path(args.repo)
    update_json(p, lambda _cur: out)
    print(_ascii("sweep: %d page(s), %d need review, %d error(s), %d unfetchable -> %s"
                 % (len(now), len(results), len(errors), len(out["skipped"]), p)))
    for r in results[:20]:
        print(_ascii("  CHANGED %-40s %s" % (r["page_id"], r["why"])))
    if args.json:
        print(json.dumps({"needs_review": results, "errors": errors}, ensure_ascii=True))
    return 0


# --------------------------------------------------------------------------- #
#  baseline (seed)
# --------------------------------------------------------------------------- #
def cmd_baseline(args, cfg) -> int:
    """SEED the rolling baseline over the whole inventory: the current picture
    of every reachable page becomes the "pre" state the next ticket compares
    against, so the first ticket to touch a screen already has a before.

    Base state only, on purpose - modals cost a page load each over 232 pages,
    and `promote` adds the states a ticket actually worked on. It writes through
    the SAME `_baseline_put` as promotion: one store, one writer, one archive.
    """
    inv = vpages.inventory(cfg, args.repo)
    rows = vpages.usable(inv)
    if args.limit:
        rows = rows[:int(args.limit)]

    index = load_baseline(args.repo)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session = vcapture.open_session(cfg, args.repo)
    errors, written, unchanged, archived = [], 0, 0, 0
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for row in rows:
                url = cfg["base_url"] + row["url"]
                try:
                    shots, _ = vcapture.capture_page(session, url, tmp,
                                                     page_id=row["page_id"], states=[])
                except Exception as exc:                        # noqa: BLE001
                    errors.append({"page_id": row["page_id"], "url": url,
                                   "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
                    continue
                if not shots or not shots[0].get("png_path"):
                    continue
                shot = shots[0]
                status = shot.get("status")
                if isinstance(status, int) and (status == 0 or status >= 400):
                    # Same rule as promotion: a screen that did not load must
                    # never become the picture a ticket calls "before".
                    errors.append({"page_id": row["page_id"], "url": url,
                                   "error": "HTTP %s" % status})
                    continue
                shot["title"] = shot.get("title") or row.get("title") or ""
                try:
                    res = _baseline_put(args.repo, index, shot, stamp,
                                        from_run="baseline")
                except (OSError, ValueError) as exc:
                    errors.append({"page_id": row["page_id"], "url": url,
                                   "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
                    continue
                if res["action"] == "unchanged":
                    unchanged += 1
                else:
                    written += 1
                    archived += 1 if res["archived"] else 0
    finally:
        session.close()

    p = _write_baseline_index(args.repo, index, cfg)
    print(_ascii("baseline: %d page(s) written, %d unchanged, %d archived, "
                 "%d error(s) -> %s" % (written, unchanged, archived, len(errors), p)))
    print(_ascii("baseline: full images live here now - add .visual-baseline/ to "
                 ".gitignore if it is not there yet"))
    if errors and args.json:
        print(json.dumps({"errors": errors}, ensure_ascii=True))
    return 0


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
COMMANDS = {"before": cmd_before, "after": cmd_after, "promote": cmd_promote,
            "sweep": cmd_sweep, "baseline": cmd_baseline}

#: Commands that never open a browser. The server/Pillow probes in `main()`
#: exist so a capture does not fail halfway; `promote` copies files that are
#: already on disk, and refusing to run it because the dev server is down would
#: strand approved pairs.
OFFLINE_COMMANDS = ("promote",)


def build_parser():
    ap = argparse.ArgumentParser(prog="shoot.py", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=sorted(COMMANDS))
    ap.add_argument("--repo", required=True, help="the app repository root")
    ap.add_argument("--diff-from-commit", default="", metavar="REV",
                    help="after: take the change from this commit instead of "
                         "the working tree - the only way to regenerate the "
                         "pairs of a ticket that is already committed")
    ap.add_argument("--files", nargs="*", default=None,
                    help="changed files (default: git working tree vs HEAD + staged)")
    ap.add_argument("--ticket", default="", help="helpdesk ticket id, names the run folder")
    ap.add_argument("--work-id", dest="work_id", default="", help="worklog work id")
    ap.add_argument("--force", action="store_true",
                    help="before: re-capture pages that already have a before shot")
    ap.add_argument("--accept-unlocated", dest="accept_unlocated", action="store_true",
                    help="the operator has confirmed a changed file shows on no "
                         "screen: record it as covered instead of asking again")
    ap.add_argument("--no-baseline", dest="no_baseline", action="store_true",
                    help="after: there is no 'before' and there cannot be one "
                         "(the migration already ran, the capture was broken). "
                         "Produces standalone shots MARKED as having no "
                         "baseline; never records them as a before")
    ap.add_argument("--baseline-reason", dest="baseline_reason", default="",
                    help="after: why no baseline exists - recorded in the "
                         "manifest and shown in the gallery")
    ap.add_argument("--pairs", nargs="*", default=None,
                    help="promote: only these pair ids (default: every after "
                         "shot of the run)")
    ap.add_argument("--limit", type=int, default=0, help="sweep/baseline: first N pages")
    ap.add_argument("--json", action="store_true", help="print the result JSON too")
    return ap


def _probe_pillow() -> None:
    """Playwright is probed explicitly and Pillow was not - so on a machine
    without it the `after` pass failed inside every annotate call, kept going,
    and exited 0 having produced no picture at all. The engine's product is
    images; a missing imaging library is a question, not a quiet zero."""
    try:
        from PIL import Image                                   # noqa: F401
    except Exception as exc:                                    # noqa: BLE001
        raise vcapture.VisualError("pillow_missing", str(exc)[:200], [{
            "key": "pillow",
            "question": "Pillow (PIL) nije instaliran u ovom Python-u - bez njega "
                        "nema nijedne slike. Instalirati?",
            "example": "python -m pip install pillow"}])


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo)
    if not repo.is_dir():
        return emit_error("usage", "no such repo: %s" % repo,
                          [{"key": "repo", "question": "Koji je koren repozitorijuma?",
                            "example": "E:/POSAO/acme-audit"}])
    # FIRST, and before the config is read or the server is probed: a run with no
    # ticket has nothing to produce, so reading a repo config and launching a
    # browser for it is work spent on pictures nobody may send.
    refused = _require_ticket(args)
    if refused:
        return refused
    cfg = vconfig.load_config(repo)
    if vconfig.is_error(cfg):
        print(json.dumps({**cfg, "exit": EXIT["needs_config"]}, ensure_ascii=True))
        return EXIT["needs_config"]
    try:
        # BEFORE the command, not inside it: a run whose files map to no page
        # never opened a session, so the health check that lives there never
        # ran, and the pass reported success against a server that was down.
        if args.command not in OFFLINE_COMMANDS:
            vcapture.check_server(cfg)
        _probe_pillow()
        return COMMANDS[args.command](args, cfg)
    except vcapture.VisualError as exc:
        return emit_error(exc.code, exc.detail, exc.questions)
    except KeyboardInterrupt:
        return emit_error("usage", "interrupted")


if __name__ == "__main__":
    raise SystemExit(main())
