#!/usr/bin/env python3
"""The app's gettext catalogue: what a `{% trans %}` string actually RENDERS as.

Every project this brain serves is translated, and that quietly broke the whole
idea of anchoring on a string. A hunk that adds

    <div class="hr-info-row__label">{% trans "Postal code" %}</div>

yields the anchor `Postal code` - the MSGID. The page renders `Postanski broj`,
so the anchor can never match, the only anchors left are the classes on it
(`hr-info-row`, which every other row carries too), and the engine ends up
pointing at six untouched rows. It is also why material-icon ligatures kept
getting boxed: `location_city` and `precision_manufacturing` are the only "text"
in a translated template that gettext does not touch, so they were the only text
anchors that ever resolved. "Ikonice i nebuloze", exactly.

So an anchor carries its TRANSLATIONS and the matcher tries all of them.

Read through `gettext.GNUTranslations` (stdlib, public API) from the two places
Django puts catalogues: `<repo>/locale/<lang>/LC_MESSAGES/` and
`<repo>/<app>/locale/<lang>/LC_MESSAGES/`. A `.po` NEWER than its `.mo` is read
as well and wins - that is not a corner case, it is the normal state of a repo
where somebody translated something today and has not run `compilemessages`
(acme-audit was in exactly that state when this was written). A missing or
unreadable catalogue is not an error: the anchor is then just the msgid, which is
what it was before this module existed.
"""
from __future__ import annotations

import gettext
import re
from pathlib import Path

#: Where Django keeps them. Two explicit globs rather than a walk: a `**` over a
#: real repo means walking `venv/` and `node_modules/`.
CATALOGUE_GLOBS = ("locale/*/LC_MESSAGES/django.*o",
                   "*/locale/*/LC_MESSAGES/django.*o")

_CACHE = {}

_PO_ENTRY = re.compile(r'^(msgid|msgstr)\s+"(.*)"\s*$')
_PO_CONT = re.compile(r'^"(.*)"\s*$')


def _read_mo(path) -> dict:
    try:
        with open(path, "rb") as fh:
            trans = gettext.GNUTranslations(fh)
    except (OSError, ValueError):
        return {}
    # `_catalog` is the only way to enumerate; every lookup below goes through
    # the public `gettext()`, so a future change of that attribute costs us the
    # KEYS, not correctness.
    out = {}
    for key in getattr(trans, "_catalog", {}):
        if isinstance(key, str) and key:
            value = trans.gettext(key)
            if value != key:
                out[key] = value
    return out


def _read_po(path) -> dict:
    """msgid/msgstr pairs, including the multi-line continuation form. Fuzzy and
    plural entries are skipped rather than guessed at."""
    out, msgid, msgstr, field = {}, "", "", ""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for raw in lines + [""]:
        line = raw.strip()
        m = _PO_ENTRY.match(line)
        if m:
            if m.group(1) == "msgid":
                if msgid and msgstr:
                    out[msgid] = msgstr
                msgid, msgstr = m.group(2), ""
            else:
                msgstr = m.group(2)
            field = m.group(1)
            continue
        c = _PO_CONT.match(line)
        if c and field:
            if field == "msgid":
                msgid += c.group(1)
            else:
                msgstr += c.group(1)
            continue
        if not line and msgid and msgstr:
            out[msgid] = msgstr
        if not line:
            msgid, msgstr, field = "", "", ""
    return {k: v for k, v in out.items() if k and v}


def catalogues(repo) -> list:
    """One `{msgid: msgstr}` per language found in the repo, cached per run."""
    repo = Path(repo)
    key = str(repo.resolve())
    if key in _CACHE:
        return _CACHE[key]
    by_dir = {}
    for pattern in CATALOGUE_GLOBS:
        for path in repo.glob(pattern):
            by_dir.setdefault(path.parent, {})[path.suffix.lower()] = path
    out = []
    for _, files in sorted(by_dir.items()):
        mo, po = files.get(".mo"), files.get(".po")
        entries = _read_mo(mo) if mo else {}
        # The .po wins when it is newer: a string translated today and not
        # compiled yet is exactly the string somebody is editing templates for.
        if po and (not mo or po.stat().st_mtime > mo.stat().st_mtime):
            entries.update(_read_po(po))
        if entries:
            out.append(entries)
    _CACHE[key] = out
    return out


def translations_of(repo, msgid) -> list:
    """Every rendered form of `msgid`, over every language in the repo.

    Order is stable (catalogue order, then first seen) and the msgid itself is
    never in the list - the caller already has it and tries it first.
    """
    text = str(msgid or "").strip()
    if not text:
        return []
    out = []
    for entries in catalogues(repo):
        value = entries.get(text)
        if value and value != text and value not in out:
            out.append(value)
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="What does this msgid render as?")
    ap.add_argument("--repo", required=True)
    ap.add_argument("msgid", nargs="+")
    a = ap.parse_args()
    for text in a.msgid:
        print("%s -> %s" % (text, translations_of(a.repo, text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
