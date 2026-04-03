import { apiBaseUrl, defaultScopes } from "../auth/msalConfig";
import {
  AgentAgUiBootstrapResponseDto,
  AgentBootstrapRequestDto,
  BffSessionKeepaliveRequestDto,
  BffSessionKeepaliveResponseDto,
  BffTokenRefreshRequestDto,
  BffTokenRefreshResponseDto,
} from "./contracts";

export type TokenRequestOptions = {
  forceRefresh?: boolean;
  scopes?: string[];
};

export type AccessTokenProvider = (options?: TokenRequestOptions) => Promise<string>;

export type ApiClientOptions = {
  baseUrl?: string;
  getAccessToken: AccessTokenProvider;
  fetchImpl?: typeof fetch;
};

export type BffAuthOptions = {
  accessToken: string;
};

export type SseEvent = {
  event: string;
  data: string;
  id?: string;
  retry?: number;
  raw: string;
};

type ApiErrorBody = {
  error?: {
    code?: string;
    message?: string;
  };
  message?: string;
  retryAfterSeconds?: number;
};

type RetryableRequest = {
  path: string;
  init?: RequestInit;
  accept?: string;
  retryNetworkErrors?: boolean;
};

const NETWORK_RETRY_DELAYS_MS = [200, 400];

export class ApiError extends Error {
  readonly status: number;
  readonly response: Response;
  readonly body: unknown;
  readonly code?: string;
  readonly retryAfterSeconds?: number;
  readonly isRetryable: boolean;

  constructor(message: string, response: Response, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = response.status;
    this.response = response;
    this.body = body;

    const parsedBody = isApiErrorBody(body) ? body : undefined;
    this.code = parsedBody?.error?.code;
    this.retryAfterSeconds = resolveRetryAfterSeconds(response, parsedBody);
    this.isRetryable = isRetryableStatus(response.status);
  }
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly getAccessToken: AccessTokenProvider;
  private readonly fetchImpl: typeof fetch;
  private readonly requestExecutor: AuthenticatedRequestExecutor;

  constructor(options: ApiClientOptions) {
    this.baseUrl = (options.baseUrl ?? apiBaseUrl).replace(/\/+$/, "");
    this.getAccessToken = options.getAccessToken;
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.requestExecutor = new AuthenticatedRequestExecutor({
      getAccessToken: this.getAccessToken,
      fetchImpl: this.fetchImpl,
      resolveUrl: (path) => this.resolveUrl(path),
    });
  }

  async request<TResponse>(path: string, init?: RequestInit): Promise<TResponse> {
    const response = await this.requestRaw(path, init);
    return parseJsonResponse<TResponse>(response);
  }

  async requestRaw(path: string, init?: RequestInit): Promise<Response> {
    return this.requestExecutor.execute({
      path,
      init,
      retryNetworkErrors: isIdempotentRequest(init),
    });
  }

  async bffTokenRefresh(
    request: BffTokenRefreshRequestDto,
    auth: BffAuthOptions,
  ): Promise<BffTokenRefreshResponseDto> {
    const response = await this.fetchImpl(this.resolveUrl("/v1/bff/token-refresh"), {
      method: "POST",
      body: JSON.stringify(request),
      headers: mergeHeaders(
        { "Content-Type": "application/json" },
        { Authorization: `Bearer ${auth.accessToken}` },
      ),
    });
    if (!response.ok) {
      throw await toApiError(response);
    }
    return parseJsonResponse<BffTokenRefreshResponseDto>(response);
  }

  async bootstrapAgUiSession(
    agentName: string,
    request?: AgentBootstrapRequestDto,
  ): Promise<AgentAgUiBootstrapResponseDto> {
    return this.request<AgentAgUiBootstrapResponseDto>(
      `/v1/agents/${encodeURIComponent(agentName)}/bootstrap`,
      {
        method: "POST",
        body: JSON.stringify(request ?? {}),
        headers: { "Content-Type": "application/json" },
      },
    );
  }

  async bffSessionKeepalive(
    request: BffSessionKeepaliveRequestDto,
  ): Promise<BffSessionKeepaliveResponseDto> {
    const response = await this.requestRaw("/v1/bff/session-keepalive", {
      method: "POST",
      body: JSON.stringify(request),
      headers: { "Content-Type": "application/json" },
    });
    return parseJsonResponse<BffSessionKeepaliveResponseDto>(response);
  }

  async *stream(path: string, init?: RequestInit): AsyncGenerator<SseEvent> {
    const response = await this.requestExecutor.execute({
      path,
      init,
      accept: "text/event-stream",
      retryNetworkErrors: false,
    });
    if (!response.body) {
      throw new Error("Expected streaming response body");
    }

    yield* new SseClient(response).read();
  }

  private resolveUrl(path: string): string {
    if (/^https?:\/\//i.test(path)) {
      return path;
    }
    return `${this.baseUrl}/${path.replace(/^\/+/, "")}`;
  }
}

class AuthenticatedRequestExecutor {
  private readonly getAccessToken: AccessTokenProvider;
  private readonly fetchImpl: typeof fetch;
  private readonly resolveUrl: (path: string) => string;

  constructor(options: {
    getAccessToken: AccessTokenProvider;
    fetchImpl: typeof fetch;
    resolveUrl: (path: string) => string;
  }) {
    this.getAccessToken = options.getAccessToken;
    this.fetchImpl = options.fetchImpl;
    this.resolveUrl = options.resolveUrl;
  }

