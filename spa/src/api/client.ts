import { apiBaseUrl, defaultScopes } from "../auth/msalConfig";

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

export type SseEvent = {
  event: string;
  data: string;
  id?: string;
  retry?: number;
  raw: string;
};

export class ApiError extends Error {
  readonly status: number;
  readonly response: Response;
  readonly body: unknown;

  constructor(message: string, response: Response, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = response.status;
    this.response = response;
    this.body = body;
  }
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly getAccessToken: AccessTokenProvider;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions) {
    this.baseUrl = (options.baseUrl ?? apiBaseUrl).replace(/\/+$/, "");
    this.getAccessToken = options.getAccessToken;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async request<TResponse>(path: string, init?: RequestInit): Promise<TResponse> {
    const response = await this.requestRaw(path, init);
    return parseJsonResponse<TResponse>(response);
  }

  async requestRaw(path: string, init?: RequestInit): Promise<Response> {
    return this.requestWithAuth(path, init, false);
  }

  async *stream(path: string, init?: RequestInit): AsyncGenerator<SseEvent> {
    const requestInit: RequestInit = {
      ...init,
      headers: mergeHeaders(init?.headers, {
        Accept: "text/event-stream",
      }),
    };

    const response = await this.requestWithAuth(path, requestInit, false);
    if (!response.body) {
      throw new Error("Expected streaming response body");
    }

    const decoder = new TextDecoder();
    const reader = response.body.getReader();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const messages = extractSseMessages(buffer);
      buffer = messages.remainder;
      for (const message of messages.events) {
        yield message;
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      const message = parseSseEvent(buffer);
      if (message) {
        yield message;
      }
    }
  }

  private async requestWithAuth(
    path: string,
    init: RequestInit | undefined,
    hasRetried: boolean,
  ): Promise<Response> {
    const token = await this.getAccessToken(
      hasRetried
        ? {
            forceRefresh: true,
            scopes: defaultScopes,
          }
        : undefined,
    );

    const response = await this.fetchImpl(this.resolveUrl(path), {
      ...init,
      headers: mergeHeaders(init?.headers, {
        Authorization: `Bearer ${token}`,
      }),
    });

    if (response.status === 401 && !hasRetried) {
      return this.requestWithAuth(path, init, true);
    }

    if (!response.ok) {
      throw await toApiError(response);
    }

    return response;
  }

  private resolveUrl(path: string): string {
    if (/^https?:\/\//i.test(path)) {
      return path;
    }
    return `${this.baseUrl}/${path.replace(/^\/+/, "")}`;
  }
}

function extractSseMessages(buffer: string): { events: SseEvent[]; remainder: string } {
  const events: SseEvent[] = [];
  let remainder = buffer;

  while (true) {
    const boundary = remainder.indexOf("\n\n");
    if (boundary === -1) {
      return { events, remainder };
    }

    const chunk = remainder.slice(0, boundary);
    remainder = remainder.slice(boundary + 2);

    const parsed = parseSseEvent(chunk);
    if (parsed) {
      events.push(parsed);
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
