#!/usr/bin/env node
// Syntax-check workflow scripts the way the runtime actually parses them.
//
// Neither obvious command is correct here, and both lie in different directions:
//
//   node --check file.js              parses as sloppy CommonJS and reported
//                                     "OK" on a file with an unescaped quote
//   node --input-type=module --check  parses as a real ES module and rejects
//                                     the top-level `return` these scripts
//                                     legitimately use
//
// The runtime wraps a workflow body in an async function, so that is what has
// to be parsed: top-level `await` and `return` are both legal there. Building
// an AsyncFunction throws on a syntax error without executing anything.
//
//   node scripts/brain/check-workflows.js [file ...]     (default: workflows/*.js)

import { readFileSync, readdirSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

const files = process.argv.length > 2
  ? process.argv.slice(2)
  : readdirSync(join(ROOT, 'workflows'))
      .filter(f => f.endsWith('.js'))
      .map(f => join(ROOT, 'workflows', f))

let failed = 0
for (const file of files) {
  const src = readFileSync(file, 'utf8')
  const rel = file.replace(ROOT, '').replace(/^[\\/]/, '')
  try {
    // `export const meta = {...}` is module syntax and not valid inside a
    // function body, so strip it before parsing and check it separately.
    const metaMatch = src.match(/export\s+const\s+meta\s*=\s*(\{[\s\S]*?\n\})/)
    if (!metaMatch) throw new SyntaxError('missing `export const meta = {...}`')
    new AsyncFunction(`return ${metaMatch[1]}`)          // meta must be a literal
    new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log',
                      'workflow', 'budget', src.replace(metaMatch[0], ''))
    console.log(`  OK    ${rel}`)
  } catch (err) {
    failed++
    console.log(`  FAIL  ${rel}\n        ${err.name}: ${err.message}`)
  }
}

console.log(failed ? `\n${failed} workflow(s) will not parse` : '\nall workflows parse')
process.exit(failed ? 1 : 0)
