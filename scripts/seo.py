#!/usr/bin/env python3
"""What a marketing page is missing — only the parts that are CHECKABLE.

**The split this tool exists to make.** SEO advice is half specification and
half folklore, and the two look identical in a blog post. Everything here is
the first half: a tag either exists or it does not, a JSON-LD block either
parses or it does not, an `og:image` is either an absolute URL or it is a
relative path that every sharing preview will fail to load.

**What is deliberately NOT here**, because it cannot be measured and this brain
does not carry rules it cannot check: keyword density, "E-E-A-T signals",
"AI citation readiness", how long the copy should be, or what the copy should
say. Those are opinions with a vocabulary. They may even be right. They are not
findings.

The other half of the job is `chrome-devtools` MCP: Lighthouse measures Core
Web Vitals on the RENDERED page, which no static reader can do.

  seo.py index.html                a single file
  seo.py <dir>                     every .html in it, plus robots/sitemap
  seo.py https://example.com       fetched through scripts/reach
  seo.py index.html --save         record today's state as the baseline
  seo.py index.html --client       the report the CUSTOMER reads, in Serbian
  seo.py index.html --json

**Two reports, two readers -- the same split the ticket store already makes
between `triage.resolution` and `triage.report`.** The default output is for
you: every check, including the ones that pass. `--client` is what a paying
customer reads, and it says only what CHANGED and what it means for them. A
customer handed the full list reads twenty "ok" lines as padding on an invoice.

`--save` writes `.seo-baseline.json` beside the page. The next run then shows
the delta, which is what makes the work billable: "these four things were
missing, these four are now present" is evidence. "I did SEO" is not.
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Google truncates the title around 580px and the description around 920px.
#: Characters are a proxy for pixels and the ranges are conventions, not law --
#: reported as a note, never as a failure.
TITLE_OK = (30, 60)
DESC_OK = (110, 165)

MISS, WARN, OK = "MISSING", "note", "ok"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.links: list[dict] = []
        self.headings: list[tuple[int, str]] = []
        self._in_h = 0
        self.images: list[dict] = []
        self.jsonld: list[str] = []
        self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            self.links.append(a)
        elif tag == "img":
            self.images.append(a)
        elif re.fullmatch(r"h[1-6]", tag):
            self._in_h = int(tag[1])
            self.headings.append((self._in_h, ""))
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self.jsonld.append("")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif re.fullmatch(r"h[1-6]", tag):
            self._in_h = 0
        elif tag == "script":
            self._in_ld = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_h and self.headings:
            lvl, txt = self.headings[-1]
            self.headings[-1] = (lvl, txt + data)
        elif self._in_ld and self.jsonld:
            self.jsonld[-1] += data


def check(html: str, name: str) -> list[dict]:
    p = Page()
    try:
        p.feed(html)
    except Exception:                                           # noqa: BLE001
        pass
    out: list[dict] = []

    def add(state, what, detail, why=""):
        out.append({"state": state, "check": what, "detail": detail, "why": why})

    # --- identity ---------------------------------------------------------
    title = " ".join(p.title.split())
    if not title:
        add(MISS, "title", "no <title>",
            "It is the clickable line in every result and every browser tab.")
    else:
        n = len(title)
        add(OK if TITLE_OK[0] <= n <= TITLE_OK[1] else WARN, "title",
            f"{n} chars: {title[:64]}",
            "" if TITLE_OK[0] <= n <= TITLE_OK[1]
            else f"outside the usual {TITLE_OK[0]}-{TITLE_OK[1]}; it will be cut or look thin")

    desc = p.meta.get("description", "").strip()
    if not desc:
        add(MISS, "meta description", "none",
            "The search result then quotes whatever text it finds first.")
    else:
        n = len(desc)
        add(OK if DESC_OK[0] <= n <= DESC_OK[1] else WARN, "meta description",
            f"{n} chars", "" if DESC_OK[0] <= n <= DESC_OK[1] else "likely truncated or thin")

    add(OK if p.lang else MISS, "html lang", p.lang or "not set",
        "" if p.lang else "Screen readers and translation both key off it.")

    add(OK if "viewport" in p.meta else MISS, "viewport",
        p.meta.get("viewport", "none"),
        "" if "viewport" in p.meta else "Without it a phone renders the desktop layout scaled down.")

    canonical = [l for l in p.links if l.get("rel", "").lower() == "canonical"]
    add(OK if canonical else MISS, "canonical",
        canonical[0].get("href", "") if canonical else "none",
        "" if canonical else
        "The same page reachable at www/non-www or with tracking parameters "
        "counts as several pages, each with a fraction of the standing.")

    # --- sharing ----------------------------------------------------------
    # For a SALES page this is the one that costs money immediately: without
    # it, every link pasted into WhatsApp, Viber or LinkedIn renders as a bare
    # grey card and nobody clicks it.
    og = {k: v for k, v in p.meta.items() if k.startswith("og:")}
    need = ["og:title", "og:description", "og:image", "og:url", "og:type"]
    lack = [k for k in need if k not in og]
    if not og:
        add(MISS, "Open Graph", "no og: tags at all",
            "Every share of this link shows a blank card. On a sales page that "
            "is the cheapest fix with the largest visible effect.")
    elif lack:
        add(WARN, "Open Graph", f"missing {', '.join(lack)}", "")
    else:
        add(OK, "Open Graph", "complete")

    img = og.get("og:image", "")
    if img and not urlparse(img).scheme:
        add(MISS, "og:image absolute", img,
            "A relative og:image is fetched by a crawler that has no page "
            "context. It must be a full https:// URL.")

    add(OK if "twitter:card" in p.meta else WARN, "twitter:card",
        p.meta.get("twitter:card", "none"),
        "" if "twitter:card" in p.meta else "X/Twitter falls back to a small card or none.")

    # --- structured data --------------------------------------------------
    if not p.jsonld:
        add(MISS, "JSON-LD", "none",
            "schema.org is how a machine learns what this page IS -- an "
            "Organization, a Service, a LocalBusiness -- rather than guessing.")
    else:
        bad = []
        types = []
        for block in p.jsonld:
            try:
                data = json.loads(block)
            except ValueError as exc:
                bad.append(str(exc)[:50])
                continue
            for d in (data if isinstance(data, list) else [data]):
                if isinstance(d, dict) and d.get("@type"):
                    types.append(str(d["@type"]))
        if bad:
            add(MISS, "JSON-LD", f"{len(bad)} block(s) do not parse: {bad[0]}",
                "An invalid block is ignored entirely -- it is the same as absent.")
        else:
            add(OK, "JSON-LD", f"{len(p.jsonld)} block(s): {', '.join(types) or '(no @type)'}")

    # --- structure --------------------------------------------------------
    h1 = [h for h in p.headings if h[0] == 1]
    if len(h1) == 1:
        add(OK, "one h1", " ".join(h1[0][1].split())[:56])
    else:
        add(MISS if not h1 else WARN, "one h1", f"{len(h1)} found",
            "One h1 states what the page is about; several state nothing.")

    skips = []
    prev = 0
    for lvl, _txt in p.headings:
        if prev and lvl > prev + 1:
            skips.append(f"h{prev}->h{lvl}")
        prev = lvl
    add(OK if not skips else WARN, "heading order",
        "no level skipped" if not skips else ", ".join(skips[:4]),
        "" if not skips else "A skipped level breaks the outline for a screen reader.")

    noalt = [i for i in p.images if "alt" not in i]
    add(OK if not noalt else WARN, "img alt",
        f"{len(p.images)} image(s), {len(noalt)} without alt",
        "" if not noalt else "An image with no alt is invisible to a reader who cannot see it.")

    # --- language ---------------------------------------------------------
    hreflang = [l for l in p.links if l.get("hreflang")]
    multilingual = bool(re.search(r"translation|i18n|lang-switch|jezik", html, re.I))
    if multilingual and not hreflang:
        add(MISS, "hreflang", "page looks multilingual but declares no alternates",
            "Without it the language versions compete with each other instead "
            "of being offered to the right reader.")
    elif hreflang:
        add(OK, "hreflang", f"{len(hreflang)} alternate(s)")

    return out


def check_site(root: Path) -> list[dict]:
    out = []
    for f, why in (("robots.txt", "Nothing tells a crawler what to skip, or where the sitemap is."),
                   ("sitemap.xml", "Every page has to be discovered by following links.")):
        add_ok = (root / f).is_file()
        out.append({"state": OK if add_ok else MISS, "check": f,
                    "detail": "present" if add_ok else "none", "why": "" if add_ok else why})
    return out


BASELINE = ".seo-baseline.json"


def baseline_path(target: str) -> Path:
    p = Path(target)
    return (p if p.is_dir() else p.parent) / BASELINE


def save_baseline(target: str, results: list) -> Path:
    path = baseline_path(target)
    payload = {"target": str(target),
               "saved": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
               "results": [{"target": t, "checks": c} for t, c in results]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_baseline(target: str) -> dict | None:
    path = baseline_path(target)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def delta(old: dict, results: list) -> list[dict]:
    """What changed since the baseline. Only movement, never the whole list."""
    before = {}
    for entry in old.get("results", []):
        for c in entry.get("checks", []):
            before[(entry.get("target"), c["check"])] = c["state"]
    moved = []
    for name, checks in results:
        for c in checks:
            was = before.get((name, c["check"]))
            if was is not None and was != c["state"]:
                moved.append({"target": name, "check": c["check"],
                              "from": was, "to": c["state"],
                              "detail": c["detail"]})
    return moved


#: What a fix actually BUYS the customer, in their terms -- in TWO tenses.
#:
#: One string cannot serve both readings. Written only in the completed tense,
#: the "what is missing" list came out as "NEDOSTAJE: Link ka sajtu sada
#: prikazuje naslov" -- missing: the link now shows the title. Nonsense, in a
#: document a customer is paying for.
#:
#: (problem now, what it became after the fix)
CLIENT_WORDS = {
    "Open Graph": (
        "Kada se link ka sajtu podeli na WhatsApp-u, Viber-u ili LinkedIn-u, "
        "prikazuje se prazna kartica bez naslova i slike.",
        "Link ka sajtu sada prikazuje naslov, opis i sliku pri deljenju na "
        "WhatsApp-u, Viber-u i LinkedIn-u."),
    "og:image absolute": (
        "Slika za deljenje je zadata putanjom koja se ne može učitati sa "
        "drugih servisa.",
        "Slika koja se prikazuje pri deljenju sada se ispravno učitava."),
    "JSON-LD": (
        "Pretraživači zaključuju čime se firma bavi iz teksta, umesto da im "
        "je to izričito rečeno.",
        "Pretraživačima je sada izričito rečeno čime se firma bavi."),
    "canonical": (
        "Ista stranica otvorena sa `www`, bez `www` ili preko linka iz "
        "kampanje broji se kao više različitih stranica.",
        "Sajt se više ne broji kao više različitih stranica."),
    "hreflang": (
        "Sajt ima više jezika, ali pretraživaču nije rečeno koja je verzija "
        "za koga — pa verzije konkurišu jedna drugoj.",
        "Posetiocu se sada nudi verzija sajta na njegovom jeziku."),
    "title": (
        "Nedostaje naslov koji se vidi u rezultatima pretrage.",
        "Postavljen je naslov koji se vidi u rezultatima pretrage."),
    "meta description": (
        "Nedostaje tekst ispod naslova u rezultatima pretrage, pa pretraživač "
        "sam bira šta će prikazati.",
        "Postavljen je tekst koji stoji ispod naslova u rezultatima pretrage."),
    "one h1": (
        "Stranica nema jedan jasan glavni naslov.",
        "Stranica sada jasno kaže o čemu je."),
    "img alt": (
        "Slike nemaju opis, pa su nedostupne posetiocima koji koriste čitač "
        "ekrana.",
        "Slike sada imaju opis i dostupne su čitačima ekrana."),
    "html lang": (
        "Jezik stranice nije prijavljen.",
        "Jezik stranice je prijavljen."),
    "viewport": (
        "Stranica se na telefonu prikazuje kao umanjena desktop verzija.",
        "Stranica se sada ispravno prikazuje na telefonu."),
    "twitter:card": (
        "Deljenje na X-u prikazuje mali link umesto slike.",
        "Deljenje na X-u sada prikazuje veliku sliku."),
    "heading order": (
        "Redosled naslova preskače nivoe, što otežava čitanje čitačem ekrana.",
        "Redosled naslova je uređen."),
    "robots.txt": (
        "Pretraživačima nije rečeno šta da indeksiraju.",
        "Pretraživačima je rečeno šta da indeksiraju."),
    "sitemap.xml": (
        "Stranice se pronalaze samo praćenjem linkova.",
        "Sve stranice su prijavljene pretraživačima."),
}


def _words(check_name: str, fixed: bool) -> str:
    pair = CLIENT_WORDS.get(check_name)
    if not pair:
        return check_name
    return pair[1] if fixed else pair[0]


def client_report(results: list, moved: list) -> None:
    print("=" * 66)
    print("IZVEŠTAJ — optimizacija za pretraživače")
    print("=" * 66)

    if moved:
        fixed = [m for m in moved if m["to"] == OK]
        broke = [m for m in moved if m["from"] == OK]
        if fixed:
            print("\nURAĐENO:\n")
            for m in fixed:
                print(f"  • {_words(m['check'], True)}")
        if broke:
            print("\nPAŽNJA — nešto što je ranije radilo više ne radi:\n")
            for m in broke:
                print(f"  • {m['check']}")
        if not fixed and not broke:
            print("\nNema promena u odnosu na prethodno merenje.")
    else:
        # No baseline yet: this is a FINDING, not a report of work done.
        print("\nSTANJE PRE POČETKA RADA\n")
        for name, checks in results:
            for c in checks:
                if c["state"] == MISS:
                    print(f"  • {_words(c['check'], False)}")
        print("\n  (Ovo je snimljeno stanje pre rada. Posle izmena, isti izveštaj")
        print("   pokazuje šta je konkretno urađeno.)")

    print("\n" + "-" * 66)
    print("  Ovaj izveštaj navodi SAMO ono što je merljivo provereno na stranici.")
    print("  Pozicija u rezultatima pretrage zavisi i od konkurencije, sadržaja i")
    print("  vremena — i nije nešto što se može obećati.")


def load(target: str) -> tuple[str, str]:
    if target.startswith(("http://", "https://")):
        sys.path.insert(0, str(Path(__file__).resolve().parent / "reach"))
        import channels as ch                                    # noqa: PLC0415
        # Fetch the RAW page: jina returns markdown, which has no <meta> left.
        return ch._get(target), target
    p = Path(target)
    return p.read_text(encoding="utf-8", errors="replace"), str(p)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip())
        return 1
    as_json = "--json" in sys.argv

    target = args[0]
    results: list[tuple[str, list[dict]]] = []

    path = Path(target)
    if not target.startswith("http") and path.is_dir():
        for f in sorted(path.rglob("*.html"))[:20]:
            if "node_modules" in f.parts:
                continue
            results.append((str(f.relative_to(path)), check(
                f.read_text(encoding="utf-8", errors="replace"), f.name)))
        results.append(("(site)", check_site(path)))
    else:
        html, name = load(target)
        results.append((name, check(html, name)))

    if "--save" in sys.argv:
        path = save_baseline(target, results)
        print(f"  baseline saved: {path}")
        print(f"  run again after the changes to see the delta")
        return 0

    old = load_baseline(target)
    moved = delta(old, results) if old else []

    if "--client" in sys.argv:
        client_report(results, moved)
        return 0

    if as_json:
        print(json.dumps({"results": [{"target": t, "checks": c} for t, c in results],
                          "changed_since_baseline": moved},
                         indent=2, ensure_ascii=False))
        return 0

    missing_total = 0
    for name, checks in results:
        print("=" * 74)
        print(f"SEO — {name}")
        print("=" * 74)
        for c in checks:
            mark = {OK: "ok  ", WARN: "note", MISS: "MISS"}[c["state"]]
            print(f"  {mark} {c['check']:<22} {c['detail'][:44]}")
            if c["why"]:
                for line in _wrap(c["why"], 66):
                    print(f"         {line}")
        missing_total += sum(1 for c in checks if c["state"] == MISS)
        print()

    if moved:
        print("-" * 74)
        print(f"  CHANGED since the baseline of {str(old.get('saved',''))[:16]}:\n")
        for m in moved:
            arrow = "fixed  " if m["to"] == OK else "BROKE  "
            print(f"    {arrow} {m['check']:<22} {m['from']} -> {m['to']}")
        print()
    elif not old:
        print("-" * 74)
        print("  No baseline. `--save` records today's state so the next run can")
        print("  show what the work actually changed -- which is what makes it")
        print("  billable evidence rather than a claim.\n")

    print("-" * 74)
    print(f"  {missing_total} missing item(s).")
    print("  Everything above is CHECKABLE. Keyword density, \"E-E-A-T\" and")
    print("  \"AI citation readiness\" are not measured here on purpose -- they")
    print("  are opinions with a vocabulary, and this brain does not carry")
    print("  rules it cannot check.")
    print("  Core Web Vitals need the RENDERED page: Lighthouse, via the")
    print("  chrome-devtools MCP.")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
