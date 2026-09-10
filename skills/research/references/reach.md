# Reach — the sources beyond WebSearch, and which one answered

Extends `research`. Read when the question needs a source that keyword search
over web pages does not reach.

```bash
python ~/.claude/skills/brain/scripts/reach/reach.py doctor
```

**Nothing here needs a key or an account.** That was the constraint, not a
convenience: a source that has to be set up is a source that is unreachable on
the next machine, and on the machine where nobody remembered. `gh auth login`
improves GitHub's rate limit and is never required.

| The question | Command |
|---|---|
| "has someone already answered this" | `reach.py so "django select_related slow"` |
| "has someone already hit this bug" | `reach.py gh django/django "queryset filter"` |
| "which version fixed it / can we upgrade" | `reach.py pypi django` |
| "did anything ship / is there an advisory" | `reach.py rss django-security` |
| "what is being said about this" | `reach.py hn "django orm"` |
| general web search | `reach.py search "..."` |
| a docs page that renders as a loading spinner | `reach.py get <url>` |

## Check `doctor` before concluding you cannot find out

An unreachable channel is **a source you did not check**, not a reason to answer
from memory. `doctor` says which are live and why each dead one is dead. Naming
the gap is the honest answer; a confident guess with a silent gap behind it is
the failure `research` exists to prevent.

`search` (Marginalia) is a small independent index and **is occasionally down
for a minute** — that is a transient, not a bug. Re-run it before reporting it.

## Every result names the backend that produced it

Read `via` before quoting anything.

- `via=jina` ran the page; **`via=raw` is a crude tag-strip that drops tables
  and code structure** — usually the exact content a documentation question is
  about.
- `via=gh` sees the repo's real issue list and private repositories;
  `via=api (...)` is the fallback and carries the reason. Measured:
  `django/django` disables issues entirely (they use Trac), so the fallback
  returns pull requests, which read as bug reports until you notice `kind: pr`.
- `so` carries `score` and `answered` deliberately. An unanswered question with
  two votes and an accepted answer with four hundred look identical in a title
  and are not the same evidence.

## What was refused, and why — so nobody re-adds it

Probed 2026-08-31 with a real technical query, then dropped:

| Source | What actually happened |
|---|---|
| **DuckDuckGo (html)** | `202` with an "anomaly/challenge" page and **zero results** |
| **DuckDuckGo (instant answer)** | 0-character abstract, 0 related topics |
| **Reddit** | `403 Blocked`, plain and browser user-agents alike |
| **searx.be** | returns HTML when asked for JSON |
| **lobste.rs** | `400 Bad Request` |
| **Exa** | works, but paid — a channel whose key will never be set is a permanently dead row in `doctor` |

DuckDuckGo is the instructive one: it answers `202` with a plausible page, so a
scraper that trusts the status code returns an empty list and the caller reads
**"nothing found"** instead of **"I was blocked"**. Those are different answers
and only one of them is true.

So: Hacker News is the technical forum that is still readable without an
account. It is not a stylistic preference — Reddit and X are simply closed to a
keyless client now.

## A query is an OUTBOUND channel

`get` sends the URL. `search`, `so` and `hn` send the search text, to a third
party.

**Never put ticket text, a customer name, a module name that identifies a
client, or anything out of `tickets_store/` into a search.** Reduce the question
to its technical part first — that is also the version that gets a better
answer, because the customer's wording is not what the page that knows uses.

## Adding a channel

One function to probe it, one to fetch, both returning `(result, via)`, and a
row in `CHANNELS`. Two rules that are not optional:

- **A channel that cannot run raises `Unavailable` with the fix**, never an
  empty list. An empty list reads as "nothing found", which is a different and
  much more expensive answer than "I could not look".
- **A new transport function must be added to `TRANSPORTS` in the test.**
  `_json` was written after `_get` and bypassed the network guard entirely;
  every keyless channel would have hit the real network from the test suite.
  A guard listing only what existed when it was written is not a guard.
