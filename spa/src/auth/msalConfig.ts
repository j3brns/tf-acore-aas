import { Configuration, PopupRequest } from "@azure/msal-browser";

const getRequiredEnv = (name: keyof ImportMetaEnv): string => {
  const value = import.meta.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
};

const parseScopes = (value: string): string[] =>
  value
    .split(",")
    .map((scope) => scope.trim())
    .filter((scope) => scope.length > 0);

export const msalConfig: Configuration = {
  auth: {
    clientId: getRequiredEnv("VITE_ENTRA_CLIENT_ID"),
    authority: getRequiredEnv("VITE_ENTRA_AUTHORITY"),
    redirectUri: getRequiredEnv("VITE_ENTRA_REDIRECT_URI"),
    postLogoutRedirectUri: getRequiredEnv("VITE_ENTRA_POST_LOGOUT_REDIRECT_URI"),
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
};

export const loginRequest: PopupRequest = {
  scopes: parseScopes(getRequiredEnv("VITE_ENTRA_LOGIN_SCOPES")),
};

export const tokenRequest: PopupRequest = {
  scopes: parseScopes(getRequiredEnv("VITE_ENTRA_TOKEN_SCOPES")),
};
