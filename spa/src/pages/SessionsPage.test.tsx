/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { createApiClientMock, createAuthContextValue } from "../test/mockFactories";
import { sessionsList } from "../test/testData";
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
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: true,
    }) as never);
  });

  it("renders sessions when API returns items", async () => {
    const request = vi.fn().mockResolvedValue(sessionsList);
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request,
    }) as never);

    render(<SessionsPage />);

    await waitFor(() => {
      expect(screen.getByText("echo-agent")).toBeInTheDocument();
      expect(screen.getByText("active")).toBeInTheDocument();
      expect(screen.getByText("research-agent")).toBeInTheDocument();
      expect(screen.getByText("completed")).toBeInTheDocument();
    });
    expect(request).toHaveBeenCalledWith("/v1/sessions");
  });

  it("renders empty state when no sessions are returned", async () => {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockResolvedValue({ items: [] }),
    }) as never);

    render(<SessionsPage />);

    await waitFor(() => {
      expect(screen.getByText("No active sessions.")).toBeInTheDocument();
    });
  });

  it("renders error state when sessions request fails", async () => {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockRejectedValue(new Error("sessions failed")),
    }) as never);

    render(<SessionsPage />);

    await waitFor(() => {
      expect(screen.getByText("sessions failed")).toBeInTheDocument();
    });
  });

  it("does not fetch sessions when user is unauthenticated", async () => {
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: false,
    }) as never);
    const request = vi.fn();
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request,
    }) as never);

    render(<SessionsPage />);

    await waitFor(() => {
      expect(screen.getByText("Loading sessions...")).toBeInTheDocument();
    });
    expect(request).not.toHaveBeenCalled();
  });
});
