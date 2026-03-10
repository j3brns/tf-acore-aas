import { useCallback } from "react";
import { getApiClient } from "../api/client";
import { defaultScopes } from "../auth/msalConfig";

export function useBffTokenRefresh() {
  const refresh = useCallback(async (scopes: string[] = defaultScopes) => {
    try {
      const client = getApiClient();
      const response = await client.bffTokenRefresh({ scopes });
      return response;
    } catch (err) {
      console.error("[BFF] Token refresh failed:", err);
      throw err;
    }
  }, []);

  return { refresh };
}
