import TestRenderer, { act } from "react-test-renderer";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createAuthContextValue } from "../test/mockFactories";
import { asyncAccepted, buildAgent } from "../test/testData";
import type { Job } from "../types";
import { InvokePage } from "./InvokePage";

const { getApiClientMock, getAccessTokenMock, navigateMock, requestMock, streamMock, useAuthMock, useJobPollingMock, useAgUiSessionMock } =
    vi.hoisted(() => {
        const request = vi.fn();
        const stream = vi.fn();
        const useAuth = vi.fn();
        return {
            getApiClientMock: vi.fn(() => ({ request, stream })),
            getAccessTokenMock: vi.fn(async () => "token"),
            navigateMock: vi.fn(),
            requestMock: request,
            streamMock: stream,
            useAuthMock: useAuth,
            useJobPollingMock: vi.fn((jobId: string | null, getAccessToken: typeof getAccessTokenMock) => {
                void jobId;
                void getAccessToken;
                return {
                    status: null as Job | null,
                    loading: false,
                    error: null as string | null,
                };
            }),
            useAgUiSessionMock: vi.fn(() => ({
                status: "idle" as string,
                bootstrap: null,
                messages: [],
                accumulatedText: "",
                sessionId: null as string | null,
                error: null as string | null,
                start: vi.fn(),
                disconnect: vi.fn(),
                reconnect: vi.fn(),
            })),
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
    useAuth: useAuthMock,
}));

vi.mock("../hooks/useJobPolling", () => ({
    useJobPolling: (jobId: string | null, getAccessToken: typeof getAccessTokenMock) =>
        useJobPollingMock(jobId, getAccessToken),
}));

vi.mock("../hooks/useSessionKeepalive", () => ({
    useSessionKeepalive: vi.fn(),
}));

vi.mock("../hooks/useAgUiSession", () => ({
    useAgUiSession: (...args: unknown[]) => useAgUiSessionMock(...args),
}));

