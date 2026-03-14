import { describe, expect, it } from "vitest";

import {
  toAgentCatalogueItem,
  toTenantAdminRow,
  type AgentSummaryDto,
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
