import { describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError, type AccessTokenProvider, type TokenRequestOptions } from "./client";

function createResponse(status: number, body: unknown, contentType = "application/json"): Response {
  const payload = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(payload, {
    status,
    headers: {
      "content-type": contentType,
    },
  });
}

function createStreamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });

  return new Response(body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
    },
  });
}

describe("ApiClient", () => {
  it("retries once with forceRefresh after a 401 response", async () => {
    const tokenProvider = vi.fn<AccessTokenProvider>().mockResolvedValueOnce("token-1").mockResolvedValueOnce("token-2");
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(createResponse(401, { error: "expired" }))
      .mockResolvedValueOnce(createResponse(200, { ok: true }));

    const client = new ApiClient({
      baseUrl: "https://api.example.com",
      getAccessToken: tokenProvider,
      fetchImpl,
    });

    const response = await client.request<{ ok: boolean }>("/v1/agents");

    expect(response).toEqual({ ok: true });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(tokenProvider).toHaveBeenNthCalledWith(1, undefined);
    expect(tokenProvider).toHaveBeenNthCalledWith(2, {
      forceRefresh: true,
      scopes: ["api://platform-dev/Agent.Invoke"],
    });

    const firstRequestHeaders = new Headers(fetchImpl.mock.calls[0][1]?.headers);
    const secondRequestHeaders = new Headers(fetchImpl.mock.calls[1][1]?.headers);
    expect(firstRequestHeaders.get("Authorization")).toBe("Bearer token-1");
    expect(secondRequestHeaders.get("Authorization")).toBe("Bearer token-2");
  });

  it("throws ApiError for non-401 failures", async () => {
    const tokenProvider = vi.fn<AccessTokenProvider>().mockResolvedValue("token-1");
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(createResponse(403, { error: { code: "FORBIDDEN" } }));

    const client = new ApiClient({
      baseUrl: "https://api.example.com",
      getAccessToken: tokenProvider,
      fetchImpl,
    });

    await expect(client.request("/v1/agents")).rejects.toBeInstanceOf(ApiError);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("calls BFF token refresh with an explicit assertion token and no auth recursion", async () => {
    const tokenProvider = vi.fn<AccessTokenProvider>().mockResolvedValue("unused");
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(createResponse(200, { accessToken: "fresh-token" }));

    const client = new ApiClient({
      baseUrl: "https://api.example.com",
      getAccessToken: tokenProvider,
      fetchImpl,
    });

    const response = await client.bffTokenRefresh(
      { scopes: ["api://platform-dev/Agent.Invoke"] },
      { accessToken: "assertion-token" },
    );

    expect(response).toEqual({ accessToken: "fresh-token" });
    expect(tokenProvider).not.toHaveBeenCalled();
    const requestHeaders = new Headers(fetchImpl.mock.calls[0][1]?.headers);
    expect(requestHeaders.get("Authorization")).toBe("Bearer assertion-token");
  });

  it("streams SSE chunks via Fetch + ReadableStream", async () => {
    const tokenProvider = vi.fn<AccessTokenProvider>().mockResolvedValue("stream-token");
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      createStreamResponse([
        "event: token\n",
        "data: hello\n\n",
        "data: world\n\n",
      ]),
    );

    const client = new ApiClient({
      baseUrl: "https://api.example.com",
      getAccessToken: tokenProvider,
      fetchImpl,
    });

    const events = [];
    for await (const event of client.stream("/v1/agents/echo-agent/invoke", { method: "POST" })) {
      events.push(event);
    }

    expect(events).toEqual([
      {
        event: "token",
        data: "hello",
        id: undefined,
        retry: undefined,
        raw: "event: token\ndata: hello",
      },
      {
        event: "message",
        data: "world",
        id: undefined,
        retry: undefined,
        raw: "data: world",
      },
    ]);

    const streamHeaders = new Headers(fetchImpl.mock.calls[0][1]?.headers);
    expect(streamHeaders.get("Accept")).toBe("text/event-stream");
    expect(streamHeaders.get("Authorization")).toBe("Bearer stream-token");
  });

  it("supports explicit forced refresh requests from callers", async () => {
    const observedOptions: Array<TokenRequestOptions | undefined> = [];
    const tokenProvider = vi.fn<AccessTokenProvider>().mockImplementation(async (options) => {
      observedOptions.push(options);
      return "token";
    });
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(createResponse(200, { ok: true }));

    const client = new ApiClient({
      baseUrl: "https://api.example.com",
      getAccessToken: tokenProvider,
      fetchImpl,
    });

    await client.request("/v1/health");
    expect(observedOptions).toEqual([undefined]);
  });
});
