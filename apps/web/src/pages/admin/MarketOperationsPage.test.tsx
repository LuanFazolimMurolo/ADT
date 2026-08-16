import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiClient } from "../../http/client";
import type {
  MarketOperation,
  MarketOperationPlanPreview,
  MarketOperationTargetList,
} from "../../types/api";
import { MarketOperationsPage } from "./MarketOperationsPage";

vi.mock("../../http/client", async () => {
  const actual =
    await vi.importActual<typeof import("../../http/client")>(
      "../../http/client",
    );
  return {
    ...actual,
    apiClient: {
      listMarketOperationTargets: vi.fn(),
      previewMarketOperationBackfill: vi.fn(),
      previewMarketOperationIncremental: vi.fn(),
      submitMarketOperation: vi.fn(),
      listMarketOperations: vi.fn(),
      getMarketOperation: vi.fn(),
      pauseMarketOperation: vi.fn(),
      resumeMarketOperation: vi.fn(),
      cancelMarketOperation: vi.fn(),
    },
  };
});

const CHECKSUM = "a".repeat(64);
const DATASET_ID = "backend-owned-dataset-id";
const POLL_INTERVAL_MS = 30_000;

const targets: MarketOperationTargetList = {
  items: [
    {
      symbol: "BTC/USDT",
      base_asset: "BTC",
      quote_asset: "USDT",
      exchange: "binance",
      market_type: "spot",
      timeframes: [
        { timeframe: "1m", dataset_id: "dataset-1m" },
        { timeframe: "12h", dataset_id: DATASET_ID },
        { timeframe: "1w", dataset_id: "dataset-1w" },
      ],
    },
  ],
  page: 1,
  page_size: 25,
  total: 1,
  total_pages: 1,
  catalog_fetched_at: "2026-08-16T10:00:00Z",
  catalog_expires_at: "2026-08-16T10:05:00Z",
  source: "binance_spot_exchange_info",
};

const preview: MarketOperationPlanPreview = {
  operation_type: "RAW_BACKFILL",
  dataset: {
    dataset_id: DATASET_ID,
    exchange: "binance",
    market_type: "spot",
    symbol: "BTC/USDT",
    timeframe: "12h",
  },
  range_start: "2026-08-01T00:00:00Z",
  range_end: "2026-08-03T00:00:00Z",
  plan: {
    checksum: CHECKSUM,
    chunks_planned: 2,
    estimated_candles: 4,
    estimated_requests: 2,
    created_at: "2026-08-16T10:00:00Z",
  },
};

