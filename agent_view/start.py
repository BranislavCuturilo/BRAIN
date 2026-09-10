#!/usr/bin/env python3
"""Start the Live Agent View server, optionally in a venv, and open the page.

Convenience launcher. The server itself is stdlib-only, so a venv is NOT
required to run it — it exists only so future extras (e.g. a text-to-speech
voice for the session) have somewhere to install without touching the system
Python. If a `.venv` exists next to this file, its Python is used; otherwise
the current interpreter runs the server directly.

  python start.py                # start on the configured host/port, open browser
  python start.py --no-open      # start, don't open a browser
  python start.py --port 8080    # override port
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"


def venv_python() -> str:
    if os.name == "nt":
        cand = VENV / "Scripts" / "python.exe"
    else:
        cand = VENV / "bin" / "python"
    return str(cand) if cand.exists() else sys.executable


def free_port(port: int) -> None:
    """Kill any process already listening on `port`, so a repeat launch does
    not leave orphans (they can share the socket via SO_REUSEADDR, and then a
    browser hits a stale instance). Best-effort and silent — if nothing holds
    the port, or the platform tools are missing, this is a no-op.
    """
    import subprocess
    nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # no flashing console via pythonw
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano"], capture_output=True,
                                 text=True, timeout=5, creationflags=nw).stdout
            pids = set()
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    pids.add(line.split()[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, timeout=5, creationflags=nw)
        else:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                                 capture_output=True, text=True, timeout=5).stdout
            for pid in out.split():
                subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
    except Exception:
        pass


def main() -> int:
    args = sys.argv[1:]
    open_browser = "--no-open" not in args
    passthru = [a for a in args if a != "--no-open"]

    py = venv_python()
    server = HERE / "server.py"

    # Resolve the port we're about to use, then free it — so re-launching does
    # not stack orphaned servers on the same port.
    port_str = "7666"
    if "--port" in passthru:
        port_str = passthru[passthru.index("--port") + 1]
    elif os.environ.get("AGENT_VIEW_PORT"):
        port_str = os.environ["AGENT_VIEW_PORT"]
    try:
        free_port(int(port_str))
    except ValueError:
        pass

    # ONE opener, and it is the server's. This launcher used to open the page
    # itself with `webbrowser.open`, which picks the DEFAULT browser — so a
    # machine whose default is Opera got two tabs on every start: Opera from
    # here and Chrome from the server. Chrome is not a preference: the voice
    # feature's Web Speech recognition works only in Chrome/Edge and does
    # nothing at all in Opera/Brave, so the tab this launcher opened was the
    # useless one of the two.
    #
    # `--no-open` therefore has to reach the CHILD, or suppressing the browser
    # here would just move the tab rather than prevent it.
    env = dict(os.environ)
    if not open_browser:
        env["AGENT_VIEW_OPEN_BROWSER"] = "0"

    # CREATE_NO_WINDOW: when the desktop shortcut launches this via pythonw,
    # the server child must not pop its own console window either.
    proc = subprocess.Popen([py, str(server), *passthru], env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return proc.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
