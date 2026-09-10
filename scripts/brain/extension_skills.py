#!/usr/bin/env python3
"""Skills for BugReporter, generated from the brain's own references.

**Why generated and not written in the extension.** The extension's model
judges a tester's report ("the list has no export", "I cannot delete here")
and the developers' agents build the same screens from `ui-bootstrap`. Two
hand-written rulebooks for one screen drift within a month, and the tester's
copy is the one nobody rereads -- so the rule a ticket is judged by is lifted
out of the reference the screen was built from. One source, a generated copy,
and `--check` says when the copy is behind (same shape as `design.py`).

**What is lifted.** Each source carries one block:

    <!-- ebr:skill id="screen-list" name="Lista -- ..." kind="list" scope="compose" -->
    ...the rule, written for the person filing the ticket...
    <!-- /ebr:skill -->

The block sits in the reference on purpose: whoever teaches `lista.md` a new
rule sees the tester's restatement right there and updates both in one edit.
The extension reads the frontmatter (`src/lib/skill-files.js`) and applies a
skill only when the session visited a screen of that `kind`, so a session on
two lists pays for the list rules and nothing else.

**What this refuses.** A listed source with no block, two blocks with one id,
a block without a name -- each is an error, not a warning, because a missing
skill is invisible from the extension's side (it simply is not sent).

  extension_skills.py             write into <PROJECTS_ROOT>/BugReporter/src/skills
  extension_skills.py --out DIR   somewhere else
  extension_skills.py --check     exit 1 if the files on disk are stale or missing
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "tickets"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Sources, in the order their skills are listed (and therefore sent). The
#: always-on one goes first so the model reads the general rule before the
#: screen-specific ones.
SOURCES: tuple[str, ...] = (
    "skills/ui-bootstrap/SKILL.md",
    "skills/ui-bootstrap/references/lista.md",
    "skills/ui-bootstrap/references/forma.md",
    "skills/ui-bootstrap/references/detalji.md",
    "skills/ui-bootstrap/references/dashboard.md",
)

EXTENSION_DIR = "BugReporter"
OUT_REL = "src/skills"
INDEX = "index.json"
SCOPES = ("both", "interview", "compose")

BLOCK_RE = re.compile(
    r"<!--\s*ebr:skill\s+(?P<attrs>[^>]*?)\s*-->\r?\n(?P<body>.*?)\r?\n<!--\s*/ebr:skill\s*-->",
    re.S,
)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


@dataclass
class Skill:
    id: str
    name: str
    kind: list[str]
    scope: str
    body: str
    source: str
    errors: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return f"{self.id}.md"


def default_out() -> Path:
    """Where the extension lives on THIS machine -- the same root the ticket
    store uses, so a machine that moved its projects moves this too."""
    from store import projects_root                       # noqa: PLC0415
    return projects_root() / EXTENSION_DIR / OUT_REL


def blocks(text: str, source: str) -> list[Skill]:
    """Every block in one source. Malformed ones come back WITH their errors
    rather than being dropped, so the report can name the file and the reason."""
    out: list[Skill] = []
    for m in BLOCK_RE.finditer(text):
        attrs = dict(ATTR_RE.findall(m.group("attrs")))
        errs: list[str] = []
        sid = attrs.get("id", "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sid or ""):
            errs.append(f"{source}: block id {sid!r} must be lowercase-kebab")
        if not attrs.get("name", "").strip():
            errs.append(f"{source}: block {sid or '?'} has no name")
        kinds = [k.strip().lower() for k in attrs.get("kind", "any").split(",") if k.strip()] or ["any"]
        scope = attrs.get("scope", "compose").strip() or "compose"
        if scope not in SCOPES:
            errs.append(f"{source}: block {sid or '?'} scope {scope!r} not in {SCOPES}")
        body = m.group("body").strip()
        if len(body) < 80:
            errs.append(f"{source}: block {sid or '?'} body is {len(body)} chars -- not a rule")
        out.append(Skill(sid, attrs.get("name", "").strip(), kinds, scope, body, source, errs))
    return out


def collect(root: Path = ROOT) -> list[Skill]:
    """All skills from SOURCES, in order. Raises with every problem at once."""
    skills: list[Skill] = []
    errors: list[str] = []
    for rel in SOURCES:
        p = root / rel
        if not p.exists():
            errors.append(f"{rel}: source missing")
            continue
        found = blocks(p.read_text(encoding="utf-8"), rel)
        if not found:
            errors.append(f"{rel}: no <!-- ebr:skill --> block")
        for s in found:
            errors.extend(s.errors)
        skills.extend(found)
    seen: dict[str, str] = {}
    for s in skills:
        if s.id in seen:
            errors.append(f"{s.source}: id {s.id!r} already used by {seen[s.id]}")
        seen[s.id] = s.source
    if errors:
        raise ValueError("\n".join(errors))
    return skills


def render(s: Skill) -> str:
    """Frontmatter the extension parses (src/lib/skill-files.js) + the body.
    LF only, one trailing newline -- so `--check` compares bytes, not moods."""
    lines = [
        "---",
        f"id: {s.id}",
        f"name: {s.name}",
        f"scope: {s.scope}",
        f"kind: [{', '.join(s.kind)}]",
        f"source: {s.source}",
        f"generated: scripts/brain/extension_skills.py -- ne menjaj ovde; izmeni {s.source}",
        "---",
        s.body.replace("\r\n", "\n"),
        "",
    ]
    return "\n".join(lines)


def render_index(skills: list[Skill]) -> str:
    return json.dumps([s.id for s in skills], ensure_ascii=False, indent=2) + "\n"


def expected(skills: list[Skill]) -> dict[str, str]:
    files = {s.filename: render(s) for s in skills}
    files[INDEX] = render_index(skills)
    return files


def _generated(p: Path) -> bool:
    try:
        return "generated: scripts/brain/extension_skills.py" in p.read_text(encoding="utf-8")[:600]
    except OSError:
        return False


def write(out: Path, skills: list[Skill]) -> list[str]:
    """Write every file; remove a generated file whose block is gone. A file
    somebody wrote by hand in that directory is left alone and reported."""
    out.mkdir(parents=True, exist_ok=True)
    files = expected(skills)
    report: list[str] = []
    for name, text in files.items():
        p = out / name
        old = p.read_text(encoding="utf-8") if p.exists() else None
        if old != text:
            p.write_text(text, encoding="utf-8", newline="\n")
            report.append(f"{'updated' if old is not None else 'written'}  {name}")
    for p in sorted(out.glob("*.md")):
        if p.name in files:
            continue
        if _generated(p):
            p.unlink()
            report.append(f"removed  {p.name} (its block is gone)")
        else:
            report.append(f"kept     {p.name} (hand-written -- not ours to touch)")
    return report


def check(out: Path, skills: list[Skill]) -> list[str]:
    """What differs between the files on disk and what the sources say."""
    diffs: list[str] = []
    for name, text in expected(skills).items():
        p = out / name
        if not p.exists():
            diffs.append(f"missing  {name}")
        elif p.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            diffs.append(f"stale    {name}")
    return diffs


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = default_out()
    if "--out" in args:
        i = args.index("--out")
        out = Path(args[i + 1])
        del args[i:i + 2]
    try:
        skills = collect()
    except ValueError as e:
        print(f"extension_skills: sources are not usable:\n{e}")
        return 2
    if "--check" in args:
        if not out.exists():
            print(f"extension_skills: {out} does not exist here -- nothing to check")
            return 0
        diffs = check(out, skills)
        for d in diffs:
            print(f"  {d}")
        print(f"extension_skills: {'STALE -- run scripts/brain/extension_skills.py' if diffs else 'up to date'} ({out})")
        return 1 if diffs else 0
    for line in write(out, skills):
        print(f"  {line}")
    print(f"extension_skills: {len(skills)} skill(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
