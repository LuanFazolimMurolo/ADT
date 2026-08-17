import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RawDatasetsPage } from "./RawDatasetsPage";

const mocks = vi.hoisted(() => ({
  listRawDatasets: vi.fn(),
  getRawDataset: vi.fn(),
  getRawDatasetGaps: vi.fn(),
  getRawDatasetQuality: vi.fn(),
}));

vi.mock("../../http/client", () => ({
  apiClient: mocks,
}));

const dataset = {
  dataset_id: "abc_DEF-123",
  exchange: "BINANCE",
  market_type: "SPOT",
  symbol: "BTC/USDT",
  base_asset: "BTC",
  quote_asset: "USDT",
  timeframe: "1h",
  first_open_time: "2026-08-01T00:00:00Z",
  last_open_time: "2026-08-01T02:00:00Z",
  coverage_start: "2026-08-01T00:00:00Z",
  coverage_end: "2026-08-01T03:00:00Z",
  candle_count: 3,
  version: "a".repeat(64),
  version_algorithm: "raw-partition-canonical-sha256-v1",
  updated_at: "2026-08-16T12:00:00Z",
  integrity: {
    present: true,
    schema_version: 1,
    checksum_algorithm: "raw-partition-canonical-sha256-v1",
    partition_count: 1,
  },
};

beforeEach(() => {
  mocks.listRawDatasets.mockReset();
  mocks.getRawDataset.mockReset();
  mocks.getRawDatasetGaps.mockReset();
  mocks.getRawDatasetQuality.mockReset();

  mocks.listRawDatasets.mockResolvedValue({
    items: [dataset],
    page: 1,
    page_size: 25,
    total: 1,
    total_pages: 1,
  });

  mocks.getRawDataset.mockResolvedValue(dataset);

  mocks.getRawDatasetQuality.mockResolvedValue({
    dataset_id: dataset.dataset_id,
    exchange: dataset.exchange,
    market_type: dataset.market_type,
    symbol: dataset.symbol,
    timeframe: dataset.timeframe,
    status: "CURRENT",
    dataset_version: dataset.version,
    version_algorithm: dataset.version_algorithm,
    baseline_dataset_version: dataset.version,
    baseline_version_algorithm: dataset.version_algorithm,
    scanner_schema_version: 2,
    scanner_version: "phase2c-3",
    coverage: {
      expected_count: 3,
      observed_count: 2,
      internal_gap_count: 1,
      missing_at_start: 0,
      missing_at_end: 0,
    },
    partition_count: 1,
    issue_totals: {
      total: 1,
      errors: 1,
      warnings: 0,
      other: 0,
    },
    issues: [
      {
        code: "gap",
        severity: "ERROR",
        category: "COVERAGE",
        open_time: "2026-08-01T01:00:00Z",
      },
    ],
  });

  mocks.getRawDatasetGaps.mockImplementation(
    async (
      _datasetId: string,
      query: {
        start: string;
        end: string;
        page?: number;
        pageSize?: number;
      },
    ) => ({
      dataset_id: dataset.dataset_id,
      exchange: dataset.exchange,
      market_type: dataset.market_type,
      symbol: dataset.symbol,
      timeframe: dataset.timeframe,
      dataset_version: dataset.version,
      version_algorithm: dataset.version_algorithm,
      checked_start: query.start,
      checked_end: query.end,
      expected_candles: 3,
      observed_candles: 2,
      missing_candles: 1,
      total_gap_count: 26,
      page: query.page ?? 1,
      page_size: query.pageSize ?? 25,
      total_pages: 2,
      items: [
        {
          start: "2026-08-01T01:00:00Z",
          end: "2026-08-01T02:00:00Z",
          missing_candles: 1,
        },
      ],
    }),
  );
});

