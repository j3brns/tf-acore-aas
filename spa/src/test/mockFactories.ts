import type { AuthContextValue } from "../auth/AuthProvider";
import { vi } from "vitest";

type ApiClientMock = {
  request: ReturnType<typeof vi.fn>;
  stream: ReturnType<typeof vi.fn>;
};

export function createAuthContextValue(
  overrides: Partial<AuthContextValue> = {},
): AuthContextValue {
  return {
    account: null,
    isAuthenticated: false,
    isLoading: false,
    login: async () => undefined,
    logout: async () => undefined,
    getAccessToken: async () => "token",
    ...overrides,
  };
}

export function createApiClientMock(
  overrides: Partial<ApiClientMock> = {},
): ApiClientMock {
  return {
    request: vi.fn(),
    stream: vi.fn(),
    ...overrides,
  };
}
