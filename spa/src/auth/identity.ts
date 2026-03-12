import type { AccountInfo } from "@azure/msal-browser";

type ClaimsRecord = Record<string, unknown>;

function toClaimsRecord(claims: unknown): ClaimsRecord | null {
  if (!claims || typeof claims !== "object") {
    return null;
  }
  return claims as ClaimsRecord;
}

export function resolveTenantId(claims: unknown): string | null {
  const map = toClaimsRecord(claims);
  if (!map) {
    return null;
  }

  const tenantId = map.tenantid ?? map.tenantId;
  if (typeof tenantId !== "string") {
    return null;
  }

  const trimmed = tenantId.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function resolveRoles(claims: unknown): string[] {
  const map = toClaimsRecord(claims);
  if (!map) {
    return [];
  }

  const roles = map.roles;
  if (!Array.isArray(roles)) {
    return [];
  }

  return roles.filter((role): role is string => typeof role === "string" && role.trim().length > 0);
}

export function hasPlatformOperatorRole(claims: unknown): boolean {
  return resolveRoles(claims).some((role) => role === "Platform.Admin" || role === "Platform.Operator");
}

export function describeRoleSet(claims: unknown): string {
  const roles = resolveRoles(claims);
  if (roles.length === 0) {
    return "Tenant User";
  }

  if (roles.includes("Platform.Admin")) {
    return "Platform Admin";
  }

  if (roles.includes("Platform.Operator")) {
    return "Platform Operator";
  }

  return roles[0];
}

export function getIdentityContext(account: AccountInfo | null) {
  const claims = account?.idTokenClaims;

  return {
    displayName: account?.name ?? "Unknown user",
    username: account?.username ?? "unknown",
    tenantId: resolveTenantId(claims),
    roles: resolveRoles(claims),
    roleLabel: describeRoleSet(claims),
    isOperator: hasPlatformOperatorRole(claims),
  };
}
