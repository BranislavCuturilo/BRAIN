#!/usr/bin/env python3
"""Offline tests for run_summary.summarize - the after-the-fact reader of a
Claude Code transcript (files edited, tests, commits, turns, last words).

Every transcript here is SYNTHETIC and written to a fresh temp dir: no real
session file is opened, nothing is spawned, nothing reaches the network. Both
directions of each verdict are asserted (a green AND a red test run, a commit
that echoed a hash AND one that did not), so a parser that always answered the
same way would fail here.
Run: python test_run_summary.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_summary  # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Transcript builders - the exact JSONL shapes Claude Code writes.
# --------------------------------------------------------------------------- #
def _assistant(blocks, at="2026-08-19T10:00:00.000Z"):
    return {"type": "assistant", "timestamp": at,
            "message": {"role": "assistant", "content": blocks}}


def _text(t):
    return {"type": "text", "text": t}


def _use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _result(tid, content, tool_use_result=None, at="2026-08-19T10:00:01.000Z"):
    e = {"type": "user", "timestamp": at,
         "message": {"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": tid,
                                  "content": content}]}}
    if tool_use_result is not None:
        e["toolUseResult"] = tool_use_result
    return e


def _write(tmp, entries, name="transcript.jsonl", extra_lines=()):
    fp = Path(tmp) / name
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    lines.extend(extra_lines)
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(fp)


DJANGO_OK = ("Creating test database...\n"
             "..........\n"
             "----------------------------------------------------------------------\n"
             "Ran 10 tests in 1.204s\n\nOK\n")
DJANGO_FAIL = ("F.........\n"
               "======================================================================\n"
               "FAIL: test_thing (app.tests.T)\n"
               "----------------------------------------------------------------------\n"
               "Ran 10 tests in 1.204s\n\nFAILED (failures=1)\n")


# --------------------------------------------------------------------------- #
def test_full_run_yields_files_tests_commits_turns_summary():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [
            _assistant([_text("Krecem."), _use("t1", "Edit", {"file_path": "e:/repo/b.py"})]),
            _result("t1", "ok"),
            _assistant([_use("t2", "Write", {"file_path": "e:/repo/a.py"})]),
            _result("t2", "ok"),
            _assistant([_use("t3", "NotebookEdit", {"notebook_path": "e:/repo/n.ipynb"})]),
            _result("t3", "ok"),
            _assistant([_use("t4", "Read", {"file_path": "e:/repo/never_edited.py"})]),
            _result("t4", "contents"),
            _assistant([_use("t5", "Bash", {"command": "python manage.py test app"})]),
            _result("t5", DJANGO_OK),
            _assistant([_use("t6", "Bash",
                             {"command": 'git commit -m "fix(popis): sabiranje kolona #94313"'})]),
            _result("t6", "[main 1a2b3c4] fix(popis): sabiranje kolona #94313\n 2 files changed"),
            _assistant([_text("Gotovo:   popravljeno    sabiranje.")],
                       at="2026-08-19T10:05:00.000Z"),
        ])
        out = run_summary.summarize(path, ["94313", "94999"])
        check("files: every write tool's target, sorted and unique",
              out["files"] == ["e:/repo/a.py", "e:/repo/b.py", "e:/repo/n.ipynb"], str(out["files"]))
        check("files: a Read is not an edit",
              "e:/repo/never_edited.py" not in out["files"], str(out["files"]))
        check("tests: one run, parsed as passed",
              out["tests"]["ran"] == 1 and out["tests"]["passed"] is True, str(out["tests"]))
        check("tests: the last line carries the counts",
              out["tests"]["last"] == "passed: 10/10", str(out["tests"]))
        check("commits: hash + subject from the echoed output",
              out["commits"] == [{"hash": "1a2b3c4",
                                  "subject": "fix(popis): sabiranje kolona #94313",
                                  "tickets": ["94313"]}], str(out["commits"]))
        check("turns: every assistant message counted", out["turns"] == 7, str(out["turns"]))
        check("summary: the LAST assistant text, whitespace collapsed",
              out["summary"] == "Gotovo: popravljeno sabiranje.", repr(out["summary"]))
        check("last_at: the newest timestamp seen",
              out["last_at"] == "2026-08-19T10:05:00.000Z", str(out["last_at"]))


def test_a_corrupt_line_is_skipped_not_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [
            _assistant([_use("t1", "Edit", {"file_path": "e:/repo/a.py"})]),
            _result("t1", "ok"),
        ], extra_lines=['{"type": "assistant", "message": {"content": [{"type": "te',
                        "", "not json at all"])
        out = run_summary.summarize(path)
        check("corrupt: the good half still reads", out["files"] == ["e:/repo/a.py"], str(out))
        check("corrupt: no exception, full shape returned",
              set(out) == set(run_summary.blank()), str(sorted(out)))


def test_a_failed_test_run_is_false_and_the_last_run_wins():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [
            _assistant([_use("t1", "Bash", {"command": "python manage.py test"})]),
            _result("t1", DJANGO_FAIL),
            _assistant([_use("t2", "Bash", {"command": "python manage.py test"})]),
            _result("t2", DJANGO_OK),
        ])
        out = run_summary.summarize(path)
        check("tests: both runs counted", out["tests"]["ran"] == 2, str(out["tests"]))
        check("tests: the LAST run decides", out["tests"]["passed"] is True, str(out["tests"]))

        path2 = _write(tmp, [
            _assistant([_use("t1", "Bash", {"command": "python manage.py test"})]),
            _result("t1", DJANGO_OK),
            _assistant([_use("t2", "Bash", {"command": "python manage.py test"})]),
            _result("t2", DJANGO_FAIL),
        ], name="t2.jsonl")
        out2 = run_summary.summarize(path2)
        check("tests: a red last run reports False",
              out2["tests"]["passed"] is False, str(out2["tests"]))


def test_unparsable_test_output_is_none_never_a_tick():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [
            _assistant([_use("t1", "Bash", {"command": "npm test"})]),
            _result("t1", "\n  something the parser has never seen\nmore noise\n"),
        ])
        out = run_summary.summarize(path)
        check("tests: ran but unparsable -> passed is None",
              out["tests"]["ran"] == 1 and out["tests"]["passed"] is None, str(out["tests"]))
        check("tests: the raw first line is shown instead",
              out["tests"]["last"] == "something the parser has never seen", str(out["tests"]))


def test_no_tests_at_all():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [_assistant([_text("nista")])])
        out = run_summary.summarize(path)
        check("tests: none ran -> zeros and None (not False)",
              out["tests"] == {"ran": 0, "passed": None, "last": ""}, str(out["tests"]))


def test_a_commit_that_did_not_happen_is_not_reported():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [
            _assistant([_use("t1", "Bash", {"command": 'git commit -m "nothing to do"'})]),
            _result("t1", "nothing to commit, working tree clean"),
            _assistant([_use("t2", "Bash", {"command": "git status"})]),
            _result("t2", "[main 9999999] not a commit echo at all"),
        ])
        out = run_summary.summarize(path)
        check("commits: no echoed hash -> no commit recorded",
              out["commits"] == [], str(out["commits"]))


def test_bash_result_read_from_tool_use_result_stdout():
    # Claude Code mirrors a Bash result as {"stdout","stderr"} in toolUseResult;
    # the parser must read that shape too, not only the string content.
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [
            _assistant([_use("t1", "Bash", {"command": "python manage.py test"})]),
            _result("t1", "", tool_use_result={"stdout": "", "stderr": DJANGO_OK}),
            _assistant([_use("t2", "Bash", {"command": "git commit -m 'popravka'"})]),
            _result("t2", "", tool_use_result={"stdout": "[main deadbee] popravka",
                                               "stderr": ""}),
        ])
        out = run_summary.summarize(path)
        check("shapes: a {stdout,stderr} result is parsed",
              out["tests"]["passed"] is True, str(out["tests"]))
        check("shapes: and so is a commit echo in it",
              [c["hash"] for c in out["commits"]] == ["deadbee"], str(out["commits"]))


def test_a_huge_transcript_is_tail_capped():
    with tempfile.TemporaryDirectory() as tmp:
        fp = Path(tmp) / "big.jsonl"
        filler = json.dumps(_assistant([_text("x" * 4000)])) + "\n"
        with open(fp, "w", encoding="utf-8") as fh:
            for _ in range(400):
                fh.write(filler)
            fh.write(json.dumps(_assistant(
                [_use("t1", "Edit", {"file_path": "e:/repo/last.py"}),
                 _text("kraj")], at="2026-08-19T11:00:00.000Z")) + "\n")
        orig = run_summary.MAX_BYTES
        run_summary.MAX_BYTES = 20_000          # force the cap without a 30 MB file
        try:
            out = run_summary.summarize(str(fp))
        finally:
            run_summary.MAX_BYTES = orig
        check("cap: the newest lines survive the cap",
              out["files"] == ["e:/repo/last.py"] and out["summary"] == "kraj", str(out))
        check("cap: the truncated head is dropped, not fatal",
              out["turns"] < 400, str(out["turns"]))


def test_missing_and_empty_transcripts_are_blank():
    with tempfile.TemporaryDirectory() as tmp:
        out = run_summary.summarize(str(Path(tmp) / "nope.jsonl"))
        check("blank: a missing transcript is the blank shape",
              out == run_summary.blank(), str(out))
        empty = Path(tmp) / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        check("blank: an empty transcript is the blank shape",
              run_summary.summarize(str(empty)) == run_summary.blank())
        check("blank: a None path never raises",
              run_summary.summarize(None) == run_summary.blank())



def test_commit_echo_forms_and_ticket_attribution():
    import run_summary as rs
    for echo in ("[main 1a2b3c4] fix", "[detached HEAD 1a2b3c4] fix", "[main (root-commit) 1a2b3c4] init"):
        m = rs._COMMIT_ECHO.search(echo)
        check("echo: %s" % echo, bool(m) and m.group(1) == "1a2b3c4", str(m))
    check("echo: no hash -> no match", rs._COMMIT_ECHO.search("[main] nothing") is None)


def test_ticket_attribution_is_not_a_substring_test():
    import re as _re
    subject = "fix #12345 thing"
    hit = [t for t in ("1234", "12345") if _re.search(r"(?<!\d)" + _re.escape(t) + r"(?!\d)", subject)]
    check("attribution: 1234 does not claim #12345", hit == ["12345"], str(hit))


def test_the_brains_own_test_scripts_count_as_test_commands():
    import hook as _hook
    check("testcmd: python agent_view/test_run_summary.py",
          _hook.is_test_command("python agent_view/test_run_summary.py"))
    check("testcmd: PYTHONIOENCODING=utf-8 python scripts/tickets/test_worklog.py 2>&1 | tail -1",
          _hook.is_test_command("PYTHONIOENCODING=utf-8 python scripts/tickets/test_worklog.py 2>&1 | tail -1"))
    check("testcmd: a plain script is not a test", not _hook.is_test_command("python build_estimates.py --root x"))

def main():
    for fn in (test_the_brains_own_test_scripts_count_as_test_commands,
               test_commit_echo_forms_and_ticket_attribution, test_ticket_attribution_is_not_a_substring_test,
               test_full_run_yields_files_tests_commits_turns_summary,
               test_a_corrupt_line_is_skipped_not_fatal,
               test_a_failed_test_run_is_false_and_the_last_run_wins,
               test_unparsable_test_output_is_none_never_a_tick,
               test_no_tests_at_all,
               test_a_commit_that_did_not_happen_is_not_reported,
               test_bash_result_read_from_tool_use_result_stdout,
               test_a_huge_transcript_is_tail_capped,
               test_missing_and_empty_transcripts_are_blank):
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
