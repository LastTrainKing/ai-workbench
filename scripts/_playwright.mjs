/**
 * Resolve `playwright-core` for the repo-level verification scripts.
 *
 * The dependency is a *frontend* dev dependency, and these scripts live at the repo
 * root — where there is no `package.json` and no `node_modules`. ESM resolves bare
 * specifiers relative to the importing file, so `import { chromium } from
 * 'playwright-core'` fails here with `ERR_MODULE_NOT_FOUND`, from any working
 * directory. `NODE_PATH` does not help either: it is a CommonJS mechanism.
 *
 * An absolute file URL does work, so the resolution is done once, here, and every
 * verification script imports this module instead of the bare package name. The
 * alternative — copying the scripts into `frontend/` — would put verification
 * tooling inside the thing it verifies.
 *
 *   import { chromium } from './_playwright.mjs'
 */

import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const entry = join(HERE, '..', 'frontend', 'node_modules', 'playwright-core')

if (!existsSync(entry)) {
  throw new Error(
    `playwright-core not found at ${entry}.\n` +
      'Run `npm install` in frontend/ first — the browser verification scripts use its ' +
      'dev dependency rather than installing a second copy.'
  )
}

// `createRequire` rather than a dynamic `import`: playwright-core is CommonJS, and a
// dynamic import of it from ESM yields `{ default: … }` with the named exports not
// statically detectable — `chromium` comes back undefined, which fails at the call
// site rather than at the import.
export const { chromium } = createRequire(import.meta.url)(entry)
