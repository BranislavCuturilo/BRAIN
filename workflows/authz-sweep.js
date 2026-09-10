export const meta = {
  name: 'authz-sweep',
  description: "Find by-pk endpoints that do not use the app's by-pk authorization helper",
  whenToUse: 'Periodic security sweep of a Django project, or after adding endpoints to several apps. Pass args: {apps: ["audits","stocktaking"]} - a JSON string is accepted too.',
  phases: [
    { title: 'Map', detail: 'per app: which helper is authoritative, which endpoints exist' },
    { title: 'Check', detail: 'rank and cap what the reader classified as suspect' },
    { title: 'Verify', detail: 'refute each finding before reporting it' },
  ],
}

// This exists because bare `View` subclasses - POST-only action endpoints with
// no template - are the surface nobody looks at, and the one where the
// authorization defect actually lives. In one real project they outnumbered
// every generic CBV combined.

// args may arrive as a JSON string rather than an object depending on how the
// workflow is invoked. Parsing defensively costs one line; not doing it made
// the invocation documented in this file's own whenToUse throw in 16ms, every
// time.
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const apps = Array.isArray(A.apps) ? A.apps : null
if (!apps) throw new Error('args must supply {apps: [...]}; run a scout first to list them')

const APP_MAP = {
  type: 'object',
  required: ['app', 'helper', 'endpoints'],
  additionalProperties: false,
  properties: {
    app: { type: 'string' },
    helper: { type: 'string', description: 'the helper the LIST view uses to narrow rows; "" if none' },
    byPkHelper: { type: 'string', description: 'the helper a DETAIL/ACTION endpoint must call - usually a DIFFERENT function from the list one (user_can_view vs visible_x). "" if none exists.' },
    listNarrows: { type: 'boolean', description: 'does the list narrow beyond scope (own-records, subtree, archive, draft)' },
    endpoints: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'file', 'line', 'kind', 'authorizesVia'],
        additionalProperties: false,
        properties: {
          name: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'integer' },
          kind: { type: 'string', description: 'detail | update | delete | action | fragment | api' },
          mutating: { type: 'boolean' },
          authorizesVia: { type: 'string', description: 'exactly what it does to authorize, quoted' },
          verdict: { type: 'string', enum: ['uses-by-pk-helper', 'inherited-from-mixin', 'bare-scope-filter', 'no-authorization', 'unclear'], description: 'YOU classify this - you have the code. A mixin or parent that calls the helper counts as inherited-from-mixin, NOT as bare.' },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['refuted', 'reason'],
  additionalProperties: false,
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
    reachableBy: { type: 'string' },
  },
}

phase('Map')
// One agent per app: the helper and the endpoint inventory come from the same
// reading, so they cannot disagree.
const maps = await pipeline(apps,
  app => agent(
    `In the Django app "${app}":
` +
    `(1) find the helper its LIST view uses to narrow rows, quoted exactly;
` +
    `(2) find the helper a DETAIL or ACTION endpoint is supposed to call. It is ` +
    `usually a DIFFERENT function from the list one - user_can_view vs ` +
    `visible_x, can_view_audit vs get_audit_queryset. This is the contract;
` +
    `(3) inventory EVERY endpoint addressed by a primary key - detail, update, ` +
    `delete, POST-only action views (bare View subclasses), fragment handlers, ` +
    `API endpoints. Quote exactly what each does to authorize, AND classify it. ` +
    `You have the code in front of you, so resolve indirection: a mixin, parent ` +
    `class or dispatch override that calls the helper is inherited-from-mixin, ` +
    `not bare. Only classify as bare-scope-filter when the endpoint really does ` +
    `nothing but filter by tenant/organization.`,
    { label: `map:${app}`, phase: 'Map', schema: APP_MAP, agentType: 'brain:scout', model: 'sonnet', effort: 'high' }),

  (m, app) => {
    if (!m) { log(`${app}: could not be mapped`); return null }
    if (!m.helper) log(`${m.app}: NO list-view helper found - every endpoint here is suspect`)
    log(`${m.app}: ${m.endpoints.length} by-pk endpoints, helper=${m.helper || 'NONE'}`)
    return m
  })

