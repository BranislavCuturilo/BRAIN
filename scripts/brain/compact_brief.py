#!/usr/bin/env python3
"""What the session was doing, put back after a compaction — read from disk.

**The problem.** Compaction summarises the conversation, and a summary keeps
what looked important to the summariser. What it drops is exactly what a long
ticket session needs next: the OTHER open tickets on this module, which screen
they touch, what is still uncommitted. The work itself is not lost — it is in
`tickets_store/`, in git, in `journal/` — but the knowledge that it is there,
and that it matters, is.

**So this does not fight the summariser.** It re-reads the durable facts and
hands them back as `additionalContext` on `SessionStart(compact)`, the sanctioned
channel. Deterministic, no model, no tokens spent producing it, and every line
of it is a pointer to a file that can be read in full.

**Why the related grouping.** The operator's own words: a compaction must not
drop things that "могу бити битне за неки од наредних сродних тикета". Active
tickets are grouped by the screen (`reading.page`, the url_name the ticket
pipeline resolves) so the next ticket on that screen arrives with its siblings
already named.

**PreCompact records, and it may veto an automatic compact.** Claude Code
v2.1.105+ honours `{"decision":"block"}` here. Manual `/compact` is never
blocked. Auto is blocked unless the window is actually full — otherwise a
pass is interrupted every few minutes.

  Incident 2026-09-09: `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=0.5` plus a
  post-compact baseline of ~88k tokens (already ~50% of a 200k window)
  fired auto-compact 20 times in an hour, reloaded CLAUDE.md on each
  SessionStart(compact), and looked like a Claude Code reset. The
  summariser is not ours to hold at the real limit; the thrash is.

  compact_brief.py             as a SessionStart(compact) hook: payload on stdin
  compact_brief.py --record    as a PreCompact hook: measure; veto auto-thrash
  compact_brief.py --print     the brief for a human, for this directory
  compact_brief.py --report    the compactions recorded so far
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "tickets"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOG = ROOT / "journal" / "compacts.jsonl"       # gitignored: this machine's sessions
#: Hard cap on what is handed back. A brief that grows without bound would be
#: paying the compaction's cost straight back; ~1.2k tokens is a page of facts.
MAX_CHARS = 5000
MAX_TICKETS = 14
MAX_DIRTY = 10
CLOSED = ("done", "closed", "skip", "solved_manually", "nonsense")
#: Once a session has carried this many tokens, it is a 1M window — even after
#: compact drops it back to ~88k, which then *looks* like a 200k session.
HIGH_WATER = 250_000
#: Auto compact is allowed only this close to the actual limit. 80% of 200k /
#: 75% of 1M. Below that it is thrash, not memory management.
FLOOR_200K = 160_000
FLOOR_1M = 750_000


def _git(cwd: Path, *args: str) -> str:
    """Raw stdout, right-trimmed only. `git status --short` writes the status in
    two columns and a modified-but-unstaged file starts with a SPACE (" M path");
    stripping the whole output ate that space and with it the first path's first
    letter -- docs/DASHBOARD.md was reported as ocs/DASHBOARD.md. Callers that
    want one value strip their own."""
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                           text=True, timeout=8, encoding="utf-8", errors="replace")
        return r.stdout.rstrip("\r\n") if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def modules_for(cwd: Path, root: Path | None = None) -> list[str]:
    """The helpdesk modules whose repo is this directory (or contains it).
    Empty when the session is not in a mapped project — the brief then simply
    has no ticket section rather than guessing at one."""
    try:
        import store                                            # noqa: PLC0415
        r = Path(root) if root else store.default_store()
        cat = json.loads((r / "modules.json").read_text(encoding="utf-8")).get("modules") or {}
    except Exception:                                           # noqa: BLE001
        return []
    here = str(cwd.resolve()).replace("\\", "/").lower()
    out = []
    for mod, repo in cat.items():
        if not repo:
            continue
        try:
            import store                                        # noqa: PLC0415
            full = str(Path(store.repo_path(repo)).resolve()).replace("\\", "/").lower()
        except Exception:                                       # noqa: BLE001
            continue
        if full and (here == full or here.startswith(full + "/")):
            out.append(mod)
    return sorted(out)


def open_tickets(module: str, root: Path | None = None) -> list[dict]:
    """Every ticket in a module that is not finished, newest first."""
    try:
        import store                                            # noqa: PLC0415
        r = Path(root) if root else store.default_store()
        d = json.loads((r / f"{module}.json").read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        return []
    out = []
    for tid, t in (d.get("tickets") or {}).items():
        if not isinstance(t, dict) or str(t.get("status") or "").lower() in CLOSED:
            continue
        o = t.get("original") if isinstance(t.get("original"), dict) else {}
        rd = t.get("reading") if isinstance(t.get("reading"), dict) else {}
        hd = t.get("helpdesk") if isinstance(t.get("helpdesk"), dict) else {}
        out.append({
            "module": module, "id": str(tid),
            "title": str(t.get("title") or o.get("title") or "")[:70],
            "priority": str(hd.get("priority") or t.get("priority") or ""),
            "created": str(o.get("created") or ""),
            "page": str(rd.get("page") or ""),
            "read": bool(rd.get("real_need")),
            "scope": str(rd.get("scope") or ""),
        })
    out.sort(key=lambda t: t["created"], reverse=True)
    return out


def by_screen(tickets: list[dict]) -> dict[str, list[str]]:
    """screen (url_name) -> the open tickets on it, for screens carrying more
    than one. This is the "srodni tiketi" line: the next ticket on that screen
    should arrive knowing its siblings exist."""
    groups: dict[str, list[str]] = {}
    for t in tickets:
        if t["page"]:
            groups.setdefault(t["page"], []).append(f"{t['module']}#{t['id']}")
    return {k: v for k, v in sorted(groups.items()) if len(v) > 1}


def journal_tail(n: int = 3) -> list[str]:
    """The newest journal entries, by their own first heading."""
    d = ROOT / "journal"
    if not d.is_dir():
        return []
    files = sorted((p for p in d.rglob("*.md") if p.name.lower() != "readme.md"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    out = []
    for p in files:
        head = ""
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("#"):
                    head = line.lstrip("# ").strip()
                    break
        except OSError:
            continue
        out.append(f"{p.name}: {head[:80]}" if head else p.name)
    return out


def build(cwd: Path, root: Path | None = None) -> str:
    """The brief. Always a string; an unreadable anything is simply absent."""
    cwd = Path(cwd)
    lines = ["=== BRAIN — vraćeno posle sažimanja (sve je na disku, pročitaj pre nego što pretpostaviš) ==="]

    name = cwd.name
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()
    last = _git(cwd, "log", "-1", "--oneline").strip()
    if branch:
        lines.append(f"Repo {name} · grana {branch}" + (f" · poslednji commit {last[:70]}" if last else ""))

    dirty = [ln[3:].strip() for ln in _git(cwd, "status", "--short").splitlines()
             if len(ln) > 3 and ln[3:].strip()]
    if dirty:
        lines.append(f"NEKOMITOVANO ({len(dirty)}): " + ", ".join(dirty[:MAX_DIRTY])
                     + (f" … +{len(dirty) - MAX_DIRTY}" if len(dirty) > MAX_DIRTY else ""))

    mods = modules_for(cwd, root)
    tickets: list[dict] = []
    for m in mods:
        tickets += open_tickets(m, root)
    if tickets:
        counts = ", ".join(f"{m} {sum(1 for t in tickets if t['module'] == m)}" for m in mods)
        lines.append(f"\nAKTIVNI TIKETI — {counts}")
        for t in tickets[:MAX_TICKETS]:
            mark = ("pročitan" + (f", ekran {t['page']}" if t["page"] else "")) if t["read"] else "bez čitanja"
            lines.append(f"  {t['module']}#{t['id']} {t['priority'] or '?':8} {t['title']:<70} · {mark}")
        if len(tickets) > MAX_TICKETS:
            lines.append(f"  … još {len(tickets) - MAX_TICKETS}; ceo red: tickets_store/{mods[0]}.json")
        groups = by_screen(tickets)
        if groups:
            lines.append("SRODNI (isti ekran) — ako radiš jedan, ovi dele isti kod:")
            for page, ids in list(groups.items())[:6]:
                lines.append(f"  {page}: {', '.join(ids)}")
    elif mods:
        lines.append(f"\nAKTIVNI TIKETI — nema otvorenih u {', '.join(mods)}")

    jt = journal_tail()
    if jt:
        lines.append("\nPOSLEDNJE ZAPISANO (journal/): " + " | ".join(jt))

    lines.append("Izvori: tickets_store/<MODUL>.json · journal/ · git log. "
                 "Ništa od ovoga nije izgubljeno sažimanjem.")
    # The one thing a compacted session cannot remember is that the full
    # transcript survived and is searchable. Named here because this is the
    # exact moment it becomes useful.
    lines.append("Fali ti nešto od ranije u razgovoru? Ceo transkript je na disku, "
                 "sažimanje ga ne briše: "
                 "python ~/.claude/skills/brain/scripts/brain/recall.py \"<pojam>\" "
                 "(--before-compact samo ono što je sažeto).")
    text = "\n".join(lines)
    return text[:MAX_CHARS]


def _ctx_from_payload(payload: dict) -> tuple[int | None, str]:
    """Tokens currently in context, from the transcript tail. (None, "") if unread."""
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "brain"))
        import usage_limit                                       # noqa: PLC0415
        t = usage_limit.transcript_session(payload.get("transcript_path") or "")
        if t and t.get("ctx_tokens"):
            return int(t["ctx_tokens"]), str(t.get("model_id") or "")
    except Exception:                                            # noqa: BLE001
        pass
    return None, ""


def high_water(log: Path, session: str) -> int:
    """Biggest ctx_tokens this session has already recorded, including blocked
    attempts. A 1M session that compacted once must not be treated as 200k
    just because the summary now sits at 88k."""
    sid = str(session or "")[:40]
    if not sid:
        return 0
    best = 0
    for r in recent(log, n=200):
        if str(r.get("session") or "")[:40] != sid:
            continue
        try:
            n = int(r.get("ctx_tokens") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > best:
            best = n
    return best


def should_block_auto(trigger: str, session: str, ctx_tokens: int | None,
                      log: Path = LOG) -> str | None:
    """Reason to veto an automatic compact, or None to let it through.

    Manual `/compact` is the operator's instruction. Auto is allowed only when
    the window is actually full. A 1M session that has already been above
    HIGH_WATER keeps the 1M floor even after compact shrinks the visible count.
    """
    if str(trigger or "") != "auto":
        return None
    ctx = int(ctx_tokens) if ctx_tokens else 0
    hw = max(ctx, high_water(log, session))
    floor = FLOOR_1M if hw >= HIGH_WATER else FLOOR_200K
    if ctx >= floor:
        return None
    if ctx == 0:
        # No measurement. A 1M session with room must not compact on a guess;
        # a brand-new session with no history is left to the runtime.
        if hw >= HIGH_WATER:
            return "1M session, tokens unread, window not full"
        return None
    which = "1M" if floor == FLOOR_1M else "200k"
    return f"{which} session at {ctx:,} tokens, floor {floor:,}"


def record(payload: dict, log: Path = LOG, now: str | None = None) -> dict | None:
    """One line per PreCompact: when, what triggered it, where, and whether
    this hook vetoed it. Always writes; blocking is a separate JSON print."""
    if not isinstance(payload, dict) or not payload.get("hook_event_name"):
        return None
    ctx, model = _ctx_from_payload(payload)
    row = {
        "at": now or datetime.now().isoformat(timespec="seconds"),
        "event": str(payload.get("hook_event_name")),
        "trigger": str(payload.get("trigger") or ""),
        "session": str(payload.get("session_id") or "")[:40],
        "cwd": str(payload.get("cwd") or ""),
        "custom": bool(payload.get("custom_instructions")),
    }
    if ctx:
        row["ctx_tokens"] = ctx
        row["model"] = model
    row["pct_override"] = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") or ""
    reason = should_block_auto(row["trigger"], row["session"], ctx, log)
    if reason:
        row["blocked"] = True
        row["block_reason"] = reason
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return row


def recent(log: Path = LOG, n: int = 20) -> list[dict]:
    try:
        rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return []
    return rows[-n:]


def _payload() -> dict:
    try:
        raw = sys.stdin.read(200_000) if not sys.stdin.isatty() else ""
        return json.loads(raw) if raw.strip() else {}
    except Exception:                                           # noqa: BLE001
        return {}


def main() -> int:
    args = sys.argv[1:]

    if "--report" in args:
        rows = recent()
        if not rows:
            print("no compaction recorded yet")
            return 0
        for r in rows:
            ctx = f"{r['ctx_tokens']:>9,} tok" if r.get("ctx_tokens") else " " * 13
            ov = f"  override={r['pct_override']}" if r.get("pct_override") else ""
            blk = "  BLOCKED" if r.get("blocked") else ""
            print(f"  {r['at']}  {r['event']:12} {r['trigger']:6} {ctx}  {Path(r['cwd']).name}{ov}{blk}")
        auto = sum(1 for r in rows if r["trigger"] == "auto")
        blocked = sum(1 for r in rows if r.get("blocked"))
        fired = [r["ctx_tokens"] for r in rows if r.get("trigger") == "auto"
                 and r.get("ctx_tokens") and not r.get("blocked")]
        print(f"\n{len(rows)} recorded · {auto} automatic · {len(rows) - auto} manual"
              f" · {blocked} blocked")
        if fired:
            print(f"automatic compaction fired between {min(fired):,} and {max(fired):,} tokens "
                  f"({len(fired)} sample(s))")
            print("  -> that is the threshold. Compare it to the window to get the fraction.")
        return 0

    if "--record" in args:
        p = _payload()
        row = record(p)                         # always writes; may also veto auto
        if row and row.get("blocked"):
            print(json.dumps({
                "decision": "block",
                "reason": row.get("block_reason") or "auto-compact would interrupt a pass that still has room",
            }))
        return 0

    if "--print" in args:
        print(build(Path(os.getcwd())))
        return 0

    # SessionStart(compact): the payload names the directory, not our cwd.
    p = _payload()
    cwd = Path(p.get("cwd") or os.getcwd())
    try:
        text = build(cwd)
    except Exception:                                           # noqa: BLE001
        return 0                                # a brief that breaks says nothing
    if not text.strip():
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
