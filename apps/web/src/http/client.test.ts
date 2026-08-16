import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./client";

function jsonResponse(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApiClient", () => {
  it("mantém o binding do fetch nativo quando não há implementação injetada", async () => {
    const nativeFetch = vi.fn(function (this: unknown) {
      expect(this).toBe(globalThis);
      return Promise.resolve(jsonResponse(200, { status: "healthy" }));
    });
    vi.stubGlobal("fetch", nativeFetch);

    try {
      const client = new ApiClient({ baseUrl: "http://api.test" });
      await expect(client.getHealth()).resolves.toEqual({ status: "healthy" });
      expect(nativeFetch).toHaveBeenCalledOnce();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("consulta a identidade /app com Bearer sem exigir rota administrativa", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        user_id: "11111111-1111-4111-8111-111111111111",
        is_admin: false,
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await expect(client.getAppMe()).resolves.toMatchObject({
      is_admin: false,
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://api.test/api/v1/app/me");
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta candles autenticados somente pela superfície /app", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [],
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.getAppMarketCandles("BTC", "USDT", {
      timeframe: "15m",
      before: "2026-08-08T00:00:00Z",
      limit: 1000,
    });

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);
    expect(requestedUrl.pathname).toBe(
      "/api/v1/app/market-data/candles/BTC/USDT",
    );
    expect(requestedUrl.pathname).not.toContain("/api/v1/admin/");
    expect(requestedUrl.searchParams.get("timeframe")).toBe("15m");
    expect(requestedUrl.searchParams.get("before")).toBe(
      "2026-08-08T00:00:00Z",
    );
    expect(requestedUrl.searchParams.get("limit")).toBe("1000");
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta o catálogo bounded de sessões somente pela superfície /app", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [],
        page: 2,
        page_size: 20,
        total: 0,
        total_pages: 0,
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.getAppPaperSessions(2, 20);

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);
    expect(requestedUrl.pathname).toBe("/api/v1/app/paper-trading/sessions");
    expect(requestedUrl.pathname).not.toContain("/api/v1/admin/");
    expect(requestedUrl.searchParams.get("page")).toBe("2");
    expect(requestedUrl.searchParams.get("page_size")).toBe("20");
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta detail, annotations e trades somente por rotas session-scoped /app", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });
    const sessionId = "a".repeat(64);

    await client.getAppPaperSession(sessionId);
    await client.getAppPaperChartAnnotations(sessionId, {
      start: "2026-08-08T00:00:00Z",
      before: "2026-08-08T01:00:00Z",
      limit: 5000,
    });
    await client.getAppPaperTrades(
      sessionId,
      { status: "OPEN", openedFrom: "2026-08-01T00:00:00Z" },
      2,
      100,
    );

    const urls = fetchMock.mock.calls.map((call) => new URL(call[0] as string));
    expect(urls.map((url) => url.pathname)).toEqual([
      `/api/v1/app/paper-trading/sessions/${sessionId}`,
      `/api/v1/app/paper-trading/sessions/${sessionId}/chart-annotations`,
      `/api/v1/app/paper-trading/sessions/${sessionId}/trades`,
    ]);
    expect(urls.every((url) => !url.pathname.includes("/api/v1/admin/"))).toBe(
      true,
    );
    expect(urls[1].searchParams.get("limit")).toBe("5000");
    expect(urls[2].searchParams.get("status")).toBe("OPEN");
    expect(urls[2].searchParams.get("page")).toBe("2");
    expect(urls[2].searchParams.get("page_size")).toBe("100");
    expect(urls[2].searchParams.has("session_id")).toBe(false);
  });

  it("consulta timeline e period metrics somente pela sessão do path /app", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });
    const sessionId = "a".repeat(64);

    await client.getAppPaperPortfolioTimeline(sessionId, {
      before: "2026-08-08T00:00:00Z",
      limit: 5000,
    });
    await client.getAppPaperPeriodMetrics(sessionId, {
      periodFrom: "2026-08-01T00:00:00Z",
      periodBefore: "2026-08-08T00:00:00Z",
      granularity: "DAILY",
    });

    const urls = fetchMock.mock.calls.map((call) => new URL(call[0] as string));
    expect(urls.map((url) => url.pathname)).toEqual([
      `/api/v1/app/paper-trading/sessions/${sessionId}/portfolio-timeline`,
      `/api/v1/app/paper-trading/sessions/${sessionId}/period-metrics`,
    ]);
    expect(urls.every((url) => !url.pathname.includes("/api/v1/admin/"))).toBe(
      true,
    );
    expect(urls[0].searchParams.get("before")).toBe("2026-08-08T00:00:00Z");
    expect(urls[0].searchParams.get("limit")).toBe("5000");
    expect(urls[1].searchParams.get("period_from")).toBe(
      "2026-08-01T00:00:00Z",
    );
    expect(urls[1].searchParams.get("granularity")).toBe("DAILY");
    expect(urls[1].searchParams.has("session_id")).toBe(false);
    expect(urls[1].searchParams.has("quote_asset")).toBe(false);
  });

  it("consulta a timeline de portfólio com cursor e limite autenticados", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [],
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });
    const sessionId = "a".repeat(64);

    await client.getPaperPortfolioTimeline(sessionId, {
      before: "2026-08-08T00:00:00Z",
      limit: 5000,
    });

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);
    expect(requestedUrl.pathname).toBe(
      `/api/v1/admin/paper-trading/sessions/${sessionId}/portfolio-timeline`,
    );
    expect(requestedUrl.searchParams.get("before")).toBe(
      "2026-08-08T00:00:00Z",
    );
    expect(requestedUrl.searchParams.get("limit")).toBe("5000");
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta o dashboard de paper trading com paginação autenticada", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [],
        totals: {},
        page: 3,
        page_size: 20,
        total: 0,
        total_pages: 0,
        runner: null,
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.getPaperTradingDashboard(3, 20);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://api.test/api/v1/admin/paper-trading/dashboard?page=3&page_size=20",
    );
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta o trade journal com filtros codificados e autenticação", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        filters: {},
        items: [],
        page: 2,
        page_size: 20,
        total: 0,
        total_pages: 0,
        totals: {},
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.getPaperTradeJournal(
      {
        baseAsset: "BTC",
        strategyName: "strategy with space",
        status: "OPEN",
      },
      2,
      20,
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://api.test/api/v1/admin/paper-trading/journal?page=2&page_size=20&base_asset=BTC&strategy_name=strategy+with+space&status=OPEN",
    );
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta targets, lista e detalhe de operações com queries bounded", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.listMarketOperationTargets({
      activeOnly: true,
      quoteAsset: "USDT",
      search: "BTC / spot",
      page: 2,
      pageSize: 25,
    });
    await client.listMarketOperations({
      limit: 20,
      offset: 40,
      state: "RUNNING",
      requestedBy: "admin id",
      datasetId: "dataset/id",
    });
    await client.getMarketOperation("operation/id");

    const urls = fetchMock.mock.calls.map((call) => new URL(call[0] as string));
    expect(urls[0].pathname).toBe(
      "/api/v1/admin/market-data/operations/targets",
    );
    expect(urls[0].search).toBe(
      "?active_only=true&quote_asset=USDT&search=BTC+%2F+spot&page=2&page_size=25",
    );
    expect(urls[1].pathname).toBe("/api/v1/admin/market-data/operations");
    expect(urls[1].search).toBe(
      "?limit=20&offset=40&state=RUNNING&requested_by=admin+id&dataset_id=dataset%2Fid",
    );
    expect(urls[2].pathname).toBe(
      "/api/v1/admin/market-data/operations/operation%2Fid",
    );
    expect(
      fetchMock.mock.calls.every((call) => call[1]?.method !== "POST"),
    ).toBe(true);
  });

  it("envia previews, submissão e controles com corpos exatos", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });
    const backfill = {
      dataset_id: "dataset-id",
      range_start: "2026-08-01T00:00:00Z",
      range_end: "2026-08-02T00:00:00Z",
    };
    const submit = {
      ...backfill,
      operation_type: "RAW_BACKFILL" as const,
      plan_checksum: "a".repeat(64),
      idempotency_key: "intent-key",
      confirmed: true as const,
    };

    await client.previewMarketOperationBackfill(backfill);
    await client.previewMarketOperationIncremental({
      dataset_id: "dataset-id",
      overlap_candles: 2,
      start: "2026-08-01T00:00:00Z",
    });
    await client.submitMarketOperation(submit);
    await client.pauseMarketOperation("operation-id", { expected_version: 7 });
    await client.resumeMarketOperation("operation-id", { expected_version: 8 });
    await client.cancelMarketOperation("operation-id", { expected_version: 9 });

    const calls = fetchMock.mock.calls;
    expect(calls.map((call) => new URL(call[0] as string).pathname)).toEqual([
      "/api/v1/admin/market-data/operations/preview/backfill",
      "/api/v1/admin/market-data/operations/preview/incremental",
      "/api/v1/admin/market-data/operations",
      "/api/v1/admin/market-data/operations/operation-id/pause",
      "/api/v1/admin/market-data/operations/operation-id/resume",
      "/api/v1/admin/market-data/operations/operation-id/cancel",
    ]);
    expect(calls.map((call) => JSON.parse(String(call[1]?.body)))).toEqual([
      backfill,
      {
        dataset_id: "dataset-id",
        overlap_candles: 2,
        start: "2026-08-01T00:00:00Z",
      },
      submit,
      { expected_version: 7 },
      { expected_version: 8 },
      { expected_version: 9 },
    ]);
  });

  it("não repete submissão de operação após 401", async () => {
    const refreshAccessToken = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(401, {
        error: { code: "expired", message: "Expirado." },
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      refreshAccessToken,
      fetchImplementation: fetchMock as typeof fetch,
    });

    await expect(
      client.submitMarketOperation({
        operation_type: "RAW_BACKFILL",
        dataset_id: "dataset-id",
        range_start: "2026-08-01T00:00:00Z",
        range_end: "2026-08-02T00:00:00Z",
        plan_checksum: "a".repeat(64),
        idempotency_key: "intent-key",
        confirmed: true,
      }),
    ).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(refreshAccessToken).not.toHaveBeenCalled();
  });

  it("envia o token Bearer sem registrá-lo", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { user_id: "id", is_admin: true }));
    const consoleSpy = vi.spyOn(console, "log");
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token-ultrassecreto",
      fetchImplementation: fetchMock as typeof fetch,
    });
    await client.getAdminMe();
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-ultrassecreto");
    expect(consoleSpy).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("token-ultrassecreto");
  });

  it("normaliza falha nativa de fetch sem expor a mensagem do navegador", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValue(
        new TypeError("Failed to fetch https://internal.example/private"),
      );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      fetchImplementation: fetchMock as typeof fetch,
    });

    try {
      await client.getHealth();
      throw new Error("A chamada deveria ter falhado.");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({
        status: 0,
        code: "network_error",
        message:
          "Não foi possível conectar à API. Tente novamente em instantes.",
      });
      expect((error as Error).message).not.toContain("internal.example");
      expect((error as Error).message).not.toContain("Failed to fetch");
    }
  });

  it.each([200, 204])(
    "rejeita resposta %i sem corpo como contrato inválido",
    async (status) => {
      const client = new ApiClient({
        baseUrl: "http://api.test",
        fetchImplementation: vi
          .fn()
          .mockResolvedValue(new Response(null, { status })) as typeof fetch,
      });

      await expect(client.getHealth()).rejects.toMatchObject({
        status,
        code: "invalid_response",
      });
    },
  );

  it("preserva JSON null como resposta pública válida", async () => {
    const client = new ApiClient({
      baseUrl: "http://api.test",
      fetchImplementation: vi
        .fn()
        .mockResolvedValue(jsonResponse(200, null)) as typeof fetch,
    });

    await expect(client.getPublicSimulation()).resolves.toBeNull();
  });

  it("não invalida a sessão administrativa por erro de endpoint público", async () => {
    const onFailure = vi.fn();
    const client = new ApiClient({
      baseUrl: "http://api.test",
      onAuthenticationFailure: onFailure,
      fetchImplementation: vi.fn().mockResolvedValue(
        jsonResponse(403, {
          error: { code: "forbidden", message: "Mensagem segura." },
        }),
      ) as typeof fetch,
    });

    await expect(client.getPublicSimulation()).rejects.toMatchObject({
      status: 403,
      code: "forbidden",
    });
    expect(onFailure).not.toHaveBeenCalled();
  });

  it.each([
    [403, "forbidden"],
    [409, "active_simulation_exists"],
    [422, "validation_error"],
    [503, "service_unavailable"],
  ])("preserva status %i e código seguro", async (status, code) => {
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: vi.fn().mockResolvedValue(
        jsonResponse(status, {
          error: { code, message: "Mensagem segura." },
        }),
      ),
    });
    await expect(client.getAdminMe()).rejects.toMatchObject({ status, code });
  });

  it("propaga 403 autenticado sem invalidar a sessão", async () => {
    const onFailure = vi.fn();
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "valid-token",
      onAuthenticationFailure: onFailure,
      fetchImplementation: vi.fn().mockResolvedValue(
        jsonResponse(403, {
          error: { code: "administrator_required", message: "Negado." },
        }),
      ) as typeof fetch,
    });

    await expect(client.getAdminMe()).rejects.toMatchObject({
      status: 403,
      code: "administrator_required",
    });
    expect(onFailure).not.toHaveBeenCalled();
  });

  it("renova uma vez em GET após 401 e mantém a sessão quando o retry funciona", async () => {
    const onFailure = vi.fn();
    const refreshAccessToken = vi.fn().mockResolvedValue("new-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(401, { error: { code: "expired", message: "Expirado." } }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, { user_id: "id", is_admin: true }),
      );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "old-token",
      refreshAccessToken,
      onAuthenticationFailure: onFailure,
      fetchImplementation: fetchMock as typeof fetch,
    });

    await expect(client.getAdminMe()).resolves.toMatchObject({
      is_admin: true,
    });
    expect(refreshAccessToken).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const retryHeaders = fetchMock.mock.calls[1]?.[1]?.headers as Headers;
    expect(retryHeaders.get("Authorization")).toBe("Bearer new-token");
    expect(onFailure).not.toHaveBeenCalled();
  });

  it("renova uma vez em GET e encerra a sessão após 401 persistente", async () => {
    const onFailure = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(401, { error: { code: "expired", message: "Expirado." } }),
      )
      .mockResolvedValueOnce(
        jsonResponse(401, { error: { code: "expired", message: "Expirado." } }),
      );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "old-token",
      refreshAccessToken: async () => "new-token",
      onAuthenticationFailure: onFailure,
      fetchImplementation: fetchMock as typeof fetch,
    });
    await expect(client.getAdminMe()).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onFailure).toHaveBeenCalledWith("new-token");
  });

  it("não repete POST após 401", async () => {
    const onFailure = vi.fn();
    const refreshAccessToken = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(401, { error: { code: "expired", message: "Expirado." } }),
      );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      refreshAccessToken,
      onAuthenticationFailure: onFailure,
      fetchImplementation: fetchMock as typeof fetch,
    });
    await expect(
      client.createSimulation({
        name: "Teste",
        initial_capital: "100",
        currency: "USD",
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(refreshAccessToken).not.toHaveBeenCalled();
    expect(onFailure).toHaveBeenCalledWith("token");
  });

  it("propaga 403 após refresh sem invalidar a sessão renovada", async () => {
    const onFailure = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(401, { error: { code: "expired", message: "Expirado." } }),
      )
      .mockResolvedValueOnce(
        jsonResponse(403, { error: { code: "forbidden", message: "Negado." } }),
      );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "old-token",
      refreshAccessToken: async () => "new-token",
      onAuthenticationFailure: onFailure,
      fetchImplementation: fetchMock as typeof fetch,
    });

    await expect(client.getAdminMe()).rejects.toMatchObject({
      status: 403,
      code: "forbidden",
    });
    expect(onFailure).not.toHaveBeenCalled();
  });

  it.each(["POST", "PATCH"])(
    "propaga 403 em %s sem refresh ou falha de autenticação",
    async (method) => {
      const onFailure = vi.fn();
      const refreshAccessToken = vi.fn();
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(403, {
          error: { code: "forbidden", message: "Negado." },
        }),
      );
      const client = new ApiClient({
        baseUrl: "http://api.test",
        getAccessToken: async () => "valid-token",
        refreshAccessToken,
        onAuthenticationFailure: onFailure,
        fetchImplementation: fetchMock as typeof fetch,
      });

      await expect(
        client.request("/api/v1/admin/resource", { method }),
      ).rejects.toMatchObject({ status: 403, code: "forbidden" });
      expect(fetchMock).toHaveBeenCalledOnce();
      expect(refreshAccessToken).not.toHaveBeenCalled();
      expect(onFailure).not.toHaveBeenCalled();
    },
  );

  it("normaliza falha ao obter a sessão e invalida sem vazar detalhes", async () => {
    const onFailure = vi.fn();
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => {
        throw new Error("storage interno indisponível em /segredo");
      },
      onAuthenticationFailure: onFailure,
      fetchImplementation: vi.fn() as typeof fetch,
    });

    await expect(client.getAdminMe()).rejects.toMatchObject({
      status: 0,
      code: "session_unavailable",
      message: "Não foi possível validar sua sessão. Entre novamente.",
    });
    expect(onFailure).toHaveBeenCalledWith(null);
  });

  it("consulta datasets RAW com filtros bounded e autenticação", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [],
        page: 2,
        page_size: 25,
        total: 0,
        total_pages: 0,
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.listRawDatasets({
      page: 2,
      pageSize: 25,
      symbol: "BTC/USDT",
      timeframe: "1h",
    });

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);

    expect(requestedUrl.pathname).toBe("/api/v1/admin/market-data/datasets");
    expect(requestedUrl.searchParams.get("page")).toBe("2");
    expect(requestedUrl.searchParams.get("page_size")).toBe("25");
    expect(requestedUrl.searchParams.get("symbol")).toBe("BTC/USDT");
    expect(requestedUrl.searchParams.get("timeframe")).toBe("1h");

    const options = fetchMock.mock.calls[0]?.[1];
    const headers = options?.headers as Headers;

    expect(options?.method).not.toBe("POST");
    expect(options?.body).toBeUndefined();
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta detalhe RAW pelo dataset_id backend-owned sem mutação", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        dataset_id: "abc_DEF-123",
        exchange: "binance",
        market_type: "spot",
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
      }),
    );

    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    const result = await client.getRawDataset("abc_DEF-123");

    expect(result.dataset_id).toBe("abc_DEF-123");

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);

    expect(requestedUrl.pathname).toBe(
      "/api/v1/admin/market-data/datasets/abc_DEF-123",
    );

    const options = fetchMock.mock.calls[0]?.[1];
    const headers = options?.headers as Headers;

    expect(options?.method).not.toBe("POST");
    expect(options?.body).toBeUndefined();
    expect(headers.get("Authorization")).toBe("Bearer token");
  });
});
