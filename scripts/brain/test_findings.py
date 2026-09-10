#!/usr/bin/env python3
"""A finding is filed in the repo it is about, once, and never silently.

Built on a temp repo. What is pinned: a finding with no evidence is REFUSED
(without it the next reader cannot check the claim); the same `key` filed twice
does not produce a second file, because a sweep tool reports the same
candidates every run; `asked` and `answered` are distinct, so a question is not
put to the operator twice; and the round trip through Markdown loses nothing,
including a value with a colon in it.

  python scripts/brain/test_findings.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import findings as F  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "myrepo"
        repo.mkdir()
        R = str(repo)

        print("add()")
        fp, note = F.add(R, "is_required ne blokira zaključivanje", where="audits/validators.py:81",
                         kind="defect", severity="critical", evidence="Čita se samo na toj liniji.",
                         found_by="undertaker", key="dead:audits/validators.py:81", today="2026-09-07")
        ok(fp is not None and fp.exists(), f"file written: {note}")
        ok(fp.parent == repo / ".claude" / "findings", f"inside the repo, not the brain: {fp.parent}")
        ok(fp.stem.startswith("2026-09-07-is-required"), f"id is dated and readable: {fp.stem}")
        ok("never commits" in note, "it says it did not commit for you")

        print("refusals")
        _p, n1 = F.add(R, "nešto", evidence="")
        ok(_p is None and "rumour" in n1, f"no evidence -> refused: {n1}")
        _p, n2 = F.add(R, "", evidence="x")
        ok(_p is None and "what" in n2, "no `what` -> refused")
        _p, n3 = F.add(R, "x", evidence="y", kind="izmišljeno")
        ok(_p is None and "kind must be" in n3, "an unknown kind -> refused")
        _p, n4 = F.add("nema-ovakvog-repoa", "x", evidence="y")
        ok(_p is None and "unknown repo" in n4, "an unknown repo -> refused, nothing written")

        print("the same key is not filed twice")
        _p, n5 = F.add(R, "isti nalaz, drugi tekst", evidence="opet",
                       key="dead:audits/validators.py:81")
        ok(_p is None and "already filed" in n5, f"a sweep re-run adds nothing: {n5}")
        ok(len(list((repo / ".claude" / "findings").glob("*.md"))) == 1, "still one file")

        print("load() / round trip")
        rows = F.load(R)
        ok(len(rows) == 1, "one finding read back")
        f = rows[0]
        ok(f["severity"] == "critical" and f["kind"] == "defect", "scalars survive")
        ok(f["where"] == "audits/validators.py:81", f"a value WITH a colon survives: {f['where']}")
        ok("Čita se samo" in f["body"], "the evidence is in the body, readable on GitHub")
        ok(f["status"] == "open", "no question -> open")

        print("asked / answered are different states")
        fq, _ = F.add(R, "brisati mrtav wrapper?", evidence="Nema pozivaoca.",
                      question="Brišemo ili ostavljamo?", kind="question", today="2026-09-07")
        got = [x for x in F.load(R) if x["id"] == fq.stem][0]
        ok(got["status"] == "asked", "a finding that carries a question is `asked`, not `open`")
        ok("## Pitanje" in got["body"], "the question is in the body too, for a human reader")
        F.update(R, fq.stem, status="answered", answer="Briši.")
        got = [x for x in F.load(R) if x["id"] == fq.stem][0]
        ok(got["status"] == "answered" and got["answer"] == "Briši.",
           "the answer is stored WITH the finding, so nothing asks again")

        print("a borrowed idea must carry an expiry")
        _p, nb = F.add(R, "pozajmljena ideja bez roka", evidence="odnekud",
                       origin="icm-architect (MIT)")
        ok(_p is None and "review_by" in nb, f"origin without review_by -> refused: {nb}")
        fb, _ = F.add(R, "pozajmljena ideja sa rokom", evidence="odnekud",
                      origin="icm-architect (MIT)", review_by="2026-01-01", today="2026-09-07")
        got = [x for x in F.load(R) if x["id"] == fb.stem][0]
        ok(got["origin"].startswith("icm") and got["review_by"] == "2026-01-01",
           "origin and review date survive the round trip")
        ok(F.overdue(got, today="2026-09-07") is True, "past its date -> overdue")
        ok(F.overdue(got, today="2025-01-01") is False, "before its date -> not overdue")
        ok(F.overdue({"review_by": ""}, today="2026-09-07") is False,
           "a finding with no review date is never overdue")
        s_ov = F.summary(F.load(R), today="2026-09-07")[R]
        ok(s_ov["overdue"] == 1, f"counted where health can see it: {s_ov}")

        print("summary()")
        s = F.summary(F.load(R))[R]
        ok(s["total"] == 3 and s["open"] == 3, f"all three still open-ish: {s}")
        ok(s["critical"] == 1, "the critical one is counted")
        ok(s["asked"] == 0, "answered no longer counts as waiting on you")
        F.update(R, "2026-09-07-is-required", status="done", resolution="Pozvano iz workflow-a.",
                 ticket="DEMO#01621")
        s2 = F.summary(F.load(R))[R]
        ok(s2["open"] == 2 and s2["critical"] == 0, f"closing removes it from open: {s2}")
        done = [x for x in F.load(R) if x["status"] == "done"][0]
        ok(done["ticket"] == "DEMO#01621" and "Pozvano" in done["resolution"],
           "the resolution and the ticket it became are kept")

        print("a workaround records what would end it")
        fx, _ = F.add(R, "kapija koja postoji jer alat nesto ne radi", evidence="danas",
                      dies_when="Claude Code sam upise koji je agent pisao commit",
                      today="2026-09-07")
        gx = [x for x in F.load(R) if x["id"] == fx.stem][0]
        ok(gx["dies_when"].startswith("Claude Code sam"),
           f"the exit condition survives the round trip: {gx['dies_when'][:40]}")
        fy, _ = F.add(R, "zanatsko pravilo bez roka trajanja", evidence="danas")
        gy = [x for x in F.load(R) if x["id"] == fy.stem][0]
        ok(gy.get("dies_when", "") == "",
           "a craft rule carries none -- it is not invented to fill the field")

        print("declining is the one status nothing re-opens")
        fd, _ = F.add(R, "predlog koji necemo", evidence="probano, ne isplati se",
                      today="2026-09-07")
        open_before = F.summary(F.load(R))[R]["open"]
        _p, nd = F.update(R, fd.stem, status="wontfix")
        ok(_p is None and "resolution" in nd, f"wontfix without a reason -> refused: {nd}")
        still = [x for x in F.load(R) if x["id"] == fd.stem][0]
        ok(still["status"] == "open", "the refusal wrote nothing -- it is still open")
        F.update(R, fd.stem, status="wontfix", resolution="Nema korisnika za to.")
        got = [x for x in F.load(R) if x["id"] == fd.stem][0]
        ok(got["status"] == "wontfix" and "Nema korisnika" in got["resolution"],
           "with a reason it is accepted, and the reason is stored WITH it")
        sd = F.summary(F.load(R))[R]
        ok(sd["declined"] == 1, f"counted where health can see it: {sd}")
        # Relative, not a hard number: this assertion broke once when a case was
        # added ABOVE it, which taught nothing about declining and cost a run.
        ok(got["status"] not in F.OPEN_STATES and sd["open"] == open_before - 1,
           f"declining removes exactly one from open -- that is why it needs its "
           f"own counter: {open_before} -> {sd['open']}")

        print("a repo with no findings, and an unknown one")
        ok(F.load(str(Path(td) / "prazan")) == [], "no directory -> [], no raise")
        ok(F.resolve("definitivno-ne-postoji") is None, "an unknown name resolves to nothing")
        ok("brain" in F.repos(), "the brain is a repo like any other -- its own findings live in it")

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
