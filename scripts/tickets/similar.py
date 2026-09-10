#!/usr/bin/env python3

"""Which past tickets are like this one, and WHAT WAS DONE about them.



**The gap this fills.** Skills are retrieved by trigger and that works. Episodes

are now recorded. But nothing ever looked back at the QUEUE: a ticket solved

three months ago, on the same screen, for the same customer, with the answer

already written in `triage.resolution` -- and no mechanism surfaces it. The

reading starts from zero every time.



**Deterministic, no embeddings, no model, no key.** The store holds ~50 tickets,

not 50,000. At that size a vector database is machinery for a problem that does

not exist; IDF over the corpus plus crude Serbian stemming finds the same

neighbours, and it can explain WHY each one matched, which an embedding cannot.



**Two decisions that carry most of the quality:**



1. **Diacritics are folded** (`š`->`s`, `ć`/`č`->`c`, `ž`->`z`, `đ`->`dj`).

   The same operator writes "sifarnik" and "šifarnik" in the same week, and the

   helpdesk carries both. Exact matching silently treats them as unrelated

   words.

2. **Tokens are truncated to a stem of 6.** Serbian inflects heavily --

   *lokacija / lokacije / lokaciju / lokacijama* are one concept and four

   strings. Full stemming needs a dictionary; six characters gets most of it

   for nothing.



**Only tickets with an OUTCOME are candidates.** A similar ticket nobody

finished answers nothing -- the point is not "this looks familiar", it is "here

is what we did and what the customer was told".



  similar.py VEZ 70103          neighbours of an existing ticket

  similar.py --text "..."       neighbours of free text (a new ticket, a question)

  similar.py VEZ 70103 --json   machine-readable

"""

from __future__ import annotations



import json

import math

import re

import sys

from collections import Counter

from pathlib import Path



if hasattr(sys.stdout, "reconfigure"):

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")



import ebr_tag  # noqa: E402  (same directory; the tag grammar lives there)



ROOT = Path(__file__).resolve().parent.parent.parent

STORE = ROOT / "tickets_store"

SKIP_FILES = {"modules.json", "ai_log.json", "estimates.csv"}



STEM = 6

TOP = 5

#: A ceiling on the free text that actually reaches a prompt. TOP caps the
#: COUNT, and a count is not a size.
#:
#: Measured over this store, 2026-09-10, on `resolution` + `report`: 57 tickets,
#: median 0 chars, mean 1,401, p90 4,123, max 4,882 -- but the five LARGEST
#: together are **22,505 chars, roughly 5,600 tokens**, and nothing stopped that
#: from landing in one prompt. The median query returns 14 chars, so this bites
#: only in the tail, which is the whole point.
#:
#: 12,000 admits the two or three biggest tickets and cuts the worst case
#: roughly in half. The item cap and the character cap are two halves of one
#: idea, borrowed from TencentDB-Agent-Memory (MIT); the count half already
#: existed as TOP.
BUDGET = 12_000



#: Words that appear in nearly every ticket and therefore separate nothing.
#: IDF already suppresses them; this is belt and braces for a corpus small
#: enough that one unlucky word can still score, and it keeps the "why it
#: matched" line free of words that explain nothing.
#:
#: The minimum word length stays at THREE, deliberately. Raising it to four
#: would drop `pdv`, `sef`, `kif`, `kuf`, `oib` -- domain acronyms that are
#: among the most meaningful tokens in this queue. The cost is that Serbian
#: three-letter function words survive the length filter, so they are named
#: here instead. Caught by the test: "Postovani, molim vas hvala" reduced to
#: ["vas"] rather than to nothing.
STOP = {
    # greetings and filler
    "molim", "hvala", "pozdrav", "postovanje", "postovani", "postovana",
    "treba", "trebalo", "trebam", "moze", "mozete", "moguce", "malo",
    # question and time words
    "kada", "gdje", "gde", "kako", "zasto", "sada", "danas", "juce", "sutra",
    # three-letter function words -- these slip past the length filter
    "vas", "nas", "sam", "sve", "svi", "ovo", "ova", "ovaj", "taj", "tim",
    "kod", "koje", "koji", "koja", "sto", "jer", "ali", "ili", "pri", "bez",
    # true of every ticket, so they separate nothing
    "problem", "greska", "tiket", "korisnik", "korisnici", "sistem",
    "aplikacija", "program", "opcija", "stranica",
    "please", "thanks", "hello", "issue", "system", "user", "the", "and", "for",
}



_FOLD = str.maketrans({

    "š": "s", "Š": "s", "ć": "c", "Ć": "c", "č": "c", "Č": "c",

    "ž": "z", "Ž": "z", "ā": "a", "é": "e",

})





