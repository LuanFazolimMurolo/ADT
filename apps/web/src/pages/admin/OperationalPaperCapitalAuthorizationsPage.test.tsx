import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  OperationalPaperCapitalAuthorization,
  OperationalPaperSessionProfileCurrent,
  SimulationListItem,
} from "../../types/api";
import { OperationalPaperCapitalAuthorizationsPage } from "./OperationalPaperCapitalAuthorizationsPage";

const hoisted = vi.hoisted(() => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
      public requestId?: string,
    ) {
      super(message);
    }
  }
  return {
    ApiError,
    mocks: {
      listOperationalPaperCapitalAuthorizations: vi.fn(),
      getOperationalPaperCapitalAuthorization: vi.fn(),
      createOperationalPaperCapitalAuthorization: vi.fn(),
      revokeOperationalPaperCapitalAuthorization: vi.fn(),
      listOperationalPaperSessionProfiles: vi.fn(),
      listSimulations: vi.fn(),
    },
  };
});

const { ApiError, mocks } = hoisted;

vi.mock("../../http/client", () => ({
  ApiError: hoisted.ApiError,
  apiClient: hoisted.mocks,
}));

const ids = {
  authorization: "11111111-1111-4111-8111-111111111111",
  revokedAuthorization: "22222222-2222-4222-8222-222222222222",
  profile: "33333333-3333-4333-8333-333333333333",
  malformedProfile: "44444444-4444-4444-8444-444444444444",
  draftProfile: "55555555-5555-4555-8555-555555555555",
  simulation: "66666666-6666-4666-8666-666666666666",
  actor: "77777777-7777-4777-8777-777777777777",
  mandate: "88888888-8888-4888-8888-888888888888",
  strategy: "99999999-9999-4999-8999-999999999999",
};

const checksums = {
  authorization: "a".repeat(64),
  profile: "b".repeat(64),
  mandate: "c".repeat(64),
  strategy: "d".repeat(64),
  snapshot: "e".repeat(64),
};

function makeAuthorization(
  state: "AUTHORIZED" | "REVOKED" = "AUTHORIZED",
  authorizationId = ids.authorization,
  recordVersion = state === "AUTHORIZED" ? 7 : 8,
): OperationalPaperCapitalAuthorization {
  return {
    authorization_id: authorizationId,
    schema_version: 1,
    state,
    record_version: recordVersion,
    profile_binding: {
      profile_id: ids.profile,
      approved_revision: 13,
      specification_checksum: checksums.profile,
    },
    simulation_id: ids.simulation,
    quote_asset: "USDT",
    authorized_capital: "100.12345678",
    authorization_checksum: checksums.authorization,
    created_by: ids.actor,
    created_at: "2026-08-30T12:00:00Z",
    revoked_by: state === "REVOKED" ? ids.actor : null,
    revoked_at: state === "REVOKED" ? "2026-08-30T13:00:00Z" : null,
  };
}

function makeProfile(
  state: "DRAFT" | "APPROVED" | "ARCHIVED" = "APPROVED",
  profileId = ids.profile,
): OperationalPaperSessionProfileCurrent {
  const approved = state === "APPROVED";
  return {
    profile: {
      profile_id: profileId,
      state,
      current_revision: 13,
      record_version: 21,
      approved_revision: approved ? 13 : null,
      approved_checksum: approved ? checksums.profile : null,
      created_by: ids.actor,
      created_at: "2026-08-25T10:00:00Z",
      approved_by: approved ? ids.actor : null,
      approved_at: approved ? "2026-08-25T11:00:00Z" : null,
      archived_by: state === "ARCHIVED" ? ids.actor : null,
      archived_at: state === "ARCHIVED" ? "2026-08-25T12:00:00Z" : null,
    },
    revision: {
      profile_id: profileId,
      revision: 13,
      specification_checksum: checksums.profile,
      created_by: ids.actor,
      created_at: "2026-08-25T10:00:00Z",
      specification: {
        schema_version: 1,
        name: state === "DRAFT" ? "Perfil rascunho" : "Perfil BTC aprovado",
        description: "Configuração congelada",
        mandate_binding: {
          mandate_id: ids.mandate,
          approved_revision: 5,
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
          source_revision: 9,
          plugin_name: "ema-cross",
          plugin_version: "1.0.0",
          plugin_schema_version: 3,
          strategy_lifecycle_version: 5,
          parameters: [],
          parameters_checksum: checksums.strategy,
          snapshot_checksum: checksums.snapshot,
        },
        execution: {
          fees: { maker_fee_bps: "0.1", taker_fee_bps: "0.1" },
          slippage: { kind: "FIXED_BPS", fixed_bps: "0.1" },
          intrabar_policy: "CONSERVATIVE",
          force_close_at_end: false,
          position_sizing: null,
        },
        instrument_constraints: {
          minimum_quantity: "0.001",
          quantity_step: "0.001",
          price_tick: "0.01",
          minimum_notional: "10",
          maximum_notional: null,
        },
        risk_limits: {
          max_order_notional: null,
          max_position_notional: null,
          max_open_orders: 3,
          max_total_orders: 100,
          max_drawdown_pct: null,
          stop_on_max_drawdown: true,
          allow_all_in: false,
          minimum_quote_reserve: "10",
          stop_loss: null,
        },
        history_window: 500,
        max_candles: 1000,
        max_orders: 100,
        max_events: 200,
        engine_version: "paper-engine-v1",
        market_regime_policy: null,
      },
    },
  };
}

