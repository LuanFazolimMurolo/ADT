import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketCandle, MarketCandlePageResponse } from "../../types/api";
import {
  AppMarketChartPage,
  MAX_LOADED_CANDLES,
  PAGE_LIMIT,
} from "./AppMarketChartPage";

const mocks = vi.hoisted(() => ({
  getAppMarketCandles: vi.fn(),
}));

vi.mock("../../http/client", () => ({
  apiClient: mocks,
}));

vi.mock("../../components/FinancialCandlestickChart", () => ({
  FinancialCandlestickChart: ({
    candles,
    fastPeriod,
    slowPeriod,
    annotations,
  }: {
    candles: readonly MarketCandle[];
    fastPeriod: number | null;
    slowPeriod: number | null;
    annotations: unknown;
  }) => (
    <div data-testid="market-chart">
      Candles visuais: {candles.length} · overlays: {String(fastPeriod)}/
      {String(slowPeriod)} · dados adicionais: {String(annotations)}
    </div>
  ),
}));

function marketCandle(openTime: string): MarketCandle {
  return {
    open_time: openTime,
    close_time: new Date(Date.parse(openTime) + 899_999).toISOString(),
    open: "100.123456789012345678",
    high: "110.223456789012345678",
    low: "90.323456789012345678",
    close: "105.423456789012345678",
    volume: "2.523456789012345678",
    quote_volume: null,
    trade_count: null,
    is_closed: true,
    source: "test_fixture",
  };
}

function page({
  items = [marketCandle("2026-08-08T00:00:00Z")],
  nextBefore = "2026-08-08T00:00:00Z",
  hasMoreBefore = true,
  datasetVersion = "a".repeat(64),
}: {
  items?: MarketCandle[];
  nextBefore?: string | null;
  hasMoreBefore?: boolean;
  datasetVersion?: string;
} = {}): MarketCandlePageResponse {
  const first = items[0]?.open_time ?? "2026-08-08T00:00:00Z";
  const last = items.at(-1)?.close_time ?? "2026-08-08T00:15:00Z";
  return {
    schema_version: 1,
    exchange: "binance",
    market_type: "spot",
    symbol: "BTC/USDT",
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe: "15m",
    requested_before: null,
    available_start: "2026-01-01T00:00:00Z",
    available_end: "2026-12-31T00:00:00Z",
    range_start: first,
    range_end: last,
    limit: PAGE_LIMIT,
    count: items.length,
    dataset_candle_count: items.length,
    dataset_version: datasetVersion,
    dataset_version_algorithm: "sha256",
    content_checksum: "b".repeat(64),
    has_more_before: hasMoreBefore,
    next_before: nextBefore,
    items,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AppMarketChartPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.getAppMarketCandles.mockReset();
  mocks.getAppMarketCandles.mockResolvedValue(page());
});

