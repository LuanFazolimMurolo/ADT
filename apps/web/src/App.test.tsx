import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('./lib/supabase', () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session: null }, error: null }),
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
    constructor(public status: number, public code: string, message: string) {
      super(message)
    }
  }
  return {
    ApiError,
    apiClient: {
      getSystemStatus: vi.fn().mockResolvedValue({
        status: 'healthy',
        version: '0.1.0',
        environment: 'development',
        timestamp: new Date().toISOString(),
      }),
    },
  }
})

describe('site público', () => {
  it('mantém o site público sem login ou cadastro visível', async () => {
    render(<App />)
    expect(screen.getByText(/Decisões frias/)).toBeDefined()
    expect(screen.getByRole('heading', { name: 'Paper trading' })).toBeDefined()
    expect(screen.queryByText('Entrar')).toBeNull()
    expect(screen.queryByText(/cadastro/i)?.textContent).toContain('sem cadastro público')
  })
})
