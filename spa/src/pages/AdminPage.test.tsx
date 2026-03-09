/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
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
    vi.mocked(useAuth).mockReturnValue({
      getAccessToken: vi.fn(),
      isAuthenticated: true,
      account: {
        idTokenClaims: { roles: ["Platform.Admin"] },
      },
    } as never);
  });

  it("renders health, tenant, and quota data", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce({
        status: "ok",
        version: "0.1.0",
        timestamp: "2026-03-01T09:00:00Z",
      })
      .mockResolvedValueOnce({
        items: [
          {
            tenantId: "t-001",
            appId: "app-001",
            displayName: "Acme",
            tier: "premium",
            status: "active",
            runtimeRegion: "eu-west-1",
          },
        ],
      })
      .mockResolvedValueOnce({
        utilisation: [
          {
            region: "eu-west-1",
            quotaName: "ConcurrentSessions",
            currentValue: 5,
            limit: 25,
            utilisationPercentage: 20,
          },
        ],
      });
    vi.mocked(getApiClient).mockReturnValue({ request } as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("Platform Health")).toBeInTheDocument();
      expect(screen.getByText("Acme")).toBeInTheDocument();
      expect(
        screen.getByText((value) => value.includes("ConcurrentSessions")),
      ).toBeInTheDocument();
    });
  });

  it("renders empty admin sections when API returns empty arrays", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce({
        status: "ok",
        version: "0.1.0",
        timestamp: "2026-03-01T09:00:00Z",
      })
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ utilisation: [] });
    vi.mocked(getApiClient).mockReturnValue({ request } as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("No quota data available.")).toBeInTheDocument();
      expect(screen.getByText("0 Total")).toBeInTheDocument();
    });
  });

  it("renders error state when admin fetch fails", async () => {
    vi.mocked(getApiClient).mockReturnValue({
      request: vi.fn().mockRejectedValue(new Error("admin data failed")),
    } as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("admin data failed")).toBeInTheDocument();
    });
  });
});
