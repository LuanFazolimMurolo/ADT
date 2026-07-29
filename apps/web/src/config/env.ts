export interface PublicConfig {
  apiUrl: string
  supabaseUrl: string
  supabasePublishableKey: string
}

export class PublicConfigError extends Error {
  readonly missingVariables: string[]

  constructor(missingVariables: string[]) {
    super('A configuração pública do frontend está incompleta.')
    this.name = 'PublicConfigError'
    this.missingVariables = missingVariables
  }
}

type PublicEnvironment = Record<string, string | boolean | undefined>

export function validatePublicConfig(
  environment: PublicEnvironment = import.meta.env,
): PublicConfig {
  const values = {
    apiUrl: environment.VITE_ADT_API_URL,
    supabaseUrl: environment.VITE_SUPABASE_URL,
    supabasePublishableKey: environment.VITE_SUPABASE_PUBLISHABLE_KEY,
  }
  const names: Record<keyof typeof values, string> = {
    apiUrl: 'VITE_ADT_API_URL',
    supabaseUrl: 'VITE_SUPABASE_URL',
    supabasePublishableKey: 'VITE_SUPABASE_PUBLISHABLE_KEY',
  }
  const missingVariables = (Object.keys(values) as Array<keyof typeof values>)
    .filter((key) => typeof values[key] !== 'string' || !values[key]?.trim())
    .map((key) => names[key])

  if (missingVariables.length > 0) {
    throw new PublicConfigError(missingVariables)
  }

  try {
    return {
      apiUrl: new URL(values.apiUrl as string).toString().replace(/\/$/, ''),
      supabaseUrl: new URL(values.supabaseUrl as string).toString().replace(/\/$/, ''),
      supabasePublishableKey: (values.supabasePublishableKey as string).trim(),
    }
  } catch {
    throw new PublicConfigError(['VITE_ADT_API_URL ou VITE_SUPABASE_URL (URL inválida)'])
  }
}

let cachedConfig: PublicConfig | undefined

export function getPublicConfig(): PublicConfig {
  cachedConfig ??= validatePublicConfig()
  return cachedConfig
}
