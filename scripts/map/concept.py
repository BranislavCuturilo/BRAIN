#!/usr/bin/env python3
"""Where does a concept live, and what touches it?

Given a name -- a model, a class, a function, a table -- find every reference
across the repository and classify it. This is the mechanical half of an impact
analysis: it finds, exactly and completely. Deciding what the references MEAN is
the impact-mapper agent's job.

Grep alone misses what breaks most often, because a name can be referenced
without being imported: a template variable, a URL name, a dotted path in
settings, a string in a migration, a patch target in a test. This looks in all
of them.

  concept.py Stocktake
  concept.py Stocktake --root C:/projects/acme-audit
  concept.py StocktakeItem --json          feed another tool, or an agent
  concept.py Stocktake --context           show the matching line

Exit 1 if nothing was found -- usually a typo or the wrong root.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", "staticfiles",
             "media", ".idea", ".vscode", "dist", "build", "graphify-out", ".mypy_cache"}
EXTS = {".py", ".html", ".txt", ".js", ".ts", ".json", ".yml", ".yaml", ".md", ".cfg", ".toml"}

# Order matters: the first pattern that matches wins, so the most specific
# classifications come first.
RULES = [
    ("definition",   re.compile(r"^\s*(class|def)\s+{n}\b")),
    ("migration",    None),                      # decided by path
    ("test",         None),                      # decided by path
    ("import",       re.compile(r"^\s*(from|import)\s+.*\b{n}\b")),
    ("fk / relation", re.compile(r"(ForeignKey|OneToOneField|ManyToManyField)\s*\(\s*['\"]?{n}\b")),
    ("orm query",    re.compile(r"\b{n}\s*\.\s*objects\b|\b{n}\.objects")),
    ("instantiation", re.compile(r"\b{n}\s*\(")),
    ("url name",     re.compile(r"(reverse|reverse_lazy)\s*\(\s*['\"][^'\"]*{n}|name\s*=\s*['\"][^'\"]*{n}")),
    ("template",     None),                      # decided by extension
    ("settings",     None),                      # decided by filename
    ("string",       re.compile(r"['\"][^'\"]*\b{n}\b[^'\"]*['\"]")),
    ("mention",      re.compile(r"\b{n}\b")),
]


def classify(path: Path, line: str, name: str, rx_cache: dict) -> str:
    parts = {p.lower() for p in path.parts}
    if "migrations" in parts:
        return "migration"
    if "tests" in parts or path.name.startswith("test_") or path.name.endswith("_test.py"):
        return "test"
    if path.name.startswith("settings") or "settings" in parts:
        return "settings"
    for kind, pattern in RULES:
        if pattern is None:
            continue
        if kind not in rx_cache:
            rx_cache[kind] = re.compile(pattern.pattern.replace("{n}", re.escape(name)))
        if rx_cache[kind].search(line):
            if kind == "string" and path.suffix in {".html", ".txt", ".md"}:
                return "template"
            return kind
    return "template" if path.suffix in {".html", ".txt"} else "mention"


def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in EXTS:
                yield p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="model, class, function or table name")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--context", action="store_true", help="print the matching line")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    word = re.compile(r"\b" + re.escape(args.name) + r"\b")
    rx_cache: dict = {}
    hits = defaultdict(list)
    total = 0

    for path in walk(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if args.name not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not word.search(line):
                continue
            kind = classify(path, line, args.name, rx_cache)
            hits[kind].append({
                "file": str(path.relative_to(root)).replace("\\", "/"),
                "line": i,
                "text": line.strip()[:160],
            })
            total += 1

    if not total:
        print(f"'{args.name}' not found under {root}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps({"concept": args.name, "root": str(root),
                          "total": total, "by_kind": dict(hits)},
                         indent=2, ensure_ascii=False))
        return 0

    # Definition first, then what writes, then what reads, then the long tail.
    order = ["definition", "fk / relation", "migration", "import", "orm query",
             "instantiation", "url name", "template", "settings", "test",
             "string", "mention"]

    print("=" * 74)
    print(f"{args.name}   {total} references across "
          f"{len({h['file'] for v in hits.values() for h in v})} files")
    print("=" * 74)

    for kind in order:
        rows = hits.get(kind)
        if not rows:
            continue
        files = defaultdict(list)
        for r in rows:
            files[r["file"]].append(r)
        print(f"\n{kind.upper()}  ({len(rows)} in {len(files)} files)")
        for fname in sorted(files):
            lines = files[fname]
            nums = ", ".join(str(r["line"]) for r in lines[:8])
            more = f" +{len(lines) - 8}" if len(lines) > 8 else ""
            print(f"  {fname}:{nums}{more}")
            if args.context:
                for r in lines[:3]:
                    print(f"      {r['line']:>5} | {r['text']}")

    print()
    print("Reading this: DEFINITION is where it lives. FK/RELATION and ORM QUERY are")
    print("what touches the data. TEMPLATE, URL NAME, SETTINGS, MIGRATION and STRING")
    print("are the references a rename breaks and a symbol search misses --")
    print("check those before changing anything (craft-code change-safety).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
