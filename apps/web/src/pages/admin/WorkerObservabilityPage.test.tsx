import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "../../http/client";
import type {
  WorkerRuntimeEventList,
  WorkerRuntimeList,
} from "../../types/api";
import { WorkerObservabilityPage } from "./WorkerObservabilityPage";

vi.mock("../../http/client", () => ({
  apiClient: {
    listWorkerRuntimes: vi.fn(),
    listWorkerRuntimeEvents: vi.fn(),
  },
}));

const runtimeList: WorkerRuntimeList = {
  observed_at: "2026-08-20T21:00:00Z",
  stale_after_seconds: 120,
  count: 2,
  items: [
    {
      health_state: "HEALTHY",
      lifecycle_state: "RUNNING",
      activity_state: "IDLE",
      started_at: "2026-08-20T20:00:00Z",
      heartbeat_at: "2026-08-20T20:59:30Z",
      stopped_at: null,
      failure_code: null,
    },
    {
      health_state: "FAILED",
      lifecycle_state: "FAILED",
      activity_state: "IDLE",
      started_at: "2026-08-19T20:00:00Z",
      heartbeat_at: "2026-08-19T20:10:00Z",
      stopped_at: "2026-08-19T20:10:05Z",
      failure_code: "LOCAL_STATE_FAILURE",
    },
  ],
};

const eventList: WorkerRuntimeEventList = {
  observed_at: "2026-08-20T21:00:00Z",
  count: 2,
  items: [
    {
      event_id: 12,
      event_type: "OPERATION_SETTLED",
      occurred_at: "2026-08-20T20:55:00Z",
      operation_id: "20000000-0000-4000-8000-000000000002",
      operation_state: "COMPLETED",
    },
    {
      event_id: 11,
      event_type: "RUNTIME_STARTED",
      occurred_at: "2026-08-20T20:00:00Z",
      operation_id: null,
      operation_state: null,
    },
  ],
};

const mockedApi = vi.mocked(apiClient);

beforeEach(() => {
  mockedApi.listWorkerRuntimes.mockResolvedValue(runtimeList);
  mockedApi.listWorkerRuntimeEvents.mockResolvedValue(eventList);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("WorkerObservabilityPage", () => {
  it("renderiza estados e eventos sanitizados sem controles de lifecycle", async () => {
    render(<WorkerObservabilityPage />);

    expect(await screen.findAllByText("Heartbeat recente")).toHaveLength(2);

    expect(screen.getByText("Falha confirmada")).toBeTruthy();

    expect(screen.getByText("Operação liquidada")).toBeTruthy();

    expect(screen.getByText("Runtime iniciado")).toBeTruthy();

    expect(
      screen.queryByRole("button", {
        name: /iniciar|parar|reiniciar|pausar|retomar/i,
      }),
    ).toBeNull();
  });

  it("explica que stale não confirma processo morto", async () => {
    render(<WorkerObservabilityPage />);

    await screen.findByText("Runtime iniciado");

    expect(
      screen.getByText(/heartbeat atrasado não confirma processo morto/i),
    ).toBeTruthy();

    expect(
      screen.getByText(/STOPPED e FAILED representam terminações confirmadas/i),
    ).toBeTruthy();
  });

  it("faz refresh manual somente pelos dois GETs bounded", async () => {
    render(<WorkerObservabilityPage />);

    await waitFor(() => {
      expect(mockedApi.listWorkerRuntimes).toHaveBeenCalledTimes(1);
      expect(mockedApi.listWorkerRuntimeEvents).toHaveBeenCalledTimes(1);
    });

    expect(mockedApi.listWorkerRuntimes).toHaveBeenLastCalledWith(20);

    expect(mockedApi.listWorkerRuntimeEvents).toHaveBeenLastCalledWith(50);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Atualizar",
      }),
    );

    await waitFor(() => {
      expect(mockedApi.listWorkerRuntimes).toHaveBeenCalledTimes(2);
      expect(mockedApi.listWorkerRuntimeEvents).toHaveBeenCalledTimes(2);
    });
  });

  it("configura polling bounded de 30 segundos", async () => {
    const setIntervalSpy = vi.spyOn(window, "setInterval");

    const { unmount } = render(<WorkerObservabilityPage />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 30_000);

    unmount();
  });
});
