# What the customer actually gets

Extends `visual-diff`. Read before sending anything. Two files per run, and what each picture is allowed to say.



## Two files per run

`<shots_root>/<key>/manifest.json` is the whole record: pairs, shots,
signatures, geometry, errors. `<shots_root>/<key>/state.json` is the small one
the V2 PreToolUse gate reads on EVERY edit:

```json
{"work_id": "...", "repo": "...", "ticket": "...",
 "before": {"at": "2026-08-20T10:00:00", "files": ["myapp/templates/myapp/list.html"]},
 "after":  {"at": "...", "files": [...]}}
```

A file counts as covered once a pass actually BASELINED it. A file that mapped
to no page is deliberately *not* covered: the pass exits non-zero with `no_pages`
and its question, and only `--accept-unlocated` — the operator's answer, "no
screen shows this" — records the coverage. A "0 pages, exit 0" run used to
satisfy the gate having captured nothing at all.

## What the customer gets

Per pair: **the BEFORE picture, the AFTER picture, every changed region of the
screen enlarged, and the screen's name** (with its URL on the record). The full
pictures are the captures themselves, unmarked; the crops are written to
`<run>/crops/`.

```json
"before": {"full": "<path>"},
"after":  {"full": "<path>"},
"regions": [{"before": {"crop": "<path>|null"},
             "after":  {"crop": "<path>"},
             "crop_from": "after|before",
             "crop_kind": "div|a.list-group-item|fallback|…"}],
"regions_found": 7,
"decision": "approved|rejected|",
"new_page": true,
"nav": {"label": "…", "path": "/settings/cities/bulk/", "steps": ["…"]}
```

`regions` is ordered top-to-bottom and empty when nothing resolved; `crop_from`
and `crop_kind` are diagnostics the customer never sees — they are how a bad crop
is found without re-running the capture.


### A screen that did not exist before

An `after_only` record whose reason is exactly `"state appeared after the
change"` (`shoot.WHY_STATE_APPEARED`) is a NEW SCREEN, and it is a **pair** — one
picture, no comparison — not a row in `unpaired`. It used to sit in `unpaired`,
which the HUD does not render, so the customer was never told the screen exists.
Every other `after_only` reason (the before capture failed, the baseline has no
such state) stays a diagnostic: those look identical on disk and selling one to a
customer as "new" is a claim we cannot support.

Its comment is generated, and the operator edits the draft before sending:

```
Nova stranica: Grupni unos gradova
Putanja: /settings/cities/bulk/
Do nje: Podešavanja → Gradovi → dugme „Grupni unos“
```

* the **label** is the page inventory's title, never the browser `<title>` — the
  new Gradovi grid reports `Gradovi | Kontrola`, which names the *list* screen;
* the **path** is a path, never the captured URL: that carries the operator's dev
  host (`http://acme.lvh.me:8020/…`) and has no business in a customer's
  comment;
* the **steps** come from the map's own `reached_from_sr` (`pages.nav_steps`),
  and **there is no fallback.** A line that does not read as a click path — prose,
  a redirect, a programmatic call, anything carrying a template path, a
  permission or a query string — yields `[]`, and the comment prints an empty
  `Do nje:` line for the operator to fill in. *A wrong click-path sent to a
  customer is worse than a missing one* (operator, 2026-08-23). Of
  acme-audit's 243 mapped screens, 183 yield a path and 60 are refused.

The parser does exactly two liftings, both from text the mapper wrote: a
parenthetical that itself carries an arrow REPLACES the label it qualifies
(`Lista gradova (Podešavanja → Gradovi, …)` is a screen name plus the menu path
to it, and the menu path is what a customer can follow), and it is cut at its
first comma, where the technical tail starts. Every other parenthetical is
dropped.

### One change photographed once, not once per screen

A page is in the change set because **its own template changed**, or because it
**includes a shared layout** that changed. Those are not the same thing, and
treating them as the same is what produced thirty pairs of one added sidebar
item ("prikazuju se jer je na side bar dodat tab, a ne jer postoji izmena").

A page reached ONLY through a shared template collapses under **one
representative**, which carries `represents: [page_id, …]` and says "+ N drugih
ekrana" in its caption. The others keep their record and their pictures, and are
marked `represented_by` + `drop` with a reason naming the representative — hidden
from the gallery, never deleted, and never silent.

* **A page whose own template changed is never collapsed.** `template changed`
  in its `why` outranks every shared reason, so a screen that really is different
  always gets its own pair — even when it also extends the changed layout.
* **Only templates collapse, not stylesheets.** An include renders the *same
  markup* on every screen; a CSS rule restyles *each screen's own markup*
  differently, so a page reached by `uses .some-class` keeps its pair.
