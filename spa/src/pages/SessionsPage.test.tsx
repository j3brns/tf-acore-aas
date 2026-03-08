/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { SessionsPage } from "./SessionsPage";

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

vi.mock("../auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

describe("SessionsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAuth).mockReturnValue({
      getAccessToken: vi.fn(),
      isAuthenticated: true,
    } as never);
  });

  it("renders sessions when API returns items", async () => {
    vi.mocked(getApiClient).mockReturnValue({
      request: vi.fn().mockResolvedValue({
        items: [
          {
            sessionId: "sess-12345678",
            agentName: "echo-agent",
            startedAt: "2026-03-01T09:00:00Z",
            lastActivityAt: "2026-03-01T09:05:00Z",
            status: "active",
          },
        ],
      }),
    } as never);

    render(<SessionsPage />);

    await waitFor(() => {
      expect(screen.getByText("echo-agent")).toBeInTheDocument();
      expect(screen.getByText("active")).toBeInTheDocument();
    });
  });

  it("renders empty state when no sessions are returned", async () => {
    vi.mocked(getApiClient).mockReturnValue({
      request: vi.fn().mockResolvedValue({ items: [] }),
    } as never);

    render(<SessionsPage />);

    await waitFor(() => {
      expect(screen.getByText("No active sessions.")).toBeInTheDocument();
    });
  });

  it("renders error state when sessions request fails", async () => {
    vi.mocked(getApiClient).mockReturnValue({
      request: vi.fn().mockRejectedValue(new Error("sessions failed")),
    } as never);

    render(<SessionsPage />);

    await waitFor(() => {
      expect(screen.getByText("sessions failed")).toBeInTheDocument();
    });
  });
});
