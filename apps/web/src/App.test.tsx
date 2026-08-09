import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('./lib/supabase', () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: vi
        .fn()
        .mockResolvedValue({ data: { session: null }, error: null }),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signOut: vi.fn().mockResolvedValue({ error: null }),
      refreshSession: vi.fn(),
    },
  }),
}))

vi.mock('./http/client', async () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message)
    }
  }
  return {
    ApiError,
    apiClient: {
      getSystemStatus: vi.fn().mockResolvedValue({
        status: 'operational',
        version: '0.1.0',
        environment: 'development',
        timestamp: new Date().toISOString(),
      }),
      getPublicSimulation: vi.fn().mockResolvedValue(null),
    },
  }
})

describe('site público', () => {
  it('mantém a landing pública e oferece somente o login geral', async () => {
    render(<App />)
    await screen.findByText('API operacional')
    await screen.findByText('Nenhuma simulação pública ativa no momento.')

    expect(screen.getByText(/Pesquisa disciplinada/)).toBeDefined()
    expect(
      screen.getByRole('heading', { name: 'Paper trading público' }),
    ).toBeDefined()
    expect(
      screen.getByRole('link', { name: 'Entrar' }).getAttribute('href'),
    ).toBe('/login')
    expect(
      screen.queryByRole('link', { name: /criar conta|registrar|sign up/i }),
    ).toBeNull()
    expect(screen.queryByText(/cadastro/i)?.textContent?.toLowerCase()).toContain(
      'sem cadastro público',
    )
  })
})
