import React, { useEffect } from "react";
import TestRenderer, { act } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useJobPolling } from "./useJobPolling";

const { getApiClientMock, requestMock } = vi.hoisted(() => {
    const request = vi.fn();
    return {
        getApiClientMock: vi.fn(() => ({ request })),
        requestMock: request,
    };
});

vi.mock("../api/client", async () => {
    const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
    return {
        ...actual,
        getApiClient: getApiClientMock,
    };
});

const tokenProvider = async () => "token";

type PollingState = ReturnType<typeof useJobPolling>;

function PollingHarness({
    jobId,
    onState,
}: {
    jobId: string | null;
    onState: (state: PollingState) => void;
}) {
    const state = useJobPolling(jobId, tokenProvider, 10);
    const { status, loading, error } = state;
    useEffect(() => {
        onState({ status, loading, error });
    }, [error, loading, onState, status]);
    return null;
}

async function flushMicrotasks(): Promise<void> {
    await act(async () => {
        await Promise.resolve();
    });
}

describe("useJobPolling", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("polls job status until terminal completion", async () => {
        const states: PollingState[] = [];

        requestMock
            .mockResolvedValueOnce({
                jobId: "job-1",
                tenantId: "tenant-1",
                agentName: "echo-agent",
                status: "running",
                createdAt: "2026-03-08T00:00:00Z",
            })
            .mockResolvedValueOnce({
                jobId: "job-1",
                tenantId: "tenant-1",
                agentName: "echo-agent",
                status: "completed",
                createdAt: "2026-03-08T00:00:00Z",
                completedAt: "2026-03-08T00:00:10Z",
                resultUrl: "https://example.test/result",
            });

        await act(async () => {
            TestRenderer.create(<PollingHarness jobId="job-1" onState={(state) => states.push(state)} />);
        });

        await flushMicrotasks();
        expect(requestMock).toHaveBeenCalledTimes(1);

        await act(async () => {
            vi.advanceTimersByTime(10);
            await Promise.resolve();
        });

        await flushMicrotasks();
        expect(requestMock).toHaveBeenCalledTimes(2);

        await act(async () => {
            vi.advanceTimersByTime(100);
            await Promise.resolve();
        });

        expect(requestMock).toHaveBeenCalledTimes(2);
        const latest = states.at(-1);
        expect(latest?.status?.status).toBe("completed");
        expect(latest?.loading).toBe(false);
        expect(latest?.error).toBeNull();
    });

    it("returns formatted contract errors when polling fails", async () => {
        const states: PollingState[] = [];
        requestMock.mockRejectedValue(
            new Error("Request failed with HTTP 500"),
        );

        await act(async () => {
            TestRenderer.create(<PollingHarness jobId="job-2" onState={(state) => states.push(state)} />);
        });

        await flushMicrotasks();

        const latest = states.at(-1);
        expect(latest?.loading).toBe(false);
        expect(latest?.error).toBe("Request failed with HTTP 500");
    });

    it("keeps polling disabled when no job id is provided", async () => {
        const states: PollingState[] = [];

        await act(async () => {
            TestRenderer.create(<PollingHarness jobId={null} onState={(state) => states.push(state)} />);
        });

        await flushMicrotasks();

        const latest = states.at(-1);
        expect(requestMock).not.toHaveBeenCalled();
        expect(latest?.status).toBeNull();
        expect(latest?.loading).toBe(false);
        expect(latest?.error).toBeNull();
    });

    it("stops polling after terminal failed status", async () => {
        const states: PollingState[] = [];
        requestMock.mockResolvedValueOnce({
            jobId: "job-3",
            tenantId: "tenant-1",
            agentName: "echo-agent",
            status: "failed",
            createdAt: "2026-03-08T00:00:00Z",
            completedAt: "2026-03-08T00:00:03Z",
            errorMessage: "upstream timeout",
        });

        await act(async () => {
            TestRenderer.create(<PollingHarness jobId="job-3" onState={(state) => states.push(state)} />);
        });

        await flushMicrotasks();

        await act(async () => {
            vi.advanceTimersByTime(100);
            await Promise.resolve();
        });

        const latest = states.at(-1);
        expect(requestMock).toHaveBeenCalledTimes(1);
        expect(latest?.status?.status).toBe("failed");
        expect(latest?.loading).toBe(false);
        expect(latest?.error).toBeNull();
    });

    it("ignores in-flight success results after unmount", async () => {
        const states: PollingState[] = [];
        let resolveRequest: ((value: unknown) => void) | null = null;
        requestMock.mockImplementationOnce(
            () =>
                new Promise((resolve) => {
                    resolveRequest = resolve;
                }),
        );

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<PollingHarness jobId="job-4" onState={(state) => states.push(state)} />);
        });

        expect(requestMock).toHaveBeenCalledTimes(1);

        await act(async () => {
            renderer!.unmount();
        });

        await act(async () => {
            resolveRequest?.({
                jobId: "job-4",
                tenantId: "tenant-1",
                agentName: "echo-agent",
                status: "running",
                createdAt: "2026-03-08T00:00:00Z",
            });
            await Promise.resolve();
        });

        expect(states.some((state) => state.status?.jobId === "job-4")).toBe(false);
    });

    it("ignores in-flight errors after unmount", async () => {
        const states: PollingState[] = [];
        let rejectRequest: ((reason?: unknown) => void) | null = null;
        requestMock.mockImplementationOnce(
            () =>
                new Promise((_, reject) => {
                    rejectRequest = reject;
                }),
        );

        let renderer: TestRenderer.ReactTestRenderer;
        await act(async () => {
            renderer = TestRenderer.create(<PollingHarness jobId="job-5" onState={(state) => states.push(state)} />);
        });

        expect(requestMock).toHaveBeenCalledTimes(1);

        await act(async () => {
            renderer!.unmount();
        });

        await act(async () => {
            rejectRequest?.(new Error("late failure"));
            await Promise.resolve();
        });

        expect(states.some((state) => state.error === "late failure")).toBe(false);
    });
});