  async execute(request: RetryableRequest): Promise<Response> {
    let networkAttempt = 0;

    while (true) {
      try {
        return await this.executeWithAuthRetry(request);
      } catch (error) {
        if (
          !request.retryNetworkErrors ||
          networkAttempt >= NETWORK_RETRY_DELAYS_MS.length ||
          !isRetryableNetworkError(error)
        ) {
          throw error;
        }

        await waitForDelay(NETWORK_RETRY_DELAYS_MS[networkAttempt]);
        networkAttempt += 1;
      }
    }
  }

  private async executeWithAuthRetry(request: RetryableRequest): Promise<Response> {
    let forceRefresh = false;

    while (true) {
      const token = await this.getAccessToken(
        forceRefresh
          ? {
              forceRefresh: true,
              scopes: defaultScopes,
            }
          : undefined,
      );

      const response = await this.fetchImpl(this.resolveUrl(request.path), {
        ...request.init,
        headers: mergeHeaders(request.init?.headers, {
          Authorization: `Bearer ${token}`,
          ...(request.accept ? { Accept: request.accept } : {}),
        }),
      });

      if (response.status === 401 && !forceRefresh) {
        forceRefresh = true;
        continue;
      }

      if (!response.ok) {
        throw await toApiError(response);
      }

      return response;
    }
  }
}

class SseClient {
  private readonly response: Response;

  constructor(response: Response) {
    this.response = response;
  }

  async *read(): AsyncGenerator<SseEvent> {
    if (!this.response.body) {
      throw new Error("Expected streaming response body");
    }

    const decoder = new TextDecoder();
    const reader = this.response.body.getReader();
    const parser = new SseParser();

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      for (const message of parser.push(decoder.decode(value, { stream: true }))) {
        yield message;
      }
    }

    for (const message of parser.flush(decoder.decode())) {
      yield message;
    }
  }
}

class SseParser {
  private buffer = "";

  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    return this.drain();
  }

  flush(chunk = ""): SseEvent[] {
    this.buffer += chunk;
    if (!this.buffer.trim()) {
      return [];
    }

    const finalEvent = parseSseEvent(this.buffer);
    this.buffer = "";
    return finalEvent ? [finalEvent] : [];
  }

  private drain(): SseEvent[] {
    const events: SseEvent[] = [];

    while (true) {
      const boundary = this.buffer.indexOf("\n\n");
      if (boundary === -1) {
        return events;
      }

      const chunk = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);

      const parsed = parseSseEvent(chunk);
      if (parsed) {
        events.push(parsed);
      }
    }
  }
}

function parseSseEvent(rawChunk: string): SseEvent | null {
  const chunk = rawChunk.replace(/\r/g, "").trim();
  if (!chunk) {
    return null;
  }

  let event = "message";
  const data: string[] = [];
  let id: string | undefined;
  let retry: number | undefined;

  for (const line of chunk.split("\n")) {
    if (!line || line.startsWith(":")) {
      continue;
    }

    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).trimStart();

    if (field === "event") {
      event = value;
      continue;
    }
    if (field === "data") {
      data.push(value);
      continue;
    }
    if (field === "id") {
      id = value;
      continue;
    }
    if (field === "retry") {
      const parsed = Number.parseInt(value, 10);
      retry = Number.isFinite(parsed) ? parsed : undefined;
    }
  }

  return {
    event,
    data: data.join("\n"),
    id,
    retry,
    raw: chunk,
  };
}

function mergeHeaders(
  headers: HeadersInit | undefined,
  additional: Record<string, string>,
): HeadersInit {
  const merged = new Headers(headers);
  for (const [name, value] of Object.entries(additional)) {
    merged.set(name, value);
  }
  return merged;
}

async function parseJsonResponse<TResponse>(response: Response): Promise<TResponse> {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.includes("application/json")) {
    throw new ApiError("Expected JSON response", response, await response.text());
  }
  return (await response.json()) as TResponse;
}

async function toApiError(response: Response): Promise<ApiError> {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  return new ApiError(`HTTP ${response.status}`, response, body);
}

function isApiErrorBody(body: unknown): body is ApiErrorBody {
  return typeof body === "object" && body !== null;
}

function resolveRetryAfterSeconds(
  response: Response,
  body: ApiErrorBody | undefined,
): number | undefined {
  const retryAfterHeader = response.headers.get("retry-after");
  if (retryAfterHeader) {
    const parsed = Number.parseInt(retryAfterHeader, 10);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }

  return body?.retryAfterSeconds;
}

function isRetryableStatus(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

function isIdempotentRequest(init: RequestInit | undefined): boolean {
  const method = init?.method?.toUpperCase() ?? "GET";
  return method === "GET" || method === "HEAD" || method === "OPTIONS";
}

function isRetryableNetworkError(error: unknown): boolean {
  if (error instanceof DOMException) {
    return error.name !== "AbortError";
  }
  return error instanceof TypeError;
}

async function waitForDelay(delayMs: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, delayMs));
}

// Global instance for convenience (populated after AuthProvider initialization or used with a mock)
let _apiClient: ApiClient | null = null;

export const getApiClient = (getAccessToken?: AccessTokenProvider): ApiClient => {
  if (getAccessToken) {
    _apiClient = new ApiClient({ getAccessToken });
  }
  if (!_apiClient) {
    throw new Error("ApiClient not initialized. Call getApiClient(getAccessToken) first.");
  }
  return _apiClient;
};
