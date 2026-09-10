#!/usr/bin/env python3
"""What one Claude run actually DID, read back from its own transcript.

The HUD opens a Claude for a ticket and, when the run stops, wants to show the
operator four things without asking anybody: which files changed, whether the
tests ran and passed, what was committed, and one line of what the agent said it
did. All four are already in the session transcript Claude Code writes as JSONL;
this module is the only reader of it.

WHY THE TRANSCRIPT AND NOT THE HOOKS. The hook stream (`hook.py` -> `/event`)
is a heartbeat: it says a tool fired, not what it touched, and the server keeps
only a 40-entry ring of it. The transcript is the complete, durable record of the
same run, and re-reading it after Stop costs one file read instead of state kept
in memory for hours across a server restart.

Pure stdlib, no network, and TOTAL: a missing file, a truncated line, a shape
that changed under us - every one of those yields a blank/partial result, never
an exception. The caller is a background thread finishing a run; a parser bug
must not lose the interval that produced it.

The shapes it reads (Claude Code JSONL, one object per line):
    {"type":"assistant","message":{"content":[{"type":"text",...},
                                              {"type":"tool_use","id","name","input"}]}}
    {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id","content"}]}}
Tool results are matched to their call by `tool_use_id`, which is what lets a
test command be paired with the output that says whether it passed.

Run offline tests: python agent_view/test_run_summary.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hook          # noqa: E402  is_test_command + tool_output_text (one definition)
import testparse     # noqa: E402  parse_final - the one test-output parser

#: A transcript of a long run is megabytes. Read at most this much, and read the
#: TAIL when the file is bigger: the question is "what did this run just do", so
#: the newest lines are the ones that must survive the cap.
MAX_BYTES = 30 * 1024 * 1024
#: The write tools, and the input key each one names its target with.
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
#: `git commit` echoes "[branch abc1234] subject" - the hash is proof the commit
#: was actually created, which the command alone is not (it can fail).
# "[main 1a2b3c4]", "[detached HEAD 1a2b3c4]", "[main (root-commit) 1a2b3c4]" — the
# hash is the LAST token inside the brackets, whatever git says before it.
_COMMIT_ECHO = re.compile(r"\[[^\]\n]*?\b([0-9a-f]{7,40})\]")
_COMMIT_CMD = re.compile(r"\bgit\b[^|;&]*\bcommit\b")
#: -m "subject" / -m 'subject' / -m subject — the first line, capped.
_COMMIT_MSG = re.compile(r"-m\s+(?:\"([^\"]*)\"|'([^']*)'|(\S+))")
SUBJECT_MAX = 100
SUMMARY_MAX = 300
FILES_MAX = 200            # a refactor touching more than this is summarised as the first N
COMMITS_MAX = 50


def blank() -> dict:
    """The empty result - the same keys, always, so the HUD never branches on a
    missing one."""
    return {"files": [], "tests": {"ran": 0, "passed": None, "last": ""},
            "commits": [], "turns": 0, "summary": "", "last_at": ""}


def _lines(path) -> list:
    """The transcript's lines, tail-capped at MAX_BYTES, read ONCE. A partial
    first line (the cut point lands mid-line) is dropped; decoding is lenient
    because a transcript can carry any tool output at all."""
    fp = Path(path)
    with open(fp, "rb") as fh:
        try:
            size = fp.stat().st_size
        except OSError:
            size = 0
        if size > MAX_BYTES:
            fh.seek(size - MAX_BYTES)
            fh.readline()                     # discard the partial line
        raw = fh.read(MAX_BYTES)
    return raw.decode("utf-8", "replace").splitlines()


def _blocks(entry) -> list:
    """The content blocks of a transcript entry, whatever wrapper it arrived in.
    A message whose content is a bare string carries no blocks."""
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _text_of(blocks) -> str:
    return "\n".join(b.get("text") or "" for b in blocks
                     if b.get("type") == "text" and isinstance(b.get("text"), str)).strip()


def _result_text(entry, block) -> str:
    """The output text of one tool_result block. `toolUseResult` (Claude Code's
    structured mirror of the same result) is preferred when present because a
    Bash result keeps stdout and stderr separate there; both shapes go through
    hook.tool_output_text."""
    text = hook.tool_output_text(entry.get("toolUseResult"))
    return text or hook.tool_output_text(block.get("content"))


def _commit_subject(cmd: str) -> str:
    m = _COMMIT_MSG.search(cmd or "")
    if not m:
        return ""
    raw = next((g for g in m.groups() if g), "")
    return raw.splitlines()[0].strip()[:SUBJECT_MAX] if raw else ""


def summarize(transcript_path, ticket_ids=None) -> dict:
    """One run's transcript -> {files, tests, commits, turns, summary, last_at}.

    `ticket_ids` are the tickets this run was launched for; a commit subject
    that names one of them carries it in `commits[i]["tickets"]`, so the HUD can
    say WHICH ticket a commit belongs to when a run covered several.

    * files    - sorted, unique, every path an Edit/Write/MultiEdit/NotebookEdit
                 named. Attempts, not confirmed writes: a rejected edit still
                 shows the file the agent went for, which is the useful signal.
    * tests    - {"ran": how many test commands, "passed": True/False/None,
                  "last": the last run's one-line result}. `passed` is None when
                 no test ran OR when nothing could be parsed out of the output -
                 "unknown" must never render as a tick.
    * commits  - only commits whose output ECHOED a hash; a `git commit` that
                 failed leaves no entry.
    * turns    - assistant messages, the cheapest "how much did it do" number.
    * summary  - the last thing the agent said, capped.

    Never raises: an unreadable transcript is `blank()`.
    """
    out = blank()
    try:
        lines = _lines(transcript_path)
    except (OSError, TypeError, ValueError):
        return out          # missing, unreadable, or no path at all (None)
    wanted = [str(t) for t in (ticket_ids or []) if str(t).strip()]
    files: set = set()
    pending: dict = {}          # tool_use_id -> {"kind": "test"|"commit", "cmd": str}
    tests_ran = 0
    tests_passed = None
    tests_last = ""
    commits: list = []
    turns = 0
    summary = ""
    last_at = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue                          # a torn line, mid-write - skip it
        if not isinstance(entry, dict):
            continue
        at = entry.get("timestamp")
        if isinstance(at, str) and at:
            last_at = at
        blocks = _blocks(entry)
        if entry.get("type") == "assistant":
            turns += 1
            text = _text_of(blocks)
            if text:
                summary = text
        for b in blocks:
            kind = b.get("type")
            if kind == "tool_use":
                name = b.get("name") or ""
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                key = EDIT_TOOLS.get(name)
                if key:
                    fp = inp.get(key)
                    if isinstance(fp, str) and fp.strip():
                        files.add(fp.strip())
                    continue
                if name != "Bash":
                    continue
                cmd = inp.get("command") if isinstance(inp.get("command"), str) else ""
                tid = b.get("id")
                if not isinstance(tid, str) or not tid:
                    continue
                if hook.is_test_command(cmd):
                    pending[tid] = {"kind": "test", "cmd": cmd}
                elif _COMMIT_CMD.search(cmd):
                    pending[tid] = {"kind": "commit", "cmd": cmd}
            elif kind == "tool_result":
                call = pending.pop(b.get("tool_use_id"), None)
                if call is None:
                    continue
                text = _result_text(entry, b)
                if call["kind"] == "test":
                    tests_ran += 1
                    res = testparse.parse_final(text)
                    status = res.get("status")
                    # The LAST test run wins - a red run fixed and re-run must
                    # not keep showing red, and vice versa.
                    tests_passed = True if status == "passed" else (
                        False if status == "failed" else None)
                    tests_last = _test_line(res, text)
                    continue
                m = _COMMIT_ECHO.search(text or "")
                if not m or len(commits) >= COMMITS_MAX:
                    continue                  # no echoed hash -> the commit did not happen
                subject = _commit_subject(call["cmd"])
                commits.append({"hash": m.group(1), "subject": subject,
                                "tickets": [t for t in wanted
                                            if re.search(r"(?<!\d)" + re.escape(t) + r"(?!\d)", subject)]})
    out["files"] = sorted(files)[:FILES_MAX]
    out["tests"] = {"ran": tests_ran, "passed": tests_passed, "last": tests_last}
    out["commits"] = commits
    out["turns"] = turns
    out["summary"] = " ".join(summary.split())[:SUMMARY_MAX]
    out["last_at"] = last_at
    return out


def _test_line(res: dict, text: str) -> str:
    """One line for the HUD: the parsed counts when the parser recognised the
    runner, else the first non-empty line of the output (better than nothing,
    and it is what tells the operator the parser missed)."""
    if res.get("status") in ("passed", "failed"):
        return "%s: %d/%d" % (res.get("status"), res.get("passed", 0),
                              res.get("total", 0))
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:SUMMARY_MAX]
    return ""
