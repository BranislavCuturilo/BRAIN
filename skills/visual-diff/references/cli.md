# The CLI — one door

Extends `visual-diff`. Read when driving a run by hand. One door: every capture goes through it, so there is one place where the rules are enforced.

## The CLI — one door

```
python scripts/visual/shoot.py before   --repo <path> --ticket DEMO#08597 [--files a b] [--work-id W]
python scripts/visual/shoot.py after    --repo <path> --ticket DEMO#08597 [--files a b] [--work-id W]
python scripts/visual/shoot.py after    --repo <path> --ticket ... --no-baseline --baseline-reason "..."
python scripts/visual/shoot.py promote  --repo <path> [--ticket DEMO#08597 | --work-id W] [--pairs id ...]
python scripts/visual/shoot.py sweep    --repo <path> [--limit N]
python scripts/visual/shoot.py baseline --repo <path> [--limit N]
```

**No ticket, no pictures.** `before` and `after` REFUSE (`no_ticket`, exit 9,
with the usual question) unless a ticket is named — on the command line, or in
the run's own `state.json` from an earlier pass that named one. The pair exists
to be sent to a customer on a helpdesk ticket; a run that cannot say which one
produces images nobody may send and the HUD then has to hide. The refusal
happens before the config is read and before the server is touched, so nothing
is written. A work id is NOT a fallback: one work can cover several tickets, so
deriving one would be a guess about whose customer receives the pictures.
`sweep` and `baseline` are repo-wide and need no ticket.

**And the ticket must NAME one — `<MODULE>#<id>`, never a bare number.** A
reference is `DEMO#43417` or `VEZ43417`; `43417` alone carries no module, and
the gallery files works by `(module, id)`. Enforced at capture time by the same
rule the gallery filters with (`scripts/brain/ticket_ref.py`, loaded by BOTH
ends) — because for a long time it was not:

> `--ticket 43417` was accepted, captured every page correctly, printed
> `2 pair(s), 2 shown` — and `shots_list` then dropped the work on
> `if not tkey: continue`, so it stayed invisible in the gallery for ever, with
> no diagnostic at either end. It happened more than once before being
> root-caused (DEMO#43417, 2026-08-28). The old `no_ticket` guard fired only
> when the FLAG WAS ABSENT, never when its value named no ticket: the producer
> required a non-empty string while the consumer required a parsable one —
> one rule in two copies, and they had drifted.

The refusal names the modules mapped to `--repo` when the store knows them, and
falls back to a `<MODUL>#<id>` placeholder when several match, since a
confidently wrong module is worse than one the operator fills in. Pinned by
`scripts/brain/test_ticket_ref.py`, which asserts both ends agree on every
reference. **A run already captured under a bad reference is repaired by
rewriting its `ticket` field in `manifest.json` and `state.json`** — the PNGs
were always fine.

The run FOLDER is named after the `--work-id` when there is one, else the
ticket. The gate looks a run up by work id and nothing else, so a run that names
both must not move out from under it.

* **`before`** — the affected pages as they are *before this change*. It
  **prefers the stored baseline**: when `<repo>/.visual-baseline/` has the page,
  that picture IS the before and nothing is captured live, so the edit may
  already be written and the server may already serve it. Only a page the
  baseline does not have is captured live, and only then does it matter that you
  run it before the first edit — the console says which pages those were. Run it
  again for another file and it **adds** pages; it never re-captures one it
  already has, because the value of the "before" shot is that it predates the
  edit. `--force` re-captures live and skips the baseline; you almost never want
  it.
* **`promote`** — the AFTER shots of a run become the repo's new baseline, and
  the pictures they replace move to `.visual-baseline/archive/<stamp>/` (kept,
  never deleted). Explicit and idempotent: running it twice promotes nothing the
  second time and archives nothing. It refuses any page whose after shot errored,
  answered HTTP 0/4xx/5xx or has no file on disk — whatever is promoted is what
  the *next* ticket shows a customer as "before". `promote` never runs by itself:
  `after` does not call it, because "this look is now the truth" is a decision
  about reviewed work. The HUD reaches it through `shoot.finalize_run` (below);
  the CLI subcommand is the same function. **Until 2026-08-23 this file claimed
  the HUD called it after approval and nothing in the repo did** — the promotion
  path had no production caller at all, which is why a ticket's pictures never
  became the next ticket's "before". Grep for the caller before believing a
  document that says something is wired.

  **A REJECTED pair is promoted like an approved one.** Rejecting means "do not
  send this to the customer" — a decision about the *message*, never a claim that
  the screenshot is wrong. The picture is still the app's true current state and
  therefore the correct reference for the next comparison (operator, 2026-08-23).
* **`after`** — at commit time. Captures the same pages, compares structurally,
  and fills `manifest.json` with the customer pairs.
* **`after --no-baseline`** — the *only* supported way to produce something when
  there is no "before" and there cannot be one: the data migration has already
  run, or the engine itself was broken when the baseline was due. It captures the
  pages as they are now and marks them — `manifest["baseline"] = {"state":
  "none", "reason": …}` and `baseline: "none"` on every record — so the gallery
  and the customer's comment both say *no picture of the previous state exists*
  instead of showing an empty half that reads as "nothing changed". It writes
  nothing into `shots.before` and nothing into `state.json`'s `before` side.
  **Never answer a missing baseline by running `before` after the edit**: that
  records a post-change screen as the pre-change one, and nobody downstream can
  tell. The reason is the operator's, so ask for it rather than inventing one.
* **`sweep`** — regression warning for **me**, never for the customer. Compares
  DOM structure + block geometry against the previous sweep of the same repo.
  No screenshots at all, so it is navigation-time only.
* **`baseline`** — SEEDS `<repo>/.visual-baseline/` over the whole inventory
  (base state only), so the first ticket to touch any screen already has a
  before. Same store and same writer as `promote`.
