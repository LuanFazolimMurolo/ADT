import { readFile, writeFile } from 'node:fs/promises'

const outputPath = process.argv[2]
if (!outputPath) {
  throw new Error('usage: normalize-openapi-types.mjs OUTPUT_PATH')
}

let source = await readFile(outputPath, 'utf8')
const insertionPoint = 'export interface paths'
if (!source.includes(insertionPoint)) {
  throw new Error('Unexpected openapi-typescript output: paths marker not found')
}

source = source.replace(
  insertionPoint,
  `export type OpenApiJsonValue =
    | null
    | boolean
    | number
    | string
    | OpenApiJsonValue[]
    | { [key: string]: OpenApiJsonValue };

${insertionPoint}`,
)

for (const mode of ['Input', 'Output']) {
  const generatedRecursiveType = `        "JsonValue-${mode}": {
            [key: string]: components["schemas"]["JsonValue-${mode}"];
        } | components["schemas"]["JsonValue-${mode}"][] | string | number | boolean | null;`
  const normalizedType = `        "JsonValue-${mode}": OpenApiJsonValue;`
  if (!source.includes(generatedRecursiveType)) {
    throw new Error(
      `Unexpected openapi-typescript output: JsonValue-${mode} marker not found`,
    )
  }
  source = source.replace(generatedRecursiveType, normalizedType)
}

await writeFile(outputPath, source, 'utf8')
