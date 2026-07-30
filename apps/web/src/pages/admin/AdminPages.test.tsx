import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'
import { SettingsPage } from './SettingsPage'
import { SimulationDetailPage } from './SimulationDetailPage'
import { SimulationsPage } from './SimulationsPage'

const mocks = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getDatabaseHealth: vi.fn(),
  listSimulations: vi.fn(),
  createSimulation: vi.fn(),
  getSimulation: vi.fn(),
  listMovements: vi.fn(),
  createMovement: vi.fn(),
  completeSimulation: vi.fn(),
  cancelSimulation: vi.fn(),
  listSettings: vi.fn(),
  updateSetting: vi.fn(),
}))

vi.mock('../../http/client', () => {
  class ApiError extends Error {
    constructor(public status: number, public code: string, message: string) {
      super(message)
    }
  }
  return { ApiError, apiClient: mocks }
})

const simulation = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Simulação principal',
  status: 'ACTIVE' as const,
  currency: 'USD',
  initial_capital: '1000.00',
  current_balance: '1100.00',
  total_profit_loss: '100.00',
  started_at: '2026-07-29T12:00:00Z',
  ended_at: null,
  created_at: '2026-07-29T12:00:00Z',
  updated_at: '2026-07-29T12:00:00Z',
  created_by: '22222222-2222-4222-8222-222222222222',
}

const emptyPagination = { page: 1, page_size: 10, total: 0, total_pages: 0 }

beforeEach(() => {
  mocks.getHealth.mockResolvedValue({ status: 'healthy' })
  mocks.getDatabaseHealth.mockResolvedValue({ status: 'healthy' })
  mocks.listSimulations.mockResolvedValue({ items: [], pagination: emptyPagination })
  mocks.createSimulation.mockResolvedValue(simulation)
  mocks.getSimulation.mockResolvedValue(simulation)
  mocks.listMovements.mockResolvedValue({ items: [], pagination: emptyPagination })
  mocks.createMovement.mockResolvedValue({ id: 'movement-id' })
  mocks.completeSimulation.mockResolvedValue({ ...simulation, status: 'COMPLETED' })
  mocks.cancelSimulation.mockResolvedValue({ ...simulation, status: 'CANCELLED' })
  mocks.listSettings.mockResolvedValue({ items: [] })
  mocks.updateSetting.mockResolvedValue({})
})

describe('dashboard e simulações', () => {
  it('exibe estado vazio quando não há simulação ativa', async () => {
    render(<MemoryRouter><DashboardPage /></MemoryRouter>)
    expect(await screen.findByText('Nenhuma simulação ativa')).toBeDefined()
    expect(screen.getByText('Criar primeira simulação')).toBeDefined()
  })

  it('cria simulação após confirmação', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/admin/simulations?create=true']}>
        <Routes><Route path="/admin/simulations" element={<SimulationsPage />} /></Routes>
      </MemoryRouter>,
    )
    await user.type(screen.getByLabelText('Nome'), 'Ciclo novo')
    await user.type(screen.getByLabelText('Capital inicial'), '5000.25')
    await user.clear(screen.getByLabelText('Moeda'))
    await user.type(screen.getByLabelText('Moeda'), 'BRL')
    await user.click(screen.getByRole('button', { name: 'Revisar e criar' }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Criar simulação' }))
    await waitFor(() => expect(mocks.createSimulation).toHaveBeenCalledWith({
      name: 'Ciclo novo',
      initial_capital: '5000.25',
      currency: 'BRL',
    }))
  })

  it('rejeita capital fora de numeric(20, 8) antes da confirmação', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/admin/simulations?create=true']}>
        <Routes><Route path="/admin/simulations" element={<SimulationsPage />} /></Routes>
      </MemoryRouter>,
    )
    await user.type(screen.getByLabelText('Nome'), 'Capital inválido')
    await user.type(screen.getByLabelText('Capital inicial'), '1000000000000')
    await user.click(screen.getByRole('button', { name: 'Revisar e criar' }))

    expect(await screen.findByText(/deve ser menor que 1000000000000/)).toBeDefined()
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(mocks.createSimulation).not.toHaveBeenCalled()
  })

  it('descreve P/L como resultado de trades e taxas', async () => {
    mocks.listSimulations.mockResolvedValue({
      items: [simulation],
      pagination: { page: 1, page_size: 100, total: 1, total_pages: 1 },
    })
    render(<MemoryRouter><DashboardPage /></MemoryRouter>)

    expect(await screen.findByText('Resultado de trades e taxas')).toBeDefined()
    expect(screen.queryByText('Movimentos acumulados')).toBeNull()
  })
})

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={[`/admin/simulations/${simulation.id}`]}>
      <Routes><Route path="/admin/simulations/:simulationId" element={<SimulationDetailPage />} /></Routes>
    </MemoryRouter>,
  )
}

