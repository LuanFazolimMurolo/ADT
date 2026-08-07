import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PaperTradeJournalPageResponse } from "../../types/api";
import { PaperTradeJournalPage } from "./PaperTradeJournalPage";

const mocks = vi.hoisted(() => ({
  getPaperTradeJournal: vi.fn(),
}));

vi.mock("../../http/client", () => ({ apiClient: mocks }));

const journal: PaperTradeJournalPageResponse = {
  filters: {
    session_id: null,
    base_asset: null,
    quote_asset: null,
    timeframe: null,
    strategy_name: null,
    strategy_version: null,
    status: null,
    opened_from: null,
    opened_before: null,
    closed_from: null,
    closed_before: null,
  },
  items: [
    {
      session_id: "a".repeat(64),
      config_checksum: "b".repeat(64),
      state_id: "c".repeat(64),
      state_checksum: "d".repeat(64),
      symbol: "BTC/USDT",
      base_asset: "BTC",
      quote_asset: "USDT",
      timeframe: "1m",
      strategy_name: "paper-journal-test",
      strategy_version: "1",
      strategy_parameters: {},
      last_candle_open_time: "2026-08-05T20:00:00Z",
      replayed_at: "2026-08-05T20:01:00Z",
      trade: {
        trade_id: "e".repeat(64),
        session_id: "a".repeat(64),
        sequence: 2,
        status: "OPEN",
        opened_at: "2026-08-05T19:00:00Z",
        last_entry_at: "2026-08-05T19:00:00Z",
        first_exit_at: null,
        closed_at: null,
        entry_executions: [
          {
            fill_id: "fill-b",
            order_id: "order-b",
            order_sequence: 3,
            side: "BUY",
            order_type: "MARKET",
            time_in_force: "GTC",
            client_tag: "entry-b",
            fill_reason: "MARKET_OPEN",
            liquidity: "TAKER",
            quantity: "0.5",
            base_price: "140",
            execution_price: "140",
            notional: "70",
            fee: "0.07",
            slippage_cost: "0",
            event_time: "2026-08-05T19:00:00Z",
            candle_index: 4,
          },
        ],
        exit_executions: [],
        opened_quantity: "0.5",
        closed_quantity: "0",
        remaining_quantity: "0.5",
        entry_notional: "70",
        exit_notional: "0",
        entry_fees: "0.07",
        exit_fees: "0",
        total_fees: "0.07",
        entry_slippage_cost: "0",
        exit_slippage_cost: "0",
        total_slippage_cost: "0",
        entry_cost_basis: "70.07",
        released_cost_basis: "0",
        remaining_cost_basis: "70.07",
        average_entry_price: "140",
        average_exit_price: null,
        realized_pnl: "0",
        unrealized_pnl: "4.93",
        net_pnl: "4.93",
        mark_price: "150",
      },
    },
  ],
  page: 1,
  page_size: 20,
  total: 1,
  total_pages: 1,
  totals: {
    trades_count: 1,
    closed_trades_count: 0,
    open_trades_count: 1,
    total_realized_pnl: "0",
    total_unrealized_pnl: "4.93",
    total_net_pnl: "4.93",
    total_fees: "0.07",
    total_slippage_cost: "0",
  },
};

beforeEach(() => {
  mocks.getPaperTradeJournal.mockResolvedValue(journal);
});

describe("PaperTradeJournalPage", () => {
  it("renderiza operações verificadas e totais nominais", async () => {
    render(
      <MemoryRouter>
        <PaperTradeJournalPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("BTC/USDT")).toBeDefined();
    const operations = screen.getByRole("region", { name: "Operações" });
    expect(within(operations).getByText("Aberta")).toBeDefined();
    expect(within(operations).getAllByText("USDT 4,93")).toHaveLength(2);
    expect(screen.getByText(/Valores podem usar ativos/)).toBeDefined();
    expect(mocks.getPaperTradeJournal).toHaveBeenCalledWith({}, 1, 20);
    expect(
      screen.getByRole("link", { name: "Abrir no gráfico" }),
    ).toBeDefined();
  });

  it("inicializa o filtro de sessão pela URL", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          `/admin/paper-trading/journal?session_id=${"a".repeat(64)}&trade_id=${"e".repeat(64)}`,
        ]}
      >
        <PaperTradeJournalPage />
      </MemoryRouter>,
    );

    await screen.findByText("BTC/USDT");
    expect(mocks.getPaperTradeJournal).toHaveBeenCalledWith(
      { sessionId: "a".repeat(64) },
      1,
      20,
    );
    expect(screen.getByDisplayValue("a".repeat(64))).toBeDefined();
  });

  it("aplica filtros somente após submissão", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <PaperTradeJournalPage />
      </MemoryRouter>,
    );

    await screen.findByText("BTC/USDT");
    await user.type(screen.getByLabelText("Ativo base"), "btc");
    await user.selectOptions(screen.getByLabelText("Status"), "OPEN");
    await user.click(screen.getByRole("button", { name: "Aplicar filtros" }));

    await waitFor(() =>
      expect(mocks.getPaperTradeJournal).toHaveBeenLastCalledWith(
        expect.objectContaining({
          baseAsset: "btc",
          status: "OPEN",
        }),
        1,
        20,
      ),
    );
  });

  it("mostra estado vazio sem inventar operações", async () => {
    mocks.getPaperTradeJournal.mockResolvedValue({
      ...journal,
      items: [],
      total: 0,
      total_pages: 0,
      totals: {
        ...journal.totals,
        trades_count: 0,
        open_trades_count: 0,
        total_unrealized_pnl: "0",
        total_net_pnl: "0",
        total_fees: "0",
      },
    });

    render(
      <MemoryRouter>
        <PaperTradeJournalPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("Nenhuma operação encontrada"),
    ).toBeDefined();
  });
});
