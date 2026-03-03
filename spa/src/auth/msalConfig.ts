import type { Configuration, PopupRequest } from "@azure/msal-browser";

function readRequiredEnv(name: keyof ImportMetaEnv): string {
  const value = import.meta.env[name];
  if (!value || !String(value).trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return String(value).trim();
}

function parseScopes(rawScopes: string): string[] {
  const scopes = rawScopes
    .split(/[\s,]+/)
    .map((scope) => scope.trim())
    .filter((scope) => scope.length > 0);

  if (scopes.length === 0) {
    throw new Error("VITE_ENTRA_SCOPES must include at least one scope");
  }

  return scopes;
}

export const apiBaseUrl = readRequiredEnv("VITE_API_BASE_URL").replace(/\/+$/, "");
export const defaultScopes = parseScopes(readRequiredEnv("VITE_ENTRA_SCOPES"));

export const msalConfig: Configuration = {
  auth: {
    clientId: readRequiredEnv("VITE_ENTRA_CLIENT_ID"),
    authority: readRequiredEnv("VITE_ENTRA_AUTHORITY"),
    redirectUri: readRequiredEnv("VITE_ENTRA_REDIRECT_URI"),
    postLogoutRedirectUri: readRequiredEnv("VITE_ENTRA_POST_LOGOUT_REDIRECT_URI"),
    navigateToLoginRequestUrl: false,
  },
  cache: {
    cacheLocation: "sessionStorage",
  },
};

export const loginRequest: PopupRequest = {
  scopes: defaultScopes,
};
