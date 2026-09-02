import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OperationalPaperSessionProfileCurrent } from "../../types/api";
import { OperationalPaperSessionProfilesPage } from "./OperationalPaperSessionProfilesPage";

const mocks = vi.hoisted(() => ({
  listOperationalPaperSessionProfiles: vi.fn(),
  getOperationalPaperSessionProfile: vi.fn(),
  listOperationalPaperSessionProfileRevisions: vi.fn(),
  getOperationalPaperSessionProfileRevision: vi.fn(),
  createOperationalPaperSessionProfile: vi.fn(),
  replaceOperationalPaperSessionProfileDraft: vi.fn(),
  approveOperationalPaperSessionProfile: vi.fn(),
  archiveOperationalPaperSessionProfile: vi.fn(),
  listOperationalMandates: vi.fn(),
  listStrategyDefinitions: vi.fn(),
}));

vi.mock("../../http/client", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message);
    }
  }
  return { ApiError, apiClient: mocks };
});

const ids = {
  profileA: "11111111-1111-4111-8111-111111111111",
  profileB: "22222222-2222-4222-8222-222222222222",
  mandate: "33333333-3333-4333-8333-333333333333",
  strategy: "44444444-4444-4444-8444-444444444444",
  actor: "55555555-5555-4555-8555-555555555555",
};
const checksums = {
  profile: "a".repeat(64),
  historical: "b".repeat(64),
  mandate: "c".repeat(64),
  strategy: "d".repeat(64),
  snapshot: "e".repeat(64),
};

function makeCurrent(
  profileId = ids.profileA,
  name = "Perfil A",
  state: "DRAFT" | "APPROVED" | "ARCHIVED" = "DRAFT",
  revision = 13,
  recordVersion = 29,
): OperationalPaperSessionProfileCurrent {
  return {
    profile: {
      profile_id: profileId,
      state,
      current_revision: revision,
      record_version: recordVersion,
      approved_revision: state === "DRAFT" ? null : revision,
      approved_checksum: state === "DRAFT" ? null : checksums.profile,
      created_by: ids.actor,
      created_at: "2026-08-25T10:00:00Z",
      approved_by: state === "DRAFT" ? null : ids.actor,
      approved_at: state === "DRAFT" ? null : "2026-08-25T11:00:00Z",
      archived_by: state === "ARCHIVED" ? ids.actor : null,
      archived_at: state === "ARCHIVED" ? "2026-08-25T12:00:00Z" : null,
    },
    revision: {
      profile_id: profileId,
      revision,
      specification_checksum: checksums.profile,
      created_by: ids.actor,
      created_at: "2026-08-25T10:00:00Z",
      specification: {
        schema_version: 1,
        name,
        description: "Configuração congelada",
        mandate_binding: {
          mandate_id: ids.mandate,
          approved_revision: 7,
          specification_checksum: checksums.mandate,
        },
        selected_instrument: {
          exchange: "binance",
          market_type: "spot",
          base_asset: "BTC",
          quote_asset: "USDT",
        },
        timeframe: "1h",
        start_at: "2026-08-25T12:00:00Z",
        warmup_candles: 120,
        strategy_snapshot: {
          snapshot_schema_version: 1,
          strategy_definition_id: ids.strategy,
          source_revision: 17,
          plugin_name: "ema-cross",
          plugin_version: "1.0.0",
          plugin_schema_version: 3,
          strategy_lifecycle_version: 5,
          parameters: [
            { name: "threshold", type: "decimal", value: "0.123456789" },
          ],
          parameters_checksum: checksums.strategy,
          snapshot_checksum: checksums.snapshot,
        },
        execution: {
          fees: { maker_fee_bps: "0.1", taker_fee_bps: "0.123456789" },
          slippage: { kind: "FIXED_BPS", fixed_bps: "0.1" },
          intrabar_policy: "CONSERVATIVE",
          force_close_at_end: false,
          position_sizing: {
            kind: "fixed_notional",
            value: "100.123456789",
            minimum_quote_reserve: "0.1",
          },
        },
        instrument_constraints: {
          minimum_quantity: "0.1",
          quantity_step: "0.123456789",
          price_tick: "0.1",
          minimum_notional: "10",
          maximum_notional: "1000",
        },
        risk_limits: {
          max_order_notional: "100",
          max_position_notional: "500",
          max_open_orders: 3,
          max_total_orders: 40,
          max_drawdown_pct: "0.1",
          stop_on_max_drawdown: true,
          allow_all_in: false,
          minimum_quote_reserve: "0.123456789",
          stop_loss: { kind: "fixed_percent", value: "0.1" },
        },
        history_window: 500,
        max_candles: 1000,
        max_orders: 100,
        max_events: 200,
        engine_version: "paper-engine-v1",
        market_regime_policy: {
          fast_ema_period: 10,
          slow_ema_period: 20,
          atr_period: 14,
          volatile_atr_ratio: "0.123456789",
          trend_strength_threshold: "0.1",
          schema_version: 1,
        },
      },
    },
  };
}

