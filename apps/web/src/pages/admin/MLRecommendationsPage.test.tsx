import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  MLRecommendation,
  MLRecommendationPage,
} from "../../types/mlRecommendations";
import { MLRecommendationsPage } from "./MLRecommendationsPage";

const mocks = vi.hoisted(() => ({
  listMLRecommendations: vi.fn(),
}));

vi.mock("../../http/client", () => ({
  apiClient: mocks,
}));

const recommendation: MLRecommendation = {
  schema_version: 1,
  identity: "a".repeat(64),
  checksum: "b".repeat(64),
  candidate: {
    identity: "c".repeat(64),
    checksum: "d".repeat(64),
    plugin_name: "moving_average_cross",
    plugin_version: "2.1.0",
    plugin_schema_version: 1,
    lifecycle_version: 2,
    parameters: [
      {
        name: "fast_period",
        kind: "integer",
        value: 10,
      },
      {
        name: "slow_period",
        kind: "integer",
        value: 30,
      },
    ],
    parameters_checksum: "e".repeat(64),
    configuration_checksum: "f".repeat(64),
    implementation: {
      name: "strategy-engine",
      version: "8.18.0",
      content_checksum: "1".repeat(64),
    },
  },
  evidence: [
    {
      kind: "PERFORMANCE_PREDICTION",
      evidence_identity: "2".repeat(64),
      evidence_checksum: "3".repeat(64),
      information_time: "2026-09-27T04:00:00Z",
      strategy_subject_identity: "4".repeat(64),
    },
    {
      kind: "MARKET_REGIME",
      evidence_identity: "5".repeat(64),
      evidence_checksum: "6".repeat(64),
      information_time: "2026-09-27T03:55:00Z",
      strategy_subject_identity: null,
    },
  ],
  rationale_codes: [
    "favorable_market_regime",
    "positive_predicted_performance",
  ],
  recommendation_time: "2026-09-27T04:05:00Z",
  implementation: {
    name: "recommendation-engine",
    version: "1.0.0",
    content_checksum: "7".repeat(64),
  },
};

const page: MLRecommendationPage = {
  schema_version: 1,
  items: [recommendation],
  count: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listMLRecommendations.mockResolvedValue(page);
});

describe("MLRecommendationsPage", () => {
  it("carrega a fonte read-only com limite explícito", async () => {
    render(<MLRecommendationsPage />);

    expect(
      await screen.findByRole("heading", { name: "Recomendações ML" }),
    ).toBeDefined();

    await waitFor(() => {
      expect(mocks.listMLRecommendations).toHaveBeenCalledTimes(1);
    });

    expect(mocks.listMLRecommendations).toHaveBeenCalledWith(50);
  });

  it("expõe candidato, rationale, evidências e lineage", async () => {
    render(<MLRecommendationsPage />);

    expect(
      await screen.findByText("moving_average_cross · 2.1.0"),
    ).toBeDefined();

    expect(screen.getByText("favorable_market_regime")).toBeDefined();
    expect(screen.getByText("positive_predicted_performance")).toBeDefined();

    expect(screen.getByText("Previsão de performance")).toBeDefined();
    expect(screen.getByText("Regime de mercado")).toBeDefined();

    expect(screen.getByText("fast_period")).toBeDefined();
    expect(screen.getByText("slow_period")).toBeDefined();

    expect(screen.getByText("recommendation-engine")).toBeDefined();
    expect(screen.getByText("strategy-engine")).toBeDefined();

    expect(screen.getByText(recommendation.identity)).toBeDefined();
    expect(screen.getByText(recommendation.checksum)).toBeDefined();
    expect(screen.getByText(recommendation.candidate.identity)).toBeDefined();

    expect(screen.getByText("Somente leitura")).toBeDefined();
  });

  it("não apresenta autoridade de aprovação ou execução", async () => {
    render(<MLRecommendationsPage />);

    await screen.findByText("moving_average_cross · 2.1.0");

    expect(screen.queryByRole("button", { name: /aprovar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /promover/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /ativar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /executar/i })).toBeNull();
  });

  it("mostra estado vazio sem fabricar recomendações", async () => {
    mocks.listMLRecommendations.mockResolvedValue({
      schema_version: 1,
      items: [],
      count: 0,
    });

    render(<MLRecommendationsPage />);

    expect(
      await screen.findByText("Nenhuma recomendação ML disponível"),
    ).toBeDefined();

    expect(screen.queryByTestId(/^recommendation-/)).toBeNull();
  });

  it("mostra erro de leitura sem manter conteúdo inexistente", async () => {
    mocks.listMLRecommendations.mockRejectedValue(new Error("network"));

    render(<MLRecommendationsPage />);

    const alert = await screen.findByRole("alert");

    expect(alert.textContent).toContain(
      "Não foi possível carregar as recomendações ML.",
    );

    expect(screen.queryByTestId(/^recommendation-/)).toBeNull();
  });

  it("permite atualização manual somente por nova leitura", async () => {
    const user = userEvent.setup();

    mocks.listMLRecommendations
      .mockResolvedValueOnce(page)
      .mockResolvedValueOnce({
        ...page,
        items: [
          {
            ...recommendation,
            identity: "8".repeat(64),
            checksum: "9".repeat(64),
          },
        ],
      });

    render(<MLRecommendationsPage />);

    await screen.findByText("moving_average_cross · 2.1.0");

    await user.click(screen.getByRole("button", { name: "Atualizar" }));

    await waitFor(() => {
      expect(mocks.listMLRecommendations).toHaveBeenCalledTimes(2);
    });

    expect(mocks.listMLRecommendations).toHaveBeenNthCalledWith(2, 50);

    expect(await screen.findByText("8".repeat(64))).toBeDefined();
  });
});
