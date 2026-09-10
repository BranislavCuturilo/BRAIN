#!/usr/bin/env python3
"""What each tab needs, whether it has it, and what the reader should do next.

Someone who clones this repo opens the HUD and sees tabs. Without this module
every unconfigured tab is an empty panel or a stack trace, and an empty panel
teaches nothing: the reader cannot tell "broken" from "you have not told it
where your helpdesk is". So every tab answers three questions before it renders:

  not_configured  nothing is set. Show the exact line to paste into Claude,
                  because the thing that knows how to configure this is the
                  agent, not a form. One line, copyable, no prose.
  needs_secret    the address is known but the credential is not. Show an
                  INPUT: the reader already has the token in their clipboard,
                  and sending them to a conversation to be told "paste your
                  token" spends a turn to reach the box we could have drawn.
  ready           get out of the way.

## Secrets do not go in the tracked config

This repository keeps its helpdesk token, Gemini key and monitor token in a
TRACKED file. That was a deliberate choice for one private repo with one user
(2026-08-19, "Option A"), and it is the wrong default for anything a stranger
clones: `save_secret()` writes only to a path git ignores, and refuses outright
if the path turns out to be tracked. The refusal is the point -- a rule that is
merely documented gets violated by the next person in a hurry, which is exactly
how three live secrets and a template claiming the opposite came to coexist.

Pure except for `save_secret()`. Everything else is a function of the config
dict, which is what makes `test_capabilities.py` possible.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: Written by the HUD when the reader types a credential. Gitignored, per
#: machine, never merged through git.
SECRETS_PATH = HERE / "agent_view.secrets.json"

NOT_CONFIGURED = "not_configured"
NEEDS_SECRET = "needs_secret"
READY = "ready"


class SecretRefused(Exception):
    """The secrets file is not ignored by git. Writing would stage a credential."""


#: One entry per tab that needs anything. `needs` lists the settings; a field
#: marked secret is never returned to the browser, only its absence.
#:
#: `ask` is what the reader pastes into Claude. A slash command and not a
#: sentence, on purpose: a sentence invites paraphrase, and a paraphrased setup
#: request is answered by a model guessing at what this repo wants.
FEATURES = [
    {
        "id": "tickets",
        "name": "Tiketi",
        "why": "Bez ovoga nema reda tiketa: nema trijaže, nema čitanja, nema profila "
               "korisnika. Sve ostalo u brain-u radi i bez toga.",
        "needs": [
            {"key": "helpdesk_url", "label": "Adresa helpdesk-a", "secret": False,
             "placeholder": "https://helpdesk.vasafirma.rs"},
            {"key": "helpdesk_token", "label": "API token", "secret": True,
             "hint": "Nalog → API → novi token. Čuva se lokalno, van git-a."},
        ],
        "ask": "/setup tickets",
    },
    {
        "id": "mail",
        "name": "Pošta",
        "why": "Čitanje, sažimanje i priprema odgovora. Ništa se ne šalje bez tvoje potvrde.",
        "needs": [
            {"key": "mail_accounts", "label": "Nalozi", "secret": False, "list": True},
        ],
        "ask": "/setup mail",
    },
    {
        "id": "monitor",
        "name": "Produkcija",
        "why": "Metrike servera i kontejnera uživo. Token sme da bude read-only.",
        "needs": [
            {"key": "monitor.url", "label": "Adresa monitora", "secret": False,
             "placeholder": "https://monitor.vasafirma.rs/mcp/"},
            {"key": "monitor.token", "label": "Read-only token", "secret": True},
        ],
        "ask": "/setup monitor",
    },
    {
        "id": "ai",
        "name": "AI",
        "why": "Trijaža tiketa, sažimanje pošte, predlozi. Bez ključa sve determinističko "
               "i dalje radi — samo bez modela.",
        "needs": [
            {"key": "gemini_api_key", "label": "Gemini API ključ", "secret": True,
             "hint": "aistudio.google.com → API keys"},
        ],
        "ask": "/setup ai",
    },
    {
        "id": "git",
        "name": "Git",
        "why": "Grafovi commita i metrike po repou.",
        "needs": [
            {"key": "git_roots", "label": "Gde su repoi", "secret": False, "list": True},
        ],
        "ask": "/setup projects",
    },
]


def _get(cfg: dict, dotted: str):
    """`monitor.token` out of a nested dict. Never raises."""
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _filled(v) -> bool:
    """A key that is present but empty is NOT configured. The template ships
    every field as "" precisely so it parses, and treating that as set is how a
    tab renders ready and then fails on its first request."""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def status_of(feature: dict, cfg: dict, secrets: dict | None = None) -> dict:
    secrets = secrets or {}
    missing_open, missing_secret = [], []
    for need in feature["needs"]:
        key = need["key"]
        have = _filled(_get(secrets, key)) if need.get("secret") else False
        have = have or _filled(_get(cfg, key))
        if have:
            continue
        (missing_secret if need.get("secret") else missing_open).append(need)

    # A feature whose ONLY requirement is a credential (the AI key) goes straight
    # to the input box, never through "run /setup first". There is nothing to set
    # up: the reader either has a key to paste or does not, and sending them to a
    # conversation to be told "paste your key" buys nothing. `ask` is still
    # returned, so someone without a key still has somewhere to go.
    if missing_open:
        state = NOT_CONFIGURED
    elif missing_secret:
        state = NEEDS_SECRET
    else:
        state = READY

    return {
        "id": feature["id"],
        "name": feature["name"],
        "why": feature["why"],
        "state": state,
        "ask": feature["ask"],
        # Only what is MISSING, and never a value -- the browser learns which box
        # to draw, never what is already in it.
        "missing": [{"key": n["key"], "label": n["label"], "secret": bool(n.get("secret")),
                     "placeholder": n.get("placeholder", ""), "hint": n.get("hint", "")}
                    for n in (missing_open + missing_secret)],
    }


def snapshot(cfg: dict, secrets: dict | None = None) -> list[dict]:
    return [status_of(f, cfg, secrets) for f in FEATURES]


# -- the secrets file -------------------------------------------------------

def load_secrets(path: Path | None = None) -> dict:
    p = Path(path or os.environ.get("AGENT_VIEW_SECRETS") or SECRETS_PATH)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def is_ignored(path: Path) -> bool:
    """Does git ignore this path? A tree that is not a repository at all counts
    as ignored -- there is nothing to stage into."""
    try:
        p = subprocess.run(["git", "check-ignore", "-q", str(path)],
                           cwd=str(path.parent), capture_output=True, timeout=10)
        if p.returncode == 0:
            return True
        rev = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                             cwd=str(path.parent), capture_output=True, text=True, timeout=10)
        return rev.returncode != 0            # not a repo -> nothing to leak into
    except (OSError, subprocess.SubprocessError):
        return False                          # cannot prove it is safe -> refuse


def save_secret(key: str, value: str, path: Path | None = None) -> Path:
    """Store one credential, or refuse.

    The refusal is not a formality. This repository had three live secrets in a
    tracked file and a template that claimed the opposite; every one of them got
    there because writing to the wrong file is one line of code and nothing
    stopped it."""
    p = Path(path or os.environ.get("AGENT_VIEW_SECRETS") or SECRETS_PATH)
    if not is_ignored(p):
        raise SecretRefused(
            f"{p.name} nije u .gitignore. Odbijam da upišem kredencijal u "
            f"praćeni fajl. Dodaj ga u .gitignore pa probaj ponovo.")
    cur = load_secrets(p)
    node = cur
    parts = key.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise SecretRefused(f"{key}: {part} nije objekat")
    node[parts[-1]] = value
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass                                  # best effort; Windows ignores it
    return p