const currentA = makeCurrent();
const currentB = makeCurrent(ids.profileB, "Perfil B", "APPROVED", 23, 41);
const mandateCatalog = {
  items: [
    {
      mandate: {
        mandate_id: ids.mandate,
        state: "APPROVED" as const,
        current_revision: 7,
        record_version: 9,
        approved_revision: 7,
        approved_checksum: checksums.mandate,
        created_by: ids.actor,
        created_at: "2026-08-20T10:00:00Z",
        approved_by: ids.actor,
        approved_at: "2026-08-20T11:00:00Z",
        archived_by: null,
        archived_at: null,
      },
      revision: {
        mandate_id: ids.mandate,
        revision: 7,
        specification_checksum: checksums.mandate,
        created_by: ids.actor,
        created_at: "2026-08-20T10:00:00Z",
        specification: {
          schema_version: 1,
          name: "Mandato aprovado",
          description: "Escopo exato",
          instruments: [
            {
              exchange: "binance" as const,
              market_type: "spot" as const,
              base_asset: "BTC",
              quote_asset: "USDT",
            },
          ],
        },
      },
    },
  ],
  limit: 100,
  offset: 0,
  total: 1,
};
const strategyCatalog = {
  items: [
    {
      id: ids.strategy,
      display_name: "EMA Cross",
      plugin_name: "ema-cross",
      plugin_version: "1.0.0",
      plugin_schema_version: 3,
      lifecycle_version: 5,
      parameters: { threshold: { kind: "decimal" as const, value: "0.1" } },
      parameters_checksum: checksums.strategy,
      state: "ACTIVE" as const,
      revision: 17,
      created_by: ids.actor,
      updated_by: ids.actor,
      created_at: "2026-08-20T10:00:00Z",
      updated_at: "2026-08-20T10:00:00Z",
      archived_at: null,
    },
  ],
  pagination: { page: 1, page_size: 100, total: 1, total_pages: 1 },
};

beforeEach(() => {
  vi.clearAllMocks();
  strategyCatalog.items[0].revision = 17;
  strategyCatalog.items[0].parameters_checksum = checksums.strategy;
  mandateCatalog.items[0].mandate.approved_revision = 7;
  mocks.listOperationalPaperSessionProfiles.mockResolvedValue({
    items: [currentA, currentB],
    limit: 20,
    offset: 0,
    total: 2,
  });
  mocks.getOperationalPaperSessionProfile.mockImplementation(
    async (profileId: string) =>
      profileId === ids.profileB ? currentB : currentA,
  );
  mocks.listOperationalPaperSessionProfileRevisions.mockImplementation(
    async (profileId: string) => ({
      items: [
        profileId === ids.profileB ? currentB.revision : currentA.revision,
      ],
      limit: 20,
      offset: 0,
      total: 1,
    }),
  );
  mocks.getOperationalPaperSessionProfileRevision.mockResolvedValue({
    ...currentA.revision,
    revision: 2,
    specification_checksum: checksums.historical,
  });
  mocks.listOperationalMandates.mockResolvedValue(mandateCatalog);
  mocks.listStrategyDefinitions.mockResolvedValue(strategyCatalog);
  mocks.createOperationalPaperSessionProfile.mockResolvedValue(currentA);
  mocks.replaceOperationalPaperSessionProfileDraft.mockResolvedValue({
    ...currentA,
    profile: { ...currentA.profile, current_revision: 14, record_version: 30 },
    revision: { ...currentA.revision, revision: 14 },
  });
  mocks.approveOperationalPaperSessionProfile.mockResolvedValue({
    ...currentA.profile,
    state: "APPROVED",
  });
  mocks.archiveOperationalPaperSessionProfile.mockResolvedValue({
    ...currentA.profile,
    state: "ARCHIVED",
  });
});

