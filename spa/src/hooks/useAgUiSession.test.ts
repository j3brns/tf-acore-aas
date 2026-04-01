import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useAgUiSession } from "./useAgUiSession";
import { agUiBootstrapResponse } from "../test/testData";

const bootstrapMock = vi.fn();
const getAccessTokenMock = vi.fn(async () => "token");

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(() => ({
    bootstrapAgUiSession: bootstrapMock,
  })),
}));

function createSseStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(encoder.encode(chunks[index]));
        index++;
      } else {
        controller.close();
      }
    },
  });
}

describe("useAgUiSession", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("starts in idle status", () => {
    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));
    expect(result.current.status).toBe("idle");
    expect(result.current.bootstrap).toBeNull();
    expect(result.current.messages).toEqual([]);
    expect(result.current.accumulatedText).toBe("");
    expect(result.current.sessionId).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("bootstraps and connects to AG-UI SSE stream", async () => {
    bootstrapMock.mockResolvedValue(agUiBootstrapResponse);

    const sseBody = createSseStream([
      'data: {"type":"text","content":"hello "}\n\n',
      'data: {"type":"text","content":"world"}\n\n',
      "data: [DONE]\n\n",
    ]);

    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      body: sseBody,
      headers: new Headers({ "content-type": "text/event-stream" }),
    } as unknown as Response);

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      await result.current.start("test prompt");
    });

    expect(bootstrapMock).toHaveBeenCalledWith("echo-agent", {});
    expect(result.current.bootstrap).toEqual(agUiBootstrapResponse);
    expect(result.current.sessionId).toBe("sess-agui-001");
    expect(result.current.accumulatedText).toBe("hello world");
    expect(result.current.status).toBe("closed");
  });

  it("sets error status when bootstrap fails", async () => {
    bootstrapMock.mockRejectedValue(new Error("bootstrap failed"));

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      await result.current.start("test prompt");
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toBe("bootstrap failed");
  });

  it("sets error status when SSE connection returns non-ok", async () => {
    bootstrapMock.mockResolvedValue(agUiBootstrapResponse);
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 502,
      body: null,
    } as unknown as Response);

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      await result.current.start("test prompt");
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toContain("502");
  });

  it("does nothing when agentName is undefined", async () => {
    const { result } = renderHook(() => useAgUiSession(undefined, getAccessTokenMock));

    await act(async () => {
      await result.current.start("test");
    });

    expect(bootstrapMock).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
  });

  it("accumulates raw text data from non-JSON SSE events", async () => {
    bootstrapMock.mockResolvedValue(agUiBootstrapResponse);

    const sseBody = createSseStream([
      "event: text\ndata: raw chunk\n\n",
      "data: [DONE]\n\n",
    ]);

    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      body: sseBody,
    } as unknown as Response);

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      await result.current.start("test");
    });

    expect(result.current.accumulatedText).toBe("raw chunk");
  });

  it("disconnect aborts the stream and sets status to closed", async () => {
    bootstrapMock.mockResolvedValue(agUiBootstrapResponse);

    // Create a stream that never closes on its own
    let streamController: ReadableStreamDefaultController<Uint8Array>;
    const hangingStream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode('data: {"type":"text","content":"start"}\n\n'));
      },
    });

    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      body: hangingStream,
    } as unknown as Response);

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    // Start in background (don't await — it will hang)
    const startPromise = act(async () => {
      await result.current.start("test");
    });

    // Give it a tick to enter connected state
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });

    act(() => {
      result.current.disconnect();
    });

    // Clean up the hanging stream
    streamController!.close();
    await startPromise;

    expect(result.current.status).toBe("closed");
  });
});
