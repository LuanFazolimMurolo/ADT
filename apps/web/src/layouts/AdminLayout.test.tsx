import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AdminLayout } from "./AdminLayout";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ signOut: vi.fn() }),
}));

vi.mock("../http/client", () => ({
  apiClient: { getHealth: vi.fn().mockResolvedValue({ status: "healthy" }) },
}));

describe("AdminLayout", () => {
  it("expõe a navegação acessível para as consoles operacionais", async () => {
    const user = userEvent.setup();
    await act(async () => {
      render(
        <MemoryRouter initialEntries={["/admin"]}>
          <Routes>
            <Route path="/admin" element={<AdminLayout />}>
              <Route index element={<h1>Admin</h1>} />
              <Route path="market-operations" element={<h1>Operações</h1>} />
              <Route
                path="operational-mandates"
                element={<h1>Mandatos</h1>}
              />
            </Route>
          </Routes>
        </MemoryRouter>,
      );
      await Promise.resolve();
    });

    const link = screen.getByRole("link", { name: "Operações de mercado" });
    expect(link.getAttribute("href")).toBe("/admin/market-operations");
    await screen.findByText("Backend conectado");
    await act(async () => user.click(link));
    expect(
      await screen.findByRole("heading", { name: "Operações" }),
    ).toBeDefined();

    const mandatesLink = screen.getByRole("link", {
      name: "Mandatos operacionais",
    });
    expect(mandatesLink.getAttribute("href")).toBe(
      "/admin/operational-mandates",
    );
    await act(async () => user.click(mandatesLink));
    expect(
      await screen.findByRole("heading", { name: "Mandatos" }),
    ).toBeDefined();
  });
});
