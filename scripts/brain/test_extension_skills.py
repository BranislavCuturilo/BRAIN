#!/usr/bin/env python3
"""Proof that the extension's skills are the brain's references, and nothing else.

The failure this guards is silent on the extension's side: a skill that was
not generated is simply not sent, and the model judges a list screen with no
list rules. Nothing errors. So the generator refuses loudly -- a listed source
without a block, two blocks sharing an id, a block with no name -- and `--check`
names every file that is behind the sources.

  python scripts/brain/test_extension_skills.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extension_skills as es  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FAILS: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


BLOCK = (
    '<!-- ebr:skill id="screen-list" name="Lista" kind="list, report" scope="compose" -->\n'
    "Lista mora da ima filter, paginaciju i prazno stanje. "
    "Nedostatak izvoza koji nikad nije postojao je change_request, ne bug.\n"
    "<!-- /ebr:skill -->\n"
)


def fake_brain(tmp: Path, block: str = BLOCK) -> Path:
    for rel in es.SOURCES:
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        sid = Path(rel).stem.lower().replace("skill", "any")
        p.write_text(f"# {rel}\n\nprose\n\n" + block.replace("screen-list", f"screen-{sid}"), encoding="utf-8")
    return tmp


def test_blocks_parse() -> None:
    print("blocks()")
    got = es.blocks("x\n" + BLOCK + "y", "src.md")
    ok(len(got) == 1 and got[0].id == "screen-list", "one block, id read")
    ok(got[0].kind == ["list", "report"], f"kind split and lowercased: {got[0].kind}")
    ok(got[0].scope == "compose" and not got[0].errors, "scope + no errors")
    ok(got[0].body.startswith("Lista mora"), "body trimmed")

    bad = es.blocks('<!-- ebr:skill id="Bad_ID" kind="x" scope="never" -->\nshort\n<!-- /ebr:skill -->', "s.md")
    ok(len(bad) == 1 and len(bad[0].errors) == 4,
       f"id, name, scope and length each refused: {bad[0].errors}")


def test_collect_refuses() -> None:
    print("collect() refuses")
    with tempfile.TemporaryDirectory() as d:
        root = fake_brain(Path(d))
        skills = es.collect(root)
        ok(len(skills) == len(es.SOURCES), f"one skill per source: {len(skills)}")
        # a source without a block
        (root / es.SOURCES[1]).write_text("# nothing here\n", encoding="utf-8")
        try:
            es.collect(root)
            ok(False, "missing block raises")
        except ValueError as e:
            ok("no <!-- ebr:skill --> block" in str(e), f"missing block named: {str(e)[:60]}")
        # duplicate id
        root = fake_brain(Path(d))
        (root / es.SOURCES[2]).write_text(BLOCK, encoding="utf-8")   # keeps id screen-list...
        (root / es.SOURCES[3]).write_text(BLOCK, encoding="utf-8")   # ...twice
        try:
            es.collect(root)
            ok(False, "duplicate id raises")
        except ValueError as e:
            ok("already used by" in str(e), "duplicate id named")


def test_write_and_check() -> None:
    print("write() / check()")
    with tempfile.TemporaryDirectory() as d:
        root = fake_brain(Path(d) / "brain")
        out = Path(d) / "ext" / "src" / "skills"
        skills = es.collect(root)
        rep = es.write(out, skills)
        ok(len(rep) == len(skills) + 1, f"every file + index written: {len(rep)}")
        idx = json.loads((out / es.INDEX).read_text(encoding="utf-8"))
        ok(idx == [s.id for s in skills], "index in source order")
        ok(es.check(out, skills) == [], "fresh copy passes --check")
        text = (out / skills[0].filename).read_text(encoding="utf-8")
        ok(text.startswith("---\nid: ") and "\ngenerated: scripts/brain/extension_skills.py" in text,
           "frontmatter carries id and the generated marker")
        ok("\r" not in text, "LF only")
        # source changes -> stale
        src = root / es.SOURCES[0]
        src.write_text(src.read_text(encoding="utf-8").replace("prazno stanje", "PRAZNO stanje"), encoding="utf-8")
        skills2 = es.collect(root)
        diffs = es.check(out, skills2)
        ok(diffs == [f"stale    {skills2[0].filename}"], f"exactly the changed file is stale: {diffs}")
        # a hand-written file is kept, a generated orphan removed
        (out / "mine.md").write_text("---\nid: mine\n---\nmy rule\n", encoding="utf-8")
        (out / "orphan.md").write_text(
            "---\nid: orphan\ngenerated: scripts/brain/extension_skills.py -- x\n---\nold\n", encoding="utf-8")
        rep = es.write(out, skills2)
        ok(any(r.startswith("kept     mine.md") for r in rep), "hand-written file kept")
        ok(not (out / "orphan.md").exists(), "generated orphan removed")
        ok(es.check(out, skills2) == [], "rewritten copy passes")


def test_real_sources() -> None:
    print("the real sources")
    try:
        skills = es.collect(ROOT)
    except ValueError as e:
        ok(False, f"real sources usable: {e}")
        return
    ids = [s.id for s in skills]
    ok(len(ids) == len(es.SOURCES), f"one skill per real source: {ids}")
    anys = [s.id for s in skills if "any" in s.kind]
    ok(anys == ["screen-any"], f"exactly one always-on skill: {anys}")
    ok(all(len(s.body) < 2200 for s in skills),
       "every skill under ~550 tokens -- it is paid on every compose call")
    kinds = {k for s in skills for k in s.kind}
    ok({"list", "create", "update", "detail", "dashboard", "report"} <= kinds, f"kinds covered: {sorted(kinds)}")


def main() -> int:
    for t in (test_blocks_parse, test_collect_refuses, test_write_and_check, test_real_sources):
        t()
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'OK'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