function makeSimulation(): SimulationListItem {
  return {
    id: ids.simulation,
    name: "Simulação principal",
    status: "ACTIVE",
    initial_capital: "1000.00",
    current_balance: "1000.00",
    total_profit_loss: "0.00",
    currency: "USDT",
    started_at: "2026-08-29T10:00:00Z",
    ended_at: null,
    created_at: "2026-08-29T10:00:00Z",
    updated_at: "2026-08-29T10:00:00Z",
  };
}

function authorizationPage(
  items: OperationalPaperCapitalAuthorization[] = [
    makeAuthorization(),
    makeAuthorization("REVOKED", ids.revokedAuthorization),
  ],
  offset = 0,
  total = items.length,
) {
  return { items, limit: 20, offset, total };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listOperationalPaperCapitalAuthorizations.mockResolvedValue(
    authorizationPage(),
  );
  mocks.getOperationalPaperCapitalAuthorization.mockImplementation(
    async (authorizationId: string) =>
      authorizationId === ids.revokedAuthorization
        ? makeAuthorization("REVOKED", ids.revokedAuthorization)
        : makeAuthorization(),
  );
  mocks.createOperationalPaperCapitalAuthorization.mockResolvedValue(
    makeAuthorization(),
  );
  mocks.revokeOperationalPaperCapitalAuthorization.mockResolvedValue(
    makeAuthorization("REVOKED"),
  );
  mocks.listOperationalPaperSessionProfiles.mockResolvedValue({
    items: [makeProfile()],
    limit: 100,
    offset: 0,
    total: 1,
  });
  mocks.listSimulations.mockResolvedValue({
    items: [makeSimulation()],
    pagination: { page: 1, page_size: 100, total: 1, total_pages: 1 },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function renderLoaded() {
  render(<OperationalPaperCapitalAuthorizationsPage />);
  await screen.findByRole("button", {
    name: `Inspecionar autorização ${ids.authorization}`,
  });
}

async function openAndFillCreate(
  user: ReturnType<typeof userEvent.setup>,
  capital = "100.12345678",
) {
  await user.click(screen.getByRole("button", { name: "Nova autorização" }));
  await screen.findByRole("option", { name: /Perfil BTC aprovado/ });
  await user.selectOptions(
    screen.getByLabelText("Perfil paper APPROVED"),
    ids.profile,
  );
  await user.selectOptions(
    screen.getByLabelText("Simulação ACTIVE mais recente"),
    ids.simulation,
  );
  await user.type(screen.getByLabelText("Capital autorizado"), capital);
}

describe("autorizações administrativas de capital paper", () => {
  it("carrega o catálogo bounded e aplica paginação e filtro com reset", async () => {
    mocks.listOperationalPaperCapitalAuthorizations.mockResolvedValue(
      authorizationPage([makeAuthorization()], 0, 21),
    );
    const user = userEvent.setup();
    await renderLoaded();

    expect(
      mocks.listOperationalPaperCapitalAuthorizations,
    ).toHaveBeenCalledWith({ limit: 20, offset: 0, state: undefined });

    await user.click(
      within(
        screen.getByRole("navigation", {
          name: "Paginação do catálogo de autorizações",
        }),
      ).getByRole("button", { name: "Próxima" }),
    );
    await waitFor(() =>
      expect(
        mocks.listOperationalPaperCapitalAuthorizations,
      ).toHaveBeenCalledWith({ limit: 20, offset: 20, state: undefined }),
    );

    await user.selectOptions(
      screen.getByLabelText("Estado da autorização"),
      "REVOKED",
    );
    await waitFor(() =>
      expect(
        mocks.listOperationalPaperCapitalAuthorizations,
      ).toHaveBeenLastCalledWith({ limit: 20, offset: 0, state: "REVOKED" }),
    );
  });

  it("apresenta estados explícitos de vazio e erro seguro", async () => {
    mocks.listOperationalPaperCapitalAuthorizations.mockResolvedValueOnce(
      authorizationPage([], 0, 0),
    );
    const { unmount } = render(<OperationalPaperCapitalAuthorizationsPage />);
    expect(
      await screen.findByRole("heading", {
        name: "Nenhuma autorização encontrada",
      }),
    ).toBeDefined();
    unmount();

    mocks.listOperationalPaperCapitalAuthorizations.mockRejectedValueOnce(
      new ApiError(403, "forbidden", "detalhe interno"),
    );
    render(<OperationalPaperCapitalAuthorizationsPage />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Acesso administrativo negado.",
    );
    expect(screen.queryByText("detalhe interno")).toBeNull();
  });

  it("busca e renderiza todo o detalhe público sem campos persistenciais", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      screen.getByRole("button", {
        name: `Inspecionar autorização ${ids.authorization}`,
      }),
    );

    const detail = await screen.findByRole("heading", {
      name: "Autorização selecionada",
    });
    const panel = detail.closest("section");
    expect(panel).not.toBeNull();
    const audit = within(panel as HTMLElement);
    for (const value of [
      ids.authorization,
      ids.profile,
      ids.simulation,
      checksums.profile,
      checksums.authorization,
      ids.actor,
      "2026-08-30T12:00:00Z",
    ]) {
      expect(audit.getByText(value)).toBeDefined();
    }
    expect(audit.getByText("100.12345678")).toBeDefined();
    expect(screen.queryByText("create_idempotency_key")).toBeNull();
    expect(screen.queryByText("create_intent_fingerprint")).toBeNull();
    expect(
      mocks.getOperationalPaperCapitalAuthorization,
    ).toHaveBeenCalledWith(ids.authorization);
  });

  it("trata detalhe 404 como recurso obsoleto e recarrega o catálogo", async () => {
    mocks.getOperationalPaperCapitalAuthorization.mockRejectedValueOnce(
      new ApiError(404, "not_found", "ausente"),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      screen.getByRole("button", {
        name: `Inspecionar autorização ${ids.authorization}`,
      }),
    );
    await waitFor(() =>
      expect(
        mocks.listOperationalPaperCapitalAuthorizations.mock.calls.length,
      ).toBeGreaterThan(1),
    );
    expect(
      screen.queryByRole("heading", { name: "Autorização selecionada" }),
    ).toBeNull();
  });

  it("pagina perfis APPROVED além de 100 e exclui registros não selecionáveis", async () => {
    const draft = makeProfile("DRAFT", ids.draftProfile);
    const malformed = makeProfile("APPROVED", ids.malformedProfile);
    malformed.profile.approved_checksum = null;
    mocks.listOperationalPaperSessionProfiles
      .mockResolvedValueOnce({
        items: [draft, malformed],
        limit: 100,
        offset: 0,
        total: 101,
      })
      .mockResolvedValueOnce({
        items: [makeProfile()],
        limit: 100,
        offset: 100,
        total: 101,
      });
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Nova autorização" }));

    await waitFor(() =>
      expect(mocks.listOperationalPaperSessionProfiles).toHaveBeenCalledWith({
        limit: 100,
        offset: 0,
        state: "APPROVED",
      }),
    );
    expect(screen.queryByRole("option", { name: /Perfil rascunho/ })).toBeNull();
    expect(
      screen.queryByRole("option", { name: new RegExp(ids.malformedProfile) }),
    ).toBeNull();

    await user.click(
      within(
        screen.getByRole("navigation", {
          name: "Paginação do seletor de perfis aprovados",
        }),
      ).getByRole("button", { name: "Próxima" }),
    );
    expect(
      await screen.findByRole("option", { name: /Perfil BTC aprovado/ }),
    ).toBeDefined();
    expect(mocks.listOperationalPaperSessionProfiles).toHaveBeenLastCalledWith({
      limit: 100,
      offset: 100,
      state: "APPROVED",
    });
  });

  it("usa seleções humanas autoritativas e exige do operador apenas o capital", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await openAndFillCreate(user);

    expect(mocks.listSimulations).toHaveBeenCalledWith(1, 100);
    expect(
      screen.getByRole("option", { name: /Simulação principal · USDT/ }),
    ).toBeDefined();
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(screen.queryByLabelText(/UUID/i)).toBeNull();
    expect(screen.queryByLabelText(/checksum/i)).toBeNull();
    expect(screen.queryByLabelText("Quote asset")).toBeNull();
  });

  it("valida o decimal e congela o intento exato antes do POST", async () => {
    const user = userEvent.setup();
    const key = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    vi.spyOn(crypto, "randomUUID").mockReturnValue(key);
    const profileResponse = {
      items: [makeProfile()],
      limit: 100,
      offset: 0,
      total: 1,
    };
    const simulationResponse = {
      items: [makeSimulation()],
      pagination: { page: 1, page_size: 100, total: 1, total_pages: 1 },
    };
    mocks.listOperationalPaperSessionProfiles.mockResolvedValue(profileResponse);
    mocks.listSimulations.mockResolvedValue(simulationResponse);
    await renderLoaded();
    await openAndFillCreate(user);
    await user.click(screen.getByRole("button", { name: "Revisar autorização" }));

    expect(mocks.createOperationalPaperCapitalAuthorization).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("100.12345678 USDT");
    expect(dialog.textContent).toContain("rev. 13");

    profileResponse.items[0].profile.approved_revision = 99;
    profileResponse.items[0].profile.approved_checksum = "f".repeat(64);
    simulationResponse.items[0].currency = "BRL";
    await user.click(
      within(dialog).getByRole("button", { name: "Criar autorização" }),
    );

    await waitFor(() =>
      expect(
        mocks.createOperationalPaperCapitalAuthorization,
      ).toHaveBeenCalledWith({
        intent: {
          profile_binding: {
            profile_id: ids.profile,
            approved_revision: 13,
            specification_checksum: checksums.profile,
          },
          simulation_id: ids.simulation,
          quote_asset: "USDT",
          authorized_capital: "100.12345678",
        },
        idempotency_key: key,
      }),
    );
    const payload = mocks.createOperationalPaperCapitalAuthorization.mock.calls[0][0];
    expect(JSON.stringify(payload)).not.toMatch(/actor|created_by|administrator/);
    expect(JSON.stringify(payload)).not.toMatch(
      /create_idempotency_key|create_intent_fingerprint/,
    );
  });

  it("rejeita decimais inválidos sem normalização financeira", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await openAndFillCreate(user, "100.123456789");
    await user.click(screen.getByRole("button", { name: "Revisar autorização" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "até 8 casas",
    );
    expect(mocks.createOperationalPaperCapitalAuthorization).not.toHaveBeenCalled();
  });

  it("reutiliza a mesma chave somente no replay manual de resposta ambígua", async () => {
    const user = userEvent.setup();
    const key = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    vi.spyOn(crypto, "randomUUID").mockReturnValue(key);
    mocks.createOperationalPaperCapitalAuthorization
      .mockRejectedValueOnce(new ApiError(0, "network_error", "offline"))
      .mockResolvedValueOnce(makeAuthorization());
    await renderLoaded();
    await openAndFillCreate(user);
    await user.click(screen.getByRole("button", { name: "Revisar autorização" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Criar autorização",
      }),
    );

    expect(
      await screen.findByText(/resposta da criação não foi confirmada/),
    ).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Repetir o mesmo envio" }));
    await waitFor(() =>
      expect(
        mocks.createOperationalPaperCapitalAuthorization,
      ).toHaveBeenCalledTimes(2),
    );
    expect(
      mocks.createOperationalPaperCapitalAuthorization.mock.calls[0][0],
    ).toEqual(
      mocks.createOperationalPaperCapitalAuthorization.mock.calls[1][0],
    );
    expect(crypto.randomUUID).toHaveBeenCalledOnce();
  });

  it("gera nova chave após cancelar, editar e confirmar um novo intento", async () => {
    const user = userEvent.setup();
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
      .mockReturnValueOnce("dddddddd-dddd-4ddd-8ddd-dddddddddddd");
    await renderLoaded();
    await openAndFillCreate(user, "10.00");
    await user.click(screen.getByRole("button", { name: "Revisar autorização" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Voltar",
      }),
    );
    const capital = screen.getByLabelText("Capital autorizado");
    await user.clear(capital);
    await user.type(capital, "20.00");
    await user.click(screen.getByRole("button", { name: "Revisar autorização" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Criar autorização",
      }),
    );
    await waitFor(() =>
      expect(
        mocks.createOperationalPaperCapitalAuthorization,
      ).toHaveBeenCalledWith(
        expect.objectContaining({
          idempotency_key: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          intent: expect.objectContaining({ authorized_capital: "20.00" }),
        }),
      ),
    );
    expect(crypto.randomUUID).toHaveBeenCalledTimes(2);
  });

  it("não repete create em 409 e recarrega as autoridades", async () => {
    const user = userEvent.setup();
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    );
    mocks.createOperationalPaperCapitalAuthorization.mockRejectedValueOnce(
      new ApiError(409, "conflict", "conflito"),
    );
    await renderLoaded();
    await openAndFillCreate(user);
    const listCallsBefore =
      mocks.listOperationalPaperCapitalAuthorizations.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Revisar autorização" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Criar autorização",
      }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "mudou no servidor",
    );
    expect(
      mocks.createOperationalPaperCapitalAuthorization,
    ).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Repetir o mesmo envio" })).toBeNull();
    expect(
      mocks.listOperationalPaperCapitalAuthorizations.mock.calls.length,
    ).toBeGreaterThan(listCallsBefore);
  });

  it("no sucesso recarrega lista e detalhe autoritativos", async () => {
    const user = userEvent.setup();
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "ffffffff-ffff-4fff-8fff-ffffffffffff",
    );
    await renderLoaded();
    await openAndFillCreate(user);
    const listCallsBefore =
      mocks.listOperationalPaperCapitalAuthorizations.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Revisar autorização" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Criar autorização",
      }),
    );
    expect(
      await screen.findByText(/Autorização criada e recarregada/),
    ).toBeDefined();
    expect(
      mocks.listOperationalPaperCapitalAuthorizations.mock.calls.length,
    ).toBeGreaterThan(listCallsBefore);
    expect(
      mocks.getOperationalPaperCapitalAuthorization,
    ).toHaveBeenCalledWith(ids.authorization);
  });

  it("mostra revoke somente para AUTHORIZED e congela id/versão no payload mínimo", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const rows = screen.getAllByRole("row");
    expect(
      within(rows[1]).getByRole("button", {
        name: `Revogar autorização ${ids.authorization}`,
      }),
    ).toBeDefined();
    expect(within(rows[2]).queryByRole("button", { name: /Revogar/ })).toBeNull();

    await user.click(
      within(rows[1]).getByRole("button", {
        name: `Inspecionar autorização ${ids.authorization}`,
      }),
    );
    await screen.findByRole("heading", { name: "Autorização selecionada" });
    await user.click(
      within(rows[1]).getByRole("button", {
        name: `Revogar autorização ${ids.authorization}`,
      }),
    );
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("versão esperada 7");
    await user.click(
      within(dialog).getByRole("button", { name: "Revogar autorização" }),
    );
    await waitFor(() =>
      expect(
        mocks.revokeOperationalPaperCapitalAuthorization,
      ).toHaveBeenCalledWith(ids.authorization, { expected_record_version: 7 }),
    );
    expect(
      mocks.revokeOperationalPaperCapitalAuthorization.mock.calls[0][1],
    ).toEqual({ expected_record_version: 7 });
    expect(
      mocks.getOperationalPaperCapitalAuthorization.mock.calls.length,
    ).toBeGreaterThan(1);
  });

  it("em revoke 409 recarrega autoridade, bloqueia retry e exige revisão", async () => {
    const user = userEvent.setup();
    mocks.revokeOperationalPaperCapitalAuthorization.mockRejectedValueOnce(
      new ApiError(409, "conflict", "conflito"),
    );
    mocks.getOperationalPaperCapitalAuthorization.mockResolvedValue(
      makeAuthorization("AUTHORIZED", ids.authorization, 8),
    );
    await renderLoaded();
    await user.click(
      screen.getByRole("button", {
        name: `Revogar autorização ${ids.authorization}`,
      }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Revogar autorização",
      }),
    );

    expect(
      await screen.findByText(/estado da autorização foi recarregado após conflito/),
    ).toBeDefined();
    expect(
      mocks.revokeOperationalPaperCapitalAuthorization,
    ).toHaveBeenCalledOnce();
    expect(
      (
        screen.getByRole("button", {
          name: `Revogar autorização ${ids.authorization}`,
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      mocks.getOperationalPaperCapitalAuthorization,
    ).toHaveBeenCalledWith(ids.authorization);

    await user.click(
      screen.getByRole("button", {
        name: "Marcar estado recarregado como revisado",
      }),
    );
    expect(
      (
        screen.getByRole("button", {
          name: `Revogar autorização ${ids.authorization}`,
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
  });
});
