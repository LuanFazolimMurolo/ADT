import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, renameSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'

const checkOnly = process.argv.includes('--check')
const webRoot = process.cwd()
const contractPath = resolve(webRoot, '.openapi.tmp.json')
const temporaryTypesPath = resolve(webRoot, '.openapi.generated.tmp.ts')
const generatedTypesPath = resolve(webRoot, 'src/types/openapi.generated.ts')
const pythonPath = resolve(webRoot, '../../services/backend/.venv/bin/python')
const exporterPath = resolve(
  webRoot,
  '../../services/backend/scripts/export_openapi.py',
)
const openApiTypescriptPath = resolve(
  webRoot,
  'node_modules/.bin/openapi-typescript',
)
const normalizerPath = resolve(
  webRoot,
  'scripts/normalize-openapi-types.mjs',
)

try {
  execFileSync(pythonPath, [exporterPath, contractPath], { stdio: 'inherit' })
  execFileSync(
    openApiTypescriptPath,
    [contractPath, '--output', temporaryTypesPath],
    { stdio: 'inherit' },
  )
  execFileSync(process.execPath, [normalizerPath, temporaryTypesPath], {
    stdio: 'inherit',
  })

  if (checkOnly) {
    if (
      !existsSync(generatedTypesPath) ||
      readFileSync(temporaryTypesPath, 'utf8') !==
        readFileSync(generatedTypesPath, 'utf8')
    ) {
      throw new Error(
        'OpenAPI contract is stale. Run npm run generate:api and review the result.',
      )
    }
  } else {
    renameSync(temporaryTypesPath, generatedTypesPath)
  }
} finally {
  rmSync(contractPath, { force: true })
  rmSync(temporaryTypesPath, { force: true })
}