def tokens(text: str) -> list[str]:

    """Fold, lowercase, stem-by-truncation. See the header for why each step."""

    if not text:

        return []

    t = str(text).translate(_FOLD).lower().replace("đ", "dj").replace("Đ", "dj")

    words = re.findall(r"[a-z0-9]{3,}", t)

    out = []

    for w in words:

        if w in STOP or w.isdigit():

            continue

        out.append(w[:STEM])

    return out





def person(name: str) -> str:

    """A comparable key for a customer name.



    The same person reaches the helpdesk as "Aleksandar Susa Office" and

    "Aleksandar Suusa" and "Aleksandar Susa" -- diacritics, a company suffix,

    a different mail client. Exact comparison scores them as three people and

    the customer boost never fires for the one it was written for. Fold, drop

    the corporate noise, and keep the sorted name parts.

    """

    n = str(name or "").translate(_FOLD).lower().replace("dj", "d")

    n = re.sub(r"\b(office|doo|d\.o\.o|ltd|kancelarija|admin)\b", " ", n)
    parts = sorted(w for w in re.findall(r"[a-z]{3,}", n))

    return " ".join(parts[:3])





def ticket_text(t: dict) -> str:

    """Everything about a ticket that carries meaning, weighted by repetition.



    `title` and the analysis are OUR words and are the most reliable; the

    original description is the customer's and is noisier but is the only text a

    brand-new ticket has. Repeating a field is a crude weight and it is enough.

    """

    o = t.get("original") or {}

    a = t.get("analysis") or {}

    parts = [

        str(t.get("title") or "") * 2,

        str(a.get("means") or ""),

        str(a.get("needs") or "") * 2,

        str(o.get("title") or ""),

        ebr_tag.strip(o.get("description") or ""),   # the [ebr ...] tag is ours, not theirs

    ]

    return " ".join(parts)





def page_of(t: dict) -> str:

    """The screen a ticket is about: the reader's url_name when it produced

    one, else the first screen the extension's trace recorded, else ""."""

    r = t.get("reading") if isinstance(t.get("reading"), dict) else {}

    p = str(r.get("page") or "").strip()

    if p:

        return p

    s = ebr_tag.screens(ebr_tag.of_ticket(t))

    return s[0] if s else ""





def outcome(t: dict) -> dict:

    tr = t.get("triage") or {}

    cl = t.get("closed") if isinstance(t.get("closed"), dict) else {}

    return {

        "resolution": (tr.get("resolution") or cl.get("resolution") or "").strip(),

        "report": (tr.get("report") or "").strip(),

    }





def load() -> list[dict]:

    out = []

    for path in sorted(STORE.glob("*.json")):

        if path.name in SKIP_FILES:

            continue

        try:

            data = json.loads(path.read_text(encoding="utf-8"))

        except (OSError, ValueError):

            continue

        for tid, t in (data.get("tickets") or {}).items():

            if not isinstance(t, dict):

                continue

            out.append({

                "module": path.stem, "id": tid, "ticket": t,

                "toks": tokens(ticket_text(t)),

                "category": ((t.get("original") or {}).get("category") or "").strip(),

                "customer": ((t.get("original") or {}).get("customer") or "").strip(),

                "page": page_of(t),

                "out": outcome(t),

            })

    return out





def idf(corpus: list[dict]) -> dict[str, float]:

    n = len(corpus) or 1

    df: Counter = Counter()

    for row in corpus:

        df.update(set(row["toks"]))

    return {w: math.log(1 + n / (1 + c)) for w, c in df.items()}





def score(query: list[str], row: dict, weights: dict[str, float],

          same_module: bool, same_category: bool, same_customer: bool,

          same_page: bool = False) -> tuple[float, list[str]]:

    q, r = set(query), set(row["toks"])

    shared = q & r

    if not shared:

        return 0.0, []

    raw = sum(weights.get(w, 0.0) for w in shared)

    # Normalise by the query's own mass so a long ticket does not win on length.

    total = sum(weights.get(w, 0.0) for w in q) or 1.0

    s = raw / total

    # Context boosts. Same module is the strong one -- the same screen and the

    # same domain vocabulary. Same customer matters because a person repeats

    # both their problems and their phrasing.

    if same_module:

        s *= 1.35

    # Same screen (url_name from the reader or the extension's trace) is the

    # same view and the same code path -- stronger than category, and the one

    # signal a brand-new ticket from the extension already carries.

    if same_page:

        s *= 1.30

    if same_category:

        s *= 1.15

    if same_customer:

        s *= 1.10

    why = sorted(shared, key=lambda w: -weights.get(w, 0.0))[:6]

    return s, why





