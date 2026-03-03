import type { Configuration, PopupRequest } from "@azure/msal-browser";

const AUTHORITY_HOST = "https://login.microsoftonline.com";

function readRequiredEnv(name: keyof ImportMetaEnv): string {
  const value = import.meta.env[name];
  if (!value || !String(value).trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return String(value).trim();
}

function readOptionalEnv(name: keyof ImportMetaEnv): string | undefined {
  const value = import.meta.env[name];
  if (!value) {
    return undefined;
  }
  const trimmed = String(value).trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function parseScopes(rawScopes: string): string[] {
  const scopes = rawScopes
    .split(/[,\s]+/)
    .map((scope) => scope.trim())
    .filter((scope) => scope.length > 0);

  if (scopes.length === 0) {
    throw new Error("VITE_ENTRA_SCOPES must include at least one scope");
  }

  return scopes;
}

const tenantId = readRequiredEnv("VITE_ENTRA_TENANT_ID");
const authority = `${AUTHORITY_HOST}/${tenantId}`;
const redirectUri =
  readOptionalEnv("VITE_ENTRA_REDIRECT_URI") ??
  (typeof window !== "undefined" ? window.location.origin : "http://localhost");
const postLogoutRedirectUri =
  readOptionalEnv("VITE_ENTRA_POST_LOGOUT_REDIRECT_URI") ?? redirectUri;

export const apiBaseUrl = readRequiredEnv("VITE_API_BASE_URL").replace(/\/+$/, "");
export const defaultScopes = parseScopes(readRequiredEnv("VITE_ENTRA_SCOPES"));

export const msalConfig: Configuration = {
  auth: {
    clientId: readRequiredEnv("VITE_ENTRA_CLIENT_ID"),
    authority,
    redirectUri,
    postLogoutRedirectUri,
    navigateToLoginRequestUrl: false,
  },
  cache: {
    cacheLocation: "sessionStorage",
  },
};

export const loginRequest: PopupRequest = {
  scopes: defaultScopes,
};
