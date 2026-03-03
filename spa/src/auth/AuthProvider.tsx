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

const msalInstance = new PublicClientApplication(msalConfig);

type TokenRequestOptions = {
  forceRefresh?: boolean;
  scopes?: string[];
};

export type AuthContextValue = {
  account: AccountInfo | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  getAccessToken: (options?: TokenRequestOptions) => Promise<string>;
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
};

export async function acquireTokenWithPopupFallback(
  params: TokenAcquisitionParams,
): Promise<AuthenticationResult> {
  const { client, account, scopes, forceRefresh = false } = params;

  try {
    return await client.acquireTokenSilent({
      account,
      scopes,
      forceRefresh,
    });
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      const popupRequest: PopupRequest = {
        account,
        scopes,
      };
      return client.acquireTokenPopup(popupRequest);
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
      const tokenResult = await acquireTokenWithPopupFallback({
        client,
        account: activeAccount,
        scopes: requestedScopes,
        forceRefresh: options?.forceRefresh,
      });
      return tokenResult.accessToken;
    },
    [account, client, login],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      account,
      isAuthenticated: account !== null,
      isLoading,
      login,
      logout,
      getAccessToken,
    }),
    [account, getAccessToken, isLoading, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
