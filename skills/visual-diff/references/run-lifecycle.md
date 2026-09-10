# A run's lifecycle — baseline, gallery, discard

Extends `visual-diff/references/cli.md`. Read when a run has finished, is stuck, or must NOT leave the gallery yet.

### The rolling baseline

```
<repo>/.visual-baseline/
  index.json                    one entry per page+state: phash, sha256, when, which run
  shots/<page>__<state>.png     the picture `before` adopts
  shots/<page>__<state>.json    its capture record (DOM signature, geometry, title)
  thumbs/<page>__<state>.webp   ~15 KB, for recognising a screen at a glance
  archive/<stamp>/…             every picture a promotion replaced
```

It is the app's **last approved look**, which is exactly what the next ticket's
"before" is: `baseline` seeds it, `promote` rolls it forward, `before` reads it.
That is what removes the stash / restart / shoot / unstash / restart dance a real
session had to do twice — and it is why a ticket can still have a baseline after
the edit is written.

Three guards keep a post-change capture from becoming a pre-change one:
promotion is explicit, it refuses a failed page, and `before` will not adopt a
baseline **its own run** promoted. A `before` adopted from the baseline is
COPIED into the run's folder, so the next promotion cannot change the picture a
sent ticket refers to.

### A finished run leaves the gallery

The gallery is one ticket's evidence, not an archive of every ticket ever done.
When a run is FINISHED the engine promotes its after shots and removes the run
folder — `shoot.finalize_run(key)`, called by the HUD.

**The order is the whole point and is not negotiable: promote, VERIFY, then
delete.** The run folder holds the only copies, so a delete that runs before a
promotion that failed loses the pictures permanently. `verify_promotion` reads
the baseline back **off disk** and proves three things per page — the index names
it, the file is there, and its bytes are this run's bytes (both hashes). One
refused page, or one that cannot be proven, and nothing is deleted at all; the
reason is reported and the run stays.

**A PROCESSED run leaves — whatever the operator decided.** Everything sent,
some of it sent, or nothing sent at all: *"ne bitno da li sam ga poslao ili nisam
ili delimicno neke komentare i slike jesam neke nisam"* (operator, 2026-08-23). A
refusal is as final a decision as a send, and a ticket whose pairs have all been
answered stops taking up room. So a run is finished when

1. every reviewable pair carries a decision (approved **or** rejected), and
2. nothing of that work is still waiting to be delivered.

**(2) is PROVEN, never inferred.** A send that worked leaves its draft in the
outbox marked `posted`; a send that failed leaves it pending. Both are records.
What is not a record is the ABSENCE of a draft — and `_shots_undelivered`
counts pending drafts, so it answers 0 both when the send landed and when **no
send was ever made**. Reading that 0 as "delivered" removes the pictures while
the customer has nothing: the one outcome with no way back, because the draft
names the PNGs by absolute path inside that folder and the retry would have
nothing to upload.

> Not hypothetical. On 2026-08-28 DEMO#43417 had both pairs decided, no shots
> draft ever written and `undelivered == 0`, so the run was promoted and
> removed — and the ticket carried no comment and no images. Its `sent_log`
> holds `estimate` and `close` and no `comment` at all. Operator: *"poslati
> snimci u komentar tiketa nisu otisli a obrisali su se iz snimci pre/posle
> sekcije"*. The BEFORE shots survived only because the change was already
> committed, so a worktree of the parent commit could be served and reshot with
> `--force`; the baseline held only the AFTER side, which the promotion had just
> written. Had the work been uncommitted, they were gone.

So `shots_finished` requires, for any pair marked **approved**, that a shots
draft for that work EXISTS on the ticket (`_shots_ever_drafted`). An
all-rejected run needs none — rejecting IS the decision not to send. The
gallery says it in the operator's words — *"odobreno, ali slike još nisu
poslate — pritisni „pošalji u komentar tiketa"* — and it is NOT `blocked`,
because the way out is the send button, not the discard route. Pinned by
`agent_view/test_shots_finished.py`.

A run with nothing to review is never "finished" either — otherwise an empty or
unreadable manifest would satisfy the test vacuously and remove its own folder.

The HUD is the only caller, because the outbox is the one thing the engine cannot
see: `POST /api/tickets/shots/finalize` when the gallery opens, and again after
every send. It is a POST and not a GET although the panel calls it on open — it
promotes a baseline and removes a folder, and a write behind a safe method is a
write any page can make the browser perform.

### When it CANNOT leave: `stay` and the discard button

A run that is fully decided and owes the customer nothing can still fail the
promotion — a page that errored on the after pass, a folder with no `shots`
section at all. It then stays, correctly, and used to stay **silently**: the card
looked exactly like an untouched one and no button on screen could clear it
(operator, 2026-08-28: *"kada kliknem da se ne šalje ne obriše se iz galerije"*).
Three pieces fix that, and the first two are what make the third safe:

* the sweep writes its refusal onto the manifest as `finalize_error: {at,
  reason}` — the reason has to outlive the request that discovered it, or the
  next person to open the gallery starts from nothing;
* `GET /api/tickets/shots` ships per work `stay: {finished, blocked, reason}`,
  the reason in Serbian, built from the facts (`open pairs`, `undelivered`) and
  never by translating the internal English back;
* `POST /api/tickets/shots/discard {work_id}` removes ONE run —
  `finalize_run(key, force=True)`, which still promotes and still verifies and
  only stops letting those results veto the delete. It refuses **409** while a
  pair is undecided or anything is still owed to the customer, and the folder
  jail is not relaxed by `force`: that check is about *where*, never about
  certainty. The automatic sweep never passes `force` — there nobody is looking,
  and failing loudly is the property.

**It holds full PNGs, so it is per machine: add `.visual-baseline/` to the app's
`.gitignore`.** Version 1 (a hash and a thumb per page) was small enough to
commit and had nothing a customer could look at; that trade is over. In
acme-audit the entry IS there (`.gitignore:261`) — verified 2026-08-28,
when the first promotion actually wrote into it.

The server must already be running. `shoot.py` **checks** it and **never starts
it** — dev settings talk to the production schema, so which command runs is the
operator's call.
