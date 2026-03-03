import { InteractionRequiredAuthError, type AccountInfo } from "@azure/msal-browser";
import { describe, expect, it, vi } from "vitest";

import { acquireTokenWithPopupFallback } from "./AuthProvider";

const account = {
  homeAccountId: "home-account",
  environment: "login.microsoftonline.com",
  tenantId: "tenant-id",
  username: "julia@example.com",
  localAccountId: "local-account",
  name: "Julia Example",
} satisfies AccountInfo;

describe("acquireTokenWithPopupFallback", () => {
  it("returns silent token when acquireTokenSilent succeeds", async () => {
    const acquireTokenSilent = vi.fn().mockResolvedValue({ accessToken: "silent-token" });
    const acquireTokenPopup = vi.fn();

    const token = await acquireTokenWithPopupFallback({
      client: {
        acquireTokenSilent,
        acquireTokenPopup,
      } as never,
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
    });

    expect(token.accessToken).toBe("silent-token");
    expect(acquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });

  it("falls back to acquireTokenPopup when interaction is required", async () => {
    const acquireTokenSilent = vi
      .fn()
      .mockRejectedValue(new InteractionRequiredAuthError("interaction_required", "login"));
    const acquireTokenPopup = vi.fn().mockResolvedValue({ accessToken: "popup-token" });

    const token = await acquireTokenWithPopupFallback({
      client: {
        acquireTokenSilent,
        acquireTokenPopup,
      } as never,
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
      forceRefresh: true,
    });

    expect(token.accessToken).toBe("popup-token");
    expect(acquireTokenSilent).toHaveBeenCalledWith({
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
      forceRefresh: true,
    });
    expect(acquireTokenPopup).toHaveBeenCalledWith({
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
    });
  });

  it("rethrows non-interaction errors", async () => {
    const acquireTokenSilent = vi.fn().mockRejectedValue(new Error("network down"));
    const acquireTokenPopup = vi.fn();

    await expect(
      acquireTokenWithPopupFallback({
        client: {
          acquireTokenSilent,
          acquireTokenPopup,
        } as never,
        account,
        scopes: ["api://platform-dev/Agent.Invoke"],
      }),
    ).rejects.toThrow("network down");

    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });
});
