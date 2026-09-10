---
name: release
description: >
  Prepare a public, forkable release of the brain — find and strip everything
  private, replace real names with placeholders, and produce a version anyone can
  clone and build on. Run before open-sourcing or before any push to a public
  remote.
disable-model-invocation: true
---

# Release — making it public and forkable

This repository grew inside real client work. **Assume it contains things that
must not be published until you have proven otherwise**, and treat the audit as
the deliverable rather than the packaging.

Run this before the first public push and before every release after it.

## 1. Find what is private — do not trust memory

Search the whole tree, including `journal/`, `registry.json`, `docs/` and every
commit message. Look for:

| Category | What it looks like here |
|---|---|
| **Credentials** | tokens, `ehd_…`-style keys, passwords, connection strings — **in git history too**, not only the working tree |
| **Client and company names** | the client, their sites, their people, their internal project names |
| **Hosts and URLs** | internal domains, helpdesk and ticket URLs, IPs, VPS paths |
| **Machine paths** | `C:/Users/<name>/…`, a home directory, a user profile |
| **Business specifics** | a defect described with enough domain detail to identify the client or their process |
| **Named individuals** | anyone who wrote a ticket, made a decision, or appears in the journal |

```bash
git -C ~/.claude/skills/brain log -p | grep -inE "token|secret|passw|\.acme\.|C:/Users/"
```

**If a credential was ever committed, rotating it is mandatory** — public history
cannot be un-published, and stripping it from the working tree does nothing.
Removing it from history means rewriting it, which for a public release is
acceptable *only before anyone has cloned it*.

## 2. Decide per finding — three outcomes

- **Generalise.** A rule earned from a real defect keeps its *lesson* and loses
  its *coordinates*: "a by-id endpoint authorized by a bare scope filter" is the
  contribution; the client's model name is not. **This is the default and it
  usually improves the rule** — a rule that names one codebase reads as
  inapplicable to anyone else's.
- **Replace with a placeholder.** Where a concrete example carries the point,
  substitute a neutral one and say it is illustrative: `acme-helpdesk`,
  `example.com`, `<repo>`.
- **Remove.** The journal, the score registry, project-specific integration
  references, and anything whose value is entirely internal.

**Never publish the journal.** It records decisions, motives and people.

## 3. What a fork actually needs

The point is that someone can clone it and have it be *theirs*, not yours:

- **Every path a variable.** No home directory, no user profile, no drive letter.
- **`ops-integrations/templates/`** carries variable *names* and nothing else.
- **`registry.json` reset** to zeros — your scores are about your work.
- **`journal/` empty**, with its README explaining what goes there.
- **A worked example** for one stack, marked as an example, so the layout is
  legible without being prescriptive.
- **A LICENSE.** Without one, nobody may legally reuse it, which defeats the
  whole exercise.
- **`README` and `docs/SETUP.md` written for a stranger** — no assumed context,
  no reference to a project only you have.
- **`CONTRIBUTING.md`**: the routing law, the size budget, and the rule that
  every rule must trace to something that actually broke. That last one is what
  keeps a forked brain from filling with generic advice.

## 4. Say what is unproven

Whatever the state of validation is, put it in the README **in the first
screen**. If the methodology has not been tested in practice, say that plainly —
publishing it as established would be the same failure the system exists to
prevent, made in public.

Keep `docs/TESTING.md` and `docs/TEST-PROMPT.md` in the release: they let a
stranger evaluate it rather than take your word.

## 5. Verify before pushing

```bash
python scripts/brain/health.py            # clean
python scripts/brain/docs.py --check      # generated docs current
claude plugin validate . --strict         # loads with no warnings
git log -p | grep -inE "token|secret|passw|\.acme\.|C:/Users/"   # empty
```

Then **read the diff of the whole release**, not just the files you touched. A
private detail survives because nobody re-read the file it was in.

Clone the release into a scratch directory and start Claude Code against it. **If
it does not load for a stranger, it does not load.**

## 6. Version and record

Bump `version` in `.claude-plugin/plugin.json`. Tag it. Write a short release
note saying what changed, what was deleted and why, and what is still unproven.

Record in the journal — the private one — what was stripped, so the next release
does not re-discover it.
