# Dashboard — metrics, charts, overview screens

Extends `ui-bootstrap`.

## A dashboard answers a question

Start from "what decision does the viewer make from this screen?" A grid of
numbers nobody acts on is a maintenance cost with no return. If a widget has
never changed anyone's behaviour, remove it.

Order by decision value, not by how easy the query was.

## Numbers

- **Every number needs its scope stated** — period, and whose data. "142" means
  nothing; "142 open, this month, your locations" means something.
- **A total must be computed over exactly the rows the viewer may see.** When
  visibility is partial, the aggregate is partial and must be labelled as such —
  otherwise the dashboard leaks the size of what the viewer cannot access, and
  reports a number that is simply wrong for them (`craft-security`).
- **A comparison needs its baseline visible.** "+12%" against what, since when.
- **Distinguish zero from unknown.** "0 findings" and "not measured" are different
  claims, and collapsing them into a blank cell destroys the difference.

## Colour and thresholds

- **Status colours come from configuration, not from code.** Tier and threshold
  colours are exactly what a customer wants to rebrand, and a hardcoded palette
  means a deploy per customer (`craft-code`). Read them from the same rows that
  define the thresholds.
- **Colour is never the only signal.** Add a label, an icon or a value —
  red/green alone fails for colour-blind users and in print.
- One documented sentinel colour for "no data / no tier applies", marked reserved
  so nobody assigns it as a real value.

## Charts

- Label both axes and state the unit. A chart whose y-axis is unlabelled is
  decoration.
- Do not truncate an axis to exaggerate a trend.
- **Give the underlying numbers a way out** — a table, a tooltip, or an export.
  Someone will need to check the chart against reality, and if they cannot, they
  will stop trusting it.
- Charts fail: no data, one data point, or a hundred series. Handle all three
  deliberately.
- **A cell matrix — a GitHub-style commit calendar, a heatmap, any fixed-size
  tile grid — is CSS `grid` with fixed tracks, never nested flexbox.** Nested
  flex (`display:flex` row of `flex-direction:column` columns holding
  fixed-height cells) lets the flex cross-axis *stretch* each cell far beyond its
  set size: 12px cells silently rendered ~130px tall, ballooning the panel to
  9× its intended height. The tell is nasty — the cells still *look* right at the
  top of the panel while the container is enormous and mostly empty, so the diff
  and a glance both pass. Use `grid-template-rows:repeat(7,12px);
  grid-auto-flow:column;grid-auto-columns:12px` and emit cells flat. **Verify the
  panel/container height by render (measure `getBoundingClientRect().height`), not
  the cell appearance** — a fixed-size cell can look correct while its container
  is wrong.

## Performance

- **Compute in the query, not in the template.** A dashboard that loops rows and
  reaches through relations issues a query per row per widget, and the page gets
  slower with every record added (`stack-django/references/queries.md`).
- Cache expensive aggregates on the record and recompute on the event that
  invalidates them, rather than recomputing per page load.
- A slow dashboard is abandoned. Budget it as a real requirement, and measure
  with production-sized data — every dashboard is fast against twelve rows.

## Empty states

A brand-new tenant sees this screen first. Design what it looks like with zero
data: what to do first, not a grid of zeros and empty charts.

## Za testera i za ekstenziju

Isto pravilo, iz ugla onog ko prijavljuje. Blok ispod je izvor za
`BugReporter/src/skills/screen-dashboard.md` (generiše `scripts/brain/extension_skills.py`);
menja se OVDE, nikad u kopiji.

<!-- ebr:skill id="screen-dashboard" name="Kontrolna tabla i izveštaj — šta moraju" kind="dashboard, report" scope="compose" -->
Kontrolna tabla ili izveštaj mora da: uz svaki broj kaže period i čije podatke ("142 otvorenih, ovaj mesec, tvoje lokacije"); razlikuje nulu od "nije mereno"; uz poređenje ("+12%") pokaže prema čemu; boju nikad ne koristi kao jedini signal; grafik ima obeležene ose i jedinicu i način da se vide brojevi iza njega (tabela ili izvoz); prazno stanje za novog klijenta.
Kako da klasifikuješ:
- Broj se ne slaže sa listom iz koje je izveden → bug, prioritet iznad Minor; traži oba ekrana i filter.
- Ukupan broj veći nego što korisnik sme da vidi (broji tuđe) → bug, Critical.
- Nula prikazana tamo gde nema merenja → bug (moraju da se razlikuju).
- Boje pragova "pogrešne" → pragovi i boje se podešavaju po klijentu: ako podešavanje postoji → "question" ili "change_request", ne bug.
- Spor izveštaj → bug; traži koji filter, koliki period, koliko sekundi.
- Novi grafik, nova kolona, drugi period → "change_request".
<!-- /ebr:skill -->
