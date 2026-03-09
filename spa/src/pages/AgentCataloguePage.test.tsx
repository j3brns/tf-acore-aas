/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { createApiClientMock, createAuthContextValue } from "../test/mockFactories";
import { catalogueMixedAgents } from "../test/testData";
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
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: true,
    }) as never);
  });

  it("renders agents on success", async () => {
    const request = vi.fn().mockResolvedValue(catalogueMixedAgents);
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request,
    }) as never);

    render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("echo-agent")).toBeInTheDocument();
      expect(screen.getByText("Version 1.0.0 • sync")).toBeInTheDocument();
      expect(screen.getByText("research-agent")).toBeInTheDocument();
      expect(screen.getByText("Version 2.1.0 • async")).toBeInTheDocument();
      expect(screen.getByText("ops-agent")).toBeInTheDocument();
      expect(screen.getByText("Version 3.0.0 • streaming")).toBeInTheDocument();
      expect(screen.getAllByText("Streaming")).toHaveLength(2);
    });
    expect(request).toHaveBeenCalledWith("/v1/agents");
  });

  it("renders empty state when no agents are returned", async () => {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockResolvedValue({ items: [] }),
    }) as never);

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
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockRejectedValue(new Error("catalogue failed")),
    }) as never);

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

  it("does not fetch catalogue when user is unauthenticated", async () => {
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: false,
    }) as never);
    const request = vi.fn();
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request,
    }) as never);

    const { container } = render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(container.querySelector(".animate-spin")).toBeInTheDocument();
    });
    expect(request).not.toHaveBeenCalled();
  });
});