describe("RawDatasetsPage", () => {
  it("carrega o catálogo RAW bounded sem detalhes de storage", async () => {
    render(<RawDatasetsPage />);

    expect(await screen.findByText("BTC/USDT")).toBeDefined();

    expect(mocks.listRawDatasets).toHaveBeenCalledWith({
      page: 1,
      pageSize: 25,
      symbol: undefined,
      timeframe: undefined,
    });

    const body = document.body.textContent ?? "";

    expect(body).not.toContain("ADT_DATA_DIR");
    expect(body).not.toContain("relative_path");
    expect(body).not.toContain("candles.parquet");
  });

  it("aplica símbolo e timeframe como filtros bounded", async () => {
    render(<RawDatasetsPage />);

    await screen.findByText("BTC/USDT");

    fireEvent.change(screen.getByLabelText("Símbolo"), {
      target: { value: "ETH/USDT" },
    });
    fireEvent.change(screen.getByLabelText("Timeframe"), {
      target: { value: "15m" },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Filtrar" }));
    });

    await waitFor(() =>
      expect(mocks.listRawDatasets).toHaveBeenLastCalledWith({
        page: 1,
        pageSize: 25,
        symbol: "ETH/USDT",
        timeframe: "15m",
      }),
    );
  });

  it("pagina o catálogo usando page_size fixo", async () => {
    mocks.listRawDatasets.mockResolvedValue({
      items: [dataset],
      page: 1,
      page_size: 25,
      total: 26,
      total_pages: 2,
    });

    render(<RawDatasetsPage />);

    await screen.findByText("Página 1 de 2");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Próxima" }));
    });

    await waitFor(() =>
      expect(mocks.listRawDatasets).toHaveBeenLastCalledWith({
        page: 2,
        pageSize: 25,
        symbol: undefined,
        timeframe: undefined,
      }),
    );
  });

  it("consulta o detalhe pelo dataset_id fornecido pelo backend", async () => {
    render(<RawDatasetsPage />);

    const inspectButton = await screen.findByRole("button", {
      name: "Inspecionar dataset BTC/USDT 1h",
    });

    await act(async () => {
      fireEvent.click(inspectButton);
    });

    await waitFor(() =>
      expect(mocks.getRawDataset).toHaveBeenCalledWith("abc_DEF-123"),
    );

    const detailHeading = await screen.findByRole("heading", {
      name: "Detalhe do dataset",
    });

    const detailSection = detailHeading.closest("section");

    expect(detailSection).not.toBeNull();

    const candlesLabel = screen.getByText("Candles persistidos");
    const candlesContainer = candlesLabel.closest("div");

    expect(candlesContainer?.querySelector("dd")?.textContent?.trim()).toBe(
      "3",
    );

    expect(detailSection?.textContent).toContain(
      "raw-partition-canonical-sha256-v1",
    );

    expect(detailSection?.textContent).toContain("abc_DEF-123");
  });


  it("carrega quality persistida pelo dataset_id sem expor storage", async () => {
    render(<RawDatasetsPage />);

    const inspectButton = await screen.findByRole("button", {
      name: "Inspecionar dataset BTC/USDT 1h",
    });

    await act(async () => {
      fireEvent.click(inspectButton);
    });

    await waitFor(() =>
      expect(mocks.getRawDatasetQuality).toHaveBeenCalledWith("abc_DEF-123"),
    );

    expect(await screen.findByText("CURRENT")).toBeDefined();
    expect(screen.getByText("phase2c-3")).toBeDefined();
    expect(screen.getByText("gap")).toBeDefined();

    const body = document.body.textContent ?? "";

    expect(body).not.toContain("relative_path");
    expect(body).not.toContain("quality-baselines/");
    expect(body).not.toContain("candles.parquet");
    expect(body).not.toContain("baseline_path");
  });

  it("consulta gaps com intervalo UTC explícito e paginação bounded", async () => {
    render(<RawDatasetsPage />);

    const inspectButton = await screen.findByRole("button", {
      name: "Inspecionar dataset BTC/USDT 1h",
    });

    await act(async () => {
      fireEvent.click(inspectButton);
    });

    fireEvent.change(screen.getByLabelText("Início UTC"), {
      target: { value: "2026-08-01T00:00:00Z" },
    });
    fireEvent.change(screen.getByLabelText("Fim UTC"), {
      target: { value: "2026-08-01T03:00:00Z" },
    });

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Consultar gaps" }),
      );
    });

    await waitFor(() =>
      expect(mocks.getRawDatasetGaps).toHaveBeenLastCalledWith(
        "abc_DEF-123",
        {
          start: "2026-08-01T00:00:00Z",
          end: "2026-08-01T03:00:00Z",
          page: 1,
          pageSize: 25,
        },
      ),
    );

    expect(await screen.findByText("Página 1 de 2")).toBeDefined();

    const gapPagination = screen
      .getByLabelText("Paginação de gaps RAW");

    await act(async () => {
      fireEvent.click(
        gapPagination.querySelectorAll("button")[1] as HTMLButtonElement,
      );
    });

    await waitFor(() =>
      expect(mocks.getRawDatasetGaps).toHaveBeenLastCalledWith(
        "abc_DEF-123",
        {
          start: "2026-08-01T00:00:00Z",
          end: "2026-08-01T03:00:00Z",
          page: 2,
          pageSize: 25,
        },
      ),
    );
  });

  it("não oferece scan, repair, backfill ou mutação na inspeção RAW", async () => {
    render(<RawDatasetsPage />);

    const inspectButton = await screen.findByRole("button", {
      name: "Inspecionar dataset BTC/USDT 1h",
    });

    await act(async () => {
      fireEvent.click(inspectButton);
    });

    await screen.findByText("Quality persistida");

    expect(
      screen.queryByRole("button", { name: /scan/i }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /repair/i }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /backfill/i }),
    ).toBeNull();
  });

  it("renderiza estado vazio sem inventar datasets", async () => {
    mocks.listRawDatasets.mockResolvedValue({
      items: [],
      page: 1,
      page_size: 25,
      total: 0,
      total_pages: 0,
    });

    render(<RawDatasetsPage />);

    expect(
      await screen.findByText("Nenhum dataset RAW encontrado"),
    ).toBeDefined();

    expect(mocks.getRawDataset).not.toHaveBeenCalled();
  });
});
