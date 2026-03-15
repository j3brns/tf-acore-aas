/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../auth/useAuth";
import { createAuthContextValue } from "../test/mockFactories";
import { SessionsPage } from "./SessionsPage";

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

  it("renders the undeployed-route placeholder instead of calling the session API", () => {
    render(<SessionsPage />);

    expect(screen.getByText("Session Listing Pending")).toBeInTheDocument();
    expect(screen.getByText("Session Listing Not Yet Available")).toBeInTheDocument();
    expect(
      screen.getByText(
        "The current tenant API still returns not implemented for session enumeration. Existing sessions remain active, but this page will not call the undeployed route.",
      ),
    ).toBeInTheDocument();
  });

  it("shows auth guidance when the user is unauthenticated", () => {
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: false,
    }) as never);

    render(<SessionsPage />);

    expect(screen.getByText("Authentication Required")).toBeInTheDocument();
    expect(
      screen.getByText("Sign in with your Entra account to view active runtime sessions for this tenant."),
    ).toBeInTheDocument();
  });
});
