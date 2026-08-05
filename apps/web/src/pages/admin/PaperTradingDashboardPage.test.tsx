import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { PaperDashboardResponse, PaperDashboardSession } from '../../types/api'
import { PaperTradingDashboardPage } from './PaperTradingDashboardPage'

const mocks = vi.hoisted(() => ({
  getPaperTradingDashboard: vi.fn(),
}))

vi.mock('../../http/client', () => ({ apiClient: mocks }))

function session(id: string, symbol: string, pnl: string): PaperDashboardSession {
  return {
    session_id: id.repeat(64).slice(0, 64),
    symbol,
    base_asset: symbol.slice(0, 3),
    quote_asset: 'USDT',
    timeframe: '1m',
    strategy_name: 'no-op',
    strategy_version: '2',
    initial_capital: '1000',
    state_available: true,
    candles_processed: 25,
    last_candle_open_time: '2026-08-04T23:00:00Z',
    replayed_at: '2026-08-04T23:01:00Z',
    orders_count: 2,
    fills_count: 1,
    open_orders_count: 1,
    risk_halt: false,
    metrics: {
      initial_capital: '1000',
      equity: pnl.startsWith('-') ? '950' : '1100',
      total_pnl: pnl,
      return_pct: pnl.startsWith('-') ? '-5' : '10',
      realized_pnl: pnl,
      unrealized_pnl: '0',
      drawdown: '10',
      drawdown_pct: '1',
      total_fees: '1',
      total_slippage_cost: '0.5',
    },
    portfolio: {
      quote_cash: '900',
      base_quantity: '1',
      average_entry_price: '100',
      realized_pnl: pnl,
      unrealized_pnl: '0',
      total_fees: '1',
      total_slippage_cost: '0.5',
      equity: pnl.startsWith('-') ? '950' : '1100',
      peak_equity: '1110',
      drawdown: '10',
      drawdown_pct: '1',
      cost_basis: '100',
    },
    position: {
      is_open: true,
      base_quantity: '1',
      average_entry_price: '100',
      cost_basis: '100',
      market_value: '200',
    },
    latest_market_regime: {
      event_time: '2026-08-04T23:00:59Z',
      regime: 'trend',
      trend_direction: 'up',
      fast_ema: '105',
      slow_ema: '100',
      atr: '2',
      atr_ratio: '0.02',
      trend_strength: '2.5',
    },
    runner: {
      status: 'UPDATED',
      started_at: '2026-08-04T23:00:00Z',
      finished_at: '2026-08-04T23:01:00Z',
      state_id: 'a'.repeat(64),
      candles_processed: 25,
      last_candle_open_time: '2026-08-04T23:00:00Z',
      error_code: null,
      matches_current_state: true,
    },
  }
}

const dashboard: PaperDashboardResponse = {
  items: [session('a', 'BTCUSDT', '100'), session('b', 'ETHUSDT', '-50')],
  totals: {
    scope: 'page',
    sessions_count: 2,
    initialized_count: 2,
    pending_count: 0,
    runner_failed_count: 0,
    risk_halted_count: 0,
    open_positions_count: 2,
    open_orders_count: 2,
    configured_capital: '2000',
    initialized_capital: '2000',
    equity: '2050',
    total_pnl: '50',
    return_pct: '2.5',
    maximum_drawdown_pct: '1',
  },
  page: 1,
  page_size: 20,
  total: 2,
  total_pages: 1,
  runner: {
    cycle_index: 7,
    status: 'COMPLETED',
    finished_at: '2026-08-04T23:01:00Z',
    next_cycle_at: '2026-08-04T23:01:30Z',
  },
}

beforeEach(() => {
  mocks.getPaperTradingDashboard.mockResolvedValue(dashboard)
})

describe('PaperTradingDashboardPage', () => {
  it('renderiza totais, runner e sessões sem misturar uma moeda agregada', async () => {
    render(<MemoryRouter><PaperTradingDashboardPage /></MemoryRouter>)

    expect(await screen.findByText('Performance agregada')).toBeDefined()
    expect(screen.getByText('Ciclo 7')).toBeDefined()
    expect(screen.getByText('BTCUSDT')).toBeDefined()
    expect(screen.getByText('ETHUSDT')).toBeDefined()
    expect(screen.getByText(/Valores nominais/)).toBeDefined()
    expect(screen.queryByText('USDT 2.050,00')).toBeNull()
    expect(mocks.getPaperTradingDashboard).toHaveBeenCalledWith(1, 20)
  })

  it('compara no máximo duas sessões carregadas', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><PaperTradingDashboardPage /></MemoryRouter>)

    const toggles = await screen.findAllByLabelText('Comparar')
    await user.click(toggles[0])
    await user.click(toggles[1])

    const comparison = screen.getByRole('region', { name: 'Sessões selecionadas' })
    expect(within(comparison).getByText(/BTCUSDT/)).toBeDefined()
    expect(within(comparison).getByText(/ETHUSDT/)).toBeDefined()
    expect(within(comparison).getByText('Duas sessões selecionadas')).toBeDefined()
  })

  it('permite atualização manual read-only', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><PaperTradingDashboardPage /></MemoryRouter>)

    await screen.findByText('BTCUSDT')
    await user.click(screen.getByRole('button', { name: 'Atualizar agora' }))

    await waitFor(() => expect(mocks.getPaperTradingDashboard).toHaveBeenCalledTimes(2))
  })

  it('mostra estado vazio quando nenhuma sessão existe', async () => {
    mocks.getPaperTradingDashboard.mockResolvedValue({
      ...dashboard,
      items: [],
      total: 0,
      total_pages: 0,
      totals: {
        ...dashboard.totals,
        sessions_count: 0,
        initialized_count: 0,
        open_positions_count: 0,
        open_orders_count: 0,
        configured_capital: '0',
        initialized_capital: '0',
        equity: '0',
        total_pnl: '0',
        return_pct: '0',
        maximum_drawdown_pct: '0',
      },
    })

    render(<MemoryRouter><PaperTradingDashboardPage /></MemoryRouter>)
    expect(await screen.findByText('Nenhuma sessão de paper trading')).toBeDefined()
  })
})
