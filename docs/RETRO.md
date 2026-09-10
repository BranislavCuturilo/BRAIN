# Practice review -- last 30 days

Repo: C:\Users\you\.claude\skills\brain    Generated: 2026-09-09 21:06 UTC

## The numbers

- agent runs: **378** across 39 kinds -- counted across EVERY project on this machine, not just this repo
- outcomes recorded: **5/378** (1%)
- commits: **318**, of which **54** carry attribution -- from `C:\Users\you\.claude\skills\brain` ALONE

> Runs and commits above are two different populations, so **their ratio is not a statistic**. A review read the two adjacently and derived a 19.6% attribution rate for the brain; the runs in that figure were mostly another repo's. Judge attribution per repo, with the same repo on both sides of the fraction.

- roster: 56 agents, 20 never ran in this window
- brain changed 318 time(s)

> **Score coverage is 1%.** Every band and every retire/keep
> decision below rests on that fraction. Treat the scoreboard as
> anecdote until this is over half.

## What ran

| agent | runs | shipped commits |
|---|---:|---:|
| general-purpose | 82 | 0 |
| backend-senior | 58 | 27 |
| frontend-senior | 53 | 25 |
| scout | 34 | 0 |
| researcher | 26 | 0 |
| reviewer | 13 | 3 |
| security | 12 | 0 |
| scribe | 9 | 0 |
| qa | 9 | 0 |
| backend-junior | 9 | 0 |
| planner | 8 | 0 |
| appsec-reviewer | 7 | 3 |
| translator | 6 | 0 |
| claude-code-guide | 5 | 0 |
| refactorer | 5 | 0 |
| Explore | 5 | 0 |
| ui-ux | 4 | 0 |
| frontend-junior | 3 | 0 |
| dj-screen-slice | 3 | 0 |
| dj-templates | 3 | 0 |
| debugger | 2 | 5 |
| dj-views | 2 | 0 |
| archivist | 2 | 0 |
| brain-keeper | 2 | 0 |
| repo-reader | 2 | 0 |

## Outcomes recorded in this window

- agent brain-keeper: 2026-09-09 helped: Uhvatio izmisljen citat u pravilu koje sam napisao isti dan (tvrdio sam da consult-chain nema kill criterion; ima ga, linija 85). Precenio je pokvarene reference i pogresio verziju klijenta (citao npm 2.1.258 umesto pokrenutog 2.1.266), ali je glavni nalaz bio tacan i skup da se propusti.
- agent researcher: 2026-09-09 helped: Analiza tudjeg repoa: 5 rangiranih nalaza, licenca tacno utvrdjena, jasno razdvojio sta vec imamo. Jedan detalj netacan (rekao da nemamo 'declined' status -- imamo 'wontfix'). Neto: upotrebljivo bez prerade.
- agent researcher: 2026-09-09 helped: Vercel agent-browser: tacno identifikovao proizvod (vercel-labs/agent-browser, Apache-2.0, lokalno), i nasao konkretan gubitak -- animations=disabled i caret=hide nisu dokumentovani, sto udara u phash u compare.py. Presuda 'ostajemo' bila je obrazlozena, ne inertna.
- agent researcher: 2026-09-09 helped: GSD: razresio dvosmisleno ime medju 4 kandidata i rekao zasto je uzeo jedan, utvrdio da je arhiviran, i sveo 65 komandi na 1.5 upotrebljivu stavku umesto da hvali repo.
- agent researcher: 2026-09-09 helped: TencentDB-Agent-Memory: nasao odlucujucu cinjenicu koju bih propustio -- to je LLM proxy, pa bi tela tiketa kupaca isla kroz njihov kontejner na placeni kljuc, zaobilazeci pretplatu. Ispravio i moju pretpostavku da je rec o MemoryOS-u.

## What changed in the brain

