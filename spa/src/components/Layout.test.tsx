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

    expect(screen.getByRole("link", { name: /agents/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /access/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /quota/i })).toBeInTheDocument();
    expect(screen.getAllByText("tenant-acme").length).toBeGreaterThan(0);
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
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: /primary navigation/i })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: /primary navigation/i })).not.toBeInTheDocument();
  });
});
