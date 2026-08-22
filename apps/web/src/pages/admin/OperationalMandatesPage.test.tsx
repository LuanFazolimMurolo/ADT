import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  OperationalMandateCurrent,
  OperationalMandateRevision,
} from "../../types/api";
import { ApiError } from "../../http/client";
import { OperationalMandatesPage } from "./OperationalMandatesPage";

const mocks = vi.hoisted(() => ({
  listOperationalMandates: vi.fn(),
  getOperationalMandate: vi.fn(),
  listOperationalMandateRevisions: vi.fn(),
  getOperationalMandateRevision: vi.fn(),
  createOperationalMandate: vi.fn(),
  replaceOperationalMandateDraft: vi.fn(),
  approveOperationalMandate: vi.fn(),
  archiveOperationalMandate: vi.fn(),
}));

vi.mock("../../http/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../http/client")>();
  return { ...original, apiClient: mocks };
});

const MANDATE_ID = "10000000-0000-4000-8000-000000000001";
const SECOND_MANDATE_ID = "10000000-0000-4000-8000-000000000002";
const ACTOR_ID = "90000000-0000-4000-8000-000000000001";

function revision(
  number: number,
  overrides: Partial<OperationalMandateRevision> = {},
): OperationalMandateRevision {
  return {
    mandate_id: MANDATE_ID,
    revision: number,
    specification: {
      schema_version: 1,
      name: number === 1 ? "Mandato inicial" : "Mandato principal",
      description: `Descrição da revisão ${number}`,
      instruments: [
        {
          exchange: "binance",
          market_type: "spot",
          base_asset: number === 1 ? "ETH" : "BTC",
          quote_asset: "USDT",
        },
      ],
    },
    specification_checksum: (number === 1 ? "a" : "b").repeat(64),
    created_by: ACTOR_ID,
    created_at: `2026-08-2${number}T10:00:00Z`,
    ...overrides,
  };
}

function current(
  overrides: Partial<OperationalMandateCurrent> = {},
): OperationalMandateCurrent {
  const currentRevision = revision(2);
  return {
    mandate: {
      mandate_id: MANDATE_ID,
      state: "DRAFT",
      current_revision: 2,
      record_version: 4,
      approved_revision: null,
      approved_checksum: null,
      created_by: ACTOR_ID,
      created_at: "2026-08-21T10:00:00Z",
      approved_by: null,
      approved_at: null,
      archived_by: null,
      archived_at: null,
    },
    revision: currentRevision,
    ...overrides,
  };
}

function listResponse(items = [current()], total = items.length) {
  return { items, limit: 20, offset: 0, total };
}

function historyResponse(items = [revision(2), revision(1)]) {
  return { items, limit: 20, offset: 0, total: items.length };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listOperationalMandates.mockResolvedValue(listResponse());
  mocks.getOperationalMandate.mockResolvedValue(current());
  mocks.listOperationalMandateRevisions.mockResolvedValue(historyResponse());
  mocks.getOperationalMandateRevision.mockImplementation(
    (_mandateId: string, number: number) => Promise.resolve(revision(number)),
  );
  mocks.createOperationalMandate.mockResolvedValue(current());
  mocks.replaceOperationalMandateDraft.mockResolvedValue(current());
  mocks.approveOperationalMandate.mockResolvedValue({
    ...current().mandate,
    state: "APPROVED",
  });
  mocks.archiveOperationalMandate.mockResolvedValue({
    ...current().mandate,
    state: "ARCHIVED",
  });
});