describe("AppMarketChartPage", () => {
  it("usa defaults bounded e somente o método autenticado /app", async () => {
    renderPage();

    expect(await screen.findByTestId("market-chart")).toBeDefined();
    expect(mocks.getAppMarketCandles).toHaveBeenCalledWith("BTC", "USDT", {
      timeframe: "15m",
      limit: 1000,
    });
    expect((screen.getByLabelText("Ativo base") as HTMLInputElement).value).toBe("BTC");
    expect((screen.getByLabelText("Ativo de cotação") as HTMLInputElement).value).toBe(
      "USDT",
    );
    expect((screen.getByLabelText("Timeframe") as HTMLSelectElement).value).toBe("15m");
  });

  it("mostra loading e empty state explicitamente", async () => {
    let resolveRequest!: (value: MarketCandlePageResponse) => void;
    mocks.getAppMarketCandles.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    renderPage();

    expect(screen.getByText("Carregando candles locais…")).toBeDefined();
    resolveRequest(page({ items: [], nextBefore: null, hasMoreBefore: false }));
    expect(await screen.findByText("Nenhum candle disponível")).toBeDefined();
  });

  it("normaliza a troca de instrumento e timeframe", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("market-chart");

    await user.clear(screen.getByLabelText("Ativo base"));
    await user.type(screen.getByLabelText("Ativo base"), " eth ");
    await user.clear(screen.getByLabelText("Ativo de cotação"));
    await user.type(screen.getByLabelText("Ativo de cotação"), "brl");
    await user.selectOptions(screen.getByLabelText("Timeframe"), "1h");
    await user.click(screen.getByRole("button", { name: "Aplicar seleção" }));

    await waitFor(() =>
      expect(mocks.getAppMarketCandles).toHaveBeenLastCalledWith("ETH", "BRL", {
        timeframe: "1h",
        limit: PAGE_LIMIT,
      }),
    );
  });

  it("bloqueia whitespace interno antes de consultar novos candles", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("market-chart");

    const baseAsset = screen.getByLabelText("Ativo base") as HTMLInputElement;
    await user.clear(baseAsset);
    await user.type(baseAsset, "BTC USDT");

    expect(baseAsset.checkValidity()).toBe(false);
    expect(baseAsset.validity.patternMismatch).toBe(true);
    await user.click(screen.getByRole("button", { name: "Aplicar seleção" }));
    expect(mocks.getAppMarketCandles).toHaveBeenCalledTimes(1);
  });

  it("renderiza candles-only e a representação textual Decimal/UTC crítica", async () => {
    renderPage();

    expect(await screen.findByText("Candles visuais: 1 · overlays: null/null · dados adicionais: null")).toBeDefined();
    expect(screen.getByText("BTC/USDT")).toBeDefined();
    expect(screen.getAllByText("15m").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/UTC/).length).toBeGreaterThan(0);
    expect(screen.getByText("100.123456789012345678")).toBeDefined();
    expect(screen.getByText("110.223456789012345678")).toBeDefined();
    expect(screen.getByText("90.323456789012345678")).toBeDefined();
    expect(screen.getByText("105.423456789012345678")).toBeDefined();
    expect(screen.getByText("2.523456789012345678")).toBeDefined();
    expect(screen.getByText("aaaaaaaaaaaa…aaaaaaaa")).toBeDefined();
    expect(screen.getByText("bbbbbbbbbbbb…bbbbbbbb")).toBeDefined();
    expect(document.body.textContent).not.toMatch(/session_id|annotation|trade|signal/i);
  });

  it("mostra erro seguro sem vazar a falha recebida", async () => {
    mocks.getAppMarketCandles.mockRejectedValue(
      new Error("token-ultrassecreto /home/private/dataset"),
    );
    renderPage();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Não foi possível carregar os candles locais",
    );
    expect(document.body.textContent).not.toContain("token-ultrassecreto");
    expect(document.body.textContent).not.toContain("/home/private");
  });

  it("carrega histórico anterior pelo cursor exclusivo", async () => {
    const user = userEvent.setup();
    const older = page({
      items: [marketCandle("2026-08-07T23:45:00Z")],
      nextBefore: null,
      hasMoreBefore: false,
    });
    mocks.getAppMarketCandles.mockResolvedValueOnce(page()).mockResolvedValueOnce(older);
    renderPage();
    await screen.findByTestId("market-chart");

    await user.click(
      screen.getByRole("button", { name: "Carregar histórico anterior" }),
    );

    await waitFor(() =>
      expect(mocks.getAppMarketCandles).toHaveBeenLastCalledWith("BTC", "USDT", {
        timeframe: "15m",
        before: "2026-08-08T00:00:00Z",
        limit: 1000,
      }),
    );
    expect(await screen.findByText(/Candles visuais: 2/)).toBeDefined();
  });

  it("recusa combinar versões diferentes durante a paginação", async () => {
    const user = userEvent.setup();
    mocks.getAppMarketCandles
      .mockResolvedValueOnce(page({ datasetVersion: "a".repeat(64) }))
      .mockResolvedValueOnce(page({ datasetVersion: "c".repeat(64) }));
    renderPage();
    await screen.findByTestId("market-chart");

    await user.click(
      screen.getByRole("button", { name: "Carregar histórico anterior" }),
    );

    expect((await screen.findByRole("alert")).textContent).toContain(
      "O dataset mudou durante a paginação",
    );
    expect(screen.getByText(/Candles visuais: 1/)).toBeDefined();
  });

  it("mantém no máximo 5000 candles no browser", async () => {
    const user = userEvent.setup();
    const base = Date.parse("2026-01-01T00:00:00Z");
    const candles = Array.from({ length: MAX_LOADED_CANDLES }, (_, index) =>
      marketCandle(new Date(base + index * 900_000).toISOString()),
    );
    for (let pageIndex = 4; pageIndex >= 0; pageIndex -= 1) {
      const start = pageIndex * PAGE_LIMIT;
      mocks.getAppMarketCandles.mockResolvedValueOnce(
        page({
          items: candles.slice(start, start + PAGE_LIMIT),
          nextBefore: pageIndex === 0 ? null : candles[start].open_time,
          hasMoreBefore: pageIndex !== 0,
        }),
      );
    }
    renderPage();
    await screen.findByText(/Candles visuais: 1000/);

    for (const expected of [2000, 3000, 4000, 5000]) {
      await user.click(
        screen.getByRole("button", { name: "Carregar histórico anterior" }),
      );
      await screen.findByText(`Candles visuais: ${expected} · overlays: null/null · dados adicionais: null`);
    }

    expect(screen.getByText("Limite local de 5000 candles atingido.")).toBeDefined();
    expect(
      (screen.getByRole("button", {
        name: "Carregar histórico anterior",
      }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(mocks.getAppMarketCandles).toHaveBeenCalledTimes(5);
  });

  it("refresh recarrega a página mais recente", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("market-chart");

    await user.click(
      screen.getByRole("button", { name: "Atualizar gráfico de mercado" }),
    );

    await waitFor(() => expect(mocks.getAppMarketCandles).toHaveBeenCalledTimes(2));
    expect(mocks.getAppMarketCandles).toHaveBeenLastCalledWith("BTC", "USDT", {
      timeframe: "15m",
      limit: PAGE_LIMIT,
    });
  });
});