const mapped = maps.filter(Boolean)

phase('Check')
// Judging in code is exact only when the inputs are already normalised. The
// first version compared each endpoint's authorization PROSE against the LIST
// helper's name -- but a correctly written by-pk endpoint calls a DIFFERENT
// function (user_can_view, not visible_x), so the strings never overlapped and
// all 74 endpoints came back suspect. Substring matching cannot see mixin
// indirection either. So the classification is now the reader's job -- it has
// the code -- and the code's job is only to rank and cap.
const RANK = { 'no-authorization': 0, 'bare-scope-filter': 1, 'unclear': 2 }
const suspects = []
const counts = {}
for (const m of mapped) {
  for (const e of m.endpoints) {
    const v = e.verdict || 'unclear'
    counts[v] = (counts[v] || 0) + 1
    if (v in RANK) suspects.push({ ...e, app: m.app, helper: m.byPkHelper || m.helper })
  }
}
log(`classified: ${Object.entries(counts).map(([k, n]) => `${k}=${n}`).join(', ')}`)
log(`${suspects.length} endpoint(s) worth verifying`)

// Worst classification first, then mutating: same defect, worse consequence.
suspects.sort((a, b) =>
  (RANK[a.verdict] - RANK[b.verdict]) || ((b.mutating ? 1 : 0) - (a.mutating ? 1 : 0)))
const CAP = 12
if (suspects.length > CAP) log(`verifying the ${CAP} highest-impact of ${suspects.length}; the rest are listed unverified`)
const toVerify = suspects.slice(0, CAP)

phase('Verify')
// Adversarial: the agent's job is to REFUTE, defaulting to refuted when unsure.
// Without this, a sweep returns a wall of plausible findings nobody trusts.
const verified = await parallel(toVerify.map(s => () =>
  agent(
    `Claim: ${s.app}.${s.name} (${s.file}:${s.line}) is reachable by primary key for a ` +
    `record the user must not touch. It was classified "${s.verdict}" and authorizes ` +
    `via "${s.authorizesVia}", where this app's by-pk contract is "${s.helper || 'NONE FOUND'}".\n\n` +
    `Try to REFUTE this. Read the actual code. It is refuted if ANY of these already ` +
    `applies equivalent or narrower access control: a mixin, a decorator, a dispatch ` +
    `override, a parent class, a URL constraint, a permission test, or the service ` +
    `the view delegates to. That last one matters -- a view with a thin queryset ` +
    `whose service re-checks membership is not a defect.\n` +
    `Default to refuted=true when you cannot establish a concrete path by which a ` +
    `real user reaches a forbidden record.`,
    { label: `verify:${s.app}.${s.name}`, phase: 'Verify', schema: VERDICT, agentType: 'brain:security' })
  // `check`, not `verdict`: the reader already put its classification on
  // s.verdict, and spreading a second `verdict` here would silently overwrite
  // it with the verifier's object.
  .then(v => ({ ...s, check: v }))))

const confirmed = verified.filter(Boolean).filter(v => v.check && !v.check.refuted)

return {
  appsMapped: mapped.map(m => ({ app: m.app, listHelper: m.helper,
                                 byPkHelper: m.byPkHelper, endpoints: m.endpoints.length })),
  classified: counts,
  confirmed: confirmed.map(c => ({
    where: `${c.file}:${c.line}`, name: `${c.app}.${c.name}`, kind: c.kind,
    mutating: c.mutating, classifiedAs: c.verdict, authorizesVia: c.authorizesVia,
    reachableBy: c.check.reachableBy, why: c.check.reason,
  })),
  unverified: suspects.slice(CAP).map(s => `${s.file}:${s.line} ${s.app}.${s.name}`),
  notReached: apps.filter(a => !mapped.some(m => m.app === a)),
}
