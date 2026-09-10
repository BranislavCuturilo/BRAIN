---
name: project-rules
description: >
  Read and honour the operator's STANDING RULES for a project before touching its
  design — the per-module `project.ai_note` from the ticket store (e.g.
  acme-audit: multi-tenant → generalise everything, per-tenant code lists,
  feature-flag every capability, never one client's hardcoded types). Load when a
  generated ticket prompt says the rules apply, when a ticket touches a model, a
  workflow, permissions, configuration or the shape of a feature, or on
  `/brain:project-rules <MODULE>`. Not needed for a cosmetic change (text, CSS,
  two paragraphs in a template).
when_to_use: >
  "/brain:project-rules VEZ", "stalna pravila projekta", a ticket prompt line
  "Stalna pravila projekta VAŽE", before adding a model / type / category /
  workflow state / permission / setting, before deciding "should this be
  configurable".
argument-hint: "<MODULE>  (e.g. VEZ, SUF, POPIS, ODS, FUK, IFRS16)"
---

# Project standing rules

Arguments: `$ARGUMENTS`

The operator writes ONE standing note per project (HUD → Tickets → 📁 repos →
"stalna napomena za AI"). It is the design law of that application and it is
NOT pasted into every generated prompt — it is read here, on demand, when the
work actually touches design.

## Do this

1. Print the rules for the module and READ them:

   ```
   python ~/.claude/skills/brain/scripts/tickets/project_rules.py $ARGUMENTS
   ```

   (no argument → `--all` lists every project that has rules).

2. Treat them as **authoritative for the design**: if the ticket's words conflict
   with them, follow the rules and say so in the report. Typical consequences:
   a "type of report" the customer names becomes a per-tenant code list, a new
   capability gets a feature flag / entitlement, nothing is hardcoded for one
   client.

3. Load the repo's own skills the rules point at (for acme-audit:
   `code-conventions`, `tenant-safety`, `feature-flags`, plus the domain skill of
   the area) — the note tells you WHAT must hold, the repo skills tell you HOW
   this codebase does it.

## When NOT to load

A cosmetic ticket — text, wording, CSS, a template paragraph — does not touch
design; the generated prompt says so ("NISU relevantna za ovaj tiket") and you
skip this. If the scope grows mid-work (a field, a model, a permission), come
back here first.