function operation(
  state: MarketOperation["state"] = "RUNNING",
  recordVersion = 2,
): MarketOperation {
  return {
    operation_id: "20000000-0000-4000-8000-000000000002",
    operation_type: "RAW_BACKFILL",
    state,
    dataset: preview.dataset,
    range_start: preview.range_start,
    range_end: preview.range_end,
    plan: preview.plan,
    progress: {
      chunks_planned: 2,
      chunks_completed: 1,
      chunks_failed: 0,
      candles_estimated: 4,
      candles_received: 2,
      candles_persisted: 2,
      requests_completed: 1,
      updated_at: "2026-08-16T10:00:00Z",
    },
    requested_by: "10000000-0000-4000-8000-000000000001",
    contract_version: 1,
    record_version: recordVersion,
    local_job_id: null,
    lease: {
      claimed_at: "2026-08-16T09:58:00Z",
      heartbeat_at: "2026-08-16T09:59:30Z",
      lease_expires_at: "2026-08-16T10:00:30Z",
    },
    result: null,
    failure: null,
    created_at: "2026-08-16T09:55:00Z",
    updated_at: "2026-08-16T10:00:00Z",
    started_at: "2026-08-16T09:58:00Z",
    finished_at: null,
    observed_at: "2026-08-16T10:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function setupUser() {
  const baseUser = userEvent.setup();
  return {
    click: (...args: Parameters<typeof baseUser.click>) =>
      act(async () => baseUser.click(...args)),
    selectOptions: (...args: Parameters<typeof baseUser.selectOptions>) =>
      act(async () => baseUser.selectOptions(...args)),
  };
}

const mockedApi = vi.mocked(apiClient);

beforeEach(() => {
  mockedApi.listMarketOperationTargets.mockResolvedValue(targets);
  mockedApi.listMarketOperations.mockResolvedValue({
    items: [],
    limit: 20,
    offset: 0,
    count: 0,
    has_more: false,
  });
  mockedApi.previewMarketOperationBackfill.mockResolvedValue(preview);
  mockedApi.previewMarketOperationIncremental.mockResolvedValue({
    action: "RUN",
    preview: { ...preview, operation_type: "RAW_INCREMENTAL_UPDATE" },
    last_open_time: "2026-08-15T12:00:00Z",
    latest_closed_end: "2026-08-16T00:00:00Z",
  });
  mockedApi.submitMarketOperation.mockResolvedValue(operation("PENDING", 1));
  mockedApi.getMarketOperation.mockResolvedValue(operation());
  mockedApi.pauseMarketOperation.mockResolvedValue(
    operation("PAUSE_REQUESTED", 3),
  );
  mockedApi.resumeMarketOperation.mockResolvedValue(operation("PENDING", 3));
  mockedApi.cancelMarketOperation.mockResolvedValue(
    operation("CANCEL_REQUESTED", 3),
  );
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function chooseTwelveHoursAndFillBackfill(
  user: ReturnType<typeof setupUser>,
) {
  await screen.findByRole("option", { name: /BTC\/USDT/ });
  await user.selectOptions(
    screen.getByLabelText("Timeframe operacional"),
    "12h",
  );
  fireEvent.change(screen.getByLabelText("Início do intervalo (UTC)"), {
    target: { value: "2026-08-01T00:00" },
  });
  fireEvent.change(screen.getByLabelText(/Fim do intervalo/), {
    target: { value: "2026-08-03T00:00" },
  });
}

async function renderPage(): Promise<ReturnType<typeof render>> {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<MarketOperationsPage />);
    await Promise.resolve();
  });
  return view;
}

