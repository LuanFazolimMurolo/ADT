import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppLayout } from './AppLayout'

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: mocks.useAuth,
}))

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/app']}>
      <Routes>
        <Route path="/app" element={<AppLayout />}>
          <Route index element={<h1>Início autenticado</h1>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  mocks.useAuth.mockReturnValue({
    identity: {
      user_id: '11111111-1111-4111-8111-111111111111',
      is_admin: false,
    },
    signOut: vi.fn(),
  })
})

describe('AppLayout', () => {
  it('mostra o boundary read-only sem link administrativo para usuário comum', () => {
    renderLayout()
    expect(screen.getByText(/Paper trading somente/i)).toBeDefined()
    expect(screen.getByRole('link', { name: 'Mercado' }).getAttribute('href')).toBe(
      '/app/market',
    )
    expect(screen.getByRole('link', { name: 'Sessões' }).getAttribute('href')).toBe(
      '/app/sessions',
    )
    expect(screen.queryByRole('link', { name: 'Administração' })).toBeNull()
  })

  it('mostra link explícito para administração somente quando autorizado', () => {
    mocks.useAuth.mockReturnValue({
      identity: {
        user_id: '22222222-2222-4222-8222-222222222222',
        is_admin: true,
      },
      signOut: vi.fn(),
    })

    renderLayout()

    expect(
      screen.getByRole('link', { name: 'Administração' }).getAttribute('href'),
    ).toBe('/admin')
    expect(screen.getByRole('link', { name: 'Mercado' }).getAttribute('href')).toBe(
      '/app/market',
    )
    expect(screen.getByRole('link', { name: 'Sessões' }).getAttribute('href')).toBe(
      '/app/sessions',
    )
  })
})
