#!/usr/bin/env python3
"""Offline tests for dispatch-to-Claude (server.launch_claude + the route gate).

The important ones: subprocess.Popen is monkeypatched to CAPTURE args WITHOUT
spawning, proving (a) a hostile prompt stays inside ONE argv element to a REAL
executable — never a .cmd/.bat shim that cmd.exe would re-parse — (b) a
`"`-laden prompt is still exactly one element (the case an earlier test missed),
(c) an npm .cmd shim is bypassed for the real program it wraps, (d) an
unresolvable shim FAILS CLOSED, and (e) a `-`-leading prompt gets a `--` guard.
The route is also exercised in-process over loopback to prove the same-origin
CSRF gate, and _client_is_local is unit-tested. No network beyond loopback, no
real process spawned. Run:  python test_claude_launch.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server      # noqa: E402


# --------------------------------------------------------------------------- #
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
#  Capture Popen without spawning anything.
# --------------------------------------------------------------------------- #
_POPEN_CALLS = []
_REAL_POPEN = subprocess.Popen
_REAL_WHICH = shutil.which


class _FakePopen:
    def __init__(self, args, **kwargs):
        _POPEN_CALLS.append((args, kwargs))

    def poll(self):
        return None


def _install_capture(which_result=r"C:\fake\bin\claude.exe"):
    """Default: claude resolves to a REAL .exe (the safe happy path). Pass a shim
    path to exercise the .cmd-bypass logic."""
    _POPEN_CALLS.clear()
    subprocess.Popen = _FakePopen                       # launch_claude does `import subprocess`
    shutil.which = lambda name: which_result if name == "claude" else which_result


def _restore():
    subprocess.Popen = _REAL_POPEN
    shutil.which = _REAL_WHICH


# A prompt that is nothing BUT shell metacharacters and command separators. If any
# of it were shell-parsed, `calc.exe`, `reboot`, `del`, `rm -rf /` would run.
HOSTILE = 'ok & calc.exe | del C:\\Windows > x < y ; rm -rf / `whoami` $(reboot) && echo pwned || nc -e'

# The payload the independent review used to break the OLD .cmd path: an embedded
# double-quote. list2cmdline escapes it as \" which cmd.exe does NOT honour, so on
# a .cmd shim it broke out. It must now be exactly one argv element to a real exe.
QUOTE_PAYLOAD = 'summarize this " & type nul > INJECTED.txt & " done'


# --------------------------------------------------------------------------- #
#  The important test: the prompt is one argv element, to a REAL executable.
# --------------------------------------------------------------------------- #
def test_prompt_is_one_argv_element():
    _install_capture()                                  # claude → real .exe
    try:
        res, code = server.launch_claude(HOSTILE)
    finally:
        _restore()
    check("launch: returns ok/200", res.get("ok") is True and code == 200, f"{res} {code}")
    check("launch: exactly one spawn", len(_POPEN_CALLS) == 1, str(len(_POPEN_CALLS)))
    args, kwargs = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    check("launch: argv is a LIST", isinstance(args, list), type(args).__name__)
    check("launch: shell is not True", kwargs.get("shell") in (False, None),
          repr(kwargs.get("shell")))
    check("launch: prompt is a single, distinct argv element (unsplit)",
          isinstance(args, list) and args.count(HOSTILE) == 1, str(args))
    check("launch: prompt is the LAST argv element",
          isinstance(args, list) and args and args[-1] == HOSTILE, str(args))
    check("launch: no OTHER argv element carries the payload",
          isinstance(args, list) and all(a == HOSTILE or "calc.exe" not in a for a in args),
          str(args))
    check("launch: argv[0] is a REAL executable, not a .cmd/.bat/.ps1 shim",
          isinstance(args, list) and args
          and not args[0].lower().endswith(server._WIN_SHIM_EXT), str(args[0] if args else None))
    if sys.platform.startswith("win"):
        check("launch(win): CREATE_NEW_CONSOLE set",
              kwargs.get("creationflags") == subprocess.CREATE_NEW_CONSOLE,
              repr(kwargs.get("creationflags")))


def test_quote_laden_prompt_stays_one_element():
    _install_capture()
    try:
        res, code = server.launch_claude(QUOTE_PAYLOAD)
    finally:
        _restore()
    args, _k = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    check("quote: ok/200", code == 200 and res.get("ok") is True, f"{res} {code}")
    check('quote: the "-laden payload is exactly one argv element, and last',
          isinstance(args, list) and args.count(QUOTE_PAYLOAD) == 1 and args[-1] == QUOTE_PAYLOAD,
          str(args))
    check("quote: argv[0] is a real exe (no cmd.exe re-parse in the path)",
          isinstance(args, list) and args
          and not args[0].lower().endswith(server._WIN_SHIM_EXT), str(args[0] if args else None))


def test_win_shim_is_bypassed_for_its_real_exe():
    if not sys.platform.startswith("win"):
        check("shim-bypass: (skipped, non-Windows)", True)
        return
    d = Path(tempfile.mkdtemp(prefix="claudeshim_"))
    bundled = d / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    bundled.parent.mkdir(parents=True, exist_ok=True)
    bundled.write_bytes(b"MZ")                          # just needs to exist as a file
    shim = d / "claude.cmd"
    shim.write_text('@ECHO off\r\nSET dp0=%~dp0\r\n'
                    '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"  %*\r\n',
                    encoding="utf-8")
    _POPEN_CALLS.clear()
    subprocess.Popen = _FakePopen
    shutil.which = lambda name: str(shim) if name == "claude" else None
    try:
        res, code = server.launch_claude("hello world")
    finally:
        _restore()
    args, _k = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    check("shim-bypass: ok/200", code == 200, f"{res} {code}")
    check("shim-bypass: argv[0] is the bundled .exe, NOT the .cmd shim",
          isinstance(args, list) and args and args[0].lower().endswith(".exe")
          and args[0].lower() != str(shim).lower(), str(args[0] if args else None))
    check("shim-bypass: prompt is the last argv element",
          isinstance(args, list) and args and args[-1] == "hello world", str(args))


def test_win_shim_failclosed_when_target_unresolvable():
    if not sys.platform.startswith("win"):
        check("failclosed: (skipped, non-Windows)", True)
        return
    d = Path(tempfile.mkdtemp(prefix="claudeshim2_"))
    shim = d / "claude.cmd"
    shim.write_text('@ECHO off\r\nREM this shim names no .exe or .js target\r\n', encoding="utf-8")
    _POPEN_CALLS.clear()
    subprocess.Popen = _FakePopen
    shutil.which = lambda name: str(shim) if name == "claude" else None
    try:
        res, code = server.launch_claude("hello")
    finally:
        _restore()
    check("failclosed: an unresolvable shim → clean 502 (never runs the .cmd)",
          code == 502 and "error" in res, f"{res} {code}")
    check("failclosed: NO spawn attempted", len(_POPEN_CALLS) == 0, str(_POPEN_CALLS))


def test_flag_leading_prompt_is_guarded():
    _install_capture()
    try:
        res, code = server.launch_claude("--dangerously-skip-permissions")
    finally:
        _restore()
    args, _k = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    check("flag-guard: ok/200", code == 200, f"{res} {code}")
    check("flag-guard: a '--' separator immediately precedes a '-'-leading prompt",
          isinstance(args, list) and len(args) >= 2 and args[-2] == "--"
          and args[-1] == "--dangerously-skip-permissions", str(args))


def test_ordinary_prompt_has_no_separator():
    _install_capture()
    try:
        server.launch_claude("please summarize my inbox")
    finally:
        _restore()
    args, _k = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    check("no-sep: no '--' immediately before an ordinary prompt",
          isinstance(args, list) and len(args) >= 2 and args[-2] != "--"
          and args[-1] == "please summarize my inbox", str(args))


def test_permission_mode_precedes_prompt():
    # The spawned argv must launch claude non-interactively: a
    # `--permission-mode <mode>` pair BEFORE the prompt, defaulting to "auto",
    # without splitting the prompt out of its single argv element.
    import os
    os.environ.pop("CLAUDE_PERMISSION_MODE", None)      # default path
    _install_capture()
    try:
        server.launch_claude("please summarize my inbox")
    finally:
        _restore()
    args, _k = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    check("permission-mode: '--permission-mode' present in argv",
          isinstance(args, list) and "--permission-mode" in args, str(args))
    ok = isinstance(args, list) and "--permission-mode" in args
    i = args.index("--permission-mode") if ok else -1
    check("permission-mode: value is the default 'auto' and immediately follows the flag",
          ok and i + 1 < len(args) and args[i + 1] == "auto", str(args))
    check("permission-mode: the flag and its value precede the prompt",
          ok and "please summarize my inbox" in args
          and i + 1 < args.index("please summarize my inbox"), str(args))
    check("permission-mode: prompt is still one unsplit element (last)",
          isinstance(args, list) and args and args[-1] == "please summarize my inbox",
          str(args))
    check("permission-mode: argv[0] is still a REAL exe (no shim)",
          isinstance(args, list) and args
          and not args[0].lower().endswith(server._WIN_SHIM_EXT), str(args[0] if args else None))


def test_permission_mode_configurable_via_env():
    import os
    _install_capture()
    os.environ["CLAUDE_PERMISSION_MODE"] = "acceptEdits"
    try:
        server.launch_claude("do a thing")
    finally:
        os.environ.pop("CLAUDE_PERMISSION_MODE", None)
        _restore()
    args, _k = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    ok = isinstance(args, list) and "--permission-mode" in args
    check("permission-mode(env): CLAUDE_PERMISSION_MODE overrides the default",
          ok and args[args.index("--permission-mode") + 1] == "acceptEdits", str(args))
    check("permission-mode(env): prompt still last and unsplit",
          isinstance(args, list) and args and args[-1] == "do a thing", str(args))


def test_permission_mode_leading_dash_prompt_still_guarded():
    # A `-`-leading prompt keeps its `--` guard AFTER the mode flags: argv ends
    # [..., "--permission-mode", <mode>, "--", <prompt>].
    import os
    os.environ.pop("CLAUDE_PERMISSION_MODE", None)
    _install_capture()
    try:
        server.launch_claude("--dangerously-skip-permissions")
    finally:
        _restore()
    args, _k = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    ok = isinstance(args, list) and "--permission-mode" in args
    check("permission-mode(-prompt): mode flag precedes the '--' guard",
          ok and args.index("--permission-mode") < args.index("--"), str(args))
    check("permission-mode(-prompt): '--' still immediately precedes the prompt",
          isinstance(args, list) and len(args) >= 2 and args[-2] == "--"
          and args[-1] == "--dangerously-skip-permissions", str(args))


def test_empty_and_overlong_rejected_before_spawn():
    _install_capture()
    try:
        r1, c1 = server.launch_claude("")
        r2, c2 = server.launch_claude("   ")
        r3, c3 = server.launch_claude("x" * (server.CLAUDE_PROMPT_FILE_MAX + 1))
    finally:
        _restore()
    check("launch: empty rejected 400", c1 == 400 and "error" in r1, f"{r1} {c1}")
    check("launch: whitespace-only rejected 400", c2 == 400, f"{r2} {c2}")
    check("launch: over the FILE cap rejected 413", c3 == 413, f"{r3} {c3}")
    check("launch: NO spawn attempted for any rejected prompt",
          len(_POPEN_CALLS) == 0, str(_POPEN_CALLS))


def test_long_prompt_spills_to_file_and_launches_pointer():
    # A prompt over CLAUDE_PROMPT_MAX (a ticket reading with its attachment digest)
    # is written to a temp file and Claude is launched with a SHORT pointer prompt
    # naming that file — never a 413, never an over-long argv element.
    _install_capture()
    body = "TIKET " + ("y" * (server.CLAUDE_PROMPT_MAX + 500))
    try:
        r, c = server.launch_claude(body)
    finally:
        _restore()
    check("spill: long prompt launches (200)", c == 200 and r.get("ok"), f"{r} {c}")
    args, _k = _POPEN_CALLS[0] if _POPEN_CALLS else (None, {})
    last = args[-1] if isinstance(args, list) and args else ""
    check("spill: the argv prompt is the short pointer, not the body",
          isinstance(last, str) and len(last) < 1000 and "TIKET" not in last, str(last)[:200])
    m = None
    for line in (last or "").splitlines():
        line = line.strip()
        if line.lower().endswith(".md") and server.CLAUDE_PROMPT_DIR_NAME in line:
            m = line
    check("spill: pointer names a .md file in the prompts dir", bool(m), str(last)[:200])
    if m:
        try:
            content = Path(m).read_text(encoding="utf-8")
            check("spill: the file holds the full original prompt", content == body,
                  f"{len(content)} vs {len(body)}")
        finally:
            try:
                Path(m).unlink()
            except OSError:
                pass


def test_non_string_prompt_rejected_cleanly():
    # A JSON array/object/number prompt must be a clean 400, never an
    # AttributeError propagating out of the route.
    _install_capture()
    try:
        results = [server.launch_claude(["calc.exe"]), server.launch_claude({"x": 1}),
                   server.launch_claude(42), server.launch_claude(None)]
    finally:
        _restore()
    check("launch: non-string prompts all rejected 400",
          all(code == 400 for _r, code in results), str(results))
    check("launch: non-string prompt never spawns", len(_POPEN_CALLS) == 0, str(_POPEN_CALLS))


def test_claude_not_found_is_clean_error():
    _POPEN_CALLS.clear()
    subprocess.Popen = _FakePopen
    shutil.which = lambda name: None                    # nothing found
    try:
        res, code = server.launch_claude("hello world")
    finally:
        _restore()
    check("launch: missing CLI -> clean 502", code == 502 and "error" in res, f"{res} {code}")
    check("launch: no spawn when the CLI is missing", len(_POPEN_CALLS) == 0)


# --------------------------------------------------------------------------- #
#  The route gate: loopback happy path, CSRF (cross-origin) rejection, and a
#  unit check that a non-loopback peer is refused.
# --------------------------------------------------------------------------- #
def _start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]


def _post(port, payload, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/claude/launch",
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_route_loopback_happy_and_csrf():
    calls = []
    orig = server.launch_claude
    # The fake must take the FULL signature the route calls with (cwd,
    # permission_mode, tickets) - a stub one argument behind raises TypeError
    # inside do_POST, which the client only sees as a dropped connection.
    server.launch_claude = (lambda prompt, cwd=None, permission_mode=None, tickets=None:
                            (calls.append((prompt, cwd, tickets)) or ({"ok": True}, 200)))
    httpd, port = _start_server()
    try:
        code, out = _post(port, {"prompt": "do a thing"})           # loopback, no Origin
        check("route: loopback POST -> 200 ok", code == 200 and out.get("ok") is True,
              f"{out} {code}")
        check("route: reached launch_claude once", len(calls) == 1, str(calls))
        check("route: a body with no tickets measures nothing",
              calls and calls[0][2] == [], str(calls))

        code2, _ = _post(port, {"prompt": "evil"},
                         origin="http://evil.example:1234")         # cross-origin browser
        check("route: cross-origin POST -> 403 (CSRF gate)", code2 == 403, str(code2))
        check("route: launch NOT reached on cross-origin", len(calls) == 1, str(calls))
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.launch_claude = orig


def test_gate_rejects_non_loopback_peer():
    # _client_is_local is the real loopback gate (the mutation guard depends on it).
    h = server.Handler.__new__(server.Handler)
    h.client_address = ("192.168.1.50", 5555)
    check("gate: LAN peer is NOT local", server.Handler._client_is_local(h) is False)
    h.client_address = ("10.0.0.9", 5555)
    check("gate: another LAN peer is NOT local", server.Handler._client_is_local(h) is False)
    h.client_address = ("127.0.0.1", 5555)
    check("gate: loopback peer IS local", server.Handler._client_is_local(h) is True)
    h.client_address = ("::1", 5555)
    check("gate: ipv6 loopback IS local", server.Handler._client_is_local(h) is True)


def test_launch_is_in_the_mutation_gate():
    import inspect
    src = inspect.getsource(server.Handler.do_POST)
    check("gate: /api/claude/launch is in the mutation tuple _MUT",
          '"/api/claude/launch"' in src)
    check("gate: /api/mail/ask is in the mutation tuple _MUT",
          '"/api/mail/ask"' in src)


def main():
    for fn in (test_prompt_is_one_argv_element, test_quote_laden_prompt_stays_one_element,
               test_win_shim_is_bypassed_for_its_real_exe,
               test_win_shim_failclosed_when_target_unresolvable,
               test_flag_leading_prompt_is_guarded, test_ordinary_prompt_has_no_separator,
               test_permission_mode_precedes_prompt, test_permission_mode_configurable_via_env,
               test_permission_mode_leading_dash_prompt_still_guarded,
               test_empty_and_overlong_rejected_before_spawn,
               test_long_prompt_spills_to_file_and_launches_pointer,
               test_non_string_prompt_rejected_cleanly, test_claude_not_found_is_clean_error,
               test_route_loopback_happy_and_csrf, test_gate_rejects_non_loopback_peer,
               test_launch_is_in_the_mutation_gate):
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
