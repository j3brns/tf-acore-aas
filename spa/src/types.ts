export type TenantTier = "basic" | "standard" | "premium";
export type TenantStatus = "active" | "suspended" | "deleted";
export type InvocationMode = "sync" | "streaming" | "async";
export type InvocationStatus = "success" | "error" | "timeout" | "throttled";
export type JobStatus = "pending" | "running" | "completed" | "failed";
export type SessionStatus = "active" | "completed" | "expired";

export interface Agent {
    agent_name: string;
    version: string;
    owner_team: string;
    tier_minimum: TenantTier;
    deployed_at: string;
    invocation_mode: InvocationMode;
    streaming_enabled: boolean;
    estimated_duration_seconds?: number;
}

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
    job_id: string;
    tenant_id: string;
    agent_name: string;
    status: JobStatus;
    created_at: string;
    started_at?: string;
    completed_at?: string;
    error_message?: string;
    result_url?: string;
}

export interface Session {
    session_id: string;
    tenant_id: string;
    agent_name: string;
    started_at: string;
    last_activity_at: string;
    status: SessionStatus;
}
