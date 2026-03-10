import {
  EventType,
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
  type AuthenticationResult,
  type IPublicClientApplication,
  type PopupRequest,
} from "@azure/msal-browser";
import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { defaultScopes, loginRequest, msalConfig } from "./msalConfig";
import { getApiClient } from "../api/client";

const msalInstance = new PublicClientApplication(msalConfig);

type TokenRequestOptions = {
  forceRefresh?: boolean;
  scopes?: string[];
  allowBffFallback?: boolean;
};

export type AuthContextValue = {
  account: AccountInfo | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  getAccessToken: (options?: TokenRequestOptions) => Promise<string>;
  refreshAccessTokenViaBff: (scopes?: string[]) => Promise<string>;
};

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

type AuthProviderProps = {
  children: ReactNode;
  client?: IPublicClientApplication;
};

type TokenAcquisitionParams = {
  client: IPublicClientApplication;
  account: AccountInfo;
  scopes: string[];
  forceRefresh?: boolean;
  allowBffFallback?: boolean;
};

export async function acquireTokenWithBffFallback(
  params: TokenAcquisitionParams,
): Promise<string> {
  const { client, account, scopes, forceRefresh = false, allowBffFallback = true } = params;

  try {
    const result = await client.acquireTokenSilent({
      account,
      scopes,
      forceRefresh,
    });
    return result.accessToken;
  } catch (error) {
    if (allowBffFallback) {
      console.warn("[Auth] MSAL silent refresh failed, attempting BFF OBO fallback", error);
      try {
        const apiClient = getApiClient();
        const bffResult = await apiClient.bffTokenRefresh({ scopes });
        console.info("[Auth] Successfully refreshed token via BFF OBO");
        return bffResult.accessToken;
      } catch (bffError) {
        console.error("[Auth] BFF OBO fallback failed", bffError);
      }
    }

    if (error instanceof InteractionRequiredAuthError) {
      console.warn("[Auth] Interaction required - MSAL popup may be shown");
      const popupRequest: PopupRequest = {
        account,
        scopes,
      };
      const result = await client.acquireTokenPopup(popupRequest);
      return result.accessToken;
    }
    throw error;
  }
}

function resolveAccount(client: IPublicClientApplication): AccountInfo | null {
  return client.getActiveAccount() ?? client.getAllAccounts()[0] ?? null;
}

export function AuthProvider({ children, client = msalInstance }: AuthProviderProps) {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const syncAccount = useCallback(() => {
    const activeAccount = resolveAccount(client);
    if (activeAccount) {
      client.setActiveAccount(activeAccount);
    }
    setAccount(activeAccount);
    return activeAccount;
  }, [client]);

  useEffect(() => {
    let mounted = true;

    const callbackId = client.addEventCallback((event) => {
      if (
        event.eventType === EventType.LOGIN_SUCCESS ||
        event.eventType === EventType.ACQUIRE_TOKEN_SUCCESS
      ) {
        const payload = event.payload as AuthenticationResult | null;
        if (payload?.account) {
          client.setActiveAccount(payload.account);
          if (mounted) {
            setAccount(payload.account);
          }
        }
      }

      if (event.eventType === EventType.LOGOUT_SUCCESS && mounted) {
        setAccount(null);
      }
    });

    void (async () => {
      try {
        await client.initialize();
        await client.handleRedirectPromise();
        if (mounted) {
          syncAccount();
        }
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      mounted = false;
      if (callbackId) {
        client.removeEventCallback(callbackId);
      }
    };
  }, [client, syncAccount]);

  const login = useCallback(async () => {
    const response = await client.loginPopup(loginRequest);
    if (response.account) {
      client.setActiveAccount(response.account);
      setAccount(response.account);
      return;
    }
    syncAccount();
  }, [client, syncAccount]);

  const logout = useCallback(async () => {
    const activeAccount = account ?? resolveAccount(client);
    await client.logoutPopup(
      activeAccount
        ? {
            account: activeAccount,
          }
        : undefined,
    );
    setAccount(null);
  }, [account, client]);

  const getAccessToken = useCallback(
    async (options?: TokenRequestOptions) => {
      let activeAccount = account ?? resolveAccount(client);
      if (!activeAccount) {
        await login();
        activeAccount = resolveAccount(client);
      }

      if (!activeAccount) {
        throw new Error("Unable to resolve active account after login");
      }

      const requestedScopes = options?.scopes?.length ? options.scopes : defaultScopes;
      return await acquireTokenWithBffFallback({
        client,
        account: activeAccount,
        scopes: requestedScopes,
        forceRefresh: options?.forceRefresh,
        allowBffFallback: options?.allowBffFallback,
      });
    },
    [account, client, login],
  );

  const refreshAccessTokenViaBff = useCallback(
    async (scopes?: string[]) => {
      const requestedScopes = scopes?.length ? scopes : defaultScopes;
      const apiClient = getApiClient();
      const bffResult = await apiClient.bffTokenRefresh({ scopes: requestedScopes });
      return bffResult.accessToken;
    },
    [],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      account,
      isAuthenticated: account !== null,
      isLoading,
      login,
      logout,
      getAccessToken,
      refreshAccessTokenViaBff,
    }),
    [account, getAccessToken, isLoading, login, logout, refreshAccessTokenViaBff],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
