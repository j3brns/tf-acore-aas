import type {
  AgentsListResponseDto,
  HealthResponseDto,
  PlatformQuotaResponseDto,
  SessionsListResponseDto,
  TenantsListResponseDto,
} from "../api/contracts";
import type { AgentInvokeResponse, Agent } from "../types";

export const catalogueSingleAgent: AgentsListResponseDto = {
  items: [
    {
      agentName: "echo-agent",
      latestVersion: "1.0.0",
      tierMinimum: "basic",
      invocationMode: "sync",
      streamingEnabled: true,
      ownerTeam: "platform-team",
    },
  ],
};

export const catalogueMixedAgents: AgentsListResponseDto = {
  items: [
    {
      agentName: "echo-agent",
      latestVersion: "1.0.0",
      tierMinimum: "basic",
      invocationMode: "sync",
      streamingEnabled: true,
      ownerTeam: "platform-team",
    },
    {
      agentName: "research-agent",
      latestVersion: "2.1.0",
      tierMinimum: "premium",
      invocationMode: "async",
      streamingEnabled: false,
      ownerTeam: "ai-research",
    },
    {
      agentName: "ops-agent",
      latestVersion: "3.0.0",
      tierMinimum: "standard",
      invocationMode: "streaming",
      streamingEnabled: true,
      ownerTeam: "ops-team",
    },
  ],
};

export const sessionsList: SessionsListResponseDto = {
  items: [
    {
      sessionId: "sess-12345678",
      agentName: "echo-agent",
      startedAt: "2026-03-01T09:00:00Z",
      lastActivityAt: "2026-03-01T09:05:00Z",
      status: "active",
    },
    {
      sessionId: "sess-abcdef12",
      agentName: "research-agent",
      startedAt: "2026-03-01T10:00:00Z",
      lastActivityAt: "2026-03-01T10:15:00Z",
      status: "completed",
    },
  ],
};

export const healthOk: HealthResponseDto = {
  status: "ok",
  version: "0.1.0",
  timestamp: "2026-03-01T09:00:00Z",
};

export const healthFail: HealthResponseDto = {
  status: "fail",
  version: "0.1.0",
  timestamp: "2026-03-01T09:00:00Z",
};

export const tenantRows: TenantsListResponseDto = {
  items: [
    {
      tenantId: "t-001",
      appId: "app-001",
      displayName: "Acme",
      tier: "premium",
      status: "active",
      runtimeRegion: "eu-west-1",
    },
    {
      tenantId: "t-002",
      appId: "app-002",
      displayName: "Beta",
      tier: "basic",
      status: "suspended",
      runtimeRegion: "eu-west-1",
    },
  ],
};

export const quotaRows: PlatformQuotaResponseDto = {
  utilisation: [
    {
      region: "eu-west-1",
      quotaName: "ConcurrentSessions",
      currentValue: 5,
      limit: 25,
      utilisationPercentage: 20,
    },
    {
      region: "eu-central-1",
      quotaName: "ConcurrentSessions",
      currentValue: 23,
      limit: 25,
      utilisationPercentage: 92,
    },
  ],
};

export function buildAgent(invocationMode: Agent["invocation_mode"]): Agent {
  return {
    agent_name: "echo-agent",
    version: "1.0.0",
    owner_team: "platform",
    tier_minimum: "basic",
    deployed_at: "2026-03-08T00:00:00Z",
    invocation_mode: invocationMode,
    streaming_enabled: invocationMode === "streaming",
    estimated_duration_seconds: 5,
  };
}

export const asyncAccepted: AgentInvokeResponse = {
  jobId: "job-777",
  status: "accepted",
  mode: "async",
  pollUrl: "/v1/jobs/job-777",
};
