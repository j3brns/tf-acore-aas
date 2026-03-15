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
  usage?: TenantUsageDto | null;
};

export type TenantUsageDto = {
  requestsToday?: number;
  budgetRemainingUsd?: number;
  usageIdentifierKey?: string;
};

export type TenantReadResponseDto = {
  tenant: TenantDto;
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

export type TenantApiKeyRotateResponseDto = {
  tenantId: string;
  apiKeySecretArn: string;
  rotatedAt: string;
  versionId?: string | null;
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

export type WebhookEventDto = "job.completed" | "job.failed";

export type WebhookRegistrationResponseDto = {
  webhookId: string;
  callbackUrl: string;
  events: WebhookEventDto[];
  createdAt: string;
  signatureHeader: string;
  signatureAlgorithm?: string;
};

export type WebhookListItemDto = WebhookRegistrationResponseDto & {
  status?: string;
  description?: string;
  updatedAt?: string;
};

export type WebhooksListResponseDto = {
  items: WebhookListItemDto[];
};

export type TenantUserInviteDto = {
  inviteId: string;
  tenantId: string;
  email: string;
  role: string;
  status: "pending";
  expiresAt: string;
  displayName?: string | null;
};

export type TenantUserInviteAcceptedResponseDto = {
  invite: TenantUserInviteDto;
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
  requiredFieldPaths: string[];
};

export const SPA_OPENAPI_CONTRACTS: OpenApiContractExpectation[] = [
  {
    name: "catalogueAgents",
    path: "/v1/agents",
    method: "get",
    statusCode: "200",
    collectionProperty: "items",
    requiredFieldPaths: [
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
    requiredFieldPaths: [
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
    requiredFieldPaths: ["tenantId", "displayName", "tier", "status"],
  },
  {
    name: "tenantDetail",
    path: "/v1/tenants/{tenantId}",
    method: "get",
    statusCode: "200",
    requiredFieldPaths: [
      "tenant.tenantId",
      "tenant.displayName",
      "tenant.tier",
      "tenant.status",
      "tenant.ownerEmail",
      "tenant.ownerTeam",
      "tenant.createdAt",
      "tenant.updatedAt",
      "tenant.apiKeySecretArn",
      "tenant.usage.requestsToday",
      "tenant.usage.budgetRemainingUsd",
    ],
  },
  {
    name: "tenantAuditExport",
    path: "/v1/tenants/{tenantId}/audit-export",
    method: "get",
    statusCode: "200",
    requiredFieldPaths: ["tenantId", "downloadUrl", "expiresAt"],
  },
  {
    name: "tenantApiKeyRotate",
    path: "/v1/tenants/{tenantId}/api-key/rotate",
    method: "post",
    statusCode: "200",
    requiredFieldPaths: ["tenantId", "apiKeySecretArn", "rotatedAt"],
  },
  {
    name: "tenantInvite",
    path: "/v1/tenants/{tenantId}/users/invite",
    method: "post",
    statusCode: "202",
    requiredFieldPaths: [
      "invite.inviteId",
      "invite.tenantId",
      "invite.email",
      "invite.role",
      "invite.status",
      "invite.expiresAt",
    ],
  },
  {
    name: "webhookList",
    path: "/v1/webhooks",
    method: "get",
    statusCode: "200",
    collectionProperty: "items",
    requiredFieldPaths: [
      "webhookId",
      "callbackUrl",
      "events",
      "status",
      "description",
      "createdAt",
      "signatureHeader",
    ],
  },
  {
    name: "webhookRegister",
    path: "/v1/webhooks",
    method: "post",
    statusCode: "201",
    requiredFieldPaths: [
      "webhookId",
      "callbackUrl",
      "events",
      "createdAt",
      "signatureHeader",
    ],
  },
  {
    name: "jobStatus",
    path: "/v1/jobs/{jobId}",
    method: "get",
    statusCode: "200",
    requiredFieldPaths: ["jobId", "tenantId", "agentName", "status", "createdAt"],
  },
  {
    name: "quota",
    path: "/v1/platform/quota",
    method: "get",
    statusCode: "200",
    collectionProperty: "utilisation",
    requiredFieldPaths: ["region", "quotaName", "currentValue", "limit", "utilisationPercentage"],
  },
  {
    name: "topTenants",
    path: "/v1/platform/ops/top-tenants",
    method: "get",
    statusCode: "200",
    collectionProperty: "tenants",
    requiredFieldPaths: ["tenantId", "tokens"],
  },
  {
    name: "securityEvents",
    path: "/v1/platform/ops/security-events",
    method: "get",
    statusCode: "200",
    collectionProperty: "events",
    requiredFieldPaths: ["timestamp", "type", "tenantId", "details"],
  },
  {
    name: "errorRate",
    path: "/v1/platform/ops/error-rate",
    method: "get",
    statusCode: "200",
    requiredFieldPaths: ["errorRate", "periodMinutes", "threshold"],
  },
  {
    name: "platformFailover",
    path: "/v1/platform/failover",
    method: "post",
    statusCode: "200",
    requiredFieldPaths: ["status", "region", "previousRegion", "lockId", "changed"],
  },
  {
    name: "health",
    path: "/v1/health",
    method: "get",
    statusCode: "200",
    requiredFieldPaths: ["status", "version", "runtimeRegion", "timestamp"],
  },
  {
    name: "bffTokenRefresh",
    path: "/v1/bff/token-refresh",
    method: "post",
    statusCode: "200",
    requiredFieldPaths: ["accessToken", "tokenType", "expiresAt"],
  },
  {
    name: "bffSessionKeepalive",
    path: "/v1/bff/session-keepalive",
    method: "post",
    statusCode: "202",
    requiredFieldPaths: ["sessionId", "status", "expiresAt"],
  },
];