vi.mock("react-router-dom", async () => {
    const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
    return {
        ...actual,
        useNavigate: () => navigateMock,
        useParams: () => ({ agentName: "echo-agent" }),
    };
});

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
        useAuthMock.mockReturnValue(createAuthContextValue({
            isAuthenticated: true,
            getAccessToken: getAccessTokenMock,
        }));
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

    it("renders agent metadata from the deployed camelCase detail contract", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("sync"));

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const pageText = JSON.stringify(renderer!.toJSON());
        expect(pageText).toContain("Invoke: ");
        expect(pageText).toContain("echo-agent");
        expect(pageText).toContain("sync mode");
        expect(pageText).toContain("Tier: ");
        expect(pageText).toContain("basic+");
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
        requestMock.mockResolvedValueOnce(buildAgent("async")).mockResolvedValueOnce(asyncAccepted);

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

    it("surfaces async contract error when accepted response has no job id", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("async")).mockResolvedValueOnce({
            jobId: "",
            status: "accepted",
            mode: "async",
            pollUrl: "",
        });

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const textarea = renderer!.root.findByType("textarea");
        act(() => {
            textarea.props.onChange({ target: { value: "run async without id" } });
        });

        const form = renderer!.root.findByType("form");
        await act(async () => {
            await form.props.onSubmit({ preventDefault: () => undefined });
        });

        const pageText = JSON.stringify(renderer!.toJSON());
        expect(pageText).toContain("Async invoke response missing jobId");
    });

    it("shows an authentication-required banner when unauthenticated", async () => {
        useAuthMock.mockReturnValue(createAuthContextValue({
            isAuthenticated: false,
            getAccessToken: getAccessTokenMock,
        }));

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        expect(requestMock).not.toHaveBeenCalled();
        const pageText = JSON.stringify(renderer!.toJSON());
        expect(pageText).toContain("Authentication Required");
        expect(pageText).toContain("Sign in again");
    });

    it("shows fetch error when initial agent lookup fails", async () => {
        requestMock.mockRejectedValueOnce(new Error("agent lookup failed"));

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        expect(JSON.stringify(renderer!.toJSON())).toContain("agent lookup failed");
    });

    it("renders async completion link and polling error details", async () => {
        useJobPollingMock.mockReturnValue({
            status: {
                jobId: "job-777",
                tenantId: "tenant-1",
                agentName: "echo-agent",
                status: "completed",
                createdAt: "2026-03-08T00:00:00Z",
                completedAt: "2026-03-08T00:00:10Z",
                resultUrl: "https://example.test/result",
            },
            loading: false,
            error: "polling warning",
        });
        requestMock.mockResolvedValueOnce(buildAgent("async")).mockResolvedValueOnce(asyncAccepted);

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const textarea = renderer!.root.findByType("textarea");
        act(() => {
            textarea.props.onChange({ target: { value: "complete async" } });
        });

        const form = renderer!.root.findByType("form");
        await act(async () => {
            await form.props.onSubmit({ preventDefault: () => undefined });
        });

        const pageText = JSON.stringify(renderer!.toJSON());
        expect(pageText).toContain("View Results");
        expect(pageText).toContain("polling warning");
    });

    it("shows AG-UI badge and interactive button for AG-UI-capable agents", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("streaming", { agUiEnabled: true }));

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const pageText = JSON.stringify(renderer!.toJSON());
        expect(pageText).toContain("AG-UI");
        expect(pageText).toContain("Start Interactive Session");
    });

    it("uses AG-UI session start for AG-UI-capable agents on invoke", async () => {
        const startMock = vi.fn();
        useAgUiSessionMock.mockReturnValue({
            status: "idle",
            bootstrap: null,
            messages: [],
            accumulatedText: "",
            sessionId: null,
            error: null,
            start: startMock,
            disconnect: vi.fn(),
            reconnect: vi.fn(),
        });
        requestMock.mockResolvedValueOnce(buildAgent("streaming", { agUiEnabled: true }));

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const textarea = renderer!.root.findByType("textarea");
        act(() => {
            textarea.props.onChange({ target: { value: "interactive test" } });
        });

        const form = renderer!.root.findByType("form");
        await act(async () => {
            await form.props.onSubmit({ preventDefault: () => undefined });
        });

        expect(startMock).toHaveBeenCalledWith("interactive test");
        // REST invoke should NOT be called
        expect(requestMock).toHaveBeenCalledTimes(1); // only the agent detail fetch
        expect(streamMock).not.toHaveBeenCalled();
    });

    it("uses REST invoke for non-AG-UI agents even when hook is present", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("sync")).mockResolvedValueOnce({
            invocationId: "inv-2",
            agentName: "echo-agent",
            mode: "sync",
            status: "success",
            output: "rest response",
            timestamp: "2026-04-01T00:00:00Z",
        });

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const textarea = renderer!.root.findByType("textarea");
        act(() => {
            textarea.props.onChange({ target: { value: "rest test" } });
        });

        const form = renderer!.root.findByType("form");
        await act(async () => {
            await form.props.onSubmit({ preventDefault: () => undefined });
        });

        expect(requestMock).toHaveBeenCalledTimes(2);
    });

    it("shows AG-UI accumulated text via ResponseDisplay", async () => {
        useAgUiSessionMock.mockReturnValue({
            status: "connected",
            bootstrap: { sessionId: "sess-1" },
            messages: [],
            accumulatedText: "AG-UI streamed output",
            sessionId: "sess-1",
            error: null,
            start: vi.fn(),
            disconnect: vi.fn(),
            reconnect: vi.fn(),
        });
        requestMock.mockResolvedValueOnce(buildAgent("streaming", { agUiEnabled: true }));

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const pageText = JSON.stringify(renderer!.toJSON());
        expect(pageText).toContain("AG-UI streamed output");
        expect(pageText).toContain("AG-UI session active");
    });

    it("shows AG-UI error with retry option", async () => {
        const reconnectMock = vi.fn();
        useAgUiSessionMock.mockReturnValue({
            status: "error",
            bootstrap: null,
            messages: [],
            accumulatedText: "",
            sessionId: null,
            error: "AG-UI connection lost after 3 reconnect attempts",
            start: vi.fn(),
            disconnect: vi.fn(),
            reconnect: reconnectMock,
        });
        requestMock.mockResolvedValueOnce(buildAgent("streaming", { agUiEnabled: true }));

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const pageText = JSON.stringify(renderer!.toJSON());
        expect(pageText).toContain("AG-UI connection lost");
        expect(pageText).toContain("Retry AG-UI");
    });

    it("navigates back to catalogue when back button is clicked", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("sync"));

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<InvokePage />);
        });

        await flushMicrotasks();

        const backButton = renderer!
            .root
            .findAllByType("button")
            .find((node) => {
                const children = node.props.children;
                if (typeof children === "string") {
                    return children.includes("Back to Catalogue");
                }
                if (Array.isArray(children)) {
                    return children.join("").includes("Back to Catalogue");
                }
                return false;
            });
        if (!backButton) {
            throw new Error("Back button not found");
        }
        act(() => {
            backButton.props.onClick();
        });

        expect(navigateMock).toHaveBeenCalledWith("/");
    });
});
