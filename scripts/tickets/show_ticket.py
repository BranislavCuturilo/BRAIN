#!/usr/bin/env python3
"""Print ONE ticket the way the models see it — for the coding agent that works a
generated prompt and needs the source: the ticket text, the comment thread, the
operator note, every attachment (listed, with the text digest of tables /
documents), and optionally the attachment FILES saved to a directory so the agent
can open the screenshots itself.

  python show_ticket.py VEZ 08597            # text + listing + digest
  python show_ticket.py VEZ 08597 --files    # also save attachments to a temp dir, print paths
  python show_ticket.py VEZ 08597 --files C:/tmp/t08597

Why: the generated prompt no longer embeds the raw ticket and its evidence (they
made every prompt long and were mostly redundant with the reading). Instead the
prompt names THIS command, so the agent pulls the source only when something in
the analysis is unclear or the work stops matching the prompt.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import store              # noqa: E402
import analyze_gemini     # noqa: E402  _ticket_text — the ONE renderer
import attachments        # noqa: E402
import project_context    # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _safe_name(name: str, n: int) -> str:
    keep = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in (name or ""))
    return f"{n:02d}_{keep or 'file'}"


def render(root, module: str, tid: str, files_dir=None) -> str:
    fp = store.resolve(root, module)
    if fp is None or not fp.is_file():
        return f"no store file for module {module!r}"
    d = store.load(fp) or {}
    t = (d.get("tickets") or {}).get(str(tid))
    if not isinstance(t, dict):
        # tolerate a missing leading zero either way
        alt = str(tid).lstrip("0")
        for k, v in (d.get("tickets") or {}).items():
            if str(k).lstrip("0") == alt:
                t, tid = v, k
                break
    if not isinstance(t, dict):
        return f"ticket {tid} not in {module}"
    out = [f"=== TIKET #{tid} (modul {module}) — {t.get('url') or ''}", analyze_gemini._ticket_text(t)]
    note = project_context.operator_note(t)
    if note:
        out.append("\n--- NAPOMENA OPERATERA (merodavna) ---\n" + note)
    pnote = project_context.project_note(root, module)
    if pnote:
        out.append("\n--- STALNA PRAVILA PROJEKTA ---\n" + pnote)
    att = analyze_gemini.ticket_attachments(t)
    out.append("\n--- PRILOZI ---\n" + attachments.listing_text(att.get("listing")))
    if att.get("digest"):
        out.append("\n" + att["digest"])
    if files_dir is not None:
        fd = Path(files_dir)
        fd.mkdir(parents=True, exist_ok=True)
        saved = []
        for n, (source, url, name) in enumerate(attachments.iter_attachments(t), 1):
            e = attachments.resolve_one(url, name)
            data = None
            if e.get("data"):
                data = base64.b64decode(e["data"])
            else:
                data = attachments.fetch_bytes(url)
            if not data:
                saved.append(f"- prilog {n}: {e.get('name') or url} — nije moguće preuzeti")
                continue
            p = fd / _safe_name(e.get("name") or url, n)
            p.write_bytes(data)
            saved.append(f"- prilog {n}: {p}  ({e.get('kind')}, {len(data)} B, iz: {source})")
        out.append("\n--- FAJLOVI PRILOGA (otvori ih Read alatom) ---\n" + "\n".join(saved))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module")
    ap.add_argument("ticket_id")
    ap.add_argument("--root", default=os.environ.get("TICKETS_STORE") or str(store.default_store()))
    ap.add_argument("--files", nargs="?", const="__tmp__", default=None,
                    help="save the attachments (default: a temp dir) and print their paths")
    a = ap.parse_args()
    fdir = None
    if a.files:
        fdir = (Path(tempfile.gettempdir()) / "agent_view_tickets" / f"{a.module}_{a.ticket_id}"
                if a.files == "__tmp__" else Path(a.files))
    print(render(a.root, a.module, a.ticket_id, fdir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
