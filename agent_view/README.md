# Live Agent View

A tiny local web app that shows Claude Code's agents **working, live** — the
same colours and icons as the brain dashboard, the same WC3 voice lines you
hear on the CLI, one browser tab per session.

Runs on **plain Python (stdlib only)** — no pip install needed to start it.
Binds `0.0.0.0` so you can open it from a **phone or another computer on the
same LAN** at `http://<this-machine-ip>:<port>/`.

```
python start.py                 # start + open browser  (0.0.0.0:7666)
python start.py --no-open       # start only
python server.py --port 8080    # server directly, custom port
python server.py --host 127.0.0.1   # loopback only (no LAN)
```

Then start Claude Code and work. The moment an agent runs, a **Sesija** tab
appears; more sessions → more tabs.

## Two views (switchable)

The top bar has a **◎ HUD / ▤ tabela** switch. Both are fed by the same live
stream — switching is presentation only.

- **HUD (default)** — a Jarvis-style screen: a central `CLAUDE` reactor with
  concentric rings and a radar sweep, each agent a node orbiting it, a packet
  of light flying core→node when an agent starts, and telemetry panels down the
  sides. Sessions are the pills above the reactor. Stays dark regardless of
  theme.
- **tabela** — the plain observability table: one tab per session, agent rows
  with state, and the event log. Follows the light/dark theme toggle.

Your choice is remembered (`localStorage`).

## How it works

```
Claude Code hook ──POST /event──▶ server.py ──SSE /stream──▶ browser
   (agent_view/hook.py)            (in-memory state)         (index.html)
```

- **`hook.py`** is wired into the brain hooks alongside `agent_sound.py`, on the
  same phases: agent **start / done**, **ask** (waiting on you), **error**. It
  fires-and-forgets a small JSON event to the server. If the server isn't
  running, it does nothing (short timeout, every error swallowed) — it can never
  slow a tool call down.
- **`server.py`** holds the "world" in memory (one entry per session), streams
  changes to every open browser over Server-Sent Events, and serves the page.
- The event carries only **metadata** — agent type, phase, group, colour, the
  tool name. Never a prompt, never file contents. That's what makes the relay
  (below) safe to run.

The agent **group → colour → icon** and **family → voice race** come from the
*same* sources the dashboard and sounds use (`docs.AGENT_GROUPS`,
`dashboard.GROUP_ICON`, `agent_sound.FAMILY_RACE`) — nothing about the visual
language is duplicated here.

## Test runs in the HUD

Test runs show up live. Any Bash command that runs a suite (`manage.py test`,
`pytest`, `go test`, `npm test`, `jest`, `tox`, `run_tests_prepush`, …) is
detected by `hook.py` on PreToolUse/PostToolUse: a run appears when it starts
and flips to passed/failed with a fail list when it ends.

For **live N/M progress** while a suite runs, wrap the command with `testrun.py`:

```
python ~/.claude/skills/brain/agent_view/testrun.py -- python manage.py test appname
python ~/.claude/skills/brain/agent_view/testrun.py -- pytest -q
```

It tees the output unchanged (you still see everything the runner prints) while
streaming a throttled progress bar to the HUD. If the HUD is down the test runs
and prints exactly as normal — the wrapper exits with the child's own exit code.

## Sound & voice — two independent channels

- **🔊 zvuk** — the **exact same WC3 clips** the CLI plays, because the `/sound`
  endpoint resolves them through `agent_sound.clip_for`. Volume mirrors the CLI
  (`0.4`).
- **🗣 govor** — spoken event lines via the browser's built-in **Web Speech
  API** (`speechSynthesis`): free, offline, no key. Says short lines like
  "scout počeo" / "security traži odobrenje", preferring an sr/hr/bs system
  voice if one is installed. Rate-limited so a burst never builds a backlog.
  The button disables itself if the browser has no Speech API.

Both are off until you click (browsers block autoplay until a user gesture);
clicking either also plays a confirming cue and unlocks audio for the session.

## Config

Optional. Copy the example and edit:

```
cp agent_view.config.example.json agent_view.config.json
```

| key      | default   | meaning |
|----------|-----------|---------|
| `host`   | `0.0.0.0` | `127.0.0.1` = this machine only, no LAN |
| `port`   | `7666`    | server port |
| `relay`  | `""`      | empty = **local only**; a URL enables the VPS bridge |
| `apiKey` | `""`      | key for your relay room |
| `room`   | `""`      | which room a remote viewer watches |

`agent_view.config.json` is **gitignored** (it may hold a key); the `.example`
file is the committed template.

## Watching from outside the LAN (VPS relay) — opt-in, not built by default

Local mode covers the whole LAN already. To watch from *anywhere* (mobile data,
another network), you put a small relay on a VPS:

1. The relay is a dumb forwarder: it accepts an authenticated push
   (`POST /push` with your `apiKey`) into a **room**, and streams it to anyone
   watching that room (read-only). It never sees code — only the same metadata
   events described above.
2. Set `relay`, `apiKey`, `room` in the config. `hook.py` then posts to the
   local server *and* the relay.
3. Open `https://<your-vps>/<room>` on the phone.

The relay itself is intentionally **not shipped here** — it's a ~40-line
addition once you have a VPS and decide you want it. The design is in the
project's feasibility page; ask and it gets built as a second, separate piece.

## Files

| file | what |
|------|------|
| `server.py` | the server: `/event`, `/stream`, `/sound`, static |
| `hook.py`   | posts hook events to the server (wired in `hooks/hooks.json`) |
| `start.py`  | launcher: (venv if present) + open browser |
| `web/index.html` | the page — tabs, animated agents, event log |
| `agent_view.config.example.json` | config template |
