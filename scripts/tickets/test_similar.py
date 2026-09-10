#!/usr/bin/env python3
"""Proof that similar-ticket retrieval matches on MEANING, not on spelling.

The Serbian-specific half is where the value is and where the silent failure
lives: without diacritic folding and stemming, "šifarnik" and "sifarnik" are
unrelated words and "lokacije" never matches "lokaciju". Nothing fails when
that breaks -- the search just quietly returns fewer neighbours, which reads as
"nothing similar" rather than "I cannot match your language".

  python scripts/tickets/test_similar.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import similar as S                                              # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def main() -> int:
    # --- the file must not contain control characters ---------------------
    # A heredoc patch once turned `\b` into a literal backspace here, giving a
    # regex that reads correctly and matches nothing. Cheap to pin forever.
    src = Path(HERE / "similar.py").read_text(encoding="utf-8")
    ck("source carries no control characters",
       not any(ch in src for ch in ("\x08", "\x0c", "\x1b")))

    # --- folding: the same word, written both ways ------------------------
    ck("fold: sifarnik == šifarnik", S.tokens("šifarnik") == S.tokens("sifarnik"))
    ck("fold: ćirilica-free č/ć/ž", S.tokens("čačak žito ćup") == S.tokens("cacak zito cup"))
    ck("fold: đ becomes dj", S.tokens("đubre")[0].startswith("dj"))

    # --- stemming: one concept, four inflections --------------------------
    forms = ["lokacija", "lokacije", "lokaciju", "lokacijama"]
    stems = {S.tokens(f)[0] for f in forms}
    ck("stem: all four inflections collapse to one", len(stems) == 1)

    # A stem short enough to collapse inflections must still keep DIFFERENT
    # words apart, or everything matches everything.
    ck("stem: different words stay different",
       S.tokens("lokacija")[0] != S.tokens("licenca")[0])

    # --- stopwords and noise ----------------------------------------------
    ck("stop: greeting words are dropped",
       S.tokens("Poštovani, molim vas hvala") == [])
    ck("noise: bare numbers are dropped", S.tokens("12345 2026") == [])
    ck("noise: two-letter words are dropped", S.tokens("na u za") == [])

    # --- the customer key -------------------------------------------------
    ck("person: same human, company suffix and diacritics",
       S.person("Aleksandar Susa Office") == S.person("Aleksandar Šuša"))
    ck("person: doo is corporate noise",
       S.person("Marko Marković doo") == S.person("marko markovic"))
    ck("person: different humans stay different",
       S.person("Ana Anić") != S.person("Petar Petrović"))
    ck("person: empty is handled", S.person(None) == "")

    # --- scoring ----------------------------------------------------------
    corpus = [
        {"toks": S.tokens("lokacija sifarnik filter region"), "id": "a"},
        {"toks": S.tokens("faktura pdv obracun"), "id": "b"},
        {"toks": S.tokens("lokacija mapa koordinate"), "id": "c"},
    ]
    w = S.idf(corpus)
    q = S.tokens("lokacije u šifarniku, treba filter")

    sa, why_a = S.score(q, corpus[0], w, False, False, False)
    sb, _ = S.score(q, corpus[1], w, False, False, False)
    sc, _ = S.score(q, corpus[2], w, False, False, False)
    ck("score: the matching ticket wins", sa > sc > sb)
    ck("score: an unrelated ticket scores zero", sb == 0.0)
    ck("score: it can SAY why it matched", len(why_a) >= 2)

    boosted, _ = S.score(q, corpus[0], w, True, False, False)
    ck("score: same module boosts", boosted > sa)

    # --- IDF actually suppresses the common word --------------------------
    # Every ticket in this corpus mentions the module; a word in all of them
    # must weigh less than one in a single ticket, or the ranking is noise.
    everywhere = [{"toks": S.tokens("lokacija alfa"), "id": "1"},
                  {"toks": S.tokens("lokacija beta"), "id": "2"},
                  {"toks": S.tokens("lokacija gama"), "id": "3"}]
    w2 = S.idf(everywhere)
    ck("idf: a word in every ticket weighs less than a unique one",
       w2[S.tokens("lokacija")[0]] < w2[S.tokens("alfa")[0]])

    # --- an empty query must not return the whole corpus ------------------
    ck("empty query returns nothing, not everything",
       S.find("", None, None) == [])
    ck("a query of only stopwords returns nothing",
       S.find("molim vas hvala", None, None) == [])

    # --- the character budget ---------------------------------------------
    # TOP caps the COUNT of neighbours, and a count is not a size. Measured
    # over this store 2026-09-10: median ticket contributes 0 chars, but the
    # five LARGEST together are 22,505 -- about 5,600 tokens straight into a
    # prompt, with nothing to stop them.
    def row(n: int, chars: int) -> dict:
        return {"score": 1.0 / (n + 1), "resolution": "x" * chars, "report": ""}

    small = [row(i, 100) for i in range(5)]
    ck("under budget, nothing is dropped",
       len(S.within_budget(small)) == 5)

    big = [row(i, 5_000) for i in range(5)]
    kept = S.within_budget(big)
    ck("over budget, the tail is cut", len(kept) < 5)
    ck("and what is kept fits the budget",
       sum(len(r["resolution"]) for r in kept) <= S.BUDGET)
    ck("the cut keeps the HIGHEST scoring rows, not an arbitrary slice",
       kept == big[:len(kept)])

    # A budget that can return nothing answers a question nobody asked.
    ck("one oversized row is still returned",
       len(S.within_budget([row(0, S.BUDGET * 3)])) == 1)

    ck("an empty list stays empty", S.within_budget([]) == [])
    ck("a row missing both fields does not raise",
       len(S.within_budget([{"score": 1.0}])) == 1)

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s)")
        return 1
    print("ok - matches on meaning, explains itself, and stays quiet when it should")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
