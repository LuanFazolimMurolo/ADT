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

  it("consulta gaps RAW bounded por GET sem mutação", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        dataset_id: "abc_DEF-123",
        exchange: "binance",
        market_type: "spot",
        symbol: "BTC/USDT",
        timeframe: "1h",
        dataset_version: "a".repeat(64),
        version_algorithm: "raw-partition-canonical-sha256-v1",
        checked_start: "2026-08-01T00:00:00Z",
        checked_end: "2026-08-02T00:00:00Z",
        expected_candles: 24,
        observed_candles: 23,
        missing_candles: 1,
        total_gap_count: 1,
        page: 2,
        page_size: 100,
        total_pages: 2,
        items: [],
      }),
    );

    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.getRawDatasetGaps("abc_DEF-123", {
      start: "2026-08-01T00:00:00Z",
      end: "2026-08-02T00:00:00Z",
      page: 2,
      pageSize: 100,
    });

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);

    expect(requestedUrl.pathname).toBe(
      "/api/v1/admin/market-data/datasets/abc_DEF-123/gaps",
    );
    expect(requestedUrl.searchParams.get("start")).toBe("2026-08-01T00:00:00Z");
    expect(requestedUrl.searchParams.get("end")).toBe("2026-08-02T00:00:00Z");
    expect(requestedUrl.searchParams.get("page")).toBe("2");
    expect(requestedUrl.searchParams.get("page_size")).toBe("100");

    const options = fetchMock.mock.calls[0]?.[1];
    const headers = options?.headers as Headers;

    expect(options?.method).not.toBe("POST");
    expect(options?.method).not.toBe("PATCH");
    expect(options?.body).toBeUndefined();
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta quality RAW persistida por GET sem scan ou mutação", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        dataset_id: "abc_DEF-123",
        exchange: "binance",
        market_type: "spot",
        symbol: "BTC/USDT",
        timeframe: "1h",
        status: "CURRENT",
        dataset_version: "a".repeat(64),
        version_algorithm: "raw-partition-canonical-sha256-v1",
        baseline_dataset_version: "a".repeat(64),
        baseline_version_algorithm: "raw-partition-canonical-sha256-v1",
        scanner_schema_version: 2,
        scanner_version: "phase2c-3",
        coverage: null,
        partition_count: 1,
        issue_totals: null,
        issues: [],
      }),
    );

    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    const result = await client.getRawDatasetQuality("abc_DEF-123");

    expect(result.status).toBe("CURRENT");

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);

    expect(requestedUrl.pathname).toBe(
      "/api/v1/admin/market-data/datasets/abc_DEF-123/quality",
    );
    expect(requestedUrl.search).toBe("");

    const options = fetchMock.mock.calls[0]?.[1];
    const headers = options?.headers as Headers;

    expect(options?.method).not.toBe("POST");
    expect(options?.method).not.toBe("PATCH");
    expect(options?.body).toBeUndefined();
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta runtimes do worker por GET autenticado e bounded", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        observed_at: "2026-08-20T21:00:00Z",
        stale_after_seconds: 120,
        count: 0,
        items: [],
      }),
    );

    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.listWorkerRuntimes(7);

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);

    expect(requestedUrl.pathname).toBe(
      "/api/v1/admin/market-data/worker-observability/runtimes",
    );
    expect(requestedUrl.searchParams.get("limit")).toBe("7");

    const options = fetchMock.mock.calls[0]?.[1];
    const headers = options?.headers as Headers;

    expect(options?.method).not.toBe("POST");
    expect(options?.method).not.toBe("PATCH");
    expect(options?.method).not.toBe("DELETE");
    expect(options?.body).toBeUndefined();
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta eventos do worker por GET autenticado e bounded", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        observed_at: "2026-08-20T21:00:00Z",
        count: 0,
        items: [],
      }),
    );

    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.listWorkerRuntimeEvents(9);

    const requestedUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);

    expect(requestedUrl.pathname).toBe(
      "/api/v1/admin/market-data/worker-observability/events",
    );
    expect(requestedUrl.searchParams.get("limit")).toBe("9");

    const options = fetchMock.mock.calls[0]?.[1];
    const headers = options?.headers as Headers;

    expect(options?.method).not.toBe("POST");
    expect(options?.method).not.toBe("PATCH");
    expect(options?.method).not.toBe("DELETE");
    expect(options?.body).toBeUndefined();
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta catálogo, detalhe e revisões de mandatos por GET bounded", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.listOperationalMandates({
      limit: 20,
      offset: 40,
      state: "APPROVED",
    });
    await client.getOperationalMandate("mandate/id");
    await client.listOperationalMandateRevisions("mandate/id", {
      limit: 10,
      offset: 20,
    });
    await client.getOperationalMandateRevision("mandate/id", 3);

    const urls = fetchMock.mock.calls.map((call) => new URL(call[0] as string));
    expect(urls.map((url) => url.pathname)).toEqual([
      "/api/v1/admin/operational-mandates",
      "/api/v1/admin/operational-mandates/mandate%2Fid",
      "/api/v1/admin/operational-mandates/mandate%2Fid/revisions",
      "/api/v1/admin/operational-mandates/mandate%2Fid/revisions/3",
    ]);
    expect(urls[0].search).toBe("?limit=20&offset=40&state=APPROVED");
    expect(urls[2].search).toBe("?limit=10&offset=20");
    for (const call of fetchMock.mock.calls) {
      expect(call[1]?.method).toBeUndefined();
      expect((call[1]?.headers as Headers).get("Authorization")).toBe(
        "Bearer token",
      );
    }
  });

  it("envia somente os contratos publicados nas quatro mutações de mandato", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });
    const specification = {
      schema_version: 1,
      name: "Principal",
      description: "Escopo operacional",
      instruments: [
        {
          exchange: "binance" as const,
          market_type: "spot" as const,
          base_asset: "BTC",
          quote_asset: "USDT",
        },
      ],
    };

    await client.createOperationalMandate({
      specification,
      idempotency_key: "intent-key",
    });
    await client.replaceOperationalMandateDraft("mandate/id", {
      specification,
      expected_revision: 2,
      expected_record_version: 4,
    });
    await client.approveOperationalMandate("mandate/id", {
      expected_revision: 2,
      expected_checksum: "a".repeat(64),
      expected_record_version: 4,
    });
    await client.archiveOperationalMandate("mandate/id", {
      expected_record_version: 5,
    });

    expect(
      fetchMock.mock.calls.map((call) => [
        new URL(call[0] as string).pathname,
        call[1]?.method,
        JSON.parse(String(call[1]?.body)),
      ]),
    ).toEqual([
      [
        "/api/v1/admin/operational-mandates",
        "POST",
        { specification, idempotency_key: "intent-key" },
      ],
      [
        "/api/v1/admin/operational-mandates/mandate%2Fid",
        "PATCH",
        {
          specification,
          expected_revision: 2,
          expected_record_version: 4,
        },
      ],
      [
        "/api/v1/admin/operational-mandates/mandate%2Fid/approve",
        "POST",
        {
          expected_revision: 2,
          expected_checksum: "a".repeat(64),
          expected_record_version: 4,
        },
      ],
      [
        "/api/v1/admin/operational-mandates/mandate%2Fid/archive",
        "POST",
        { expected_record_version: 5 },
      ],
    ]);
  });

  it("consulta catálogo, detalhe e revisões de perfis paper pelos caminhos exatos", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "profile-token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.listOperationalPaperSessionProfiles({
      limit: 20,
      offset: 40,
      state: "APPROVED",
    });
    await client.getOperationalPaperSessionProfile("profile/id");
    await client.listOperationalPaperSessionProfileRevisions("profile/id", {
      limit: 10,
      offset: 20,
    });
    await client.getOperationalPaperSessionProfileRevision("profile/id", 7);

    const urls = fetchMock.mock.calls.map((call) => new URL(call[0] as string));
    expect(urls.map((url) => url.pathname)).toEqual([
      "/api/v1/admin/operational-paper-session-profiles",
      "/api/v1/admin/operational-paper-session-profiles/profile%2Fid",
      "/api/v1/admin/operational-paper-session-profiles/profile%2Fid/revisions",
      "/api/v1/admin/operational-paper-session-profiles/profile%2Fid/revisions/7",
    ]);
    expect(urls[0].search).toBe("?limit=20&offset=40&state=APPROVED");
    expect(urls[2].search).toBe("?limit=10&offset=20");
    for (const call of fetchMock.mock.calls) {
      expect(call[1]?.method).toBeUndefined();
      expect((call[1]?.headers as Headers).get("Authorization")).toBe(
        "Bearer profile-token",
      );
    }
  });

  it("envia exatamente os quatro contratos de mutação do perfil paper", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });
    const intent = {
      name: "Perfil auditável",
      description: "Configuração administrativa",
      mandate_binding: {
        mandate_id: "11111111-1111-4111-8111-111111111111",
        approved_revision: 13,
        specification_checksum: "a".repeat(64),
      },
      selected_instrument: {
        exchange: "binance" as const,
        market_type: "spot" as const,
        base_asset: "BTC",
        quote_asset: "USDT",
      },
      timeframe: "1h",
      start_at: "2026-08-25T12:00:00Z",
      warmup_candles: 120,
      strategy_definition_id: "22222222-2222-4222-8222-222222222222",
      expected_strategy_definition_revision: 17,
      expected_strategy_parameters_checksum: "b".repeat(64),
      execution: {
        fees: { maker_fee_bps: "0.1", taker_fee_bps: "0.123456789" },
        slippage: { kind: "FIXED_BPS" as const, fixed_bps: "0.1" },
        intrabar_policy: "CONSERVATIVE" as const,
        force_close_at_end: true,
        position_sizing: null,
      },
      instrument_constraints: {
        minimum_quantity: "0.1",
        quantity_step: "0.1",
        price_tick: "0.123456789",
        minimum_notional: "10",
        maximum_notional: null,
      },
      risk_limits: {
        max_order_notional: "100",
        max_position_notional: "200",
        max_open_orders: 3,
        max_total_orders: 50,
        max_drawdown_pct: "0.1",
        stop_on_max_drawdown: true,
        allow_all_in: false,
        minimum_quote_reserve: "0.123456789",
        stop_loss: null,
      },
      history_window: 500,
      max_candles: 1000,
      max_orders: 100,
      max_events: 200,
      engine_version: "paper-engine-v1",
      market_regime_policy: null,
    };

    await client.createOperationalPaperSessionProfile({
      intent,
      idempotency_key: "profile-intent-key",
    });
    await client.replaceOperationalPaperSessionProfileDraft("profile/id", {
      intent,
      expected_revision: 19,
      expected_record_version: 23,
    });
    await client.approveOperationalPaperSessionProfile("profile/id", {
      expected_revision: 29,
      expected_checksum: "c".repeat(64),
      expected_record_version: 31,
    });
    await client.archiveOperationalPaperSessionProfile("profile/id", {
      expected_record_version: 37,
    });

    expect(
      fetchMock.mock.calls.map((call) => [
        new URL(call[0] as string).pathname,
        call[1]?.method,
        JSON.parse(String(call[1]?.body)),
      ]),
    ).toEqual([
      [
        "/api/v1/admin/operational-paper-session-profiles",
        "POST",
        { intent, idempotency_key: "profile-intent-key" },
      ],
      [
        "/api/v1/admin/operational-paper-session-profiles/profile%2Fid",
        "PATCH",
        { intent, expected_revision: 19, expected_record_version: 23 },
      ],
      [
        "/api/v1/admin/operational-paper-session-profiles/profile%2Fid/approve",
        "POST",
        {
          expected_revision: 29,
          expected_checksum: "c".repeat(64),
          expected_record_version: 31,
        },
      ],
      [
        "/api/v1/admin/operational-paper-session-profiles/profile%2Fid/archive",
        "POST",
        { expected_record_version: 37 },
      ],
    ]);
  });

  it("consulta autorizações de capital paper com query, detalhe codificado e decimal string", async () => {
    const authorization = {
      authorization_id: "33333333-3333-4333-8333-333333333333",
      schema_version: 1,
      state: "AUTHORIZED",
      record_version: 1,
      profile_binding: {
        profile_id: "11111111-1111-4111-8111-111111111111",
        approved_revision: 7,
        specification_checksum: "a".repeat(64),
      },
      simulation_id: "22222222-2222-4222-8222-222222222222",
      quote_asset: "USDT",
      authorized_capital: "100.12345678",
      authorization_checksum: "b".repeat(64),
      created_by: "44444444-4444-4444-8444-444444444444",
      created_at: "2026-08-30T12:00:00Z",
      revoked_by: null,
      revoked_at: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(200, {
          items: [authorization],
          limit: 20,
          offset: 0,
          total: 1,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          items: [authorization],
          limit: 10,
          offset: 30,
          total: 1,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, authorization));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "capital-token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    const defaultPage =
      await client.listOperationalPaperCapitalAuthorizations();
    await client.listOperationalPaperCapitalAuthorizations({
      limit: 10,
      offset: 30,
      state: "REVOKED",
    });
    const detail =
      await client.getOperationalPaperCapitalAuthorization("authorization/id");

    const authorizedCapital: string = defaultPage.items[0].authorized_capital;
    expect(authorizedCapital).toBe("100.12345678");
    expect(detail.authorized_capital).toBe("100.12345678");
    expect(detail).not.toHaveProperty("create_idempotency_key");
    expect(detail).not.toHaveProperty("create_intent_fingerprint");

    const urls = fetchMock.mock.calls.map((call) => new URL(call[0] as string));
    expect(urls.map((url) => `${url.pathname}${url.search}`)).toEqual([
      "/api/v1/admin/operational-paper-capital-authorizations",
      "/api/v1/admin/operational-paper-capital-authorizations?limit=10&offset=30&state=REVOKED",
      "/api/v1/admin/operational-paper-capital-authorizations/authorization%2Fid",
    ]);
    for (const call of fetchMock.mock.calls) {
      expect(call[1]?.method).toBeUndefined();
      expect((call[1]?.headers as Headers).get("Authorization")).toBe(
        "Bearer capital-token",
      );
    }
  });

  it("envia criação e revogação de capital paper com payloads exatos", async () => {
    const authorization = {
      authorization_id: "33333333-3333-4333-8333-333333333333",
      schema_version: 1,
      state: "AUTHORIZED",
      record_version: 1,
      profile_binding: {
        profile_id: "11111111-1111-4111-8111-111111111111",
        approved_revision: 7,
        specification_checksum: "a".repeat(64),
      },
      simulation_id: "22222222-2222-4222-8222-222222222222",
      quote_asset: "USDT",
      authorized_capital: "100.12345678",
      authorization_checksum: "b".repeat(64),
      created_by: "44444444-4444-4444-8444-444444444444",
      created_at: "2026-08-30T12:00:00Z",
      revoked_by: null,
      revoked_at: null,
    };
    const createPayload = {
      intent: {
        profile_binding: authorization.profile_binding,
        simulation_id: authorization.simulation_id,
        quote_asset: authorization.quote_asset,
        authorized_capital: "100.12345678",
      },
      idempotency_key: "capital-intent-key",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(201, authorization))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          ...authorization,
          state: "REVOKED",
          record_version: 2,
          revoked_by: "44444444-4444-4444-8444-444444444444",
          revoked_at: "2026-08-30T13:00:00Z",
        }),
      );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "mutation-token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    const created =
      await client.createOperationalPaperCapitalAuthorization(createPayload);
    const revoked = await client.revokeOperationalPaperCapitalAuthorization(
      "authorization/id",
      { expected_record_version: 1 },
    );

    expect(created.authorized_capital).toBe("100.12345678");
    expect(revoked.authorized_capital).toBe("100.12345678");
    expect(
      fetchMock.mock.calls.map((call) => [
        new URL(call[0] as string).pathname,
        call[1]?.method,
        JSON.parse(String(call[1]?.body)),
      ]),
    ).toEqual([
      [
        "/api/v1/admin/operational-paper-capital-authorizations",
        "POST",
        createPayload,
      ],
      [
        "/api/v1/admin/operational-paper-capital-authorizations/authorization%2Fid/revoke",
        "POST",
        { expected_record_version: 1 },
      ],
    ]);
    const serializedCreate = JSON.parse(
      String(fetchMock.mock.calls[0]?.[1]?.body),
    );
    expect(serializedCreate.intent.authorized_capital).toBe("100.12345678");
    expect(serializedCreate).not.toHaveProperty("created_by");
    expect(serializedCreate).not.toHaveProperty("actor_id");
    expect(serializedCreate).not.toHaveProperty("administrator_id");
    expect(serializedCreate).not.toHaveProperty("create_idempotency_key");
    expect(serializedCreate).not.toHaveProperty("create_intent_fingerprint");
    for (const call of fetchMock.mock.calls) {
      expect((call[1]?.headers as Headers).get("Authorization")).toBe(
        "Bearer mutation-token",
      );
    }
  });

  it.each([401, 409])(
    "não repete criação de autorização de capital paper após %i",
    async (status) => {
      const refreshAccessToken = vi.fn();
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(status, {
          error: { code: `capital_${status}`, message: "Conflito seguro." },
        }),
      );
      const client = new ApiClient({
        baseUrl: "http://api.test",
        getAccessToken: async () => "token",
        refreshAccessToken,
        fetchImplementation: fetchMock as typeof fetch,
      });

      await expect(
        client.createOperationalPaperCapitalAuthorization({
          intent: {
            profile_binding: {
              profile_id: "11111111-1111-4111-8111-111111111111",
              approved_revision: 7,
              specification_checksum: "a".repeat(64),
            },
            simulation_id: "22222222-2222-4222-8222-222222222222",
            quote_asset: "USDT",
            authorized_capital: "100.12345678",
          },
          idempotency_key: "capital-intent-key",
        }),
      ).rejects.toMatchObject({ status });
      expect(fetchMock).toHaveBeenCalledOnce();
      expect(refreshAccessToken).not.toHaveBeenCalled();
    },
  );

  it("consulta descoberta administrativa de estratégias com query exata", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { items: [], pagination: {} }));
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "strategy-token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.listStrategyDefinitions({
      page: 2,
      pageSize: 50,
      includeArchived: false,
    });

    const url = new URL(fetchMock.mock.calls[0]?.[0] as string);
    expect(url.pathname).toBe("/api/v1/admin/strategies");
    expect(url.search).toBe("?page=2&page_size=50&include_archived=false");
    expect(
      (fetchMock.mock.calls[0]?.[1]?.headers as Headers).get("Authorization"),
    ).toBe("Bearer strategy-token");
  });
});
