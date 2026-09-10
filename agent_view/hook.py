#!/usr/bin/env python3
"""Push one hook event to the Live Agent View server. Fire-and-forget.

Wired to the same hook phases as `agent_sound.py` — start / done / ask /
error — so the picture on the web page and the sound you hear come from the
same events. It must never slow a tool call: a short timeout, every failure
swallowed, always exits 0. If the server is not running, this does nothing.

  hook.py start   <agent>
  hook.py done    <agent>
  hook.py ask
  hook.py error
  hook.py toolfail          (reads the payload; only fires on a real failure)

The agent's GROUP, COLOUR and voice RACE are resolved from the SAME sources
the dashboard and the sounds use — docs.AGENT_GROUPS + dashboard.GROUP_ICON
for the colour, agent_sound.FAMILY_RACE for the voice — so nothing about the
visual language is duplicated here. If those imports fail (run standalone),
it still posts the event without the derived fields and the server copes.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRAIN = HERE.parent
SCRIPTS = BRAIN / "scripts" / "brain"
CONFIG = HERE / "agent_view.config.json"

DEFAULT_PORT = 7666
POST_TIMEOUT_S = 0.6


# -- test-command detection (shared with testrun.py, which imports these) ---- #
#: A Bash command that runs a test suite. The wrapper (testrun.py) OWNS its own
#: run events, so a command that goes through it is skipped here — otherwise the
#: same run would be tracked twice under two different run_ids.
_TEST_CMD_RE = re.compile(
    r"""(?xi)
      (?: manage\.py \s+ test\b )
    | (?: \bpytest\b )
    | (?: python \s+ -m \s+ pytest\b )
    | (?: python \s+ -m \s+ unittest\b )
    | (?: run_tests_prepush )
    | (?: \bnpm \s+ (?: run \s+ )? test\b )
    | (?: \bjest\b )
    | (?: \bgo \s+ test\b )
    | (?: \btox\b )
    | (?: python(?:3)? \s+ \S*test_\w+\.py\b )   # the brain's own `python test_x.py` scripts
    """)


def is_test_command(cmd) -> bool:
    c = cmd or ""
    if "testrun.py" in c:
        return False          # the streaming wrapper posts its own run events
    return bool(_TEST_CMD_RE.search(c))


def normalize_cmd(cmd) -> str:
    """Collapse whitespace so trivial reformatting maps to the SAME run_id."""
    return " ".join((cmd or "").split())


def run_id_for(session, cmd) -> str:
    """A deterministic id from session + normalized command, so a PreToolUse
    start and its PostToolUse end land on the one run."""
    base = "%s\x00%s" % (session or "-", normalize_cmd(cmd))
    return hashlib.sha1(base.encode("utf-8", "replace")).hexdigest()[:16]


def _config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _endpoint(path: str = "/event") -> str:
    cfg = _config()
    port = os.environ.get("AGENT_VIEW_PORT") or cfg.get("port") or DEFAULT_PORT
    # Post to loopback even when the server binds 0.0.0.0 — the hook and the
    # server are on the same machine; browsers are the ones that come over LAN.
    return f"http://127.0.0.1:{port}{path}"


# -- shared mappings, imported not copied ----------------------------------- #
def _derive(agent: str) -> dict:
    """group + colour + race for an agent, from the canonical sources."""
    out = {"group": "", "color": "", "race": ""}
    agent = (agent or "").split(":")[-1]
    sys.path.insert(0, str(SCRIPTS))
    try:
        import docs as docs_mod            # AGENT_GROUPS
        import dashboard as dash_mod        # GROUP_ICON -> colour slug
        group = next((title for title, names in docs_mod.AGENT_GROUPS
                      if agent in names), "")
        out["group"] = group
        slug = dash_mod.GROUP_ICON.get(group, ("", ""))[1]
        # Light-theme colour per group, mirrored from dashboard CSS.
        out["color"] = _GROUP_COLOR.get(slug, "")
    except Exception:
        pass
    try:
        import agent_sound as snd_mod       # FAMILY_RACE + family_of
        out["race"] = snd_mod.FAMILY_RACE.get(snd_mod.family_of(agent), "human")
    except Exception:
        pass
    return out


#: colour per group slug — the SAME hexes as dashboard.py's `.gc-*` (light).
#: Kept here as a fallback map because dashboard.py holds them in CSS text, not
#: a dict; if that ever becomes a dict, import it instead.
_GROUP_COLOR = {
    "coordination": "#8b5cf6", "reading": "#0ea5e9", "consultants": "#14b8a6",
    "review": "#e11d48", "backend": "#f59e0b", "views": "#3b82f6",
    "front": "#ec4899", "slices": "#22c55e", "operations": "#64748b",
    "brain": "#a855f7",
}


def _payload_agent() -> tuple[str, dict]:
    """When no agent arg is given, read it from the hook's JSON on stdin."""
    data = {}
    try:
        if not sys.stdin.isatty():
            # Read ALL of stdin. A 20 000-char cap here truncated every large
            # PostToolUse payload (a long Read/Bash tool_response), json.loads
            # failed, data stayed {} and the event landed in a phantom "default"
            # session with no prompt, no agents and an empty tool name — the
            # "session with 37 tools and '/'" the operator kept seeing (2026-08-19).
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
    except Exception:
        data = {}
    ti = data.get("tool_input") or {}
    # Agent-tool launches carry the type in tool_input; SubagentStart/SubagentStop
    # (also fired for Workflow-spawned agents, which never go through the Agent
    # tool) carry it top-level as agent_type — read both so workflow agents show
    # up as nodes instead of the session looking like "main only".
    agent = (ti.get("subagent_type") or ti.get("agentType")
             or data.get("agent_type") or "")
    return agent, data


