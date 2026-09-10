#!/usr/bin/env python3
"""UserPromptSubmit: name the skills and the delegation BEFORE the work starts.

**The hole this fills, measured.** `require_skill.py` is a PreToolUse gate on
`Edit|Write` — it can refuse, and it works. But a repo-wide audit that changes
nothing never touches those tools, so the gate never fires. One measured
session swept 27 apps, found ten real defects, and invoked **zero** skills and
**zero** agents, entirely legitimately as far as the gate was concerned.

Read-only work is exactly where domain rules change the JUDGEMENT rather than
the syntax, so that is the worst place to have no coverage.

**This is weaker than the gate and the difference matters.** A
`UserPromptSubmit` hook cannot deny anything; it injects context. What makes it
worth having anyway is WHEN it runs: before the first tool call, before a plan
exists. The fourteen reminders that were ignored all day fired at Edit time —
after the approach was already chosen, when loading a skill would mean
rethinking work already done. Advice at the start is a different proposition
from advice at the end, even when neither can compel.

Do not oversell it. It nudges. The gate refuses.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript as tx                                          # noqa: E402

#: Prompt signal -> the skills that actually bear on that kind of work.
#: Keyed on what a request SAYS, not on what files it will touch, because at
#: this point no file has been named yet.
TOPICS = [
    (r"\b(test|testov|suite|pytest|flaky|pada|prolaz)\w*", ["craft-testing", "testing-rules"]),
    (r"\b(security|bezbedn|authz|permission|dozvol|izolacij|tenant|leak)\w*", ["craft-security", "tenant-safety"]),
    # The PLAIN-LANGUAGE shape of a cross-scope question. Every word in the
    # pattern above is one an engineer reaches for AFTER classifying the
    # problem; the question as actually typed names two parties and a verb.
    # Found by eval, not by review: "da li klijent A moze da vidi podatke
    # klijenta B" matched only the domain skill, so the single most-repeated
    # defect in this brain's history got no rule named for the prompt that
    # describes it most directly.
    (r"(klijent\w*[^.?!]{0,40}\b(vidi|vide|pristup|dohvat|otvor)\w*|"
     r"\b(vidi|vide|pristup\w*|dohvat\w*)[^.?!]{0,25}\b(tu[dđ]\w*|drug\w+)|"
     r"\b(drug|tu[dđ])\w*\s+(klijent|korisnik|firm|tenant|organizacij)\w*|"
     r"\b(other|another)\s+(customer|tenant|client|company|user)\w*|"
     r"cross.(tenant|scope|client|account))",
     ["craft-security", "tenant-safety"]),
    (r"\b(feature flag|entitlement|parametar|param|registr|podesiv|naplat)\w*", ["feature-flags"]),
    (r"\b(prevod|translat|i18n|locale|msgid|gettext)\w*", ["craft-i18n", "i18n-rules"]),
    (r"\b(migrac|migration|schema|constraint|index)\w*", ["stack-django"]),
    (r"\b(ekran|screen|template|navbar|tabel|dugm|dizajn|ui)\w*", ["ui-bootstrap", "ui-lists-tables"]),
    (r"\b(commit|push|rebase|merge|grana|branch)\w*", ["craft-git"]),
    (r"\b(spor|brz|perform|optimiz|traje predugo|slow)\w*", ["craft-code"]),
    # "which tool for X" has an evaluated answer on file, rejections included.
    # Answering from a fresh web search throws that away and re-proposes what
    # was already turned down.
    (r"\b(koji (alat|tool|mcp)|ima li (neki )?alat|preporuci alat|"
     r"which tool|is there a tool|mcp server|da li postoji alat)\w*",
     ["ops-integrations"]),
    (r"\b(faktur|invoice|sef|suf|eotpremnic|efaktur)\w*", ["invoice-management"]),
    (r"\b(trebovan|procurement|nabavk)\w*", ["procurement-domain"]),
    (r"\b(popis|stocktaking|inventur)\w*", ["popis-domain"]),
    (r"\b(eskalacij|escalation|korektiv)\w*", ["escalation-domain"]),
    (r"\b(revizij|audit|provera koda|pregled koda|review)\w*", ["craft-code", "craft-security"]),
    # A question that can only be answered by a LIVE page. ui-bootstrap has
    # rules demanding exactly this -- "assert zero console/page errors", and
    # geometry measured with getBoundingClientRect while the CSS is being
    # written -- and until chrome-devtools there was no tool that could.
    # visual-diff shoots pictures; it cannot read a console.
    (r"\b(konzol|console|javascript (greska|error)|js (greska|error)|"
     r"network tab|mrezn\w* zahtev|request.{0,12}(500|404|403)|"
     r"devtools|lighthouse|ne radi dugme|dugme ne radi)\w*",
     ["ops-integrations", "ui-bootstrap"]),
]

#: Signals that the work is WIDE — several apps, a whole repo, a sweep. These
#: are the cases where doing it alone in the main thread is the expensive
#: mistake: the output floods the context and the parts are independent.
BREADTH = re.compile(
    r"\b(ceo repo|cel(i|og) projek|sve aplikacij|svaki modul|kroz ceo|"
    r"pretraži sve|pretrazi sve|spisak svih|sve gde|audit|revizij|"
    r"whole repo|every app|across all|sweep|inventory)\w*",
    re.IGNORECASE)

#: Prompts about the TOOLING are not prompts about the codebase.
#:
#: Every `dj-*` agent is keyed on a verb -- "izmen", "edit", "obrisi",
#: "delete", "lista", "filter" -- and those are everyday words. Measured: in
#: one conversation about token accounting `dj-update-view` was suggested
#: THREE times, on the bare words "edit" and "izmene", and `screenshot-reader`
#: once for a prompt with no image. A router wrong that often teaches the main
#: loop to skip reading it; only 10 of 37 sessions delegated at all.
#:
#: The first fix required a Django NOUN alongside the verb. It broke two eval
#: cases immediately: "dodaj dugme da rukovodilac moze da odobri ili odbije
#: trebovanje" and "treba da se moze obrisati lokacija iz sifarnika" name a
#: BUSINESS object, not a framework artefact, and that vocabulary is endless.
#: So the test is inverted -- suppress only when the prompt is explicitly about
#: the tooling, which is a closed set. Deliberately excludes "model" and "api":
#: both are ordinary Django words.
META_PROMPT = re.compile(
    r"\b(token|tokeni|tokena|kontekst|context|claude|gemini|subagent|"
    r"sub-agent|hook|skill|transkript|transcript|prompt|cache|ke[sš]|"
    r"delegir|fan-?out|kompakt|compact|sesij|session)\w*", re.IGNORECASE)

#: Named in this many EARLIER prompts, never once launched -> stop offering it.
#: The session has answered.
SUPPRESS_AFTER = 3

#: Prompt signal -> the agents that are actually right for it.
#:
#: Naming them is the whole point. "Consider delegating" is advice of exactly
#: the kind that got ignored fourteen times a day; a NAME is a decision the
#: caller can accept or refuse. Measured: the one prompt that produced five
#: agents in a day was the one that said which agents to run.
AGENTS = [
    (r"\b(pretraž|pretraz|nadji|nađi|gde se|find|locate|where is)\w*",
     ["scout", "impact-mapper"]),
    (r"\b(audit|revizij|pregled koda|review|proveri kod)\w*",
     ["security", "reviewer"]),
    (r"\b(security|bezbedn|authz|izolacij|dozvol|leak)\w*", ["security"]),
    # Same gap as in TOPICS: the agent has to be named for the plain-language
    # phrasing too, or the skill is loaded and nobody is sent to audit.
    (r"(klijent\w*[^.?!]{0,40}\b(vidi|vide|pristup|dohvat)\w*|"
     r"\b(drug|tu[dđ])\w*\s+(klijent|korisnik|firm|tenant)\w*|"
     r"\b(other|another)\s+(customer|tenant|client)\w*)", ["security"]),
    (r"\b(test|testov|coverage|pokriven)\w*", ["qa"]),
    (r"\b(spor|brz|perform|optimiz|traje predugo|slow)\w*", ["optimizer"]),
    (r"\b(prevod|translat|i18n|locale)\w*", ["translator"]),
    (r"\b(plan|faza|redosled|arhitektur|kako da krenem)\w*", ["planner"]),
    (r"\b(refaktor|preimenuj|premesti|propagir|rename|mechanical)\w*",
     ["refactorer"]),
    (r"\b(zasto puca|zašto puca|debug|ne radi|greška|greska|bug)\w*",
     ["debugger"]),
    # An attached picture is read FIRST. The tickets doctrine is unambiguous --
    # "attachments outrank text, every time" -- and a screenshot routinely turns
    # a three-word ticket into a specification and routinely contradicts the
    # words next to it. The agent existed with a deterministic path to nowhere.
    (r"\b(slik|slici|sliku|screenshot|snimak|snimk|prilog|prilaz|attach|"
     r"na slici|u prilogu|image)\w*", ["screenshot-reader"]),
    # Cases that are certain to arrive and had nothing naming them.
    (r"\b(konflikt|conflict|diverged|non.fast.forward|ne mogu da (push|pull)|"
     r"rebase|cherry.pick)\w*", ["merge-resolver"]),
    (r"\b(mysql|mariadb|innodb|collation|kolacij|charset|utf8mb4|"
     r"lock wait|deadlock|foreign key|indeks|index)\w*", ["mysql-consultant"]),
    (r"\b(popis|zaliha|zalih|magacin|stocktaking|inventur|otpremnic|"
     r"prijemnic|trebovanj|kalo|manjak|visak|vi[sš]ak)\w*",
     ["inventory-consultant"]),
    (r"\b(backlog|prioritet|redosled rada|[sš]ta da radim|plan rada|rok |"
     r"procen[ai]|obim posla|scope)\w*", ["pm"]),
    (r"\b(template tag|templatetag|badge|bed[zž]|formatiranje u templ|"
     r"custom filter)\w*", ["dj-templatetag"]),
    (r"\b(nisam siguran|ne znam kako|nova biblioteka|dokumentacij|"
     r"koja verzija|best practice|nepoznat)\w*", ["researcher"]),
    (r"\b(formset|clean_|validacij[ae] forme|modelform|polje forme)\w*",
     ["dj-forms"]),
    # --- the CRUD roster ------------------------------------------------
    # These were 21 of the 55 agents and had NEVER been invoked. The cause was
    # mechanical, not habitual: the orchestrator was the only thing meant to
    # select them, and it carried `Agent` in its OWN `disallowedTools`, so it
    # never selected anything. The brief blamed the harness; measured
    # 2026-09-10, the harness allows nesting and the grant is restored. These
    # triggers stay anyway: an agent no trigger names is an agent nobody
    # chooses, and the router reaches prompts that never go near the
    # orchestrator. Meanwhile `general-purpose` absorbed 81 runs.
    #
    # dj-action-view leads deliberately. A POST that changes state has no
    # template, so nobody reviews it, and craft-security calls those "the ones
    # that get forgotten". It is a senior agent for that reason, and it must
    # survive the agents[:5] truncation below.
    (r"\b(odobri|odbij|zatvor|aktivir|deaktivir|potvrd|ponisti|poništ|"
     r"otkaz|dodel|konvertuj|pokreni ponovo|approve|reject|assign|toggle|"
     r"resolve|retry|convert)\w*", ["dj-action-view"]),
    (r"\b(obri[sš]|brisanj|delete|ukloni)\w*", ["dj-delete-view"]),
    (r"\b(izmen|izmjen|uredi|edit|update ekran|update forme)\w*", ["dj-update-view"]),
    (r"\b(novi zapis|dodavanj|kreiraj|unos nov|create view|nova forma)\w*",
     ["dj-create-view"]),
    (r"\b(lista|listu|spisak|tabel|kolon|filter|pagin|sortir)\w*", ["dj-list-view"]),
    (r"\b(detalj|detail view|prikaz jednog|kartica zapisa)\w*", ["dj-detail-view"]),
    (r"\b(queryset|manager|selektor|selector|scoped repo)\w*", ["dj-manager"]),
    (r"\b(mixin|bazna klasa|base class|nasle[dđ]uj)\w*", ["dj-mixin"]),
    (r"\b(signal|receiver|post_save|pre_save)\w*", ["dj-signals"]),
    (r"\b(management command|cron|zakazan|import skript|seed)\w*", ["dj-command"]),
    (r"\b(ekran|screen|template|navbar|dizajn ekrana)\w*", ["dj-templates"]),
    (r"\b(migracij|migration)\w*", ["dj-migrations"]),
    # Not `\bservice`: a class name like `DeadlineService` has the word
    # boundary before `Deadline`, so an anchored pattern misses exactly the
    # phrasing people actually use when reporting a problem with one.
    (r"(servis|service|poslovna logika)", ["dj-service"]),
    # "declared but nothing reads it" -- the pattern that produced ten real
    # defects in one sweep. It reads as ordinary prose, so nothing else here
    # catches it.
    (r"\b(nema (nijedan )?poziv|bez poziva|niko (ga )?ne (cita|čita|zove)|"
     r"mrtav kod|dead code|zero call|never called|unused)\w*",
     ["undertaker", "impact-mapper", "scout"]),
    # Removal is a different ask from "find it". The operator asks for this in
    # plain terms -- "pocisti", "obrisi visak", "zaostalo" -- and the agent
    # that does it safely had no trigger at all.
    (r"\b(pocisti|počisti|ocisti|očisti|obri[sš]i (visak|višak|zaostal|"
     r"suvi[sš]n)|zaostal|prevazi[dj]đ|prevazidj|suvi[sš]n|"
     r"clean ?up (dead|unused)|remove dead|prune code)\w*",
     ["undertaker"]),
]


def main() -> int:
    try:
        raw = sys.stdin.read(100_000)
        payload = json.loads(raw) if raw.strip() else {}
        prompt = str(payload.get("prompt") or "")
    except Exception:                                           # noqa: BLE001
        return 0                                                # fail silent
    if not prompt.strip():
        return 0

    low = prompt.lower()
    skills: list[str] = []
    for pattern, names in TOPICS:
        if re.search(pattern, low):
            for name in names:
                if name not in skills:
                    skills.append(name)

    agents: list[str] = []
    for pattern, names in AGENTS:
        if re.search(pattern, low):
            for name in names:
                if name not in agents:
                    agents.append(name)

    # --- noise control -------------------------------------------------
    # A bare verb is not a routing signal; see DJANGO_NOUNS above.
    if agents and META_PROMPT.search(prompt):
        agents = [a for a in agents if not a.startswith("dj-")]

    # Drop a name this session has already been offered and declined. Derived
    # from the transcript rather than a state file, for the reason
    # `fanout_nudge.py` gives: the transcript is the honest record and a state
    # file drifts. An agent that WAS launched stays eligible -- it works.
    tpath = str(payload.get("transcript_path") or "")
    if agents and tpath:
        already = tx.launched(tpath)
        offered: Counter = Counter()
        for past in tx.user_prompts(tpath):
            plow = past.lower()
            for pattern, names in AGENTS:
                if re.search(pattern, plow):
                    for n in names:
                        offered[n] += 1
        agents = [a for a in agents
                  if a in already or offered[a] < SUPPRESS_AFTER]

    wide = bool(BREADTH.search(prompt))
    if not skills and not agents and not wide:
        return 0

    lines = []
    if skills:
        lines.append(
            "Before the first tool call, invoke the skills that govern this: "
            + ", ".join(skills[:6])
            + ". Reading the file with Read does not count -- rules consulted "
              "after the approach is chosen did not shape it.")
    if agents:
        picked = agents[:5]
        lines.append(
            "Agents that fit this task: " + ", ".join(picked)
            + ". Delegating is not overhead here -- an agent reads the files "
              "in ITS context and returns the conclusion, so the search output "
              "never enters this one. Use them or say in one line why not.")
        # Naming the agents was not enough: measured over 37 sessions, 172 of
        # 235 launches were ONE agent alone (73%). The missing instruction is
        # not "delegate", it is "in the same message". The figure read 100%
        # until transcript.py fixed the grouping -- see ops-delegation.
        if len(picked) > 1:
            lines.append(
                "Those are independent of each other, so put them in ONE "
                "message -- " + " + ".join(f"Agent(brain:{a})" for a in picked[:3])
                + " in a single assistant turn. Calls in separate messages run "
                  "one after another and each result lands back here before the "
                  "next starts; the transcript looks the same and it is several "
                  "times the wall-clock. Sequence only what needs a previous "
                  "agent's OUTPUT.")
    if wide:
        lines.append(
            "This request is WIDE (many modules, or a sweep). The parts are "
            "independent and the raw output would flood this context. Before "
            "launching anything, NAME EVERY UNIT -- one per module, screen, "
            "ticket or review dimension -- and emit them as N Agent calls in a "
            "single message. The unit that never gets named is the second one, "
            "because after the first result arrives you are reading rather than "
            "planning. If working out that split needs more codebase reading than this "
            "context should carry, hand the whole thing to "
            "Agent(brain:orchestrator) instead -- it plans, launches and "
            "collects inside its own context, and only its synthesis comes "
            "back. Recipes: /brain:ops-delegation references/fan-out.md.")

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": " ".join(lines),
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