def within_budget(rows: list[dict], budget: int = BUDGET) -> list[dict]:
    """Cut a ranked list where the text bound for a prompt passes `budget`.

    Only `resolution` and `report` are measured: they are the free text, and
    everything else on a row is short and fixed.

    The highest-scoring row is kept whatever its size. A budget that can return
    nothing is worse than no budget -- the caller asked which past tickets
    resemble this one, and "none, they were too long" answers a question nobody
    asked.
    """
    out: list[dict] = []
    used = 0
    for row in rows:
        size = (len(str(row.get("resolution") or ""))
                + len(str(row.get("report") or "")))
        if out and used + size > budget:
            break
        out.append(row)
        used += size
    return out


def find(query_text: str, module: str | None, exclude: str | None,

         customer: str | None = None, category: str | None = None,

         top: int = TOP, page: str | None = None) -> list[dict]:

    corpus = load()

    weights = idf(corpus)

    q = tokens(query_text)

    if not q:

        return []



    scored = []

    for row in corpus:

        if exclude and row["id"] == exclude and row["module"] == module:

            continue

        # A ticket nobody finished cannot tell you what was done.

        if not (row["out"]["resolution"] or row["out"]["report"]):

            continue

        s, why = score(q, row, weights,

                       same_module=bool(module) and row["module"] == module,

                       same_category=bool(category) and row["category"] == category,

                       same_customer=bool(customer)

                       and person(row["customer"]) == person(customer),

                       same_page=bool(page) and bool(row["page"])

                       and row["page"].lower() == str(page).lower())

        if s > 0:

            scored.append({"module": row["module"], "id": row["id"],

                           "title": row["ticket"].get("title"),

                           "score": round(s, 3), "why": why,

                           "customer": row["customer"], "category": row["category"],

                           "page": row["page"],

                           "resolution": row["out"]["resolution"],

                           "report": row["out"]["report"]})

    scored.sort(key=lambda r: -r["score"])

    return within_budget(scored[:top])





def main() -> int:

    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    as_json = "--json" in sys.argv



    if not STORE.is_dir():

        print("no tickets_store/")

        return 1



    module = exclude = customer = category = None

    if "--text" in sys.argv:

        i = sys.argv.index("--text")

        query = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""

        if not query:

            print('similar.py --text "..."')

            return 1

        label = f'"{query[:50]}"'

    elif len(args) == 2:

        module, exclude = args[0].upper(), args[1]

        path = STORE / f"{module}.json"

        try:

            t = (json.loads(path.read_text(encoding="utf-8")).get("tickets") or {})[exclude]

        except (OSError, ValueError, KeyError):

            print(f"{module}#{exclude} not found in the store")

            return 1

        query = ticket_text(t)

        o = t.get("original") or {}

        customer, category = (o.get("customer") or "").strip(), (o.get("category") or "").strip()

        label = f"{module}#{exclude}  {str(t.get('title'))[:44]}"

    else:

        print(__doc__.strip().splitlines()[-3])

        print('  similar.py <MODULE> <id>   |   similar.py --text "..."')

        return 1



    hits = find(query, module, exclude, customer, category)



    if as_json:

        print(json.dumps({"query": label, "hits": hits}, ensure_ascii=False, indent=2))

        return 0



    print("=" * 74)

    print(f"SLICNI TIKETI  <-  {label}")

    print("=" * 74)

    if not hits:

        print("\n  Nema poklapanja sa zavrsenim tiketom.")

        print("  To je NALAZ, ne greska: ovo je nov problem, ili je slican vec")

        print("  resen tiket zatvoren bez rezolucije i izvestaja.")

        return 0



    for h in hits:

        print(f"\n  {h['score']:.2f}  {h['module']}#{h['id']}  {str(h['title'])[:46]}")

        print(f"        poklapanje: {', '.join(h['why'])}")

        if h["customer"]:

            print(f"        kupac: {h['customer']}"

                  + (f"   kategorija: {h['category']}" if h["category"] else ""))

        if h["resolution"]:

            print(f"        KUPCU:  {h['resolution'][:150].replace(chr(10), ' ')}")

        if h["report"]:

            print(f"        NAMA:   {h['report'][:150].replace(chr(10), ' ')}")



    print("\n  Skor je slicnost teksta, ne dokaz. Otvori tiket pre nego sto")

    print("  ponovis njegovo resenje -- isti ekran ne znaci isti uzrok.")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

