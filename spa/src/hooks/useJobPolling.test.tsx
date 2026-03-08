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
});
