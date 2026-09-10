#!/usr/bin/env python3
"""Play a short sound for what just happened. Purely for enjoyment.

Built so it can never get in the way: every failure is swallowed, playback is
fire-and-forget, and the script exits 0 no matter what. A broken sound must
never block a tool call.

  agent_sound.py start   <agent>    an agent was launched
  agent_sound.py done    <agent>    an agent finished
  agent_sound.py turn                the main loop finished a turn
  agent_sound.py error               something failed
  agent_sound.py toolfail            a tool failed (checks the payload first)
  agent_sound.py ask                 waiting on you
  agent_sound.py --check             print the mapping and exit

**Volume.** `System.Media.SoundPlayer` has no volume control at all, so this
uses WPF's `MediaPlayer`, which does. Set `BRAIN_SOUND_VOLUME` (0.0-1.0);
default 0.25. Nothing is re-encoded, so the vendored clips stay untouched and
the level can be changed at any time.

**Which clip.** Hashed on the AGENT NAME, so two agents are reliably different
from each other and each one sounds the same every time. The same sound for the
same thing is information; a random one is noise.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BRAIN = Path(__file__).resolve().parent.parent.parent
PACKS = BRAIN / "sounds"
USER_SOUNDS = Path.home() / ".claude" / "sounds"
SYSTEM = Path("C:/Windows/Media")

DEFAULT_PACK = "wc3-voice"
DEFAULT_VOLUME = 0.25

#: The pack is laid out `<pack>/<race>/<event>/*.wav|mp3` and holds spoken
#: lines, not music — "Work complete", "Job's done", "Ready to work", "What?".
#: A line you can understand tells you what happened without looking; a chord
#: only tells you that something did.
EVENT_ACCEPT = "accept"        # "Yes milord" / "Ready to work"
EVENT_COMPLETE = "complete"    # "Work complete" / "Job's done"
EVENT_ASK = "ask"              # "What?"

#: phase -> which spoken line fits it.
PHASE_EVENT = {
    "start": EVENT_ACCEPT,
    "done": EVENT_COMPLETE,
    "turn": EVENT_COMPLETE,
    "ask": EVENT_ASK,
    "error": EVENT_ASK,
}

#: Agent family -> which race speaks for it. The pack ships FOUR voices
#: (human/nightelf/orc/undead), so at most four families can sound distinct —
#: the mapping's whole job is to spend those four on the widest useful split.
#: The earlier version wasted one: `read` and `research` were BOTH nightelf, so
#: a scout and a researcher were indistinguishable, and every writer shared
#: human with no way to tell a backend change from a template edit. Now each
#: race carries one clearly different KIND of work:
#:   nightelf — passive READING (scout, readers): light, airy, "no side effect"
#:   human    — WRITING code (backend, views, front end, slices): the workhorse
#:   orc      — REVIEW / QA / debugging: gruff, "something is being judged"
#:   undead   — SECURITY and anything advisory/coordinating: the grave voice
#: research/coordination share undead with security on purpose — they are the
#: "think, don't touch" families, and pairing them with the error voice means
#: an unexpected undead line always says "nothing was written."
FAMILY_RACE = {
    "read": "nightelf",
    "write": "human",
    "review": "orc",
    "security": "undead",
    "research": "undead",
    "default": "human",
}

#: A failure always speaks with the same voice regardless of who was working.
#: Consistency is the information here — an error that sounds like whoever
#: happened to be running is an error you have to think about to recognise.
ERROR_RACE = "undead"

MEMBERS = {
    "read": {"scout", "repo-reader", "dj-model-reader", "dj-view-reader",
             "impact-mapper", "ticket-reader", "project-expert",
             "screenshot-reader"},
    "security": {"security", "appsec-reviewer"},
    "review": {"reviewer", "qa", "debugger", "optimizer", "refactorer",
               "brain-keeper", "watchdog"},
    "research": {"researcher", "mysql-consultant", "data-model-consultant",
                 "inventory-consultant", "planner", "synthesizer",
                 "orchestrator", "pm"},
}

#: The main loop is not a subagent and carries no agent type, but it is the
#: thing running most of the time — so it gets its own voice rather than
#: silence, which is what the previous version left it with.
MAIN_LOOP = "main"


def family_of(agent: str) -> str:
    agent = (agent or "").split(":")[-1]
    if not agent or agent == MAIN_LOOP:
        return "default"
    for name, members in MEMBERS.items():
        if agent in members:
            return name
    return "write"


def _stable(text: str) -> int:
    """A hash that does not change between runs.

    Python's `hash()` is randomised per process for strings, so using it would
    give an agent a different voice every session — which defeats the entire
    point of a stable mapping.
    """
    value = 0
    for char in text or "-":
        value = (value * 31 + ord(char)) & 0xFFFFFFFF
    return value


def clip_for(agent: str, phase: str) -> Path | None:
    agent = (agent or MAIN_LOOP).split(":")[-1]
    fam = family_of(agent)
    event = PHASE_EVENT.get(phase, EVENT_COMPLETE)
    race = ERROR_RACE if phase == "error" else FAMILY_RACE.get(fam, "human")

    # Anything the owner drops in wins, most specific name first.
    for stem in (f"{agent}-{phase}", agent, f"{fam}-{phase}", fam, "default"):
        for ext in (".wav", ".mp3"):
            candidate = USER_SOUNDS / f"{stem}{ext}"
            if candidate.exists():
                return candidate

    event_dir = (PACKS / os.environ.get("BRAIN_SOUNDPACK", DEFAULT_PACK)
                 / race / event)
    if event_dir.is_dir():
        clips = sorted(p for p in event_dir.iterdir()
                       if p.suffix.lower() in (".wav", ".mp3"))
        if event == EVENT_ACCEPT:
            # The pack files both the affirmatives ("Yes milord", "Ready to
            # work") and the questioning ones ("What?") under `accept`. Only
            # the affirmatives mean "starting" — leaving the questions in made
            # a task launch sound exactly like being asked a question, which
            # is the one distinction the sounds exist to draw.
            affirmative = [p for p in clips
                           if 'yes' in p.stem.lower()
                           or 'ready' in p.stem.lower()]
            clips = affirmative or clips
        if clips:
            # Per AGENT, not per family: two writers in the same race must not
            # share a line. `complete` ships one clip per race, so completions
            # differ by family only — which is the right granularity for the
            # sound you hear most.
            #
            # A failure is the exception: it hashes on a constant so it is the
            # SAME line every time, from anyone. One sound you learn to
            # recognise beats five you have to think about.
            seed = 'failure' if phase == 'error' else agent
            return clips[_stable(seed) % len(clips)]

    fallback = SYSTEM / "Windows Ding.wav"
    return fallback if fallback.exists() else None


def play(path: Path) -> None:
    volume = os.environ.get("BRAIN_SOUND_VOLUME", str(DEFAULT_VOLUME))
    try:
        level = min(1.0, max(0.0, float(volume)))
    except ValueError:
        level = DEFAULT_VOLUME
    if level <= 0:
        return
    script = (
        "Add-Type -AssemblyName presentationCore;"
        "$p=New-Object System.Windows.Media.MediaPlayer;"
        f"$p.Open([uri]'{path}');"
        f"$p.Volume={level};"
        "$p.Play();"
        # MediaPlayer is asynchronous and dies with its process, so the shell
        # has to outlive the clip. Capped, so a long file cannot leave a
        # PowerShell sitting around.
        "Start-Sleep -Milliseconds 2500;$p.Close()")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _payload() -> dict:
    """Hook payloads arrive as JSON on stdin. Absent or unreadable is fine."""
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read(20000)
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def main() -> int:
    try:
        args = sys.argv[1:]
        if args and args[0] == "--check":
            phases = ("start", "done", "ask", "error")
            print(f"  {'agent':<18} " + " ".join(f"{p:<26}" for p in phases))
            for agent in ("main", "security", "scout", "dj-models", "qa",
                          "researcher", "dj-forms", "devops"):
                cells = []
                for phase in phases:
                    clip = clip_for(agent, phase)
                    cells.append(f'{clip.parent.parent.name}/{clip.name}'
                                 if clip else "-")
                print(f"  {agent:<18} " + " ".join(f"{c:<26}" for c in cells))
            print(f"  volume {os.environ.get('BRAIN_SOUND_VOLUME', DEFAULT_VOLUME)}")
            return 0

        phase = args[0] if args else "turn"
        agent = args[1] if len(args) > 1 else ""
        data = {} if agent else _payload()

        if phase == "toolfail":
            # Read the failure from the payload rather than guessing it from
            # prose: a response carrying is_error IS a failure, while matching
            # the word "error" in output fires on every message that merely
            # mentions one.
            response = data.get("tool_response")
            failed = isinstance(response, dict) and bool(
                response.get("is_error") or response.get("error"))
            if not failed:
                return 0
            phase = "error"
        elif not agent:
            tool_input = data.get("tool_input") or {}
            agent = (tool_input.get("subagent_type")
                     or tool_input.get("agentType") or "")

        clip = clip_for(agent or MAIN_LOOP, phase)
        if clip:
            play(clip)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
