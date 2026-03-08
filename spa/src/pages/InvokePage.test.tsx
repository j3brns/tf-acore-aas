import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InvokePage } from "./InvokePage";

const { getApiClientMock, getAccessTokenMock, navigateMock, requestMock, streamMock, useJobPollingMock } =
    vi.hoisted(() => {
        const request = vi.fn();
        const stream = vi.fn();
        return {
            getApiClientMock: vi.fn(() => ({ request, stream })),
            getAccessTokenMock: vi.fn(async () => "token"),
            navigateMock: vi.fn(),
            requestMock: request,
            streamMock: stream,
            useJobPollingMock: vi.fn(() => ({ status: null, loading: false, error: null })),
        };
    });

vi.mock("../api/client", async () => {
    const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
    return {
        ...actual,
        getApiClient: getApiClientMock,
    };
});

vi.mock("../auth/useAuth", () => ({
    useAuth: () => ({
        getAccessToken: getAccessTokenMock,
        isAuthenticated: true,
    }),
}));

vi.mock("../hooks/useJobPolling", () => ({
    useJobPolling: (jobId: string | null, getAccessToken: typeof getAccessTokenMock) =>
        useJobPollingMock(jobId, getAccessToken),
}));

vi.mock("react-router-dom", async () => {
    const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
    return {
        ...actual,
        useNavigate: () => navigateMock,
        useParams: () => ({ agentName: "echo-agent" }),
    };
});

type AgentMode = "sync" | "streaming" | "async";

function buildAgent(invocationMode: AgentMode) {
    return {
        agent_name: "echo-agent",
        version: "1.0.0",
        owner_team: "platform",
        tier_minimum: "basic" as const,
        deployed_at: "2026-03-08T00:00:00Z",
        invocation_mode: invocationMode,
        streaming_enabled: invocationMode === "streaming",
        estimated_duration_seconds: 5,
    };
}

async function flushMicrotasks(): Promise<void> {
    await act(async () => {
        await Promise.resolve();
    });
}

function getInvokeBody(): Record<string, unknown> {
    const call = requestMock.mock.calls[1];
    const init = call?.[1] as { body?: string } | undefined;
    if (!init?.body) {
        throw new Error("Invoke request body not captured");
    }
    return JSON.parse(init.body) as Record<string, unknown>;
}

describe("InvokePage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("sends sync invoke requests with contract-compatible input payload", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("sync")).mockResolvedValueOnce({
            invocationId: "inv-1",
            agentName: "echo-agent",
            mode: "sync",
            status: "success",
            output: "hello",
            timestamp: "2026-03-08T00:00:00Z",
        });

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const textarea = renderer!.root.findByType("textarea");
        act(() => {
            textarea.props.onChange({ target: { value: "ping" } });
        });

        const form = renderer!.root.findByType("form");
        await act(async () => {
            await form.props.onSubmit({ preventDefault: () => undefined });
        });

        expect(requestMock).toHaveBeenNthCalledWith(2, "/v1/agents/echo-agent/invoke", expect.objectContaining({
            method: "POST",
        }));
        expect(getInvokeBody()).toEqual({ input: "ping" });
    });

    it("uses streaming invoke path with contract-compatible payload", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("streaming"));
        streamMock.mockReturnValue(
            (async function* () {
                yield { data: "hello " };
                yield { data: "world" };
            })(),
        );

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const textarea = renderer!.root.findByType("textarea");
        act(() => {
            textarea.props.onChange({ target: { value: "stream this" } });
        });

        const form = renderer!.root.findByType("form");
        await act(async () => {
            await form.props.onSubmit({ preventDefault: () => undefined });
        });

        expect(streamMock).toHaveBeenCalledTimes(1);
        expect(streamMock).toHaveBeenCalledWith("/v1/agents/echo-agent/invoke", expect.objectContaining({
            method: "POST",
            body: JSON.stringify({ input: "stream this" }),
        }));
    });

    it("handles async accepted responses and starts polling with jobId", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("async")).mockResolvedValueOnce({
            jobId: "job-777",
            status: "accepted",
            mode: "async",
            pollUrl: "/v1/jobs/job-777",
        });

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const textarea = renderer!.root.findByType("textarea");
        act(() => {
            textarea.props.onChange({ target: { value: "run async" } });
        });

        const form = renderer!.root.findByType("form");
        await act(async () => {
            await form.props.onSubmit({ preventDefault: () => undefined });
        });

        expect(getInvokeBody()).toEqual({ input: "run async" });
        expect(useJobPollingMock).toHaveBeenLastCalledWith("job-777", getAccessTokenMock);
    });
});
