#!/usr/bin/env python3
"""Read things outside the repo, and say which source actually answered.

  reach.py doctor                     which channels work RIGHT NOW
  reach.py search <query>             general web search
  reach.py so <query>                 stackoverflow, with score and answered
  reach.py hn <query>                 hacker news discussion
  reach.py gh <owner/repo> [query]    that repo's issues
  reach.py pypi <package>             current version, requires_python, releases
  reach.py rss <feed|name>            recent entries  (names: django, python, ...)
  reach.py get <url>                  a page as readable text

`--json` on any of them for machine-readable output.

**Nothing here needs a key or an account.** That was a design constraint, not a
convenience: a source that needs setting up is a source that is unreachable on
the next machine. `gh auth login` improves GitHub's rate limit and is never
required.

**Every result names the backend that produced it** (`via=jina` vs `via=raw`,
`via=gh` vs `via=api`). A fallback that answers silently is worse than one that
fails: the crude HTML strip drops tables and code, and a documentation question
is usually about exactly those.

**A query is an outbound channel.** `get` sends the URL; `search` sends the
search text. This machine holds real customer names and ticket bodies -- reduce
the question to its technical part before searching. That is also the version
that gets the better answer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import channels as ch                                               # noqa: E402


def out(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def doctor(as_json: bool) -> int:
    rows = []
    for name, (probe, what) in ch.CHANNELS.items():
        try:
            ok, detail = probe()
        except Exception as exc:                                    # noqa: BLE001
            ok, detail = False, f"probe raised: {type(exc).__name__}"
        rows.append({"channel": name, "ok": ok, "what": what, "detail": detail})

    if as_json:
        out(rows, True)
        return 0

    print("=" * 70)
    print("REACH — what can be read right now")
    print("=" * 70)
    for r in rows:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'} {r['channel']:<9} {r['what']:<26} {r['detail']}")
    down = [r["channel"] for r in rows if not r["ok"]]
    print()
    if down:
        print(f"unavailable: {', '.join(down)} — the line above each says why.")
        print("An unavailable channel is a SOURCE YOU CANNOT CHECK, not a reason")
        print("to answer from memory. Say which one was unreachable.")
    else:
        print("All channels reachable.")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__.strip())
        return 1

    cmd, rest = args[0], args[1:]
    try:
        if cmd == "doctor":
            return doctor(as_json)

        if cmd == "get":
            if not rest:
                print("reach.py get <url>")
                return 1
            text, via = ch.web(rest[0])
            if as_json:
                out({"via": via, "url": rest[0], "text": text}, True)
            else:
                print(f"[via={via}]  {rest[0]}\n")
                print(text[:20000])
                if via == "raw":
                    print("\n[!] via=raw — jina was unreachable, so tables and code "
                          "structure are probably lost. Say so if it matters.")
            return 0

        if cmd == "gh":
            if not rest:
                print("reach.py gh <owner/repo> [query]")
                return 1
            items, via = ch.github_issues(rest[0], " ".join(rest[1:]))
            if as_json:
                out({"via": via, "items": items}, True)
            else:
                print(f"[via={via}]  {rest[0]}  {len(items)} issue(s)\n")
                for i in items:
                    kind = i.get("kind", "issue")
                    print(f"  #{i['number']:<7} {kind:<5} {str(i['state']):<6} "
                          f"{str(i['title'])[:64]}")
                    print(f"          {i['url']}")
            return 0

        if cmd == "rss":
            if not rest:
                print(f"reach.py rss <feed|name>   names: {', '.join(sorted(ch.FEEDS))}")
                return 1
            items, via = ch.rss(rest[0])
            if as_json:
                out({"via": via, "items": items}, True)
            else:
                print(f"[via={via}]  {rest[0]}  {len(items)} entr(y/ies)\n")
                for i in items:
                    print(f"  {str(i['date'])[:25]:<26} {str(i['title'])[:60]}")
                    print(f"    {i['link']}")
            return 0

        if cmd == "search":
            if not rest:
                print("reach.py search <query>")
                return 1
            items, via = ch.search(" ".join(rest))
            if as_json:
                out({"via": via, "items": items}, True)
            else:
                print(f"[via={via}]  {len(items)} result(s)\n")
                for i in items:
                    print(f"  {str(i['title'])[:70]}\n    {i['url']}")
                    if i.get("text"):
                        print(f"    {i['text'][:180].replace(chr(10), ' ')}")
            return 0

        if cmd == "so":
            if not rest:
                print("reach.py so <query>")
                return 1
            items, via = ch.stackoverflow(" ".join(rest))
            if as_json:
                out({"via": via, "items": items}, True)
            else:
                print(f"[via={via}]  {len(items)} question(s)\n")
                for i in items:
                    # score and answered first: an unanswered question with two
                    # votes and an accepted answer with four hundred look the
                    # same in a title, and they are not the same evidence.
                    mark = "answered" if i.get("answered") else "OPEN    "
                    print(f"  {mark}  score {str(i.get('score')):>4}  "
                          f"{i.get('answers')} ans   {str(i['title'])[:56]}")
                    print(f"            {i['url']}")
            return 0

        if cmd == "hn":
            if not rest:
                print("reach.py hn <query>")
                return 1
            items, via = ch.hn(" ".join(rest))
            if as_json:
                out({"via": via, "items": items}, True)
            else:
                print(f"[via={via}]  {len(items)} story/ies\n")
                for i in items:
                    print(f"  {str(i.get('date')):<11} {str(i.get('points')):>4}pt "
                          f"{str(i.get('comments')):>4}c  {str(i['title'])[:52]}")
                    print(f"              {i['url']}")
                    if i["url"] != i["discussion"]:
                        print(f"              discussion: {i['discussion']}")
            return 0

        if cmd == "pypi":
            if not rest:
                print("reach.py pypi <package>")
                return 1
            info, via = ch.pypi(rest[0])
            if as_json:
                out({"via": via, "info": info}, True)
            else:
                print(f"[via={via}]  {info['name']} {info['version']}")
                print(f"  {info['summary']}")
                print(f"  requires_python: {info['requires_python']}")
                print(f"  {info['home']}\n")
                for r in info["recent"]:
                    print(f"    {r['date']}  {r['version']}")
            return 0

    except ch.Unavailable as exc:
        # A channel that cannot run is a QUESTION, never a traceback and never
        # a silent empty result -- the same rule the visual engine follows.
        print(f"UNAVAILABLE: {exc}")
        return 3

    print(f"unknown command {cmd!r}")
    print(__doc__.strip())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