- 2026-09-09  CLAUDE.md: verify a source at the moment you quote it, not from memory of having read it
- 2026-09-09  health: score coverage is a ratio, not a date -- the check I shipped today was wrong by lunchtime
- 2026-09-09  findings: two borrowed ideas from the gsd and TencentDB research, with review dates
- 2026-09-09  ops-maintain, ops-sync: stop telling the reader to run the one git command this repo forbids
- 2026-09-09  brain: give the six unnamed agents a path, make scoring blindness visible, fix nine dead pointers
- 2026-09-09  cadence: record the five reviews that had never run
- 2026-09-09  brain-keeper: correct a citation I fabricated today, and move the mechanism out of the router
- 2026-09-09  brain: adopt what one-skill-to-rule-them-all got right, as OUR measurements
- 2026-09-09  chore(brain): sync — 1 file(s), committed automatically
- 2026-09-09  chore(store): sync — 3 file(s), committed automatically
- 2026-09-09  chore(docs): regenerate — 2 file(s), committed automatically
- 2026-09-09  ops-delegation: a subagent's report is the one thing context can never reclaim
- 2026-09-09  chore(brain): sync — 2 file(s), committed automatically
- 2026-09-09  chore(store): sync — 5 file(s), committed automatically
- 2026-09-09  chore(docs): regenerate — 2 file(s), committed automatically
- 2026-09-09  compact_brief: record the context size at the moment a compaction fires
- 2026-09-09  agent_view: offer the limit commands only when they would actually help
- 2026-09-09  usage_limit: discover the windows instead of listing them, and read the /limit-reset signal
- 2026-09-09  findings: a borrowed idea carries where it came from and when it expires
- 2026-09-09  brain: three doors to a rule, not one -- an incident, a measurement, or a marked hypothesis
- 2026-09-09  dead.py: a symbol the docs name and nothing calls is a GHOST, not alive
- 2026-09-09  chore(store): sync — 3 file(s), committed automatically
- 2026-09-09  chore(docs): regenerate — 1 file(s), committed automatically
- 2026-09-09  chore(store): sync — 1 file(s), committed automatically
- 2026-09-09  chore(docs): regenerate — 2 file(s), committed automatically
- 2026-09-09  journal: session-start InstructionsLoaded rows from this machine
- 2026-09-09  chore(store): sync — 1 file(s), committed automatically
- 2026-09-09  chore(store): sync — 1 file(s), committed automatically
- 2026-09-08  chore(store): sync — 1 file(s), committed automatically
- 2026-09-08  chore(store): sync — 1 file(s), committed automatically

## Commits with no attribution

Work that did not go through the brain, or went through it without
recording. Both are findings; they are different findings.

- `6e82795c6` 2026-09-09 CLAUDE.md: verify a source at the moment you quote it, not from memory
- `9a16535f5` 2026-09-09 health: score coverage is a ratio, not a date -- the check I shipped t
- `8d308dfc6` 2026-09-09 findings: two borrowed ideas from the gsd and TencentDB research, with
- `163fd64ec` 2026-09-09 ops-maintain, ops-sync: stop telling the reader to run the one git com
- `2239b6794` 2026-09-09 brain: give the six unnamed agents a path, make scoring blindness visi
- `c281418d6` 2026-09-09 cadence: record the five reviews that had never run
- `671c6428b` 2026-09-09 brain-keeper: correct a citation I fabricated today, and move the mech
- `0832613e9` 2026-09-09 brain: adopt what one-skill-to-rule-them-all got right, as OUR measure
- `bbe56d5eb` 2026-09-09 chore(brain): sync — 1 file(s), committed automatically
- `217e54548` 2026-09-09 chore(store): sync — 3 file(s), committed automatically
- `5fa990fbf` 2026-09-09 chore(docs): regenerate — 2 file(s), committed automatically
- `a8e24082b` 2026-09-09 ops-delegation: a subagent's report is the one thing context can never
- `04628d463` 2026-09-09 chore(brain): sync — 2 file(s), committed automatically
- `d1f72b09c` 2026-09-09 chore(store): sync — 5 file(s), committed automatically
- `d3257b5d5` 2026-09-09 chore(docs): regenerate — 2 file(s), committed automatically

## Never ran in this window

`dj-action-view`, `dj-delete-view`, `dj-detail-view`, `dj-list-view`, `dj-manager`, `dj-migrations`, `dj-mixin`, `dj-model-slice`, `dj-settings`, `dj-signals`, `dj-templatetag`, `dj-update-view`, `merge-resolver`, `mysql-consultant`, `pm`, `project-expert`, `screenshot-reader`, `synthesizer`, `ticket-reader`, `watchdog`

Not proof of bloat. The question is whether the work never came up
or the selection never reached them -- and only the commits above
can tell you which.
