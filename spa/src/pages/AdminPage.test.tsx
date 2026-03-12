/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { createApiClientMock, createAuthContextValue } from "../test/mockFactories";
import { healthFail, healthOk, quotaRows, tenantRows } from "../test/testData";
import { AdminPage } from "./AdminPage";

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

vi.mock("../auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

describe("AdminPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: true,
      account: {
        idTokenClaims: { roles: ["Platform.Admin"] },
      } as never,
    }) as never);
  });

  it("renders health, tenant, and quota data", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(tenantRows)
      .mockResolvedValueOnce(quotaRows);
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("Platform Health")).toBeInTheDocument();
      expect(screen.getByText("Acme")).toBeInTheDocument();
      expect(screen.getByText("Beta")).toBeInTheDocument();
      expect(
        screen.getAllByText((value) => value.includes("ConcurrentSessions")),
      ).toHaveLength(2);
      expect(screen.getByText("92%")).toBeInTheDocument();
    });
    expect(request).toHaveBeenNthCalledWith(1, "/v1/health");
    expect(request).toHaveBeenNthCalledWith(2, "/v1/tenants");
    expect(request).toHaveBeenNthCalledWith(3, "/v1/platform/quota");
  });

  it("renders empty admin sections when API returns empty arrays", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthFail)
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ utilisation: [] });
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("No quota data available.")).toBeInTheDocument();
      expect(screen.getByText("0 Total")).toBeInTheDocument();
      expect(screen.getByText("fail")).toBeInTheDocument();
    });
  });

  it("renders error state when admin fetch fails", async () => {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockRejectedValue(new Error("admin data failed")),
    }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("admin data failed")).toBeInTheDocument();
    });
  });

  it("renders access denied for non-admin roles and skips requests", async () => {
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: true,
      account: {
        idTokenClaims: { roles: ["Agent.Developer"] },
      } as never,
    }) as never);
    const request = vi.fn();
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("Access Denied")).toBeInTheDocument();
      expect(screen.getByText("Platform operator role required.")).toBeInTheDocument();
    });
    expect(request).not.toHaveBeenCalled();
  });
});