async function renderLoaded() {
  render(<OperationalPaperSessionProfilesPage />);
  await screen.findByRole(
    "heading",
    { name: "Perfil A", level: 2 },
    { timeout: 4_000 },
  );
}

async function fillRichCreate(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Novo perfil" }));
  await user.type(screen.getByLabelText("Nome do perfil"), "Perfil completo");
  await user.type(screen.getByLabelText("Descrição"), "Intento auditável");
  await user.type(screen.getByLabelText("Timeframe canônico"), "1h");
  await user.type(
    screen.getByLabelText("Início UTC (ISO 8601)"),
    "2026-08-25T12:00:00Z",
  );
  await user.type(screen.getByLabelText("Warmup candles"), "120");
  await user.type(screen.getByLabelText("Engine version"), "paper-engine-v1");
  await user.type(screen.getByLabelText("Mandate UUID"), ids.mandate);
  await user.type(screen.getByLabelText("Approved mandate revision"), "7");
  await user.type(
    screen.getByLabelText("Mandate specification SHA-256"),
    checksums.mandate,
  );
  await user.type(screen.getByLabelText("Base asset"), "BTC");
  await user.type(screen.getByLabelText("Quote asset"), "USDT");

  const strategyFields = within(
    screen.getByRole("group", {
      name: "Estratégia — evidência exata de origem",
    }),
  ).getAllByRole("textbox");

  expect(strategyFields).toHaveLength(3);
  await user.type(strategyFields[0], ids.strategy);
  await user.type(strategyFields[1], "17");
  await user.type(strategyFields[2], checksums.strategy);
  await user.type(screen.getByLabelText("Maker fee (bps)"), "0.1");
  await user.type(screen.getByLabelText("Taker fee (bps)"), "0.123456789");
  await user.type(screen.getByLabelText("Slippage fixo (bps)"), "0.1");
  await user.selectOptions(
    screen.getByLabelText("Force close at end"),
    "false",
  );
  await user.click(screen.getByLabelText("Configurar position sizing"));
  await user.selectOptions(
    screen.getByLabelText("Position sizing kind"),
    "fixed_notional",
  );
  await user.type(
    screen.getByLabelText("Position sizing value"),
    "100.123456789",
  );
  await user.type(
    screen.getByLabelText("Position sizing minimum quote reserve"),
    "0.1",
  );
  await user.type(screen.getByLabelText("Quantidade mínima"), "0.1");
  await user.type(screen.getByLabelText("Passo de quantidade"), "0.123456789");
  await user.type(screen.getByLabelText("Tick de preço"), "0.1");
  await user.type(screen.getByLabelText("Notional mínimo"), "10");
  await user.type(screen.getByLabelText("Notional máximo (opcional)"), "1000");
  await user.type(
    screen.getByLabelText("Notional máximo por ordem (opcional)"),
    "100",
  );
  await user.type(
    screen.getByLabelText("Notional máximo da posição (opcional)"),
    "500",
  );
  await user.type(screen.getByLabelText("Máximo de ordens abertas"), "3");
  await user.type(screen.getByLabelText("Máximo total de ordens"), "40");
  await user.type(screen.getByLabelText("Drawdown máximo % (opcional)"), "0.1");
  await user.selectOptions(
    screen.getByLabelText("Parar no drawdown máximo"),
    "true",
  );
  await user.selectOptions(screen.getByLabelText("Permitir all-in"), "false");
  await user.type(
    screen.getByLabelText("Reserva mínima de cotação"),
    "0.123456789",
  );
  await user.click(screen.getByLabelText("Configurar stop loss fixed_percent"));
  await user.type(screen.getByLabelText("Stop loss %"), "0.1");
  await user.type(screen.getByLabelText("Janela histórica"), "500");
  await user.type(screen.getByLabelText("Máximo de candles"), "1000");
  await user.type(screen.getByLabelText("Máximo de ordens"), "100");
  await user.type(screen.getByLabelText("Máximo de eventos"), "200");
  await user.click(screen.getByLabelText("Incluir política completa"));
  await user.type(screen.getByLabelText("EMA rápida"), "10");
  await user.type(screen.getByLabelText("EMA lenta"), "20");
  await user.type(screen.getByLabelText("Período ATR"), "14");
  await user.type(screen.getByLabelText("Razão ATR volátil"), "0.123456789");
  await user.type(screen.getByLabelText("Limiar de força da tendência"), "0.1");
  await user.type(screen.getByLabelText("Schema da política"), "1");
}

