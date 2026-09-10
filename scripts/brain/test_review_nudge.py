#!/usr/bin/env python3
"""Proof that the review nudge speaks about THIS edit and stays quiet otherwise.

The reviewer found a real unauthenticated cross-tenant export the first time it
was pointed at a project -- because someone ran it. A hook removes the
remembering. What a hook cannot survive is being noisy: fourteen reminder hooks
in one project produced, measured across a full day, zero skill invocations.

So the rule pinned here is the one that keeps it usable: **a finding is shown
only when the odd-one-out view is in the file just written.** FUK carried eleven
findings; printing eleven on every edit is the same as printing none.

  python scripts/brain/test_review_nudge.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NUDGE = HERE / "review_nudge.py"
FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def run(file_path: str, raw: str | None = None) -> str:
    payload = raw if raw is not None else json.dumps(
        {"tool_input": {"file_path": file_path}})
    p = subprocess.run([sys.executable, str(NUDGE)], input=payload,
                       capture_output=True, text=True, timeout=120)
    return (p.stdout or "").strip()


GUARDED = '''
from django.views.generic import ListView, DetailView, CreateView
from django.contrib.auth.mixins import LoginRequiredMixin

class WidgetListView(LoginRequiredMixin, ListView):
    model = Widget

class WidgetDetailView(LoginRequiredMixin, DetailView):
    model = Widget

class WidgetCreateView(LoginRequiredMixin, CreateView):
    model = Widget
'''

DRIFTED = GUARDED + '''
def export_widget_csv(request):
    return HttpResponse(Widget.objects.all())
'''


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        # An app whose views all agree.
        clean = tmp / "clean"
        clean.mkdir()
        (clean / "views.py").write_text(GUARDED, encoding="utf-8")

        # An app with one view out of step.
        dirty = tmp / "dirty"
        dirty.mkdir()
        (dirty / "views.py").write_text(DRIFTED, encoding="utf-8")
        (dirty / "models.py").write_text("# not a views file\n", encoding="utf-8")

        out = run(str(dirty / "views.py"))
        ck("speaks when the edited file holds the odd view out", bool(out))
        ck("names the view", "export_widget_csv" in out)
        ck("names the model it was compared against", "Widget" in out)
        # Matched the exact sentence before and broke when the wording changed
        # from one rule to seven. Assert the claim, not the phrasing.
        ck("says these are shapes, not verdicts",
           "SHAPE" in out.upper() and "not verdict" in out)
        ck("points at the rule rather than restating it",
           "craft-security" in out)
        ck("says how to run the full sweep", "review.py" in out)

        ck("silent when every sibling agrees", run(str(clean / "views.py")) == "")
        ck("silent on a models.py", run(str(dirty / "models.py")) == "")
        ck("silent on a file that does not exist",
           run(str(dirty / "nope.py")) == "")
        ck("silent on a non-python path", run(str(dirty / "x.html")) == "")

        # The brain has no Django views; scanning it on every script edit would
        # be pure cost, and it must never report on itself.
        ck("silent inside the brain itself", run(str(HERE / "review_nudge.py")) == "")

        # Fail open on anything unexpected: a nudge reporting its own breakage
        # as a finding is worse than one that says nothing.
        ck("silent on a malformed payload", run("", raw="not json") == "")
        ck("silent on an empty payload", run("", raw="{}") == "")
        ck("silent when tool_input is missing",
           run("", raw='{"tool_name": "Edit"}') == "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
