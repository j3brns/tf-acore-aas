import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import {
    createInvokePayload,
    extractJobIdFromPollUrl,
    formatApiErrorMessage,
    isAsyncInvokeAccepted,
    isTerminalJob,
} from "./invokeContract";

describe("invokeContract", () => {
    it("maps prompt input to the backend contract payload shape", () => {
        expect(createInvokePayload("hello world")).toEqual({ input: "hello world" });
    });

    it("detects async accepted responses", () => {
        expect(
            isAsyncInvokeAccepted({
                jobId: "job-123",
                mode: "async",
                status: "accepted",
                pollUrl: "/v1/jobs/job-123",
            }),
        ).toBe(true);

        expect(
            isAsyncInvokeAccepted({
                invocationId: "inv-123",
                agentName: "echo-agent",
                mode: "sync",
                status: "success",
                output: "ok",
                timestamp: "2026-03-08T00:00:00Z",
            }),
        ).toBe(false);
    });

    it("extracts job id from polling urls", () => {
        expect(extractJobIdFromPollUrl("/v1/jobs/job-abc")).toBe("job-abc");
        expect(extractJobIdFromPollUrl("https://api.example.test/v1/jobs/job%2F123")).toBe("job/123");
        expect(extractJobIdFromPollUrl("/v1/agents/echo-agent/invoke")).toBeNull();
    });

    it("formats API error payloads with contract error details", () => {
        const response = new Response(JSON.stringify({ error: { code: "INVALID_REQUEST", message: "Missing input" } }), {
            status: 400,
            headers: { "content-type": "application/json" },
        });
        const error = new ApiError("HTTP 400", response, {
            error: { code: "INVALID_REQUEST", message: "Missing input" },
        });

        expect(formatApiErrorMessage(error)).toBe("INVALID_REQUEST: Missing input");
        expect(formatApiErrorMessage(new Error("network down"))).toBe("network down");
    });

    it("marks completed and failed jobs as terminal", () => {
        expect(
            isTerminalJob({
                jobId: "job-1",
                tenantId: "tenant-1",
                agentName: "echo-agent",
                status: "completed",
                createdAt: "2026-03-08T00:00:00Z",
            }),
        ).toBe(true);
        expect(
            isTerminalJob({
                jobId: "job-2",
                tenantId: "tenant-1",
                agentName: "echo-agent",
                status: "running",
                createdAt: "2026-03-08T00:00:00Z",
            }),
        ).toBe(false);
    });
});