describe("perfis administrativos de sessão paper", () => {
  it("carrega catálogo bounded, preserva ordem e encaminha filtro/paginação", async () => {
    mocks.listOperationalPaperSessionProfiles.mockResolvedValue({
      items: [currentA, currentB],
      limit: 20,
      offset: 0,
      total: 21,
    });
    const user = userEvent.setup();
    await renderLoaded();

    expect(mocks.listOperationalPaperSessionProfiles).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
      state: undefined,
    });
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("Perfil A")).toBeDefined();
    expect(within(rows[2]).getByText("Perfil B")).toBeDefined();

    await user.click(
      within(
        screen.getByRole("navigation", {
          name: "Paginação do catálogo de perfis",
        }),
      ).getByRole("button", { name: "Próxima" }),
    );
    await waitFor(() =>
      expect(mocks.listOperationalPaperSessionProfiles).toHaveBeenCalledWith({
        limit: 20,
        offset: 20,
        state: undefined,
      }),
    );

    await user.selectOptions(screen.getByLabelText("Estado"), "APPROVED");
    await waitFor(() =>
      expect(
        mocks.listOperationalPaperSessionProfiles,
      ).toHaveBeenLastCalledWith({
        limit: 20,
        offset: 0,
        state: "APPROVED",
      }),
    );
  });

  it("consulta histórico bounded e revisão histórica exata sem controles próprios", async () => {
    mocks.listOperationalPaperSessionProfileRevisions.mockResolvedValue({
      items: [currentA.revision],
      limit: 20,
      offset: 0,
      total: 21,
    });
    const user = userEvent.setup();
    await renderLoaded();
    expect(
      mocks.listOperationalPaperSessionProfileRevisions,
    ).toHaveBeenCalledWith(ids.profileA, { limit: 20, offset: 0 });
    await user.click(
      within(
        screen.getByRole("navigation", {
          name: "Paginação do histórico do perfil",
        }),
      ).getByRole("button", { name: "Próxima" }),
    );
    await waitFor(() =>
      expect(
        mocks.listOperationalPaperSessionProfileRevisions,
      ).toHaveBeenCalledWith(ids.profileA, { limit: 20, offset: 20 }),
    );
    await user.click(
      screen.getByRole("button", {
        name: "Inspecionar revisão histórica 13",
      }),
    );
    await waitFor(() =>
      expect(
        mocks.getOperationalPaperSessionProfileRevision,
      ).toHaveBeenCalledWith(ids.profileA, 13),
    );
    const historical = await screen.findByLabelText(
      "Revisão histórica imutável do perfil",
    );
    expect(within(historical).queryByRole("button")).toBeNull();
    expect(within(historical).getByText(/somente leitura/i)).toBeDefined();
  });

  it("renderiza o estado vazio do catálogo", async () => {
    mocks.listOperationalPaperSessionProfiles.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      total: 0,
    });
    render(<OperationalPaperSessionProfilesPage />);
    expect(await screen.findByText("Nenhum perfil encontrado")).toBeDefined();
  });

  it("renderiza erro seguro quando o catálogo falha", async () => {
    mocks.listOperationalPaperSessionProfiles.mockRejectedValue(
      new Error("falha segura"),
    );
    render(<OperationalPaperSessionProfilesPage />);
    expect(await screen.findByText("falha segura")).toBeDefined();
  });

  it("congela o intento completo, preserva decimais e reutiliza idempotência no retry", async () => {
    const { ApiError } = await import("../../http/client");
    mocks.createOperationalPaperSessionProfile
      .mockRejectedValueOnce(new ApiError(0, "network_error", "ambíguo"))
      .mockResolvedValueOnce(currentA);
    const user = userEvent.setup({ delay: null });
    await renderLoaded();
    await fillRichCreate(user);
    await user.click(screen.getByRole("button", { name: "Revisar criação" }));
    const dialog = screen.getByRole("alertdialog");
    expect(
      within(dialog).getByText(/Nenhuma sessão paper será iniciada/),
    ).toBeDefined();

    await user.click(
      within(dialog).getByRole("button", { name: "Criar perfil DRAFT" }),
    );
    await screen.findByText(/resposta incerta/i);
    const firstPayload =
      mocks.createOperationalPaperSessionProfile.mock.calls[0][0];
    expect(firstPayload.intent).toEqual(
      expect.objectContaining({
        name: "Perfil completo",
        mandate_binding: {
          mandate_id: ids.mandate,
          approved_revision: 7,
          specification_checksum: checksums.mandate,
        },
        selected_instrument: {
          exchange: "binance",
          market_type: "spot",
          base_asset: "BTC",
          quote_asset: "USDT",
        },
        strategy_definition_id: ids.strategy,
        expected_strategy_definition_revision: 17,
        expected_strategy_parameters_checksum: checksums.strategy,
        start_at: "2026-08-25T12:00:00Z",
        timeframe: "1h",
        history_window: 500,
        max_candles: 1000,
        max_orders: 100,
        max_events: 200,
        engine_version: "paper-engine-v1",
      }),
    );
    expect(firstPayload.intent.execution.fees).toEqual({
      maker_fee_bps: "0.1",
      taker_fee_bps: "0.123456789",
    });
    expect(firstPayload.intent.instrument_constraints.quantity_step).toBe(
      "0.123456789",
    );
    expect(firstPayload.intent.risk_limits.minimum_quote_reserve).toBe(
      "0.123456789",
    );
    expect(firstPayload.intent.market_regime_policy?.volatile_atr_ratio).toBe(
      "0.123456789",
    );

    await user.click(
      screen.getByRole("button", { name: "Repetir o mesmo envio" }),
    );
    await waitFor(() =>
      expect(mocks.createOperationalPaperSessionProfile).toHaveBeenCalledTimes(
        2,
      ),
    );
    expect(mocks.createOperationalPaperSessionProfile.mock.calls[1][0]).toEqual(
      firstPayload,
    );
  }, 15_000);

  it("substitui DRAFT com intent da revisão congelada e tokens correntes capturados", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      screen.getByRole("button", { name: "Substituir rascunho" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Revisar substituição" }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Substituir rascunho",
      }),
    );
    await waitFor(() =>
      expect(
        mocks.replaceOperationalPaperSessionProfileDraft,
      ).toHaveBeenCalledWith(
        ids.profileA,
        expect.objectContaining({
          expected_revision: 13,
          expected_record_version: 29,
          intent: expect.objectContaining({
            strategy_definition_id: ids.strategy,
            expected_strategy_definition_revision: 17,
            expected_strategy_parameters_checksum: checksums.strategy,
          }),
        }),
      ),
    );
  });

  it("aprova somente revisão corrente/checksum corrente/record version", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      screen.getByRole("button", { name: "Aprovar revisão atual" }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Aprovar perfil",
      }),
    );
    await waitFor(() =>
      expect(mocks.approveOperationalPaperSessionProfile).toHaveBeenCalledWith(
        ids.profileA,
        {
          expected_revision: 13,
          expected_checksum: checksums.profile,
          expected_record_version: 29,
        },
      ),
    );
  });

  it("arquiva com record version exata e não oferece ação a ARCHIVED", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Arquivar perfil" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Arquivar perfil",
      }),
    );
    await waitFor(() =>
      expect(mocks.archiveOperationalPaperSessionProfile).toHaveBeenCalledWith(
        ids.profileA,
        { expected_record_version: 29 },
      ),
    );
  });

  it("não oferece arquivo nem mutações para perfil já ARCHIVED", async () => {
    const archived = makeCurrent(ids.profileA, "Arquivado", "ARCHIVED");
    mocks.listOperationalPaperSessionProfiles.mockResolvedValue({
      items: [archived],
      limit: 20,
      offset: 0,
      total: 1,
    });
    mocks.getOperationalPaperSessionProfile.mockResolvedValue(archived);
    mocks.listOperationalPaperSessionProfileRevisions.mockResolvedValue({
      items: [archived.revision],
      limit: 20,
      offset: 0,
      total: 1,
    });
    render(<OperationalPaperSessionProfilesPage />);
    await screen.findByRole("heading", { name: "Arquivado", level: 2 });
    expect(
      screen.queryByRole("button", { name: "Arquivar perfil" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Aprovar revisão atual" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Substituir rascunho" }),
    ).toBeNull();
  });

  it("em conflito limpa snapshot, recarrega autoridade e não tenta novamente", async () => {
    const { ApiError } = await import("../../http/client");
    mocks.approveOperationalPaperSessionProfile.mockRejectedValue(
      new ApiError(409, "profile_conflict", "conflito"),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const callsBefore =
      mocks.getOperationalPaperSessionProfile.mock.calls.length;
    await user.click(
      screen.getByRole("button", { name: "Aprovar revisão atual" }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Aprovar perfil",
      }),
    );
    expect(
      await screen.findByText(/estado autoritativo foi recarregado/i),
    ).toBeDefined();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(mocks.approveOperationalPaperSessionProfile).toHaveBeenCalledTimes(
      1,
    );
    expect(
      mocks.getOperationalPaperSessionProfile.mock.calls.length,
    ).toBeGreaterThan(callsBefore);
  });

  it("ignora detalhe e histórico tardios do perfil anteriormente selecionado", async () => {
    let resolveA:
      ((value: OperationalPaperSessionProfileCurrent) => void) | undefined;
    let resolveHistoryA:
      | ((value: {
          items: (typeof currentA.revision)[];
          limit: number;
          offset: number;
          total: number;
        }) => void)
      | undefined;
    const detailA = new Promise<OperationalPaperSessionProfileCurrent>(
      (resolve) => {
        resolveA = resolve;
      },
    );
    const historyA = new Promise<{
      items: (typeof currentA.revision)[];
      limit: number;
      offset: number;
      total: number;
    }>((resolve) => {
      resolveHistoryA = resolve;
    });
    mocks.getOperationalPaperSessionProfile.mockImplementation(
      (profileId: string) =>
        profileId === ids.profileA ? detailA : Promise.resolve(currentB),
    );
    mocks.listOperationalPaperSessionProfileRevisions.mockImplementation(
      (profileId: string) =>
        profileId === ids.profileA
          ? historyA
          : Promise.resolve({
              items: [currentB.revision],
              limit: 20,
              offset: 0,
              total: 1,
            }),
    );
    const user = userEvent.setup();
    render(<OperationalPaperSessionProfilesPage />);
    await user.click(
      await screen.findByRole("button", {
        name: "Inspecionar perfil Perfil B",
      }),
    );
    await screen.findByRole("heading", { name: "Perfil B", level: 2 });
    resolveA?.(currentA);
    resolveHistoryA?.({
      items: [currentA.revision],
      limit: 20,
      offset: 0,
      total: 1,
    });
    await Promise.resolve();
    expect(
      screen.getByRole("heading", { name: "Perfil B", level: 2 }),
    ).toBeDefined();
    expect(
      screen.queryByRole("heading", { name: "Perfil A", level: 2 }),
    ).toBeNull();
    expect(screen.getAllByText("Revisão 23 · Perfil B")).toHaveLength(2);
  });

  it("não expõe controles de runtime, capital, runner ou materialização", async () => {
    await renderLoaded();
    for (const name of [
      /materializar/i,
      /^iniciar/i,
      /^executar/i,
      /capital/i,
      /runner/i,
      /session_id/i,
    ]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.getByText(/Aprovar um perfil não inicia/i)).toBeDefined();
  });
});
