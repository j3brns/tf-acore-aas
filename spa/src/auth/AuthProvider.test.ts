import { InteractionRequiredAuthError, type AccountInfo } from "@azure/msal-browser";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { acquireTokenWithBffFallback } from "./AuthProvider";
import { getApiClient } from "../api/client";

// Mock ApiClient
vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

const account = {
  homeAccountId: "home-account",
  environment: "login.microsoftonline.com",
  tenantId: "tenant-id",
  username: "julia@example.com",
  localAccountId: "local-account",
  name: "Julia Example",
} satisfies AccountInfo;

describe("acquireTokenWithBffFallback", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns silent token when acquireTokenSilent succeeds", async () => {
    const acquireTokenSilent = vi.fn().mockResolvedValue({ accessToken: "silent-token" });
    const acquireTokenPopup = vi.fn();

    const token = await acquireTokenWithBffFallback({
      client: {
        acquireTokenSilent,
        acquireTokenPopup,
      } as never,
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
    });

    expect(token).toBe("silent-token");
    expect(acquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });

  it("falls back to BFF when MSAL silent fails but BFF succeeds", async () => {
    const acquireTokenSilent = vi.fn().mockRejectedValue(new Error("silent-fail"));
    const acquireTokenPopup = vi.fn();
    const bffTokenRefresh = vi.fn().mockResolvedValue({ accessToken: "bff-token" });
    (getApiClient as any).mockReturnValue({ bffTokenRefresh });

    const token = await acquireTokenWithBffFallback({
      client: {
        acquireTokenSilent,
        acquireTokenPopup,
      } as never,
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
      allowBffFallback: true,
    });

    expect(token).toBe("bff-token");
    expect(acquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(bffTokenRefresh).toHaveBeenCalledWith({ scopes: ["api://platform-dev/Agent.Invoke"] });
    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });

  it("falls back to acquireTokenPopup when interaction is required and BFF fails", async () => {
    const acquireTokenSilent = vi
      .fn()
      .mockRejectedValue(new InteractionRequiredAuthError("interaction_required", "login"));
    const acquireTokenPopup = vi.fn().mockResolvedValue({ accessToken: "popup-token" });
    const bffTokenRefresh = vi.fn().mockRejectedValue(new Error("bff-fail"));
    (getApiClient as any).mockReturnValue({ bffTokenRefresh });

    const token = await acquireTokenWithBffFallback({
      client: {
        acquireTokenSilent,
        acquireTokenPopup,
      } as never,
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
      forceRefresh: true,
      allowBffFallback: true,
    });

    expect(token).toBe("popup-token");
    expect(acquireTokenSilent).toHaveBeenCalledWith({
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
      forceRefresh: true,
    });
    expect(bffTokenRefresh).toHaveBeenCalledTimes(1);
    expect(acquireTokenPopup).toHaveBeenCalledWith({
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
    });
  });

  it("rethrows non-interaction errors when allowBffFallback is false", async () => {
    const acquireTokenSilent = vi.fn().mockRejectedValue(new Error("network down"));
    const acquireTokenPopup = vi.fn();

    await expect(
      acquireTokenWithBffFallback({
        client: {
          acquireTokenSilent,
          acquireTokenPopup,
        } as never,
        account,
        scopes: ["api://platform-dev/Agent.Invoke"],
        allowBffFallback: false,
      }),
    ).rejects.toThrow("network down");

    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });
});
