export type TenantTier = "basic" | "standard" | "premium";
export type TenantStatus = "provisioning" | "active" | "suspended" | "deleted" | "failed";
export type InvocationMode = "sync" | "streaming" | "async";
export type InvocationStatus = "success" | "error" | "timeout" | "throttled";
export type JobStatus = "pending" | "running" | "completed" | "failed";
export type SessionStatus = "active" | "completed" | "expired";

export interface Invocation {
    invocation_id: string;
    tenant_id: string;
    agent_name: string;
    agent_version: string;
    session_id: string;
    input_tokens: number;
    output_tokens: number;
    latency_ms: number;
    status: InvocationStatus;
    invocation_mode: InvocationMode;
    timestamp: string;
}

export interface Job {
    jobId: string;
    tenantId: string;
    agentName: string;
    status: JobStatus;
    createdAt: string;
    startedAt?: string | null;
    completedAt?: string | null;
    errorMessage?: string | null;
    resultUrl?: string | null;
    webhookDelivered?: boolean;
    webhookUrl?: string | null;
}

export interface Session {
    session_id: string;
    tenant_id: string;
    agent_name: string;
    started_at: string;
    last_activity_at: string;
    status: SessionStatus;
}

export interface AgentInvokeSyncResponse {
    invocationId: string;
    agentName: string;
    agentVersion?: string;
    mode: Exclude<InvocationMode, "async">;
    status: InvocationStatus;
    output: string;
    sessionId?: string | null;
    timestamp: string;
    usage?: {
        inputTokens?: number;
        outputTokens?: number;
        latencyMs?: number;
    };
}

export interface AgentInvokeAsyncAccepted {
    jobId: string;
    status: "accepted";
    mode: "async";
    pollUrl: string;
    webhookDelivery?: "registered" | "not_registered";
}

export type AgentInvokeResponse = AgentInvokeSyncResponse | AgentInvokeAsyncAccepted;
