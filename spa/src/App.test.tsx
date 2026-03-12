/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "./App";
import { useAuth } from "./auth/useAuth";
import { createAuthContextValue } from "./test/mockFactories";

vi.mock("./auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

vi.mock("./pages/AgentCataloguePage", () => ({
  AgentCataloguePage: () => <div>Agent Catalogue Page</div>,
}));

vi.mock("./pages/SessionsPage", () => ({
  SessionsPage: () => <div>Sessions Page</div>,
}));

vi.mock("./pages/InvokePage", () => ({
  InvokePage: () => <div>Invoke Page</div>,
}));

vi.mock("./pages/TenantDashboardPage", () => ({
  TenantDashboardPage: () => <div>Tenant Dashboard Page</div>,
}));

vi.mock("./pages/AdminPage", () => ({
  AdminPage: ({ initialSection }: { initialSection?: string }) => (
    <div>Admin Page {initialSection}</div>
  ),
}));

describe("AppRoutes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects operator users to the operations landing route", () => {
    vi.mocked(useAuth).mockReturnValue(
      createAuthContextValue({
        isAuthenticated: true,
        account: {
          idTokenClaims: {
            roles: ["Platform.Admin"],
            tenantid: "tenant-acme",
          },
        } as never,
      }) as never,
    );

    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppRoutes />
      </MemoryRouter>,
    );

    expect(screen.getByText("Admin Page overview")).toBeInTheDocument();
  });

  it("redirects legacy tenant route to the tenant overview", () => {
    vi.mocked(useAuth).mockReturnValue(
      createAuthContextValue({
        isAuthenticated: true,
        account: {
          idTokenClaims: {
            roles: ["Agent.Invoke"],
            tenantid: "tenant-acme",
          },
        } as never,
      }) as never,
    );

    render(
      <MemoryRouter initialEntries={["/tenant"]}>
        <AppRoutes />
      </MemoryRouter>,
    );

    expect(screen.getByText("Tenant Dashboard Page")).toBeInTheDocument();
  });

  it("denies operator routes for non-operator users with clear messaging", () => {
    vi.mocked(useAuth).mockReturnValue(
      createAuthContextValue({
        isAuthenticated: true,
        account: {
          idTokenClaims: {
            roles: ["Agent.Invoke"],
            tenantid: "tenant-acme",
          },
        } as never,
      }) as never,
    );

    render(
      <MemoryRouter initialEntries={["/operations/overview"]}>
        <AppRoutes />
      </MemoryRouter>,
    );

    expect(screen.getByText("Access Denied")).toBeInTheDocument();
    expect(
      screen.getByText(/Platform operator routes require the `Platform\.Admin` or `Platform\.Operator` role\./i),
    ).toBeInTheDocument();
  });

  it("blocks tenant routes when tenant context is absent", () => {
    vi.mocked(useAuth).mockReturnValue(
      createAuthContextValue({
        isAuthenticated: true,
        account: {
          idTokenClaims: {
            roles: ["Agent.Invoke"],
          },
        } as never,
      }) as never,
    );

    render(
      <MemoryRouter initialEntries={["/tenant/access"]}>
        <AppRoutes />
      </MemoryRouter>,
    );

    expect(screen.getByText("Tenant Route Unavailable")).toBeInTheDocument();
    expect(screen.getByText(/token does not contain tenant context/i)).toBeInTheDocument();
  });
});
