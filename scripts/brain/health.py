#!/usr/bin/env python3
"""Is the brain in good shape? Silent when it is.

Run by a SessionStart hook in --quiet mode, so it must say NOTHING when
everything is fine. A maintenance reminder that fires every session is noise,
and noise gets ignored -- which is how a system stops being maintained.

  health.py           the full report
  health.py --quiet   print only real problems; exit 1 if any (for the hook)
  health.py --fix     regenerate what can be regenerated (docs), then report
  health.py --no-git  skip the working-tree/push checks (for CI, which has neither)
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docs as docs_mod                                       # noqa: E402
import usage as usage_mod                                     # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
MAX_DESC = 600
MAX_BODY = 12_000
MAX_ALWAYS = 6_000          # tokens always in context, across all descriptions

#: Claude Code's own constants for the skill listing, read out of the shipped
#: binary rather than the docs, which do not mention any of this. Settings keys
#: `skillListingBudgetFraction` and `skillListingMaxDescChars` override the
#: first and third; `SLASH_COMMAND_TOOL_CHAR_BUDGET` overrides the budget
#: outright with an absolute character count.
LISTING_FRACTION = 0.01          # z2o
LISTING_CHARS_PER_TOKEN = 4      # cjn
LISTING_PER_SKILL = 1_536        # V2o
#: No equivalent exists for agents -- this is ours, purely to keep the number
#: visible.
LISTING_AGENTS_WATCH = 12_000


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def claude_binary() -> Path | None:
    """The installed Claude Code executable, or None.

    Returning None is a real answer and must stay silent. A check that cannot
    find the thing it inspects has established nothing, and reporting that as a
    problem is the false alarm that gets the whole session-start check ignored.
    """
    import os                                                   # noqa: PLC0415
    import shutil                                               # noqa: PLC0415

    guesses = []
    exe = shutil.which("claude")
    if exe:
        guesses.append(Path(exe))
    for base in (os.environ.get("APPDATA", ""), os.environ.get("HOME", ""),
                 os.path.expanduser("~")):
        if not base:
            continue
        guesses += [
            Path(base) / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
            Path(base) / ".npm-global/lib/node_modules/@anthropic-ai/claude-code/cli.js",
            Path(base) / "node_modules/@anthropic-ai/claude-code/bin/claude",
        ]
    for g in guesses:
        try:
            if g.is_file() and g.stat().st_size > 1_000_000:
                return g
        except OSError:
            continue
    return None


def hook_events_present(events: set) -> tuple[set, str]:
    """(events NOT found in the binary, a note about how it was checked).

    **Why this exists.** Some events this brain hooks -- `InstructionsLoaded`,
    `StopFailure` -- are real in the runtime and appear in NEITHER the public
    docs nor the hook table Claude Code shows the model. They were read out of
    the shipped binary. Anthropic can rename one without a changelog line, and
    the failure mode is the worst shape there is: the hook simply never fires,
    nothing errors, and a rule that has stopped working reads exactly like one
    that still does.

    So the names are re-checked against the binary that is actually installed.
    Cached per version, because the file is ~200 MB and this runs at session
    start.
    """
    exe = claude_binary()
    if exe is None:
        return set(), ""

    try:
        stamp = f"{exe}|{exe.stat().st_mtime_ns}|{exe.stat().st_size}"
    except OSError:
        return set(), ""

    cache = ROOT / "journal" / "hook-events.json"
    try:
        got = json.loads(cache.read_text(encoding="utf-8"))
        if got.get("stamp") == stamp and set(got.get("checked", [])) >= events:
            missing = events - set(got.get("found", []))
            return missing, "cached"
    except (OSError, ValueError):
        pass

    found = set()
    needles = {e: e.encode("ascii", "ignore") for e in events}
    try:
        with exe.open("rb") as fh:
            tail = b""
            while True:
                chunk = fh.read(8 << 20)
                if not chunk:
                    break
                blob = tail + chunk
                for name, needle in needles.items():
                    if name not in found and needle in blob:
                        found.add(name)
                if len(found) == len(needles):
                    break
                tail = blob[-64:]          # a name split across two reads
    except OSError:
        return set(), ""

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(
            {"stamp": stamp, "checked": sorted(events), "found": sorted(found),
             "binary": str(exe)}, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return events - found, "read"


def check(with_git: bool = True) -> tuple[list[str], list[str]]:
    """Return (problems, notes). Problems need action; notes are informational.

    `with_git=False` drops the working-tree and push checks. Those describe the
    state of ONE MACHINE, not the state of the brain -- a CI runner always has
    a clean tree and no upstream to be ahead of, so leaving them in would make
    the pipeline report on something it cannot know about.
    """
    problems: list[str] = []
    notes: list[str] = []

    agents = docs_mod.read_agents()
    skills = docs_mod.read_skills()

    # --- generated docs -----------------------------------------------------
    for name in ("AGENTS.md", "SKILLS.md"):
        path = ROOT / "docs" / name
        if not path.exists():
            problems.append(f"docs/{name} missing -- run scripts/brain/docs.py")
    if (ROOT / "docs" / "AGENTS.md").exists():
        want = docs_mod.render_agents(agents)
        if (ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8") != want:
            problems.append("docs/AGENTS.md is stale -- run scripts/brain/docs.py")
    if (ROOT / "docs" / "SKILLS.md").exists():
        want = docs_mod.render_skills(skills)
        if (ROOT / "docs" / "SKILLS.md").read_text(encoding="utf-8") != want:
            problems.append("docs/SKILLS.md is stale -- run scripts/brain/docs.py")

    # --- git ---------------------------------------------------------------
    if with_git:
        dirty = git("status", "--porcelain")
        if dirty:
            problems.append(f"{len(dirty.splitlines())} uncommitted change(s) in the brain")
        ahead = git("rev-list", "--count", "@{u}..HEAD")
        if ahead.isdigit() and int(ahead) > 0:
            problems.append(f"{ahead} commit(s) not pushed -- they exist on this machine only")
        behind = git("rev-list", "--count", "HEAD..@{u}")
        if behind.isdigit() and int(behind) > 0:
            notes.append(f"{behind} commit(s) behind origin -- pull")

    # --- budget ------------------------------------------------------------
    always = sum(len(a["desc"]) for a in agents.values()) + \
             sum(len(s["desc"]) for s in skills.values() if not s["hand"])
    if always // 4 > MAX_ALWAYS:
        problems.append(f"always-in-context is ~{always//4:,} tokens "
                        f"(budget {MAX_ALWAYS:,}) -- tighten descriptions")
    for n, s in skills.items():
        if len(s["desc"]) > MAX_DESC:
            notes.append(f"description over {MAX_DESC}: {n} ({len(s['desc'])})")
        if s["body"] > MAX_BODY:
            problems.append(f"{n} body is {s['body']:,} chars -- split into references/")

    # --- personal skills living OUTSIDE the brain ---------------------------
    # The blind spot this closes: every check above globs inside the brain
    # repo, so a skill installed beside it at ~/.claude/skills/<name>/ was
    # never measured at all. They load into the same session and cost the same
    # tokens; the brain simply could not see them.
    #
    # Found the moment this ran: graphify was a 71,170-char monolith with no
    # references/ -- about 17,800 tokens for the whole session, six times the
    # limit health.py had been enforcing on the brain's own skills every day.
    #
    # These are NOT the brain's to fix, so a merely-oversized one is a note.
    # Past twice the limit it becomes a problem, because at that point it is
    # the largest single thing in the session and nothing was saying so.
    try:
        siblings = ROOT.parent
        if siblings.name == "skills" and siblings.is_dir():
            for path in sorted(siblings.glob("*/SKILL.md")):
                if path.parent.resolve() == ROOT.resolve():
                    continue                      # the brain itself
                text = path.read_text(encoding="utf-8", errors="replace")
                end = text.find("\n---\n", 3)
                body = len(text[end + 5:]) if end > 0 else len(text)
                refs = list((path.parent / "references").rglob("*.md"))
                if body <= MAX_BODY:
                    continue
                where = f"{path.parent.name} (outside the brain)"
                detail = (f"{body:,} chars ~{body//4:,} tok, {len(refs)} reference(s)"
                          f" -- split into references/")
                if body > MAX_BODY * 2:
                    problems.append(f"{where} body is {detail}")
                else:
                    notes.append(f"{where} body is {detail}")
    except OSError:
        pass

    # --- structure ---------------------------------------------------------
    # An archived skill is reported differently from a missing one. Both leave a
    # dangling preload, but the cause and the fix are opposite: a missing skill
    # means something broke, an archived one means the retirement was finished
    # everywhere except here -- and the agent has been running with a silently
    # empty preload ever since.
    archived = {d.name for d in (ROOT / "archive").glob("*") if (d / "SKILL.md").is_file()}
    for name, a in agents.items():
        for pre in a["pre"]:
            if pre in archived:
                problems.append(
                    f"agent {name} preloads {pre}, which is ARCHIVED -- it has been "
                    f"loading nothing since. Drop the preload, or restore the skill "
                    f"(scripts/brain/archive.py {pre} --restore)")
            elif pre not in skills:
                problems.append(f"agent {name} preloads a skill that does not exist: {pre}")
    for md in ROOT.glob("agents/*.md"):
        if md.stem.lower() == "readme":
            problems.append("agents/README.md is parsed AS AN AGENT -- rename to .txt")

    # Frontmatter that does not parse is the worst failure mode here: the file
    # loads with EMPTY metadata, so the agent silently runs with no tool
    # restriction, no model and no preloaded skills, and nothing says so. The
    # usual cause is an unquoted description containing a colon followed by a
    # space, which YAML cannot read as a plain scalar.
    try:
        import yaml                                            # noqa: PLC0415
    except ImportError:
        yaml = None                                            # type: ignore
    if yaml is not None:
        for md in list(ROOT.glob("agents/*.md")) + list(ROOT.glob("skills/*/SKILL.md")):
            if md.stem.lower() == "readme":
                continue
            text = md.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                continue
            end = text.find("\n---\n", 3)
            if end < 0:
                problems.append(f"{md.relative_to(ROOT)}: frontmatter is not closed")
                continue
            try:
                meta = yaml.safe_load(text[4:end])
            except yaml.YAMLError as exc:
                first = str(exc).splitlines()[0]
                problems.append(f"{md.relative_to(ROOT)}: frontmatter will not parse "
                                f"-- loads with EMPTY metadata ({first})")
                continue
            if not isinstance(meta, dict) or not meta.get("name"):
                problems.append(f"{md.relative_to(ROOT)}: frontmatter has no name")

    # A reference nothing points at will never be read.
    for name in skills:
        skill_md = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        # rglob, not glob: references now nest (references/patterns/observer.md),
        # and a check that only looks one level deep hides exactly the orphans
        # that a split creates.
        refs = list((ROOT / "skills" / name / "references").rglob("*.md"))
        linked = skill_md + "".join(r.read_text(encoding="utf-8") for r in refs)
        for ref in refs:
            rel = ref.relative_to(ROOT / "skills" / name / "references").as_posix()
            if rel not in linked and ref.name not in linked and ref.stem not in linked:
                notes.append(f"{name}/references/{rel} is not linked from anywhere")

    # --- workflows parse the way the runtime parses them --------------------
    wf = ROOT / "scripts" / "brain" / "check-workflows.js"
    if wf.exists() and (ROOT / "workflows").is_dir():
        try:
            r = subprocess.run(["node", str(wf)], capture_output=True, text=True, timeout=30)
            if r.returncode:
                for line in r.stdout.splitlines():
                    if "FAIL" in line:
                        problems.append("workflow will not parse:" + line.split("FAIL")[-1].strip())
        except (OSError, subprocess.SubprocessError):
            pass          # node absent is not a brain problem

    # --- the counters themselves --------------------------------------------
    # A miscounting dashboard is worse than no dashboard: it prints a plausible
    # number instead of an error, and the number gets acted on.
    selftest = ROOT / "scripts" / "brain" / "test_usage.py"
    if selftest.exists():
        try:
            r = subprocess.run([sys.executable, str(selftest)], capture_output=True,
                               text=True, timeout=30)
            if r.returncode:
                problems.append("usage counting is broken -- run scripts/brain/test_usage.py")
        except (OSError, subprocess.SubprocessError):
            pass

    # --- registry vs reality -----------------------------------------------
    reg_path = ROOT / "registry.json"
    if reg_path.exists():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        for kind, live in (("skills", skills), ("agents", agents)):
            for n, e in reg.get(kind, {}).items():
                uses = e.get("helped", 0) + e.get("hindered", 0) + e.get("neutral", 0)
                if n not in live and uses and not e.get("baseline"):
                    notes.append(f"registry has scores for a deleted {kind[:-1]}: {n}")

        # score.py needs MIN_USES=5 recorded outcomes before a band means
        # anything, and NOTHING records an outcome automatically -- there is no
        # telemetry for "did this help". So the machinery goes blind silently:
        # the table still renders, every row just reads UNPROVEN, and that looks
        # like a young system rather than a stopped habit.
        #
        # Measured the day this check was written: 373 agent runs counted, 30
        # outcomes recorded, the last one 34 days old. Recording happened in a
        # burst in August and then stopped, and nothing said so for a month.
        # The fix is the same one that finally moved the never-run reviews:
        # make the decay a number somebody has to look at.
        last = max((str(v.get("last")) for kind in ("skills", "agents", "workflows")
                    for v in reg.get(kind, {}).values() if v.get("last")), default="")
        if last:
            try:
                age = (date.today() - date.fromisoformat(last)).days
            except ValueError:
                age = 0
            runs = sum(v.get("n", 0) for v in
                        (usage_mod.collect().get("agents") or {}).values())
            got = sum(v.get("helped", 0) + v.get("hindered", 0) + v.get("neutral", 0)
                      for kind in ("skills", "agents", "workflows")
                      for v in reg.get(kind, {}).values())
            if age > 30:
                problems.append(
                    f"no outcome recorded since {last} ({age}d) while {runs:,} agent "
                    f"run(s) are counted -- every band in score.py reads UNPROVEN "
                    f"because of it (score.py record agent <name> helped \"why\")")
            elif runs and got * 2 < runs:
                # The staleness trigger alone was wrong, and using it caught the
                # bug the same day it shipped: recording ONE outcome silenced it
                # for another 30 days while 376 runs stayed unjudged. Recency is
                # not coverage. ops-maintain's own rule is the threshold -- below
                # half, "the bands are anecdote wearing a number" -- so the ratio
                # gets said out loud even when the last entry is fresh. A note,
                # not a problem: it is a standing condition to improve, and a
                # permanently red line is one nobody reads.
                notes.append(
                    f"score coverage {got}/{runs} ({got * 100 // runs}%) all-time -- below half, "
                    f"every band in score.py is anecdote wearing a number "
                    f"(retro.py reports the same ratio over 30 days, so the two "
                    f"differ by design, not by disagreement)")

    # --- a pointer that resolves to nothing is a rule that never loads -----
    # A reference is worth exactly as much as the path to it. Nine pointers of
    # the form `references/x.md` named a file living in ANOTHER skill: a reader
    # resolving the path from the file it is written in finds nothing and moves
    # on. It stays silent because the surrounding prose usually names the owning
    # skill, so a human fills the gap and never notices -- which is precisely
    # the failure mode of a split that saves context and quietly loses the
    # behaviour. brain-keeper found three by reading; a sweep found six more.
    # This check is here so those six cannot come back.
    try:
        import re as _re                                        # noqa: PLC0415
        for f in sorted((ROOT / "skills").rglob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                for m in _re.findall(r"`(references/[a-z0-9-]+\.md)`", line):
                    if (f.parent / m).exists() or (f.parent.parent / m).exists():
                        continue
                    problems.append(
                        f"{f.relative_to(ROOT).as_posix()}:{i} points at `{m}`, which "
                        f"does not resolve from there -- put the owning skill in the path")
    except OSError:
        pass

    # --- the dashboard is a snapshot, and a stale one reads as "nothing ran" -
    # The failure is silent and misleading: an out-of-date page shows zeros for
    # work that happened after it was generated, which looks like broken
    # telemetry rather than an unrefreshed file.
    dash = ROOT / "docs" / "dashboard.html"
    if dash.exists():
        try:
            newest = max((p.stat().st_mtime for p in
                          (Path.home() / ".claude" / "projects").glob("*/*.jsonl")),
                         default=0)
            age_h = (newest - dash.stat().st_mtime) / 3600
            if age_h > 1:
                notes.append(f"docs/dashboard.html is {age_h:.0f}h behind the newest "
                             f"session -- regenerate before reading it "
                             f"(scripts/brain/dashboard.py)")
        except (OSError, ValueError):
            pass

    # --- did the automatic sync actually work -------------------------------
    # sync.py runs at SessionEnd and is `|| true`, so a broken push would be
    # invisible -- which is the failure mode it was written to remove. It
    # records its outcome; this reads it back.
    try:
        marker = ROOT / "journal" / "sync-last.json"
        if marker.is_file():
            m = json.loads(marker.read_text(encoding="utf-8"))
            if not m.get("ok"):
                problems.append(
                    f"the automatic sync FAILED at {str(m.get('at',''))[:16]} -- "
                    f"the brain is not on the other machine: "
                    f"{str(m.get('detail',''))[:90]}")
    except (OSError, ValueError):
        pass

    # --- periodic reviews ---------------------------------------------------
    # Overdue is a NOTE; badly overdue is a problem. A review that is a day late
    # must not appear at session start -- that is the reminder-as-wallpaper
    # failure again -- but one that has been skipped for twice its period has
    # stopped being a cycle and needs to be seen.
    try:
        import cadence as cadence_mod                           # noqa: PLC0415
        for name, age, period, what, _how in cadence_mod.due():
            if age is None:
                # NEVER run is worse than stale, not better, and `due()` only
                # yields what is already overdue -- so a null age is not missing
                # data, it is a tool that has been due since the day it was
                # written. Filed as a note, seven of them sat there permanently
                # while a tool that ran ONCE and went stale was escalated to a
                # problem. That is inverted severity, and it is what a duty
                # written in prose beside a command decays into.
                problems.append(
                    f"{name} has NEVER run (due every {period}d) -- {what}")
            elif age >= period * 2:
                problems.append(f"{name} last ran {age}d ago (every {period}d) -- {what}")
            else:
                notes.append(f"{name} is due ({age}d, every {period}d)")
    except Exception:                                           # noqa: BLE001
        pass

    # --- the skill listing budget -----------------------------------------
    # Claude Code sends the skill index to the model as one line per skill,
    # `- name: description - when_to_use`, and it enforces a HARD character
    # budget on the whole list. Over budget it does not shorten descriptions
    # proportionally -- it drops them ENTIRELY, skill by skill, leaving
    # `- name` with nothing to route on.
    #
    # The order it drops them in is the part that matters:
    #
    #     priority = usageCount * 0.5 ** (days_since_last_use / 7)
    #
    # A skill never used scores ZERO and is dropped first. Without a
    # description the router cannot select it, so it stays unused, so it stays
    # at zero. **The skill waiting for its moment is exactly the one that goes
    # dark**, which is the opposite of what this brain is for.
    #
    # Verified against @anthropic-ai/claude-code 2.1.258 on 2026-09-06:
    #   LC_ALL=C grep -a -o -E 'function ujn\(e,n,r,o,d=cjn\)\{.{1500}' bin/claude.exe
    #   LC_ALL=C grep -a -o -E 'function kLe\(.{500}' bin/claude.exe
    # Constants there: z2o=0.01 (fraction), cjn=4 (chars/token), V2o=1536
    # (per-skill cap), and the budget is fraction * chars_per_token * the
    # model's ACTUAL context window -- 200_000 is only the fallback.
    try:
        listing = 0
        for n, s in skills.items():
            if s["hand"]:
                continue          # disable-model-invocation: not in the listing
            entry = s["desc"] + (f" - {s['when']}" if s.get("when") else "")
            listing += len(n) + 4 + min(len(entry), LISTING_PER_SKILL)
        listing += max(0, len(skills) - 1)

        for label, window in (("1M", 1_000_000), ("200k", 200_000)):
            budget = int(window * LISTING_CHARS_PER_TOKEN * LISTING_FRACTION)
            if listing <= budget:
                continue
            msg = (f"the skill listing is {listing:,} chars against a "
                   f"{budget:,} budget on a {label}-context model -- over it, "
                   f"descriptions are DROPPED entirely, least-used first, and "
                   f"a skill with no description cannot be routed to")
            # Over on the big window is a live defect; over only on the small
            # one is a cliff worth knowing about before switching models, not
            # something to act on today.
            (problems if window >= 1_000_000 else notes).append(msg)

        for n, s in skills.items():
            entry = s["desc"] + (f" - {s['when']}" if s.get("when") else "")
            if len(entry) > LISTING_PER_SKILL:
                problems.append(
                    f"{n}: description + when_to_use is {len(entry):,} chars, "
                    f"over the {LISTING_PER_SKILL:,} per-skill cap -- the tail "
                    f"is cut off before the model ever sees it")
    except Exception:                                           # noqa: BLE001
        pass

    # The agent briefs ride a DIFFERENT mechanism: `- type: description
    # (Tools: ...)`, with no budget, no cap and no warning. Skills get
    # truncated to protect the context; agents just get expensive. So this is
    # a number to watch, never a threshold to fail.
    try:
        briefs = sum(len(n) + 4 + len(a["desc"]) for n, a in agents.items())
        if briefs > LISTING_AGENTS_WATCH:
            notes.append(f"{len(agents)} agent briefs are {briefs:,} chars "
                         f"(~{briefs//4:,} tok) in every session -- uncapped by "
                         f"design, so nothing will ever truncate or warn")
    except Exception:                                           # noqa: BLE001
        pass

    # --- the hook events this brain depends on still exist ------------------
    # Two of them -- InstructionsLoaded, StopFailure -- are in neither the
    # public docs nor the table Claude Code shows the model; they were read out
    # of the binary. If Anthropic renames one, the hook stops firing and
    # NOTHING errors. That is the failure shape this brain fears most, because
    # a rule that has quietly stopped working reads exactly like one that has
    # not. So the names are verified against the binary actually installed.
    try:
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        events = set(cfg.get("hooks", {}))
        missing, how = hook_events_present(events)
        for e in sorted(missing):
            problems.append(
                f"hooks.json registers `{e}`, which does not appear in the "
                f"installed Claude Code binary -- that hook is firing on "
                f"nothing, silently. Check the release notes and either fix "
                f"the name or drop the hook")
        if how == "read" and not missing:
            notes.append(f"all {len(events)} hook event name(s) verified "
                         f"against the installed binary")
    except Exception:                                           # noqa: BLE001
        pass

    # --- a Stop hook that failed -------------------------------------------
    # verify_gate.py fails OPEN by design, so its silence when it crashes is
    # indistinguishable from its silence when the work was verified. The
    # StopFailure event says which; this is where that lands.
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "brain"))
        import hook_failure                                     # noqa: PLC0415
        fails = hook_failure.recent()
        if fails:
            last = fails[-1]
            problems.append(
                f"{len(fails)} hook failure(s) in the last "
                f"{hook_failure.RECENT_DAYS} days -- most recently "
                f"{last.get('event')} at {str(last.get('at'))[:16]}: "
                f"{str(last.get('error') or last.get('command'))[:90]}. "
                f"A Stop hook that crashes is a gate that is not guarding "
                f"(scripts/brain/hook_failure.py --recent)")
    except Exception:                                           # noqa: BLE001
        pass

    # --- usage -------------------------------------------------------------
    try:
        use = usage_mod.collect()
        cold = [n for n in agents if use["agents"].get(n, {}).get("n", 0) == 0]
        if len(cold) == len(agents) and agents:
            notes.append(f"none of the {len(agents)} agents appear in any transcript yet "
                         f"-- nothing has been exercised")
        elif len(cold) > len(agents) * 0.7:
            notes.append(f"{len(cold)}/{len(agents)} agents never seen in a transcript")
    except Exception:
        pass

    # --- the extension's shipped skills are the brain's references ---------
    # BugReporter/src/skills/*.md is generated out of ui-bootstrap's
    # <!-- ebr:skill --> blocks. A stale copy means the tester's model judges a
    # screen by rules the developers have already moved past -- and the
    # extension cannot tell, because a rule it was not given is not an error.
    # Only checked where the extension is checked out; elsewhere it is nobody's.
    try:
        import extension_skills                                 # noqa: PLC0415
        out = extension_skills.default_out()
        if out.exists():
            diffs = extension_skills.check(out, extension_skills.collect(ROOT))
            if diffs:
                problems.append(
                    f"{len(diffs)} extension skill file(s) behind the brain's "
                    f"references ({', '.join(d.split()[-1] for d in diffs[:3])}"
                    f"{', ...' if len(diffs) > 3 else ''}) -- run "
                    f"scripts/brain/extension_skills.py and commit in the extension")
    except ValueError as e:                                     # a broken source block
        problems.append(f"extension_skills: {str(e).splitlines()[0][:120]}")
    except Exception:                                           # noqa: BLE001
        pass

    # --- the extension's tickets vs docs/pages -------------------------------
    # A ticket the extension called a limitation and the close note calls
    # fixed means a page said "on purpose" about a real defect, and the
    # reporter was told there was nothing to fix. That is the one outcome
    # this whole loop exists to prevent, so it is a problem, not a note.
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "tickets"))
        import ebr_review                                       # noqa: PLC0415
        r = ebr_review.review()
        if r["lies"]:
            problems.append(
                f"{len(r['lies'])} ticket(s) the extension called a limitation were "
                f"closed as fixed -- docs/pages lies for "
                f"{', '.join(s for x in r['lies'] for s in x['screens'][:1]) or '?'} "
                f"(scripts/tickets/ebr_review.py)")
        if r["write_next"]:
            top = r["write_next"][0]
            notes.append(f"{len(r['write_next'])} screen(s) generate extension tickets with no "
                         f"docs/pages file; first: {top['screen']} ({top['tickets']} ticket(s))")
    except Exception:                                           # noqa: BLE001
        pass

    # --- the plan's own limit -------------------------------------------------
    # ops-models says rate-limit headroom is the shared budget; this is the
    # number. Read from the cache when fresh, one short call otherwise; silent
    # when there is no token (API-key auth) -- that is not a brain problem.
    try:
        import usage_limit                                      # noqa: PLC0415
        u = usage_limit.refresh(timeout=5.0)
        if u.get("windows"):
            line = usage_limit.one_line(u)
            if usage_limit.worst(u) >= 80:
                notes.append(f"plan usage high: {line} -- ops-models: no fan-outs on mechanical "
                             f"work until a window resets")
    except Exception:                                           # noqa: BLE001
        pass

    # --- findings filed in the project repos --------------------------------
    # They live in `<repo>/.claude/findings/` so they travel with the code and
    # a collaborator can see them; this is the only place that says out loud
    # that they exist. A finding waiting on the OPERATOR'S ANSWER is the one
    # that stalls silently -- nothing else will ever ask again.
    try:
        import findings as findings_mod                         # noqa: PLC0415
        rows = findings_mod.across()
        for repo, s in sorted(findings_mod.summary(rows).items()):
            if s["critical"]:
                problems.append(
                    f"{repo}: {s['critical']} critical finding(s) still open "
                    f"(scripts/brain/findings.py --repo {repo})")
            if s["asked"]:
                problems.append(
                    f"{repo}: {s['asked']} finding(s) waiting on YOUR answer -- "
                    f"nothing proceeds until you decide "
                    f"(findings.py --repo {repo})")
            if s.get("overdue"):
                problems.append(
                    f"{repo}: {s['overdue']} borrowed idea(s) past their review date -- "
                    f"earn them or drop them (findings.py --repo {repo})")
            elif s["open"]:
                notes.append(f"{repo}: {s['open']} open finding(s)")
            if s.get("declined"):
                notes.append(
                    f"{repo}: {s['declined']} declined finding(s) -- nothing downstream "
                    f"re-opens these; they are visible here or nowhere")
    except Exception:                                           # noqa: BLE001
        pass

    return problems, notes


def main() -> int:
    if "--fix" in sys.argv:
        subprocess.run([sys.executable, str(ROOT / "scripts/brain/docs.py")], check=False)

    problems, notes = check(with_git="--no-git" not in sys.argv)
    quiet = "--quiet" in sys.argv

    if quiet:
        if not problems:
            return 0
        print("BRAIN NEEDS ATTENTION:")
        for p in problems:
            print(f"  - {p}")
        print("  run: python ~/.claude/skills/brain/scripts/brain/health.py")
        return 1

    print("=" * 66)
    print("BRAIN HEALTH")
    print("=" * 66)
    if problems:
        print(f"\n{len(problems)} PROBLEM(S) -- these need action:")
        for p in problems:
            print(f"  ! {p}")
    else:
        print("\nNo problems.")
    if notes:
        print(f"\n{len(notes)} note(s) -- worth a look, not urgent:")
        for n in notes:
            print(f"  - {n}")
    print("\nPeriodic review beyond this: /brain:ops-maintain")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
