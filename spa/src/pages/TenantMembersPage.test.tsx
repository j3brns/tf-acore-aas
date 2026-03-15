/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { createApiClientMock, createAuthContextValue } from "../test/mockFactories";
import { TenantMembersPage } from "./TenantMembersPage";

vi.mock("../auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

describe("TenantMembersPage", () => {
  const client = createApiClientMock();

  beforeEach(() => {
    vi.clearAllMocks();
    client.request.mockReset();
    vi.mocked(getApiClient).mockReturnValue(client as never);
    vi.mocked(useAuth).mockReturnValue(
      createAuthContextValue({
        isAuthenticated: true,
        account: {
          idTokenClaims: {
            tenantid: "tenant-acme",
          },
        } as never,
      }) as never,
    );
  });

  it("does not call the undeployed invite listing route on load", () => {
    render(<TenantMembersPage />);

    expect(client.request).not.toHaveBeenCalled();
    expect(
      screen.getByText("Pending invites sent from this page will appear here."),
    ).toBeInTheDocument();
  });

  it("submits invites through the documented route and renders the returned invite", async () => {
    client.request.mockResolvedValue({
      invite: {
        inviteId: "invite-1",
        email: "new.user@example.com",
        role: "Agent.Invoke",
        status: "pending",
        expiresAt: "2026-03-31T00:00:00Z",
      },
    });

    render(<TenantMembersPage />);

    fireEvent.change(screen.getByLabelText("Email Address"), {
      target: { value: "new.user@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send Invite" }));

    await waitFor(() => {
      expect(client.request).toHaveBeenCalledWith("/v1/tenants/tenant-acme/users/invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: "new.user@example.com",
          role: "Agent.Invoke",
        }),
      });
    });

    expect(client.request).not.toHaveBeenCalledWith("/v1/tenants/tenant-acme/users/invites");
    expect(screen.getByText("Invite sent to new.user@example.com.")).toBeInTheDocument();
    expect(screen.getByText("new.user@example.com")).toBeInTheDocument();
    expect(screen.getAllByText("Agent.Invoke")).not.toHaveLength(0);
  });

  it("does not offer platform roles in the invite form", () => {
    render(<TenantMembersPage />);

    expect(screen.getByText("Agent.Invoke (tenant-scoped access)")).toBeInTheDocument();
    expect(screen.queryByText("Platform.Operator (Admin Access)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Role")).not.toBeInTheDocument();
  });
});
