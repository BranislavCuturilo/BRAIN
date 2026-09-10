#!/usr/bin/env python3
"""PostToolUse: run the deterministic reviewer on the views file just written.

**Why a hook and not a command.** `review.py` found an unauthenticated
cross-tenant CSV export the first time it was pointed at a real project. It
found it because someone ran it. A tool that has to be remembered is a tool
that runs after the defect ships -- the same finding `require_skill.py` and
`verify_gate.py` were built on. This one costs no tokens and no API key, so
there is nothing to weigh against running it every time.

**The one rule that keeps it from becoming wallpaper: report only what THIS
edit is about.** FUK carried eleven findings before any of them were fixed.
Printing eleven on every edit is the fourteen-reminder-hooks failure again --
measured, that produced zero skill invocations in a day. So a finding is shown
only when the view that is the odd one out lives in the file just written. Edit
a well-guarded view and this says nothing at all.

**It advises rather than blocks.** Unlike `require_skill.py`, a drift finding
is a SHAPE, not a defect -- four of FUK's first eleven were legitimate
variation, and the login view will never have a permission check. Blocking on a
shape would be blocking on a guess.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

MAX_SHOWN = 3


def app_root(views_file: Path) -> Path | None:
    """The Django app directory whose siblings this file's views belong with.

    A `views.py` sits directly in the app; a `views/` package sits one level
    down. Anything else is not a shape this understands, and guessing wider
    would scan an entire project on every keystroke.
    """
    if views_file.name == "views.py":
        return views_file.parent
    if views_file.parent.name in ("views", "viewsets"):
        return views_file.parent.parent
    if views_file.name.startswith("views") and views_file.suffix == ".py":
        return views_file.parent
    return None


def main() -> int:
    # Fail silent on anything unexpected. A nudge that reports its own
    # breakage as if it were a finding is worse than a nudge that stays quiet.
    try:
        raw = sys.stdin.read(200_000)
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:                                           # noqa: BLE001
        return 0

    path = ((payload.get("tool_input") or {}).get("file_path") or "")
    if not path.endswith(".py"):
        return 0

    try:
        f = Path(path).resolve()
    except OSError:
        return 0
    if not f.is_file():
        return 0

    app = app_root(f)
    if app is None or not app.is_dir():
        return 0

    # Never in the brain itself: it holds no Django views, and scanning it on
    # every edit to a script would be pure cost.
    try:
        f.relative_to(ROOT)
        return 0
    except ValueError:
        pass

    try:
        import review                                            # noqa: PLC0415
        ctx = review.build(app)
        if not ctx.views:
            return 0
        findings = []
        for _name, (fn, _w, _y) in review.RULES.items():
            try:
                findings += fn(ctx)
            except Exception:                                    # noqa: BLE001
                continue        # one broken rule must not silence the others
    except Exception:                                           # noqa: BLE001
        return 0

    here = str(f).replace("\\", "/")
    mine = [x for x in findings if x["where"].rsplit(":", 1)[0] == here]
    if not mine:
        return 0

    # no-page-context is one fact per file, not one per view: three lines of
    # "write docs/pages/X.md" would crowd out a real defect below them.
    npc = [x for x in mine if x["rule"] == "no-page-context"]
    if npc:
        names = [x.get("url_name", x["view"]) for x in npc]
        mine = [x for x in mine if x["rule"] != "no-page-context"] + [{
            "rule": "no-page-context", "view": f"{len(npc)} screen(s)",
            "where": npc[0]["where"], "severity": "note",
            "message": (f"no docs/pages file: {', '.join(names[:5])}"
                        + (f", +{len(names) - 5} more" if len(names) > 5 else "")
                        + " -- write it while the answers are in your head"),
        }]

    # Every rule writes its own `message`; reading sibling-drift's private
    # fields here broke the moment a second rule existed.
    mine.sort(key=lambda x: x.get("severity") != "high")
    lines = []
    for x in mine[:MAX_SHOWN]:
        mark = "!!" if x.get("severity") == "high" else "!"
        lines.append(f"  {mark} [{x['rule']}] {x['view']} "
                     f"(line {x['where'].rsplit(':', 1)[1]}): {x['message']}")
    more = f"\n  ({len(mine) - MAX_SHOWN} more in this file.)" if len(mine) > MAX_SHOWN else ""

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "BRAIN review — the deterministic rules found something in the "
            "file you just wrote:\n\n" + "\n".join(lines) + more +
            "\n\nThese are SHAPES, not verdicts — each is either a defect or a "
            "deliberate exception, and reading the view tells you which. Do "
            "not 'fix' one by copying a guard you have not understood: check "
            "what the LIST view for this model narrows by and apply the same "
            "narrowing (/brain:craft-security rule 2). `!!` means nothing "
            "protects it at all. If it is deliberate, say so in one line and "
            "move on.\n"
            "Full sweep: python ~/.claude/skills/brain/scripts/review.py <app>")}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
