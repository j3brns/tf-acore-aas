/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../auth/useAuth";
import { createAuthContextValue } from "../test/mockFactories";
import { Layout } from "./Layout";

vi.mock("../auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

describe("Layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders tenant routes for tenant-scoped users and operator routes for operators", () => {
    vi.mocked(useAuth).mockReturnValue(
      createAuthContextValue({
        isAuthenticated: true,
        account: {
          name: "Op User",
          username: "op.user@example.com",
          idTokenClaims: {
            tenantid: "tenant-acme",
            roles: ["Platform.Operator"],
          },
        } as never,
      }) as never,
    );

    render(
      <MemoryRouter initialEntries={["/tenant/overview"]}>
        <Layout>
          <div>content</div>
        </Layout>
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("link", { name: /agents/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /access control/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /infrastructure/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("tenant-a").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Platform Operator").length).toBeGreaterThan(0);
  });

  it("keeps mobile navigation operable and closable with escape", () => {
    vi.mocked(useAuth).mockReturnValue(
      createAuthContextValue({
        isAuthenticated: true,
        account: {
          name: "Tenant User",
          username: "tenant.user@example.com",
          idTokenClaims: {
            tenantid: "tenant-acme",
            roles: ["Agent.Invoke"],
          },
        } as never,
      }) as never,
    );

    render(
      <MemoryRouter initialEntries={["/agents"]}>
        <Layout>
          <div>content</div>
        </Layout>
      </MemoryRouter>,
    );

    const [trigger] = screen.getAllByRole("button", { name: /open navigation/i });
    const navigation = document.getElementById("mobile-navigation");

    expect(navigation).not.toBeNull();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(navigation).toHaveClass("-translate-x-full");

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(navigation).toHaveClass("translate-x-0");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(navigation).toHaveClass("-translate-x-full");
  });
});
