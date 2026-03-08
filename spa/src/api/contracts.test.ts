import { describe, expect, it } from "vitest";

import {
  toAgentCatalogueItem,
  toSessionRow,
  toTenantAdminRow,
  type AgentSummaryDto,
  type SessionSummaryDto,
  type TenantDto,
} from "./contracts";

describe("contracts adapters", () => {
  it("maps AgentSummaryDto to catalogue model", () => {
    const dto: AgentSummaryDto = {
      agentName: "echo-agent",
      latestVersion: "1.2.3",
      tierMinimum: "standard",
      invocationMode: "streaming",
      streamingEnabled: true,
      ownerTeam: "team-platform",
    };

    expect(toAgentCatalogueItem(dto)).toEqual({
      agentName: "echo-agent",
      version: "1.2.3",
      tier: "standard",
      invocationMode: "streaming",
      streamingEnabled: true,
      ownerTeam: "team-platform",
    });
  });

  it("maps SessionSummaryDto to session row model", () => {
    const dto: SessionSummaryDto = {
      sessionId: "s-123",
      agentName: "echo-agent",
      startedAt: "2026-03-01T09:00:00Z",
      lastActivityAt: "2026-03-01T09:05:00Z",
      status: "active",
    };

    expect(toSessionRow(dto)).toEqual({
      sessionId: "s-123",
      agentName: "echo-agent",
      startedAt: "2026-03-01T09:00:00Z",
      lastActivityAt: "2026-03-01T09:05:00Z",
      status: "active",
    });
  });

  it("maps TenantDto to admin tenant row", () => {
    const dto: TenantDto = {
      tenantId: "t-001",
      appId: "app-001",
      displayName: "Acme",
      tier: "premium",
      status: "active",
      runtimeRegion: "eu-west-1",
    };

    expect(toTenantAdminRow(dto)).toEqual({
      tenantId: "t-001",
      displayName: "Acme",
      tier: "premium",
      status: "active",
      runtimeRegion: "eu-west-1",
    });
  });
});
