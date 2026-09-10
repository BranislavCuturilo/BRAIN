"""THE ticket-reference rule: `<MODULE>#<id>` -> (module, id) -> comparison key.

One implementation, read by BOTH ends of the visual-diff pipeline:

* `scripts/visual/shoot.py` refuses a capture whose `--ticket` names no ticket;
* `agent_view/server.py` groups and filters the gallery by the key.

**Why it lives here instead of in either of them** (defect, seen more than
once, root-caused 2026-08-28 on DEMO#43417): the two ends applied DIFFERENT
rules to the same field. `shoot.py` required only a non-empty string, the HUD
required a string that parses into `(module, id)`. A capture run with
`--ticket 43417` — a bare number, no module — therefore succeeded, wrote
correct PNGs, printed `2 pair(s), 2 shown`, and was then **permanently
invisible** in the gallery, with no diagnostic at either end. The operator's
report was "you claim you made the pictures and I cannot see them".

The regex is the trap: `^(.*?)(\\d+)$` lets the non-greedy module group match
EMPTY, so every bare number splits into `("", "43417")` and is rejected by the
`mod and tid` test one line later. Nothing about that is visible from the
calling side, which is exactly why the rule may not be copied.

`scripts/brain/` is the home because `agent_view/server.py` already loads
`visual_gate` from here by path, and the visual engine loads foreign modules
by path under an alias (`isolate.load_module`). Keep this module dependency
free — stdlib only — so both loaders can take it unchanged.
"""
import re

#: `<anything><digits>` with the module part non-greedy. A bare number leaves
#: the module empty, which `parse` refuses — that is the whole point.
TICKET_RUN = re.compile(r"^(.*?)(\d+)$")


def parse(raw):
    """`(module, ticket_id)` from a reference, or None when it names no ticket.

    Accepts both spellings — `DEMO#43417` and `VEZ43417` — and splits them the
    same way. Grants nothing on its own: the module still has to resolve to a
    store file and the id to a ticket inside it.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    mod, sep, tid = s.partition("#")
    if not sep:
        m = TICKET_RUN.match(s)
        if m is None:
            return None
        mod, tid = m.group(1), m.group(2)
    mod, tid = mod.strip(), tid.strip()
    return (mod, tid) if mod and tid else None


def key(raw) -> str:
    """The "is this the same ticket" key: module upper-cased, id without its
    leading zeros. `DEMO#05513`, `DEMO#5513` and `VEZ05513` are one ticket and
    not three. `""` when the reference names no ticket."""
    ref = parse(raw)
    return "" if ref is None else ref[0].upper() + "#" + (ref[1].lstrip("0") or "0")


def names_a_ticket(raw) -> bool:
    """True when this reference can be filed under a ticket at all.

    The check a producer must run BEFORE it writes anything: a reference that
    fails here yields images nobody can ever approve or send.
    """
    return parse(raw) is not None


def modules_for_repo(repo, root=None) -> list:
    """Every module in the ticket store whose repo is `repo`, sorted.

    Used ONLY to make a refusal helpful — "you meant DEMO#43417" — never to
    resolve a ticket automatically: a repo can serve more than one module, and
    picking one would be a guess about whose customer receives the pictures.
    Returns [] on any failure; this must never raise into a capture run.
    """
    try:
        import sys
        from pathlib import Path

        here = Path(__file__).resolve().parent
        tickets = here.parent / "tickets"
        if str(tickets) not in sys.path:
            sys.path.insert(0, str(tickets))
        import store                                          # noqa: PLC0415

        base = Path(root) if root else store.default_store()
        want = Path(str(repo)).resolve()
        found = []
        for fp in sorted(base.glob("*.json")):
            if not store.is_module_file(fp):
                continue
            try:
                data = store.load(fp)
            except Exception:                                  # noqa: BLE001
                continue
            recorded = ((data.get("project") or {}) if isinstance(
                data.get("project"), dict) else {}).get("repo", "")
            mapped = store.module_repo(base, fp.stem, recorded)
            if not mapped:
                continue
            try:
                if Path(mapped).resolve() == want:
                    found.append(fp.stem)
            except OSError:
                continue
        return found
    except Exception:                                          # noqa: BLE001
        return []
