/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { AgentCataloguePage } from "./AgentCataloguePage";

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

vi.mock("../auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

describe("AgentCataloguePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAuth).mockReturnValue({
      getAccessToken: vi.fn(),
      isAuthenticated: true,
    } as never);
  });

  it("renders agents on success", async () => {
    vi.mocked(getApiClient).mockReturnValue({
      request: vi.fn().mockResolvedValue({
        items: [
          {
            agentName: "echo-agent",
            latestVersion: "1.0.0",
            tierMinimum: "basic",
            invocationMode: "sync",
            streamingEnabled: true,
            ownerTeam: "platform-team",
          },
        ],
      }),
    } as never);

    render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("echo-agent")).toBeInTheDocument();
      expect(screen.getByText("Version 1.0.0 • sync")).toBeInTheDocument();
    });
  });

  it("renders empty state when no agents are returned", async () => {
    vi.mocked(getApiClient).mockReturnValue({
      request: vi.fn().mockResolvedValue({ items: [] }),
    } as never);

    render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("No agents found.")).toBeInTheDocument();
    });
  });

  it("renders error state when request fails", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(getApiClient).mockReturnValue({
      request: vi.fn().mockRejectedValue(new Error("catalogue failed")),
    } as never);

    render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("catalogue failed")).toBeInTheDocument();
    });
    spy.mockRestore();
  });
});
