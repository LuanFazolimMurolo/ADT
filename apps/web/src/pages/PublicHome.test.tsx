import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PublicHome } from './PublicHome'

const mocks = vi.hoisted(() => ({
  getSystemStatus: vi.fn(),
  getPublicSimulation: vi.fn(),
  getPublicOfficialPaper: vi.fn(),
}))

vi.mock('../http/client', () => ({
  apiClient: {
    getSystemStatus: mocks.getSystemStatus,
    getPublicSimulation: mocks.getPublicSimulation,
    getPublicOfficialPaper: mocks.getPublicOfficialPaper,
  },
}))

function renderHome() {
  return render(
    <MemoryRouter>
      <PublicHome />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  mocks.getSystemStatus.mockResolvedValue({
    status: 'operational',
    version: '0.1.0',
    environment: 'test',
    timestamp: '2026-08-08T12:00:00Z',
  })
  mocks.getPublicSimulation.mockResolvedValue(null)
  mocks.getPublicOfficialPaper.mockResolvedValue({
    observed_at: '2026-09-29T20:00:00Z',
    limit: 20,
    count: 0,
    items: [],
  })
})

describe('PublicHome', () => {
  it('renderiza a apresentação institucional e o loading público sem autenticação', () => {
    mocks.getSystemStatus.mockReturnValue(new Promise(() => undefined))
    mocks.getPublicSimulation.mockReturnValue(new Promise(() => undefined))
    mocks.getPublicOfficialPaper.mockReturnValue(new Promise(() => undefined))

    renderHome()

    expect(
      screen.getByRole('heading', {
        level: 1,
        name: /Pesquisa disciplinada.*Evidência verificável/i,
      }),
    ).toBeDefined()
    expect(screen.getAllByText(/Automatic Dry Trade/i)).not.toHaveLength(0)
    expect(screen.getByText('Verificando disponibilidade')).toBeDefined()
    expect(screen.getByText('Carregando simulação pública…')).toBeDefined()
  })

  it('mantém o runtime PAPER oficial em loading enquanto o contrato não responde', () => {
    mocks.getSystemStatus.mockReturnValue(new Promise(() => undefined))
    mocks.getPublicSimulation.mockReturnValue(new Promise(() => undefined))
    mocks.getPublicOfficialPaper.mockReturnValue(new Promise(() => undefined))

    renderHome()

    expect(screen.getByText('Consultando runtime oficial…')).toBeDefined()
  })

  it('mostra o estado operacional retornado pelo endpoint público', async () => {
    renderHome()

    expect(await screen.findByText('API operacional')).toBeDefined()
    expect(screen.getByText('Ambiente test · versão 0.1.0')).toBeDefined()
  })

  it('mantém conteúdo seguro quando o status público está indisponível', async () => {
    mocks.getSystemStatus.mockRejectedValue(
      new Error('Traceback at http://127.0.0.1:43102/internal'),
    )

    renderHome()

    expect(
      await screen.findByText('API temporariamente indisponível'),
    ).toBeDefined()
    expect(screen.getByRole('heading', { level: 1 })).toBeDefined()
    expect(document.body.textContent).not.toContain('Traceback')
    expect(document.body.textContent).not.toContain('43102')
  })

  it('exibe somente a projeção permitida da simulação ACTIVE como capital simulado', async () => {
    mocks.getPublicSimulation.mockResolvedValue({
      name: 'Dry Run público',
      currency: 'USD',
      initial_capital: '10000.00000000',
      current_balance: '9749.50000000',
      total_profit_loss: '-250.50000000',
      started_at: '2026-07-29T15:00:00Z',
      status: 'ACTIVE',
      id: 'private-simulation-uuid',
      admin_id: 'private-admin-uuid',
      strategy_configuration: 'private-strategy-configuration',
    })

    renderHome()

    expect(await screen.findByText('Dry Run público')).toBeDefined()
    expect(screen.getByText('Capital simulado')).toBeDefined()
    expect(screen.getByText('Não representa capital real')).toBeDefined()
    expect(screen.getByText('USD 10.000,00')).toBeDefined()
    expect(screen.getByText('USD 9.749,50')).toBeDefined()
    expect(screen.getByText('USD -250,50')).toBeDefined()
    expect(screen.getByText('ACTIVE')).toBeDefined()
    expect(document.body.textContent).not.toContain('private-simulation-uuid')
    expect(document.body.textContent).not.toContain('private-admin-uuid')
    expect(document.body.textContent).not.toContain(
      'private-strategy-configuration',
    )
  })

  it('mostra estado vazio quando não há simulação pública ativa', async () => {
    renderHome()

    expect(
      await screen.findByRole('heading', {
        level: 3,
        name: 'Nenhuma simulação pública ativa no momento.',
      }),
    ).toBeDefined()
  })

  it('isola falha da simulação sem derrubar a landing ou expor o erro', async () => {
    mocks.getPublicSimulation.mockRejectedValue(
      new Error('database password at /private/path'),
    )

    renderHome()

    expect(
      await screen.findByRole('heading', {
        level: 3,
        name: 'Simulação pública temporariamente indisponível.',
      }),
    ).toBeDefined()
    expect(await screen.findByText('API operacional')).toBeDefined()
    expect(screen.getByRole('heading', { level: 1 })).toBeDefined()
    expect(document.body.textContent).not.toContain('database password')
    expect(document.body.textContent).not.toContain('/private/path')
  })

  it('exibe somente o snapshot operacional permitido do PAPER oficial', async () => {
    mocks.getPublicOfficialPaper.mockResolvedValue({
      observed_at: '2026-09-29T20:00:00Z',
      limit: 20,
      count: 1,
      items: [
        {
          session_id: 'public-session-01',
          desired_state: 'RUNNING',
          observed_state: 'RUNNING',
          availability: 'RUNNING',
          ownership_fresh: true,
          heartbeat_at: '2026-09-29T19:59:55Z',
          lease_expires_at: '2026-09-29T20:00:30Z',
          failure_code: null,
        },
      ],
    })

    renderHome()

    expect(
      await screen.findByRole('heading', {
        level: 2,
        name: 'Runtime PAPER oficial',
      }),
    ).toBeDefined()

    expect(await screen.findByText('Em execução')).toBeDefined()
    expect(screen.getByText('public-session-01')).toBeDefined()
    expect(screen.getByText('Válido')).toBeDefined()
    expect(screen.queryByText('Código de falha')).toBeNull()
  })

  it('mostra estado vazio quando não há runtime PAPER oficial ativo', async () => {
    renderHome()

    expect(
      await screen.findByRole('heading', {
        level: 3,
        name: 'Nenhuma sessão PAPER oficial ativa no momento.',
      }),
    ).toBeDefined()
  })

  it('isola falha do runtime PAPER oficial sem expor detalhes internos', async () => {
    mocks.getPublicOfficialPaper.mockRejectedValue(
      new Error('postgres://secret@private-host/internal'),
    )

    renderHome()

    expect(
      await screen.findByRole('heading', {
        level: 3,
        name: 'Runtime oficial temporariamente indisponível.',
      }),
    ).toBeDefined()

    expect(document.body.textContent).not.toContain('private-host')
    expect(document.body.textContent).not.toContain('postgres://')
  })

  it('oferece somente login geral e chama apenas os três contratos públicos', async () => {
    renderHome()

    const login = screen.getByRole('link', { name: 'Entrar' })
    expect(login.getAttribute('href')).toBe('/login')
    expect(
      screen.queryByRole('link', { name: /criar conta|registrar|sign up/i }),
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: /criar conta|registrar|sign up/i }),
    ).toBeNull()
    expect(screen.queryByRole('link', { name: /admin/i })).toBeNull()

    await waitFor(() => {
      expect(mocks.getSystemStatus).toHaveBeenCalledOnce()
      expect(mocks.getPublicSimulation).toHaveBeenCalledOnce()
      expect(mocks.getPublicOfficialPaper).toHaveBeenCalledOnce()
    })
  })
})