* **The representative is chosen by the data**, never by discovery order: a page
  whose pair survived the structural sweep first, then the shortest url path (a
  segment's entry screen is the one a person recognises), then the page id.

The provenance this rests on is `affected.from_diff`'s `why` list, which already
existed. There is no second heuristic anywhere.

**Keep-vs-drop is structural, and only structural** (the layout-only collapse
above is the one other thing that can hide a pair, and it says so in
`drop_reason`). `compare.sweep_changed`
compares the DOM tree signature and the block geometry of the two shots: a pair
is marked `drop: true` — kept in the manifest, kept out of the gallery — when
and only when that comparison ran and says *same tree, same layout*. Different
numbers in the same layout is **not** a change. The engine has **no pixel
comparison at all** any more: a per-pair `pixel_ratio` made every page whose
counters, chart values or row counts had moved read as "this changed", which is
most pages, because the dev database is the production one. If a signature is
missing on one side the verdict is `comparable: false` and the pair is KEPT —
a page we cannot compare is the one somebody has to look at.

**A decision is saved WHEN IT IS MADE.** Each "odobri" / "odbaci" / caption
POSTs `/api/tickets/shots/decide`, which writes the same three-state `decision`
the send path writes and touches no outbox — deciding is not a promise to send.
It used to be browser-local until the send button, so reviewing a batch and
leaving without sending threw the whole review away (operator, 2026-08-28:
*"kada odaberem odobri/odbaci na svim parovima i ne pošaljem već izađem, obriše
se ono što sam uradio, ne čuva se odabir"*). A save that FAILS says so on the
group's note: a decision that looks saved and is not is the same defect wearing
a different coat. `_shots_apply_decisions` is the one writer both routes call.

**Crops need a diff, and a committed change has none in the working tree.**
`affected.from_diff` reads hunks; after a commit there are no hunks, so the run
comes back `regions: []` and the customer gets two full pictures and no
enlargement — which reads as "the engine stopped detecting". Pass
`--diff-from-commit <rev>` to take the hunks from that commit instead. Reach for
it whenever the pair is (re)shot after the work is committed.

Nothing is sent automatically. Approval is the HUD's 🖼 Snimci gallery (V3).
**Each ticket sends itself**, from a button on its own collapsible group (header
and footer both); there is no global send button, because the only control there
was could ship another customer's pictures — and with the groups collapsed, whose
was not readable.

**THE SEND SENDS. It comments; it does not close, and it does not read or write
the ticket's status.** Pressing "pošalji u komentar tiketa" posts that ticket's
comment — the approved pairs' description lines and their pictures — inside that
one request, and answers with what the helpdesk actually said. An open ticket and
a closed one behave identically: *"moze da bude i zatvoren i otvoren tiket, niti
se menja status"* (operator, 2026-08-23), because the gallery is used to explain a
change before a sync and after one.

It did not work that way until 2026-08-23: approval wrote a draft into the
`outbox` and **nothing reached the customer until somebody later ran `writeback
--close --post-outbox`**. The operator pressed send, the panel went green, no
comment existed, and the run stayed in the gallery for ever. Coupling delivery to
closing is the defect; the draft is now an implementation detail of the send and
not a stage the operator has to know about.

Four rules the send is built on:

* **`writeback.run(..., close_resolution=None, only_drafts=[id])` is the door**
  — never a second poster. That door already carries claim-before-call (a crash
  mid-send never double-posts to a customer), the split of one draft into several
  comments when the pictures do not fit one, resume-not-restart after a failure,
  and the `sent_log` row for every call. `only_drafts` is why the confirmation can
  be honest: the button delivers the draft it just wrote and nothing else the
  ticket happens to have waiting.
* **Three outcomes, reported distinctly: posted / failed / ambiguous.**
  Ambiguous — the request left and the answer never came — is neither, is never
  painted as success, and is never re-sent on its own; the operator goes and
  looks. A failed send keeps the run, the pictures and the decisions, and the
  button retries it.
* **It confirms first**, naming the ticket, the pair count and the picture count
  (`att_n`, computed server-side off the same attachment plan the draft uses — a
  count re-derived in JavaScript would lie the day the policy is flipped). One
  click is an irreversible, outward-facing act.
* **A draft that is already part-delivered is RESUMED, never doubled.** Half a
  set is with the customer; writing a second draft beside it would send those
  pictures again. Decisions changed since then are recorded in the manifest and do
  not enter that comment — they go out on the next send, once the first one is
  complete.

Because a posted comment cannot be edited or withdrawn, the panel will not send a
work that owes the customer nothing (`undelivered: 0` in the listing): pressing
send twice would be two comments, not one corrected draft.

**The review has THREE states and the manifest stores three.** `decision` is
`"approved" | "rejected" | ""` (never reviewed); `approved` stays beside it as
the same fact in the older shape, so nothing that reads only that key changed.
The manifest used to store the boolean alone, so "odbaci" and "never looked at"
were one value on disk: a rejection survived until the next reload and then came
back as unreviewed. A manifest written before `decision` needs no migration —
`approved: true` IS approved and everything else IS unreviewed, which is what the
reader derives.
