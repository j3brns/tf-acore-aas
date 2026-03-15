export type AgentSummaryDto = {
  agentName: string;
  latestVersion: string;
  tierMinimum: "basic" | "standard" | "premium";
  invocationMode: "sync" | "streaming" | "async";
  streamingEnabled: boolean;
  estimatedDurationSeconds?: number | null;
  ownerTeam?: string;
};

export type AgentsListResponseDto = {
  items: AgentSummaryDto[];
};

export type AgentDetailVersionDto = {
  version: string;
  deployedAt: string;
  invocationMode?: "sync" | "streaming" | "async";
  streamingEnabled?: boolean;
};

export type AgentDetailDto = AgentSummaryDto & {
  versions?: AgentDetailVersionDto[];
};

export type AgentCatalogueItem = {
  agentName: string;
  version: string;
  tier: "basic" | "standard" | "premium";
  invocationMode: "sync" | "streaming" | "async";
  streamingEnabled: boolean;
  ownerTeam: string;
};

export function toAgentCatalogueItem(dto: AgentSummaryDto): AgentCatalogueItem {
  return {
    agentName: dto.agentName,
    version: dto.latestVersion,
    tier: dto.tierMinimum,
    invocationMode: dto.invocationMode,
    streamingEnabled: dto.streamingEnabled,
    ownerTeam: dto.ownerTeam ?? "unknown",
  };
}

export type TenantDto = {
  tenantId: string;
  appId: string;
  displayName: string;
  tier: "basic" | "standard" | "premium";
  status: "active" | "suspended" | "deleted";
  runtimeRegion?: string | null;
  fallbackRegion?: string | null;
  monthlyBudgetUsd?: number | null;
  ownerEmail?: string | null;
  ownerTeam?: string | null;
  accountId?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type SessionSummaryDto = {
  sessionId: string;
  agentName: string;
  startedAt: string;
  lastActivityAt: string;
  status: "active" | "completed" | "expired" | "failed";
};

export type SessionsListResponseDto = {
  items: SessionSummaryDto[];
  nextToken?: string | null;
};

export type TenantUpdateRequestDto = {
  displayName?: string;
  tier?: "basic" | "standard" | "premium";
  status?: "active" | "suspended" | "deleted";
  runtimeRegion?: string;
  fallbackRegion?: string;
  monthlyBudgetUsd?: number | null;
};

export type AuditExportResponseDto = {
  tenantId: string;
  downloadUrl: string;
  expiresAt: string;
};

export type TopTenantsResponseDto = {
  tenants: {
    tenantId: string;
    tokens: number;
  }[];
};

export type SecurityEventDto = {
  timestamp: string;
  type: string;
  tenantId: string;
  details: string;
};

export type SecurityEventsResponseDto = {
  events: SecurityEventDto[];
};

export type ErrorRateResponseDto = {
  errorRate: number;
  periodMinutes: number;
  threshold: number;
};

export type FailoverRequestDto = {
  targetRegion: string;
  lockId: string;
};

export type FailoverResponseDto = {
  status: "completed";
  region: string;
  previousRegion: string;
  lockId: string;
  changed: boolean;
};

export type TenantsListResponseDto = {
  items: TenantDto[];
  nextToken?: string | null;
};

export type SessionDto = {
  sessionId: string;
  agentName: string;
  startedAt: string;
  lastActivityAt: string;
  status: "active" | "completed" | "expired";
};

export type SessionsListResponseDto = {
  items: SessionDto[];
  nextToken?: string | null;
};

export type TenantAdminRow = {
  tenantId: string;
  displayName: string;
  tier: "basic" | "standard" | "premium";
  status: "active" | "suspended" | "deleted";
  runtimeRegion: string | null;
};

export function toTenantAdminRow(dto: TenantDto): TenantAdminRow {
  return {
    tenantId: dto.tenantId,
    displayName: dto.displayName,
    tier: dto.tier,
    status: dto.status,
    runtimeRegion: dto.runtimeRegion ?? null,
  };
}

export type PlatformQuotaEntryDto = {
  region: string;
  quotaName: string;
  currentValue: number;
  limit: number;
  utilisationPercentage: number;
};

export type PlatformQuotaResponseDto = {
  utilisation: PlatformQuotaEntryDto[];
};

export type HealthResponseDto = {
  status: "ok" | "degraded" | "fail";
  version: string;
  runtimeRegion: string;
  timestamp: string;
};

export type BffTokenRefreshRequestDto = {
  scopes: string[];
};

export type BffTokenRefreshResponseDto = {
  accessToken: string;
  tokenType: string;
  expiresAt: string;
  scope: string;
};

export type BffSessionKeepaliveRequestDto = {
  sessionId: string;
  agentName: string;
};

export type BffSessionKeepaliveResponseDto = {
  sessionId: string;
  status: "accepted";
  expiresAt: string;
};

export type OpenApiContractExpectation = {
  name: string;
  path: string;
  method: "get" | "post" | "patch" | "delete";
  statusCode: string;
  collectionProperty?: string;
  requiredFields: string[];
};

export const SPA_OPENAPI_CONTRACTS: OpenApiContractExpectation[] = [
  {
    name: "catalogueAgents",
    path: "/v1/agents",
    method: "get",
    statusCode: "200",
    collectionProperty: "items",
    requiredFields: [
      "agentName",
      "latestVersion",
      "tierMinimum",
      "invocationMode",
      "streamingEnabled",
    ],
  },
  {
    name: "agentDetail",
    path: "/v1/agents/{agentName}",
    method: "get",
    statusCode: "200",
    requiredFields: [
      "agentName",
      "latestVersion",
      "tierMinimum",
      "invocationMode",
      "streamingEnabled",
      "versions",
    ],
  },
  {
    name: "tenants",
    path: "/v1/tenants",
    method: "get",
    statusCode: "200",
    collectionProperty: "items",
    requiredFields: ["tenantId", "displayName", "tier", "status"],
  },
  {
    name: "quota",
    path: "/v1/platform/quota",
    method: "get",
    statusCode: "200",
    collectionProperty: "utilisation",
    requiredFields: ["region", "quotaName", "currentValue", "limit", "utilisationPercentage"],
  },
  {
    name: "health",
    path: "/v1/health",
    method: "get",
    statusCode: "200",
    requiredFields: ["status", "version", "timestamp"],
  },
  {
    name: "bffTokenRefresh",
    path: "/v1/bff/token-refresh",
    method: "post",
    statusCode: "200",
    requiredFields: ["accessToken", "tokenType", "expiresAt"],
  },
  {
    name: "bffSessionKeepalive",
    path: "/v1/bff/session-keepalive",
    method: "post",
    statusCode: "202",
    requiredFields: ["sessionId", "status", "expiresAt"],
  },
];
