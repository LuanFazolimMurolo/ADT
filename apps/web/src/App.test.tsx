import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from './App'

// Mock the API client
vi.mock('./http/client', () => ({
  apiClient: {
    getSystemStatus: vi.fn(() =>
      Promise.resolve({
        status: 'healthy',
        version: '0.0.0',
        environment: 'development',
        timestamp: new Date().toISOString(),
      }),
    ),
  },
}))

describe('App', () => {
  it('renders the title', async () => {
    render(<App />)
    expect(screen.getByText('ADT')).toBeDefined()
  })

  it('renders the subtitle', async () => {
    render(<App />)
    expect(screen.getByText('Automatic Dry Trade')).toBeDefined()
  })

  it('renders component cards', async () => {
    render(<App />)
    expect(screen.getByText('API')).toBeDefined()
    expect(screen.getByText('Supabase')).toBeDefined()
    expect(screen.getByText('Market Data')).toBeDefined()
    expect(screen.getByText('Workers')).toBeDefined()
  })
})
