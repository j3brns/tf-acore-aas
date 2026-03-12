/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter } from "react-router-dom";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { createApiClientMock, createAuthContextValue } from "../test/mockFactories";
import { TenantDashboardPage } from "./TenantDashboardPage";

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

vi.mock("../auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

describe("TenantDashboardPage", () => {
  const mockTenant = {
    tenant: {
      tenantId: "test-tenant",
      displayName: "Test Tenant",
      tier: "standard",
      status: "active",
      usage: {
        requestsToday: 42,
        budgetRemainingUsd: 150.50
      }
    }
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: true,
      account: {
        idTokenClaims: { tenantid: "test-tenant" },
      } as never,
    }) as never);
  });

  it("renders tenant dashboard data", async () => {
    const request = vi.fn().mockResolvedValue(mockTenant);
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(
      <BrowserRouter>
        <TenantDashboardPage />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Overview for Test Tenant (test-tenant)")).toBeInTheDocument();
      expect(screen.getByText("42")).toBeInTheDocument();
      expect(screen.getByText("$150.50")).toBeInTheDocument();
      expect(screen.getByText("standard")).toBeInTheDocument();
      expect(screen.getByText("active")).toBeInTheDocument();
    });
    
    expect(request).toHaveBeenCalledWith("/v1/tenants/test-tenant");
  });

  it("renders error state when fetch fails", async () => {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockRejectedValue(new Error("fetch failed")),
    }) as never);

    render(
      <BrowserRouter>
        <TenantDashboardPage />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Error: fetch failed")).toBeInTheDocument();
    });
  });

  it("renders message when no tenant data is returned", async () => {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockResolvedValue({ tenant: null }),
    }) as never);

    render(
      <BrowserRouter>
        <TenantDashboardPage />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("No tenant data.")).toBeInTheDocument();
    });
  });
});
