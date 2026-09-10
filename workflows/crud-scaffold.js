export const meta = {
  name: 'crud-scaffold',
  description: 'Read a Django model, then build its CRUD surface from the extracted spec',
  whenToUse: 'A new model needs list/detail/create/update/delete views, templates and tests. Pass args: {app, model, appPath}',
  phases: [
    { title: 'Read', detail: 'extract the model structure once' },
    { title: 'Survey', detail: 'find the app\'s existing conventions' },
    { title: 'Write', detail: 'one agent per surface, in parallel' },
    { title: 'Review', detail: 'senior pass over everything written' },
  ],
}

// args: { app: 'stocktaking', model: 'RegistryItem', appPath: 'stocktaking' }
// args may arrive as a JSON string rather than an object depending on how the
// workflow is invoked. Parsing defensively costs one line; not doing it made
// the invocation documented in this file's own whenToUse throw in 16ms, every
// time.
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const app = A.app
const model = A.model
const appPath = A.appPath || app
if (!app || !model) throw new Error('args must supply {app, model}')

// The reader's 1,500 lines are thrown away here; every writer below starts
// from ~30 lines of spec instead of the file.
const MODEL_SPEC = {
  type: 'object',
  required: ['name', 'fields', 'scopeField', 'strRepr'],
  additionalProperties: false,
  properties: {
    name: { type: 'string' },
    scopeField: { type: 'string', description: 'the tenant/owner FK, or "" if unscoped' },
    strRepr: { type: 'string' },
    orderingHint: { type: 'string' },
    fields: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'kind', 'required'],
        additionalProperties: false,
        properties: {
          name: { type: 'string' },
          kind: { type: 'string', description: 'Char/Text/Decimal/FK/M2M/Bool/Date/File/...' },
          required: { type: 'boolean' },
          relatedModel: { type: 'string' },
          choices: { type: 'boolean' },
          editable: { type: 'boolean', description: 'false for auto/derived fields' },
        },
      },
    },
    constraints: { type: 'array', items: { type: 'string' } },
    cleanRules: { type: 'array', items: { type: 'string' } },
    listColumns: { type: 'array', items: { type: 'string' }, description: 'fields worth showing in a list' },
    searchFields: { type: 'array', items: { type: 'string' } },
  },
}

const CONVENTIONS = {
  type: 'object',
  required: ['scopeHelper', 'baseTemplate', 'urlNamespace', 'notes'],
  additionalProperties: false,
  properties: {
    scopeHelper: { type: 'string', description: 'the exact call every by-pk view must authorize through' },
    listMixins: { type: 'string' },
    baseTemplate: { type: 'string' },
    urlNamespace: { type: 'string' },
    templateDir: { type: 'string' },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

phase('Read')
const spec = await agent(
  `Read ${appPath}/models.py and extract ONLY the structure of the ${model} model. ` +
  `Do not read views, templates or tests. Do not propose changes. ` +
  `For listColumns pick the fields a human would want in a table; for searchFields ` +
  `pick what someone would type to find a row.`,
  { label: `read:${model}`, phase: 'Read', schema: MODEL_SPEC, model: 'sonnet', effort: 'low' })

if (!spec) throw new Error(`could not read ${model}`)
log(`${spec.name}: ${spec.fields.length} fields, scope=${spec.scopeField || 'NONE'}`)

phase('Survey')
// Conventions are read once and handed to every writer, so no writer has to
// go looking and none of them can disagree about the answer.
const conv = await agent(
  `In the ${appPath} app, establish the conventions a NEW view must follow. ` +
  `Find: the exact scope/visibility helper existing by-pk views authorize through; ` +
  `the mixins list views use; the base template; the URL namespace; the template ` +
  `directory. Report what IS there, not what should be.`,
  { label: 'survey:conventions', phase: 'Survey', schema: CONVENTIONS, model: 'sonnet', effort: 'high' })

if (!conv?.scopeHelper) {
  log('WARNING: no existing scope helper found - every by-pk view below needs one written first')
}

const brief = JSON.stringify({ spec, conv }, null, 1)

phase('Write')
const surfaces = [
  { key: 'list', agent: 'dj-list-view', what: 'the list view with filtering, ordering and pagination, plus its template' },
  { key: 'detail', agent: 'dj-detail-view', what: 'the detail view and its template' },
  { key: 'create', agent: 'dj-create-view', what: 'the ModelForm and the create view, plus the form template' },
  { key: 'update', agent: 'dj-update-view', what: 'the update view, reusing the create form' },
  { key: 'delete', agent: 'dj-delete-view', what: 'the delete view - establish first whether this model should soft-delete' },
]

const written = await parallel(surfaces.map(s => () =>
  agent(
    `Write ${s.what} for ${app}.${model}.\n\n` +
    `Model spec and the app's existing conventions:\n${brief}\n\n` +
    `Follow the conventions exactly - match the existing code, do not introduce a ` +
    `second way of doing this. Authorize through the scope helper named above. ` +
    `Add the URL entry. Do NOT touch models.py or migrations. ` +
    `Report the files you changed and anything you had to guess.`,
    { label: `write:${s.key}`, phase: 'Write', agentType: `brain:${s.agent}` })
  .then(r => ({ surface: s.key, report: r }))))

const ok = written.filter(Boolean)
if (ok.length < surfaces.length) log(`${surfaces.length - ok.length} surface(s) failed and were skipped`)

phase('Review')
// Review is a grade up and a separate context - never the agent that wrote it.
const review = await agent(
  `Review the ${model} CRUD surface just written in the ${appPath} app.\n\n` +
  `Reports from the writers:\n${ok.map(w => `[${w.surface}] ${w.report}`).join('\n\n')}\n\n` +
  `Check in this order, and try to REFUTE rather than approve:\n` +
  `1. Does EVERY by-pk view authorize through ${conv?.scopeHelper || 'the app scope helper'}, ` +
  `   not a bare scope filter plus a wide permission? The delete and update views ` +
  `   are the ones that get this wrong.\n` +
  `2. Does any view write on GET?\n` +
  `3. Do the five surfaces agree with each other and with the rest of the app, ` +
  `   or did parallel writers invent three different conventions?\n` +
  `4. Is anything duplicated that should be shared?\n` +
  `Report findings only - do not fix.`,
  { label: 'review:crud', phase: 'Review', agentType: 'brain:reviewer' })

return { model: spec.name, conventions: conv, written: ok.map(w => w.surface), review }
