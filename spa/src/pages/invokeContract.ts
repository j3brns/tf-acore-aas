import { ApiError } from "../api/client";
import { AgentInvokeAsyncAccepted, AgentInvokeResponse, Job } from "../types";

type ErrorBody = {
    error?: {
        code?: string;
        message?: string;
    };
    message?: string;
};

export function createInvokePayload(input: string, sessionId?: string | null): { input: string; sessionId?: string } {
    return { input, sessionId: sessionId || undefined };
}

export function isAsyncInvokeAccepted(
    response: AgentInvokeResponse,
): response is AgentInvokeAsyncAccepted {
    return response.mode === "async" && typeof (response as AgentInvokeAsyncAccepted).jobId === "string";
}

export function extractJobIdFromPollUrl(pollUrl: string): string | null {
    const trimmed = pollUrl.trim();
    if (!trimmed) {
        return null;
    }

    const match = trimmed.match(/\/v1\/jobs\/([^/?#]+)/);
    if (!match?.[1]) {
        return null;
    }
    return decodeURIComponent(match[1]);
}

export function formatApiErrorMessage(error: unknown): string {
    if (error instanceof ApiError) {
        const body = error.body as ErrorBody;
        const message = body?.error?.message ?? body?.message;
        const code = body?.error?.code;
        if (message && code) {
            return `${code}: ${message}`;
        }
        if (message) {
            return message;
        }
        return `Request failed with HTTP ${error.status}`;
    }
    if (error instanceof Error && error.message) {
        return error.message;
    }
    return "Request failed";
}

export function isTerminalJob(job: Job): boolean {
    return job.status === "completed" || job.status === "failed";
}