async function fillMovement(user: ReturnType<typeof userEvent.setup>, type: 'Depósito' | 'Retirada' | 'Ajuste', amount = '25') {
  await screen.findByText('Novo movimento')
  await user.selectOptions(screen.getByLabelText('Tipo'), type)
  await user.type(screen.getByLabelText('Valor absoluto'), amount)
  await user.type(screen.getByLabelText('Motivo'), 'Teste administrativo')
  await user.click(screen.getByRole('button', { name: 'Registrar movimento' }))
}

describe('movimentos e encerramento', () => {
  it('cria depósito com valor positivo', async () => {
    const user = userEvent.setup()
    renderDetail()
    await fillMovement(user, 'Depósito')
    await waitFor(() => expect(mocks.createMovement).toHaveBeenCalledWith(simulation.id, expect.objectContaining({
      type: 'DEPOSIT',
      amount: '25',
    })))
  })

  it('cria retirada com sinal negativo somente após confirmação', async () => {
    const user = userEvent.setup()
    renderDetail()
    await fillMovement(user, 'Retirada')
    expect(mocks.createMovement).not.toHaveBeenCalled()
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Registrar movimento' }))
    await waitFor(() => expect(mocks.createMovement).toHaveBeenCalledWith(simulation.id, expect.objectContaining({
      type: 'WITHDRAWAL',
      amount: '-25',
    })))
  })

  it('mostra erro claro para saldo insuficiente', async () => {
    const { ApiError } = await import('../../http/client')
    mocks.createMovement.mockRejectedValue(new ApiError(409, 'insufficient_balance', 'Saldo insuficiente.'))
    const user = userEvent.setup()
    renderDetail()
    await fillMovement(user, 'Retirada', '9999')
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Registrar movimento' }))
    expect(await screen.findByText('Saldo insuficiente para realizar esta retirada.')).toBeDefined()
  })

  it('rejeita movimento fora de numeric(20, 8) antes da chamada', async () => {
    const user = userEvent.setup()
    renderDetail()
    await fillMovement(user, 'Depósito', '1000000000000')

    expect(await screen.findByText(/deve ser menor que 1000000000000 em magnitude/)).toBeDefined()
    expect(mocks.createMovement).not.toHaveBeenCalled()
  })

  it('preserva o sinal de um ajuste válido sem conversão numérica', async () => {
    const user = userEvent.setup()
    renderDetail()
    await screen.findByText('Novo movimento')
    await user.selectOptions(screen.getByLabelText('Tipo'), 'ADJUSTMENT')
    await user.type(screen.getByLabelText('Valor assinado'), '-25.125')
    await user.type(screen.getByLabelText('Motivo'), 'Correção auditável')
    await user.click(screen.getByRole('button', { name: 'Registrar movimento' }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Registrar movimento' }))

    await waitFor(() => expect(mocks.createMovement).toHaveBeenCalledWith(
      simulation.id,
      expect.objectContaining({
        type: 'ADJUSTMENT',
        amount: '-25.125',
      }),
    ))
  })

  it('encerra a simulação como COMPLETED após confirmação', async () => {
    const user = userEvent.setup()
    renderDetail()
    await user.click(await screen.findByRole('button', { name: 'Marcar como concluída' }))
    await user.click(screen.getByRole('button', { name: 'Concluir simulação' }))
    await waitFor(() => expect(mocks.completeSimulation).toHaveBeenCalledWith(simulation.id))
  })

  it('cancela a simulação como CANCELLED após confirmação', async () => {
    const user = userEvent.setup()
    renderDetail()
    await user.click(await screen.findByRole('button', { name: 'Cancelar simulação' }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cancelar simulação' }))
    await waitFor(() => expect(mocks.cancelSimulation).toHaveBeenCalledWith(simulation.id))
  })
})

describe('configurações', () => {
  it('atualiza somente o value após confirmação e recarrega os dados', async () => {
    mocks.listSettings.mockResolvedValue({
      items: [{
        key: 'display_currency',
        value: 'USD',
        description: 'Moeda exibida por padrão.',
        is_public: true,
        updated_by: null,
        created_at: '2026-07-29T12:00:00Z',
        updated_at: '2026-07-29T12:00:00Z',
      }],
    })
    const user = userEvent.setup()
    render(<SettingsPage />)
    const input = await screen.findByLabelText('Valor')
    await user.clear(input)
    await user.type(input, 'BRL')
    await user.click(screen.getByRole('button', { name: 'Salvar alteração' }))
    await user.click(screen.getByRole('button', { name: 'Salvar valor' }))
    await waitFor(() => expect(mocks.updateSetting).toHaveBeenCalledWith('display_currency', 'BRL'))
    expect(mocks.listSettings).toHaveBeenCalledTimes(2)
  })
})