describe("OperationalMandatesPage", () => {
  it("apresenta loading, vazio e erro seguro no catálogo", async () => {
    let release: ((value: ReturnType<typeof listResponse>) => void) | undefined;
    mocks.listOperationalMandates.mockReturnValueOnce(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const { unmount } = render(<OperationalMandatesPage />);
    expect(screen.getByRole("status").textContent).toContain(
      "Carregando mandatos",
    );
    await act(async () => release?.(listResponse([])));
    expect(await screen.findByText("Nenhum mandato encontrado")).toBeDefined();
    unmount();

    mocks.listOperationalMandates.mockRejectedValueOnce(
      new ApiError(503, "database_unavailable", "trace interna"),
    );
    render(<OperationalMandatesPage />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Não foi possível carregar os mandatos",
    );
    expect(screen.queryByText("trace interna")).toBeNull();
  });

  it("filtra estados e pagina usando limit, offset e total da API", async () => {
    const user = userEvent.setup();
    mocks.listOperationalMandates
      .mockResolvedValueOnce(listResponse([current()], 41))
      .mockResolvedValueOnce({ ...listResponse([current()], 41), offset: 0 })
      .mockResolvedValueOnce({ ...listResponse([current()], 41), offset: 20 });
    render(<OperationalMandatesPage />);
    await screen.findByText("Página 1 de 3 · 41 registro(s)");

    await user.selectOptions(screen.getByLabelText("Estado"), "APPROVED");
    await waitFor(() =>
      expect(mocks.listOperationalMandates).toHaveBeenLastCalledWith({
        limit: 20,
        offset: 0,
        state: "APPROVED",
      }),
    );
    await user.click(
      within(
        screen.getByRole("navigation", {
          name: "Paginação do catálogo de mandatos",
        }),
      ).getByRole("button", { name: "Próxima" }),
    );
    await waitFor(() =>
      expect(mocks.listOperationalMandates).toHaveBeenLastCalledWith({
        limit: 20,
        offset: 20,
        state: "APPROVED",
      }),
    );
    expect(screen.getAllByText(/Rascunho \(DRAFT\)/).length).toBeGreaterThan(0);
  });

  it("renderiza agregado atual, tokens, auditoria e instrumentos canônicos", async () => {
    render(<OperationalMandatesPage />);
    await screen.findByRole("heading", { name: "Mandato principal" });

    const detail = screen.getByRole("heading", {
      name: "Mandato principal",
    }).closest("section");
    expect(detail).not.toBeNull();
    expect(within(detail as HTMLElement).getByText("Revisão atual")).toBeDefined();
    expect(within(detail as HTMLElement).getByText("Versão do registro")).toBeDefined();
    expect(within(detail as HTMLElement).getAllByText(ACTOR_ID).length).toBeGreaterThan(0);
    expect(within(detail as HTMLElement).getByText("BTC/USDT")).toBeDefined();
    expect(within(detail as HTMLElement).getByText("b".repeat(64))).toBeDefined();
    expect(within(detail as HTMLElement).getByText(/Rascunho \(DRAFT\)/)).toBeDefined();
  });

  it("preserva a ordem newest-first e inspeciona uma revisão histórica exata", async () => {
    const user = userEvent.setup();
    render(<OperationalMandatesPage />);
    const revisionButtons = await screen.findAllByRole("button", {
      name: /Inspecionar revisão histórica/,
    });
    expect(revisionButtons.map((button) => button.textContent)).toEqual([
      "Inspecionar revisão 2",
      "Inspecionar revisão 1",
    ]);

    await user.click(revisionButtons[1]);
    expect(
      await screen.findByRole("heading", { name: "Revisão 1" }),
    ).toBeDefined();
    const historical = screen.getByLabelText("Revisão histórica exata");
    expect(within(historical).getByText("ETH/USDT")).toBeDefined();
    expect(within(historical).getByText("a".repeat(64))).toBeDefined();
    expect(within(historical).getByText(/somente a especificação histórica/)).toBeDefined();
    expect(within(historical).queryByText(/Rascunho \(DRAFT\)/)).toBeNull();
    expect(mocks.listOperationalMandateRevisions).toHaveBeenCalledWith(
      MANDATE_ID,
      { limit: 20, offset: 0 },
    );
    expect(mocks.getOperationalMandateRevision).toHaveBeenCalledWith(
      MANDATE_ID,
      1,
    );
  });

  it("cria payload mínimo sem ator e conserva a chave no retry ambíguo", async () => {
    const user = userEvent.setup();
    const key = "11111111-1111-4111-8111-111111111111";
    vi.spyOn(crypto, "randomUUID").mockReturnValue(key);
    mocks.createOperationalMandate
      .mockRejectedValueOnce(new ApiError(0, "network_error", "offline"))
      .mockResolvedValueOnce(current());
    render(<OperationalMandatesPage />);
    await screen.findByRole("heading", { name: "Mandato principal" });

    await user.click(screen.getByRole("button", { name: "Novo mandato" }));
    await user.type(screen.getByLabelText("Nome"), "Mandato BTC");
    await user.type(screen.getByLabelText("Descrição"), "Somente spot");
    await user.type(screen.getByLabelText("Ativo base"), "btc");
    await user.click(screen.getByRole("button", { name: "Revisar criação" }));
    expect(mocks.createOperationalMandate).not.toHaveBeenCalled();
    await within(screen.getByRole("alertdialog")).getByRole("button", {
      name: "Criar mandato DRAFT",
    }).click();
    expect(await screen.findByText(/resposta da criação não foi confirmada/)).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Repetir o mesmo envio" }));
    await waitFor(() => expect(mocks.createOperationalMandate).toHaveBeenCalledTimes(2));

    const [firstPayload] = mocks.createOperationalMandate.mock.calls[0];
    const [secondPayload] = mocks.createOperationalMandate.mock.calls[1];
    expect(firstPayload).toEqual(secondPayload);
    expect(firstPayload).toEqual({
      specification: {
        schema_version: 1,
        name: "Mandato BTC",
        description: "Somente spot",
        instruments: [
          {
            exchange: "binance",
            market_type: "spot",
            base_asset: "BTC",
            quote_asset: "USDT",
          },
        ],
      },
      idempotency_key: key,
    });
    expect(JSON.stringify(firstPayload)).not.toContain("actor");
    expect(crypto.randomUUID).toHaveBeenCalledOnce();
    expect(mocks.listOperationalMandates.mock.calls.length).toBeGreaterThan(1);
  });

  it("substitui com os tokens exibidos e aceita NOOP sem revisão sintética", async () => {
    const user = userEvent.setup();
    render(<OperationalMandatesPage />);
    await screen.findByRole("heading", { name: "Mandato principal" });
    await user.click(screen.getByRole("button", { name: "Substituir rascunho" }));
    await user.click(
      screen.getByRole("button", { name: "Substituir com tokens exibidos" }),
    );
    await waitFor(() =>
      expect(mocks.replaceOperationalMandateDraft).toHaveBeenCalledWith(
        MANDATE_ID,
        expect.objectContaining({
          expected_revision: 2,
          expected_record_version: 4,
        }),
      ),
    );
    expect(
      await screen.findByText(/NOOP semântico/),
    ).toBeDefined();
    expect(screen.queryByText("Revisão 3")).toBeNull();
  });

  it("não repete replacement em 409 e exige revisão do estado recarregado", async () => {
    const user = userEvent.setup();
    const updated = current({
      mandate: { ...current().mandate, current_revision: 3, record_version: 5 },
      revision: revision(3, {
        specification: {
          ...revision(2).specification,
          name: "Mandato atualizado no servidor",
        },
      }),
    });
    mocks.replaceOperationalMandateDraft.mockRejectedValueOnce(
      new ApiError(409, "operational_mandate_revision_conflict", "stale"),
    );
    mocks.getOperationalMandate
      .mockResolvedValueOnce(current())
      .mockResolvedValue(updated);
    render(<OperationalMandatesPage />);
    await screen.findByRole("heading", { name: "Mandato principal" });
    await user.click(screen.getByRole("button", { name: "Substituir rascunho" }));
    await user.click(
      screen.getByRole("button", { name: "Substituir com tokens exibidos" }),
    );

    expect((await screen.findByText(/mudou no servidor/)).textContent).toContain(
      "mudou no servidor",
    );
    expect(
      await screen.findByRole("heading", {
        name: "Mandato atualizado no servidor",
      }),
    ).toBeDefined();
    expect(mocks.replaceOperationalMandateDraft).toHaveBeenCalledOnce();
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "Aprovar revisão atual",
      }).disabled,
    ).toBe(true);
    await user.click(
      screen.getByRole("button", {
        name: "Marcar estado recarregado como revisado",
      }),
    );
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "Aprovar revisão atual",
      }).disabled,
    ).toBe(false);
  });

  it("aprova somente após confirmação com revisão, checksum e versão exatos", async () => {
    const user = userEvent.setup();
    render(<OperationalMandatesPage />);
    await screen.findByRole("heading", { name: "Mandato principal" });
    await user.click(screen.getByRole("button", { name: "Aprovar revisão atual" }));
    expect(mocks.approveOperationalMandate).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("revisão 2");
    expect(dialog.textContent).toContain("bbbbbbbbbbbb");
    expect(dialog.textContent).toContain("BTC/USDT");
    await within(dialog).getByRole("button", { name: "Aprovar esta revisão" }).click();
    await waitFor(() =>
      expect(mocks.approveOperationalMandate).toHaveBeenCalledWith(MANDATE_ID, {
        expected_revision: 2,
        expected_checksum: "b".repeat(64),
        expected_record_version: 4,
      }),
    );
  });

  it("arquiva somente após confirmação e não repete um conflito", async () => {
    const user = userEvent.setup();
    mocks.archiveOperationalMandate.mockRejectedValueOnce(
      new ApiError(409, "operational_mandate_record_version_conflict", "stale"),
    );
    render(<OperationalMandatesPage />);
    await screen.findByRole("heading", { name: "Mandato principal" });
    await user.click(screen.getByRole("button", { name: "Arquivar mandato" }));
    expect(mocks.archiveOperationalMandate).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("versão de registro 4");
    await within(dialog).getByRole("button", { name: "Arquivar mandato" }).click();
    expect((await screen.findByText(/mudou no servidor/)).textContent).toContain(
      "mudou no servidor",
    );
    expect(mocks.archiveOperationalMandate).toHaveBeenCalledOnce();
    expect(mocks.archiveOperationalMandate).toHaveBeenCalledWith(MANDATE_ID, {
      expected_record_version: 4,
    });
  });

  it("impede uma resposta antiga de substituir o mandato selecionado", async () => {
    const user = userEvent.setup();
    const first = current();
    const second = current({
      mandate: { ...current().mandate, mandate_id: SECOND_MANDATE_ID },
      revision: revision(2, {
        mandate_id: SECOND_MANDATE_ID,
        specification: {
          ...revision(2).specification,
          name: "Segundo mandato",
        },
      }),
    });
    let releaseFirst: ((value: OperationalMandateCurrent) => void) | undefined;
    mocks.listOperationalMandates.mockResolvedValue(listResponse([first, second], 2));
    mocks.getOperationalMandate.mockImplementation((mandateId: string) => {
      if (mandateId === MANDATE_ID) {
        return new Promise((resolve) => {
          releaseFirst = resolve;
        });
      }
      return Promise.resolve(second);
    });
    render(<OperationalMandatesPage />);
    await screen.findByRole("button", { name: "Inspecionar mandato Segundo mandato" });
    await user.click(
      screen.getByRole("button", { name: "Inspecionar mandato Segundo mandato" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Segundo mandato" }),
    ).toBeDefined();
    await act(async () => releaseFirst?.(first));
    expect(
      screen.getByRole("heading", { name: "Segundo mandato" }),
    ).toBeDefined();
    expect(screen.queryByRole("heading", { name: "Mandato principal" })).toBeNull();
  });
});