describe("MarketOperationsPage", () => {
  it("expõe loading acessível e o estado vazio da lista", async () => {
    const pendingTargets = deferred<MarketOperationTargetList>();
    const pendingOperations = deferred<{
      items: MarketOperation[];
      limit: number;
      offset: number;
      count: number;
      has_more: boolean;
    }>();
    mockedApi.listMarketOperationTargets.mockReturnValue(
      pendingTargets.promise,
    );
    mockedApi.listMarketOperations.mockReturnValue(pendingOperations.promise);
    await renderPage();

    expect(screen.getByText("Carregando alvos válidos…")).toBeDefined();
    expect(screen.getByText("Carregando operações…")).toBeDefined();
    expect(screen.getAllByRole("status")).toHaveLength(2);

    await act(async () => {
      pendingTargets.resolve(targets);
      pendingOperations.resolve({
        items: [],
        limit: 20,
        offset: 0,
        count: 0,
        has_more: false,
      });
      await Promise.resolve();
    });
    expect(
      await screen.findByRole("heading", { name: "Nenhuma operação" }),
    ).toBeDefined();
  });

  it("usa target backend-owned, invalida prévia alterada e exige confirmação explícita", async () => {
    const user = setupUser();
    const randomUuid = vi
      .spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValue("30000000-0000-4000-8000-000000000003");
    await renderPage();
    await chooseTwelveHoursAndFillBackfill(user);

    await user.click(screen.getByRole("button", { name: "Gerar prévia" }));
    await screen.findByRole("heading", {
      name: /RAW backfill · BTC\/USDT · 12h/,
    });
    expect(mockedApi.previewMarketOperationBackfill).toHaveBeenCalledWith({
      dataset_id: DATASET_ID,
      range_start: "2026-08-01T00:00:00Z",
      range_end: "2026-08-03T00:00:00Z",
    });

    fireEvent.change(screen.getByLabelText(/Fim do intervalo/), {
      target: { value: "2026-08-04T00:00" },
    });
    expect(
      screen.queryByRole("button", { name: "Confirmar e submeter" }),
    ).toBeNull();

    await user.click(screen.getByRole("button", { name: "Gerar prévia" }));
    await user.click(
      await screen.findByRole("button", { name: "Confirmar e submeter" }),
    );
    expect(mockedApi.submitMarketOperation).not.toHaveBeenCalled();
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Confirmar e submeter",
      }),
    );

    await waitFor(() =>
      expect(mockedApi.submitMarketOperation).toHaveBeenCalledOnce(),
    );
    expect(mockedApi.submitMarketOperation).toHaveBeenCalledWith({
      operation_type: "RAW_BACKFILL",
      dataset_id: DATASET_ID,
      range_start: preview.range_start,
      range_end: preview.range_end,
      plan_checksum: CHECKSUM,
      idempotency_key: "30000000-0000-4000-8000-000000000003",
      confirmed: true,
    });
    expect(randomUuid).toHaveBeenCalledOnce();
  });

  it("renderiza incremental RUN e impede submit quando a prévia retorna NOOP", async () => {
    const user = setupUser();
    await renderPage();
    await screen.findByRole("option", { name: /BTC\/USDT/ });
    await user.selectOptions(
      screen.getByLabelText("Timeframe operacional"),
      "12h",
    );
    await user.click(screen.getByRole("radio", { name: "RAW incremental" }));
    await user.click(screen.getByRole("button", { name: "Gerar prévia" }));
    await screen.findByRole("heading", {
      name: /RAW incremental · BTC\/USDT · 12h/,
    });

    mockedApi.previewMarketOperationIncremental.mockResolvedValueOnce({
      action: "NOOP",
      preview: null,
      last_open_time: "2026-08-15T12:00:00Z",
      latest_closed_end: "2026-08-16T00:00:00Z",
    });
    fireEvent.change(screen.getByLabelText("Overlap de candles"), {
      target: { value: "3" },
    });
    await user.click(screen.getByRole("button", { name: "Gerar prévia" }));

    expect(await screen.findByText(/Incremental sem trabalho/)).toBeDefined();
    expect(screen.getByText(/\u00daltimo candle armazenado:/)).toBeDefined();
    expect(screen.getByText(/Limite fechado disponível:/)).toBeDefined();
    expect(screen.getByText(/Nenhuma operação será submetida/)).toBeDefined();
    expect(
      screen.queryByRole("button", { name: "Confirmar e submeter" }),
    ).toBeNull();
    expect(mockedApi.submitMarketOperation).not.toHaveBeenCalled();
  });

  it("reutiliza a chave após resultado ambíguo e cria outra para nova intenção", async () => {
    const user = setupUser();
    vi.spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce("30000000-0000-4000-8000-000000000003")
      .mockReturnValueOnce("40000000-0000-4000-8000-000000000004");
    mockedApi.submitMarketOperation
      .mockRejectedValueOnce(new ApiError(0, "network_error", "Falha de rede."))
      .mockRejectedValueOnce(new ApiError(0, "network_error", "Falha de rede."))
      .mockResolvedValueOnce(operation("PENDING", 1));
    await renderPage();
    await chooseTwelveHoursAndFillBackfill(user);
    await user.click(screen.getByRole("button", { name: "Gerar prévia" }));
    await user.click(
      await screen.findByRole("button", { name: "Confirmar e submeter" }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Confirmar e submeter",
      }),
    );

    const retryButton = await screen.findByRole("button", {
      name: "Repetir o mesmo envio",
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Confirmar e submeter" }),
    ).toBeNull();
    expect(document.activeElement).toBe(retryButton);
    await user.click(retryButton);
    await waitFor(() =>
      expect(mockedApi.submitMarketOperation).toHaveBeenCalledTimes(2),
    );
    const firstPayload = mockedApi.submitMarketOperation.mock.calls[0]?.[0];
    const retryPayload = mockedApi.submitMarketOperation.mock.calls[1]?.[0];
    expect(retryPayload?.idempotency_key).toBe(firstPayload?.idempotency_key);

    fireEvent.change(screen.getByLabelText(/Fim do intervalo/), {
      target: { value: "2026-08-04T00:00" },
    });
    await user.click(screen.getByRole("button", { name: "Gerar prévia" }));
    await user.click(
      await screen.findByRole("button", { name: "Confirmar e submeter" }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Confirmar e submeter",
      }),
    );
    await waitFor(() =>
      expect(mockedApi.submitMarketOperation).toHaveBeenCalledTimes(3),
    );
    expect(
      mockedApi.submitMarketOperation.mock.calls[2]?.[0].idempotency_key,
    ).toBe("40000000-0000-4000-8000-000000000004");
  });

  it("encerra a intenção após 409 definitivo sem oferecer retry", async () => {
    const user = setupUser();
    mockedApi.submitMarketOperation.mockRejectedValueOnce(
      new ApiError(
        409,
        "market_operation_plan_conflict",
        "A prévia não é mais válida.",
      ),
    );
    await renderPage();
    await chooseTwelveHoursAndFillBackfill(user);
    await user.click(screen.getByRole("button", { name: "Gerar prévia" }));
    await user.click(
      await screen.findByRole("button", { name: "Confirmar e submeter" }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Confirmar e submeter",
      }),
    );

    expect(
      await screen.findByText("A prévia não é mais válida."),
    ).toBeDefined();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Repetir o mesmo envio" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Confirmar e submeter" }),
    ).toBeNull();
    expect(mockedApi.submitMarketOperation).toHaveBeenCalledOnce();
  });

  it("mantém a operação submetida quando uma lista anterior termina depois", async () => {
    const user = setupUser();
    const oldList = deferred<{
      items: MarketOperation[];
      limit: number;
      offset: number;
      count: number;
      has_more: boolean;
    }>();
    const submitted = operation("PENDING", 1);
    mockedApi.listMarketOperations
      .mockReturnValueOnce(oldList.promise)
      .mockResolvedValueOnce({
        items: [submitted],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      });
    mockedApi.submitMarketOperation.mockResolvedValueOnce(submitted);
    mockedApi.getMarketOperation.mockResolvedValueOnce(submitted);
    await renderPage();
    await chooseTwelveHoursAndFillBackfill(user);
    await user.click(screen.getByRole("button", { name: "Gerar prévia" }));
    await user.click(
      await screen.findByRole("button", { name: "Confirmar e submeter" }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Confirmar e submeter",
      }),
    );

    expect(await screen.findByText(/Operação .* submetida/)).toBeDefined();
    expect(
      await screen.findByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    ).toBeDefined();
    await act(async () => {
      oldList.resolve({
        items: [],
        limit: 20,
        offset: 0,
        count: 0,
        has_more: false,
      });
      await Promise.resolve();
    });
    expect(
      screen.getByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    ).toBeDefined();
  });

  it("lista, detalha lease sem afirmar worker online e usa record_version atual", async () => {
    const user = setupUser();
    mockedApi.listMarketOperations.mockResolvedValue({
      items: [operation()],
      limit: 20,
      offset: 0,
      count: 1,
      has_more: false,
    });
    await renderPage();

    await user.click(
      await screen.findByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    );
    expect(await screen.findByText("Lease operacional")).toBeDefined();
    expect(
      screen.getByText("Lease válida no instante observado pelo servidor."),
    ).toBeDefined();
    expect(screen.queryByText(/worker online/i)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Solicitar pausa" }));
    expect(mockedApi.pauseMarketOperation).toHaveBeenCalledWith(
      operation().operation_id,
      { expected_version: 2 },
    );
  });

  it("promove snapshot mais novo da lista para o detalhe e para expected_version", async () => {
    const user = setupUser();
    mockedApi.listMarketOperations
      .mockResolvedValueOnce({
        items: [operation("RUNNING", 2)],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      })
      .mockResolvedValueOnce({
        items: [operation("RUNNING", 4)],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      });
    mockedApi.getMarketOperation.mockResolvedValueOnce(operation("RUNNING", 2));
    await renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    );
    expect(await screen.findByText("Versão 2")).toBeDefined();

    await user.click(
      screen.getByRole("button", { name: "Atualizar operações" }),
    );
    expect(await screen.findByText("Versão 4")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Solicitar pausa" }));
    expect(mockedApi.pauseMarketOperation).toHaveBeenCalledWith(
      operation().operation_id,
      { expected_version: 4 },
    );
  });

  it("rejeita observed_at mais antigo quando record_version é igual", async () => {
    const user = setupUser();
    const newerObservation = {
      ...operation("RUNNING", 2),
      observed_at: "2026-08-16T10:01:00Z",
    };
    const olderObservation = {
      ...operation("RUNNING", 2),
      observed_at: "2026-08-16T10:00:00Z",
    };
    mockedApi.listMarketOperations.mockResolvedValue({
      items: [newerObservation],
      limit: 20,
      offset: 0,
      count: 1,
      has_more: false,
    });
    mockedApi.getMarketOperation.mockResolvedValueOnce(olderObservation);
    await renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    );

    expect(
      await screen.findByText(
        "Lease expirada no instante observado pelo servidor.",
      ),
    ).toBeDefined();
    expect(
      screen.queryByText("Lease válida no instante observado pelo servidor."),
    ).toBeNull();
  });

  it("renderiza resultado e falha sanitizados sem despejar JSON interno", async () => {
    const user = setupUser();
    const completed: MarketOperation = {
      ...operation("COMPLETED", 5),
      lease: null,
      progress: {
        ...operation().progress,
        chunks_completed: 2,
        candles_received: 4,
        candles_persisted: 4,
        requests_completed: 2,
      },
      result: {
        dataset_version: "b".repeat(64),
        dataset_checksum: "c".repeat(64),
        completed_at: "2026-08-16T10:01:00Z",
      },
      finished_at: "2026-08-16T10:01:00Z",
    };
    const failed: MarketOperation = {
      ...operation("FAILED", 4),
      operation_id: "20000000-0000-4000-8000-000000000003",
      lease: null,
      failure: {
        code: "NETWORK_FAILURE",
        failed_at: "2026-08-16T10:01:00Z",
      },
      finished_at: "2026-08-16T10:01:00Z",
    };
    mockedApi.listMarketOperations.mockResolvedValue({
      items: [completed, failed],
      limit: 20,
      offset: 0,
      count: 2,
      has_more: false,
    });
    mockedApi.getMarketOperation.mockResolvedValue(completed);
    await renderPage();

    expect(await screen.findByText("Resultado disponível")).toBeDefined();
    expect(screen.getByText("Falha: NETWORK_FAILURE")).toBeDefined();
    await user.click(
      (
        await screen.findAllByRole("button", {
          name: /Inspecionar RAW backfill BTC\/USDT 12h/,
        })
      )[0],
    );
    expect(await screen.findByText(/Dataset b{12}…b{8}/)).toBeDefined();
    expect(screen.getByText(/Checksum c{12}…c{8}/)).toBeDefined();
    expect(screen.queryByText(/\{"dataset_version"/)).toBeNull();
  });

  it("em 409 recarrega o detalhe e não repete a mutação", async () => {
    const user = setupUser();
    mockedApi.listMarketOperations.mockResolvedValue({
      items: [operation()],
      limit: 20,
      offset: 0,
      count: 1,
      has_more: false,
    });
    mockedApi.getMarketOperation
      .mockResolvedValueOnce(operation("RUNNING", 2))
      .mockResolvedValueOnce(operation("RUNNING", 3));
    mockedApi.pauseMarketOperation.mockRejectedValue(
      new ApiError(409, "market_operation_version_conflict", "Conflito."),
    );
    await renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    );
    await screen.findByText("Versão 2");
    await user.click(screen.getByRole("button", { name: "Solicitar pausa" }));

    expect((await screen.findByRole("alert")).textContent).toMatch(
      /mudou no servidor/,
    );
    expect(await screen.findByText("Versão 3")).toBeDefined();
    expect(mockedApi.pauseMarketOperation).toHaveBeenCalledOnce();
  });

  it.each([
    ["PAUSED" as const, "Retomar", "resumeMarketOperation" as const],
    [
      "PAUSED" as const,
      "Solicitar cancelamento",
      "cancelMarketOperation" as const,
    ],
  ])(
    "oferece controle %s coerente com o domínio",
    async (state, label, method) => {
      const user = setupUser();
      mockedApi.listMarketOperations.mockResolvedValue({
        items: [operation(state, 7)],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      });
      mockedApi.getMarketOperation.mockResolvedValue(operation(state, 7));
      await renderPage();
      await user.click(
        await screen.findByRole("button", {
          name: /Inspecionar RAW backfill BTC\/USDT 12h/,
        }),
      );
      await user.click(await screen.findByRole("button", { name: label }));
      expect(mockedApi[method]).toHaveBeenCalledWith(operation().operation_id, {
        expected_version: 7,
      });
    },
  );

  it.each([
    ["PENDING", true, false, true],
    ["CLAIMED", true, false, true],
    ["RUNNING", true, false, true],
    ["PAUSE_REQUESTED", false, false, false],
    ["PAUSED", false, true, true],
    ["CANCEL_REQUESTED", false, false, false],
    ["CANCELLED", false, false, false],
    ["COMPLETED", false, false, false],
    ["FAILED", false, false, false],
    ["RECOVERING", false, false, false],
  ] as const)(
    "expõe a matriz completa de controles para %s",
    async (state, pause, resume, cancel) => {
      const user = setupUser();
      const snapshot = operation(state, 7);
      mockedApi.listMarketOperations.mockResolvedValue({
        items: [snapshot],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      });
      mockedApi.getMarketOperation.mockResolvedValue(snapshot);
      await renderPage();
      await user.click(
        await screen.findByRole("button", {
          name: /Inspecionar RAW backfill BTC\/USDT 12h/,
        }),
      );
      await screen.findByText("Versão 7");

      expect(
        screen.queryByRole("button", { name: "Solicitar pausa" }) !== null,
      ).toBe(pause);
      expect(screen.queryByRole("button", { name: "Retomar" }) !== null).toBe(
        resume,
      );
      expect(
        screen.queryByRole("button", {
          name: "Solicitar cancelamento",
        }) !== null,
      ).toBe(cancel);
    },
  );

  it("não deixa polls antigos regredirem lista ou detalhe após mutação", async () => {
    const user = setupUser();
    const oldList = deferred<{
      items: MarketOperation[];
      limit: number;
      offset: number;
      count: number;
      has_more: boolean;
    }>();
    const oldDetail = deferred<MarketOperation>();
    mockedApi.listMarketOperations
      .mockResolvedValueOnce({
        items: [operation("RUNNING", 2)],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      })
      .mockReturnValueOnce(oldList.promise)
      .mockResolvedValueOnce({
        items: [operation("PAUSE_REQUESTED", 3)],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      });
    mockedApi.getMarketOperation.mockReturnValueOnce(oldDetail.promise);
    mockedApi.pauseMarketOperation.mockResolvedValueOnce(
      operation("PAUSE_REQUESTED", 3),
    );
    await renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    );
    await waitFor(() =>
      expect(mockedApi.getMarketOperation).toHaveBeenCalled(),
    );
    await user.click(
      screen.getByRole("button", { name: "Atualizar operações" }),
    );
    await waitFor(() =>
      expect(mockedApi.listMarketOperations).toHaveBeenCalledTimes(2),
    );
    await user.click(screen.getByRole("button", { name: "Solicitar pausa" }));

    expect(await screen.findByText("Versão 3")).toBeDefined();
    expect(
      within(screen.getByRole("table")).getByText("Pausa solicitada"),
    ).toBeDefined();
    await act(async () => {
      oldDetail.resolve(operation("RUNNING", 2));
      oldList.resolve({
        items: [operation("RUNNING", 2)],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      });
      await Promise.resolve();
    });
    expect(screen.getByText("Versão 3")).toBeDefined();
    expect(
      within(screen.getByRole("table")).getByText("Pausa solicitada"),
    ).toBeDefined();
    expect(mockedApi.pauseMarketOperation).toHaveBeenCalledOnce();
  });

  it("mantém a identidade selecionada quando o detalhe anterior chega por último", async () => {
    const user = setupUser();
    const first = operation("RUNNING", 2);
    const second: MarketOperation = {
      ...operation("PAUSED", 8),
      operation_id: "20000000-0000-4000-8000-000000000099",
      dataset: { ...preview.dataset, symbol: "ETH/USDT" },
    };
    const oldDetail = deferred<MarketOperation>();
    mockedApi.listMarketOperations.mockResolvedValue({
      items: [first, second],
      limit: 20,
      offset: 0,
      count: 2,
      has_more: false,
    });
    mockedApi.getMarketOperation
      .mockReturnValueOnce(oldDetail.promise)
      .mockResolvedValueOnce(second);
    await renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    );
    await waitFor(() =>
      expect(mockedApi.getMarketOperation).toHaveBeenCalledTimes(1),
    );
    await user.click(
      screen.getByRole("button", {
        name: /Inspecionar RAW backfill ETH\/USDT 12h/,
      }),
    );
    expect(await screen.findByText("Versão 8")).toBeDefined();
    expect(screen.getByText(`ID ${second.operation_id}`)).toBeDefined();

    await act(async () => {
      oldDetail.resolve(first);
      await Promise.resolve();
    });
    expect(screen.getByText("Versão 8")).toBeDefined();
    expect(screen.getByText(`ID ${second.operation_id}`)).toBeDefined();
  });

  it("não sobrepõe polls, cancela o timer e ignora resposta após unmount", async () => {
    vi.useFakeTimers();
    const pending = deferred<{
      items: MarketOperation[];
      limit: number;
      offset: number;
      count: number;
      has_more: boolean;
    }>();
    mockedApi.listMarketOperations.mockReturnValueOnce(pending.promise);
    const view = await renderPage();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    });
    expect(mockedApi.listMarketOperations).toHaveBeenCalledOnce();
    view.unmount();
    await act(async () => {
      pending.resolve({
        items: [operation()],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      });
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    });
    expect(mockedApi.listMarketOperations).toHaveBeenCalledOnce();
  });

  it("ignora uma resposta antiga quando o filtro inicia uma consulta nova", async () => {
    const user = setupUser();
    const oldResponse = deferred<{
      items: MarketOperation[];
      limit: number;
      offset: number;
      count: number;
      has_more: boolean;
    }>();
    mockedApi.listMarketOperations
      .mockReturnValueOnce(oldResponse.promise)
      .mockResolvedValueOnce({
        items: [operation("PAUSED", 7)],
        limit: 20,
        offset: 0,
        count: 1,
        has_more: false,
      });
    await renderPage();

    await user.selectOptions(screen.getByLabelText("Estado"), "PAUSED");
    await waitFor(() =>
      expect(mockedApi.listMarketOperations).toHaveBeenCalledTimes(2),
    );
    expect(
      await screen.findByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    ).toBeDefined();

    await act(async () => {
      oldResponse.resolve({
        items: [],
        limit: 20,
        offset: 0,
        count: 0,
        has_more: false,
      });
      await Promise.resolve();
    });
    expect(
      screen.getByRole("button", {
        name: /Inspecionar RAW backfill BTC\/USDT 12h/,
      }),
    ).toBeDefined();
  });

  it("expõe falhas 403 em estado acessível sem fabricar conteúdo", async () => {
    mockedApi.listMarketOperationTargets.mockRejectedValue(
      new ApiError(403, "forbidden", "Acesso negado."),
    );
    mockedApi.listMarketOperations.mockRejectedValue(
      new ApiError(403, "forbidden", "Acesso negado."),
    );
    await renderPage();

    const alerts = await screen.findAllByRole("alert");
    expect(
      alerts.some((alert) => alert.textContent?.includes("Acesso negado.")),
    ).toBe(true);
    expect(screen.queryByText(/worker online/i)).toBeNull();
  });
});
