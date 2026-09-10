# When the page is a presentation, not a screen

Extends `ui-bootstrap`. Read when the job is a **landing, marketing or
presentation page** — for a client, or for our own software — rather than a
business screen.

Everything else in this skill assumes the opposite job: a table with filters, a
form with permissions, a document that has to balance. Those rules exist because
a business screen is judged on whether the number is right and the button is
allowed. A presentation page is judged on whether someone keeps scrolling, and
almost none of the rules transfer.

**What still applies:** tokens instead of hex literals, the card body wrapper,
"never trust CSS you have not seen render". Those are about not lying to
yourself, and that does not change with the audience.

**What does not:** the empty state, the "N of M" count, the sticky toolbar, the
by-pk authorization every other page needs. A landing page has one visitor role
and no rows.

## Why this file exists at all

It was going to be a rejection. The three tools below were evaluated on
2026-09-10 and turned down because *"we have no task that needs this"* — and the
owner pointed out that this brain's own law says the opposite:

> **Never delete on silence alone.** A skill nobody triggered may be waiting for
> a situation that has not come up; that is an argument for fixing its
> description, not for removing it. Retire on a recorded loss, not on absence.
> — `/brain:ops-prune`

Rejecting a tool because the job has not arrived is the same error, so the
finding became this file instead: not adopted, not discarded, **placed where the
job will look.**

## What to reach for

| Tool | Licence | What it actually does | When it is the right answer |
|---|---|---|---|
| [`img2threejs`](https://github.com/img2threejs/img2threejs) | Apache-2.0 | A reference image → a **procedural Three.js model in code**, not a mesh file | The client wants their product turnable on the page, and you have photographs rather than a CAD model |
| [`threeui`](https://github.com/MengTo/threeui) | MIT — but assets and fonts carry **separate** licences, check before shipping | A catalogue of ready 3D/shader components | You need one hero effect and not a scene. React 19 with `three` as a peer dependency, so it costs a build step we otherwise do not have |
| [`archify`](https://github.com/tt-a1i/archify) | MIT | An agent writes typed JSON IR; archify compiles it **deterministically** into one self-contained interactive HTML (plus PNG/SVG/WebM and a 1200×630 share card) | You need a system map someone will open and click through — on a client page, in a proposal, or in front of a room. `npx skills add tt-a1i/archify -g`; no repository required, a description in chat is enough |

### archify — the part that is ours, and the part that is not

Two things in it match how this brain already works, which is why it is here
rather than in a bookmark:

- **The agent authors, the renderer decides.** The model produces typed JSON;
  compilation is deterministic and re-runs identically. That is the same split
  as `scripts/brain/` — deterministic tools measure, agents judge.
- **Before / Delta / After between two validated snapshots**, with the added,
  removed, changed, moved and rerouted nodes named. For a codebase the size of
  `acme-audit` (592 view functions, 685 URL patterns, measured 2026-09-10),
  that is the only cheap way to see what a refactor actually moved.

**What its checks do NOT cover, and this matters.** The receipts validate the
IR — that the JSON is well-formed, that every edge connects declared nodes,
that a traced route exists in what was authored. They say nothing about whether
the authored topology matches the running system. A confidently rendered
diagram of an architecture the agent guessed at is *more* persuasive than prose
that guessed, not less. Diagram from files you read; do not diagram from
memory of a codebase.

Two further costs, both worth knowing before the first install: it publishes as
a **development version** (`v2.17.0-dev.1` on 2026-09-10), and `-g` installs it
globally rather than per project.

## What was turned down, and why — so it is not re-evaluated

[`scroll-world`](https://github.com/oso95/scroll-world) (MIT) reads as the
obvious fit and is not. **It is not Three.js at all** — measured, not assumed:
its own skill mentions `video` 40 times, `Higgsfield` 24, `Monid` 23, and
`three.js` **zero** times. It generates AI stills, turns them into pre-rendered
video, and scrubs the video's timeline on scroll.

Two consequences. It cannot receive a model from `img2threejs`, so the three do
not chain the way they appear to. And it bills: roughly **$27** per six-scene
chain in Monid, plus Higgsfield credits, plus ffmpeg.

If a scroll-scrubbed video really is what the client asked for, install it as
the Claude Code plugin it already is — it ships `.claude-plugin/plugin.json` and
a `skills/` tree of its own. Do not copy pieces of it in here.

## The one idea worth stealing without the 3D

`threeui`'s **catalogue shape**: component → variants → live controls → a source
tab. That is what `ui-bootstrap`'s own references lack — they describe a list, a
form and a dashboard in prose, and a reader has to build the thing to see it.
Borrowed, not earned: nothing has yet been measured to say a catalogue would
have prevented a defect here, so it is a candidate, not a rule.
