# Counts, progress and review state

Extends `ui-bootstrap`. Read whenever the screen shows a number about its own rows — "N of M", "K left to review", a progress bar, a completion state — or when a producer decided some rows are not worth showing.

A number the user cannot reconcile with what is on screen is read as a bug in the feature, not as a bug in the count.

**A list the producer TRUNCATED must say so, and must not renumber.** Two
separate rules, and the second is the one that bites:

- Silent truncation reads as "that was all of it". Ship the producer's own
  found-count alongside the delivered list and print "showing N of M".
- **Never drop an unrenderable item out of the middle of a numbered list.** The
  user reads position ("3/4") while the URL, the filename and the export key off
  the ORIGINAL index — drop one and those two numberings part company from that
  item on, so "3/3" on screen sits next to a file called `-4.png`. Emit every
  item, give the unrenderable one a designed empty row, and let the count line
  explain the gap. Check it: build a fixture whose middle item cannot resolve and
  assert the on-screen position equals the exported name's number.

**A list the producer FILTERED cannot be filtered by the client unless the flag
ships.** When a producer marks rows "not worth showing" — collapsed behind a
representative, superseded, already covered elsewhere — and the API strips that
flag on the way out, the screen renders every row as a thing to act on. "The
client already hides it" is not a defence: **the client cannot hide what never
arrives.** Three parts, all checkable:

- **Ship the verdict, not the reasoning.** One boolean (`shown`) plus, if rows
  nest, the id of the row that stands for this one. The rule that decides it
  lives in the producer and is READ — never re-derived in JavaScript, or the two
  drift.
- **Count what is rendered.** Every "N left to review" is computed over the rows
  that are actually cards. Measured: a gallery said "32 nepregledano" above 5
  reviewable ones because the count walked the raw list.
- **Filtered is not deleted.** The filtered rows stay reachable — nested under
  the row that represents them, read-only, with whatever pictures or detail they
  carry — and anything whose representative is itself not shown needs a
  fallback home, or it silently disappears.

**And when the producer already states a number, do not state it again in
another unit.** If the representative's own caption says "+ 22 other screens",
the disclosure that opens them must carry no count: yours would be in rows and
theirs in screens, the two will differ, and the user reads that as a
contradiction. One honest total, once, and say what it counts.

**An indicator must be able to say "I do not know".** Before drawing a progress
bar, name the artefact that *moves while the job runs*. If everything the job
writes is written when it ENDS, there is no progress to read and a bar is a lie
with a denominator on it — report what you can actually count, with no
denominator and no bar, and say the total is unknown. Same rule for a completion
state: silence is evidence of "stopped", never of "still going", so cap it and
render "did not finish" rather than a spinner that never ends.

**A review control with two buttons usually has three states.** approve / reject
is a boolean, and initialising it to `false` makes *not yet decided* render
identically to *decided against* — so the one question the screen exists to
answer, "how far have I got", is unanswerable. Initialise to **absent**, give the
third state its own visual, and put the three counts where they can be seen. The
wire format need not change: undecided can serialise exactly as rejected does.
