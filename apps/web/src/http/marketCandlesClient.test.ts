import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "./client";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApiClient market candles and chart annotations", () => {
  it("codifica instrumento, cursor e limite em GET autenticado", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        items: [],
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });

    await client.getMarketCandles("BTC", "USDT", {
      timeframe: "15m",
      before: "2026-08-06T21:00:00Z",
      limit: 5000,
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://api.test/api/v1/admin/market-data/candles/BTC/USDT?timeframe=15m&before=2026-08-06T21%3A00%3A00Z&limit=5000",
    );
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("consulta anotações por sessão e intervalo meio aberto", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        orders: [],
        fills: [],
      }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "token",
      fetchImplementation: fetchMock as typeof fetch,
    });
    const sessionId = "a".repeat(64);

    await client.getPaperChartAnnotations(sessionId, {
      start: "2026-08-06T20:00:00Z",
      before: "2026-08-06T21:00:00Z",
      limit: 5000,
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/admin/paper-trading/sessions/${sessionId}/chart-annotations?start=2026-08-06T20%3A00%3A00Z&before=2026-08-06T21%3A00%3A00Z&limit=5000`,
    );
  });
});
