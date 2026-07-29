import { describe, expect, it } from 'vitest'
import { PublicConfigError, validatePublicConfig } from './env'

describe('configuração pública', () => {
  it('rejeita variáveis ausentes sem exibir valores', () => {
    expect(() => validatePublicConfig({})).toThrow(PublicConfigError)
    try {
      validatePublicConfig({ VITE_ADT_API_URL: 'secret-looking-value' })
    } catch (error) {
      expect(String(error)).not.toContain('secret-looking-value')
    }
  })

  it('aceita somente as três variáveis públicas obrigatórias', () => {
    const config = validatePublicConfig({
      VITE_ADT_API_URL: 'http://localhost:8000/',
      VITE_SUPABASE_URL: 'https://example.supabase.co/',
      VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_test',
    })
    expect(config.apiUrl).toBe('http://localhost:8000')
    expect(config.supabasePublishableKey).toBe('sb_publishable_test')
  })
})