def post(ev: dict, path: str = "/event") -> None:
    body = json.dumps(ev).encode()
    req = urllib.request.Request(_endpoint(path), data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=POST_TIMEOUT_S).read()
    except Exception:
        pass  # server down / slow — never block the tool call


# -- test-run start / end from a Bash tool call ----------------------------- #
def tool_output_text(resp) -> str:
    """The best-effort text of a Bash tool_response, across the shapes it takes
    (a plain string, a {stdout,stderr,...} dict, or a list of {text} blocks).

    PUBLIC because run_summary.py reads the same shapes out of a SAVED
    transcript - one definition of "the text of a tool result", so a shape that
    turns up later is handled once for both the live hook and the summary."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        parts = [resp[k] for k in ("stdout", "stderr", "output", "content", "result")
                 if isinstance(resp.get(k), str)]
        return "\n".join(parts)
    if isinstance(resp, list):
        parts = []
        for b in resp:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


def _parse_final_safe(text: str) -> dict:
    """testparse.parse_final, but resilient to the module not importing (the
    server or the parser could be absent when the hook fires standalone)."""
    try:
        sys.path.insert(0, str(HERE))
        import testparse                       # sibling module, same dir
        return testparse.parse_final(text)
    except Exception:
        return {"status": "unknown", "total": 0, "passed": 0, "failed": 0, "fails": []}


def _handle_testrun(data: dict, phase_hint: str = "") -> None:
    """Post a testrun start (PreToolUse) or end (PostToolUse) for a test Bash
    command. Driven by the PAYLOAD's hook event + command, INDEPENDENT of the
    argv phase, so one call posts exactly the right one. Non-Bash / non-test
    tools are a no-op. Every failure here is swallowed by the caller."""
    if not isinstance(data, dict) or (data.get("tool_name") or "") != "Bash":
        return
    event = (data.get("hook_event_name") or "").strip()
    if not event:                              # older payloads omit it — use the
        event = {"pretool": "PreToolUse",       # phase the hook was invoked with
                 "toolfail": "PostToolUse"}.get(phase_hint, "")
    if event not in ("PreToolUse", "PostToolUse"):
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not is_test_command(cmd):
        return
    session = str(data.get("session_id")
                  or os.environ.get("CLAUDE_SESSION_ID") or "default")
    run_id = run_id_for(session, cmd)
    norm = normalize_cmd(cmd)
    if event == "PreToolUse":
        post({"phase": "start", "run_id": run_id, "session": session, "cmd": norm},
             path="/testrun")
    else:                                       # PostToolUse
        res = _parse_final_safe(tool_output_text(data.get("tool_response")))
        post({"phase": "end", "run_id": run_id, "session": session, "cmd": norm,
              "status": res.get("status", "unknown"),
              "total": res.get("total", 0), "passed": res.get("passed", 0),
              "failed": res.get("failed", 0), "fails": res.get("fails", [])},
             path="/testrun")


def main() -> int:
    try:
        args = sys.argv[1:]
        phase = args[0] if args else "turn"
        agent = args[1] if len(args) > 1 else ""
        data = {}
        if not agent:
            agent, data = _payload_agent()

        # Test-run tracking — driven by the PAYLOAD (hook event + Bash command),
        # not the argv phase, so a test Bash command posts a testrun start on
        # PreToolUse and a parsed end on PostToolUse. Wrapped fail-safe: it must
        # never affect the ordinary /event post below.
        try:
            _handle_testrun(data, phase)
        except Exception:
            pass

        if phase == "pretool":
            # PreToolUse on Bash exists to fire the testrun start above — and,
            # for a run launched FOR TICKETS (BRAIN_WORK_ID set), to send a bare
            # heartbeat BEFORE a long command starts, so a 40-minute test run is
            # not mistaken for an idle session by the work-log reaper. A non-test
            # Bash command in an unmeasured session was never a tracked /event.
            if os.environ.get("BRAIN_WORK_ID"):
                post({"session_id": (data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "default"),
                      "phase": "heartbeat", "agent": "main",
                      "work_id": os.environ.get("BRAIN_WORK_ID"),
                      "transcript_path": data.get("transcript_path") or ""})
            return 0

        if phase == "toolfail":
            # This fires on EVERY tool (PostToolUse matcher *). A failure is an
            # "error"; a success becomes a "tool" event so the view can show
            # which tools the session is using — reusing this one invocation
            # rather than spawning a second python per tool call.
            resp = data.get("tool_response")
            failed = isinstance(resp, dict) and bool(
                resp.get("is_error") or resp.get("error"))
            phase = "error" if failed else "tool"
            # TodoWrite is Claude's own plan — surface it as Flow-view
            # milestones instead of a plain tool tick.
            if phase == "tool" and data.get("tool_name") == "TodoWrite":
                phase = "todos"

        agent = agent or "main"
        ev = {
            "session_id": (data.get("session_id")
                           or os.environ.get("CLAUDE_SESSION_ID") or "default"),
            "cwd": data.get("cwd") or os.getcwd(),
            "agent": agent,
            "phase": phase,
            "tool": (data.get("tool_name") or ""),
        }
        # The work log. A Claude the HUD launched FOR TICKETS was given
        # BRAIN_WORK_ID in its environment; carrying it on every event is what
        # lets the server keep that run's interval alive and close it at the end.
        # Both keys are OMITTED when absent, so an ordinary session's event is
        # byte-for-byte what it always was. Nothing is read or parsed here — the
        # server reads the transcript later, off the hook path.
        work_id = os.environ.get("BRAIN_WORK_ID")
        if work_id:
            ev["work_id"] = work_id
        transcript = data.get("transcript_path")
        if transcript:
            ev["transcript_path"] = transcript
            # WHO LAUNCHED THIS. A subagent's events were flat: `agent` said
            # WHICH kind ran, never under whom, so two concurrent fan-outs of the
            # same agent kind were indistinguishable in the HUD. The parent is
            # not guessed and not read from the environment -- a subagent's
            # transcript is written to `<parent-session>/subagents/agent-*.jsonl`,
            # so the path itself names the parent. Derived, therefore correct or
            # absent, never wrong. (The idea is ag-ui's `parent_run_id`; the
            # mechanism is ours, because their protocol is not in play here.)
            try:
                tp = pathlib.PurePath(transcript)
                if tp.parent.name == "subagents":
                    parent = tp.parent.parent.name
                    if parent:
                        ev["parent_session"] = parent
            except (ValueError, IndexError):
                pass
        if phase == "prompt":
            # UserPromptSubmit carries the prompt; the Flow view shows it whole
            # (a 3-line clamp with click-to-expand), so keep the full text — the
            # old 400-char cap made the "full prompt" modal show a truncated one.
            # Bounded generously so a pathological paste can't bloat the event ring.
            ev["label"] = (data.get("prompt") or "")[:50000]
        if phase == "todos":
            ti = data.get("tool_input") or {}
            ev["todos"] = [{"content": (t.get("content") or "")[:120],
                            "status": t.get("status") or "pending"}
                           for t in (ti.get("todos") or [])][:40]
        ev.update(_derive(agent))
        post(ev)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
