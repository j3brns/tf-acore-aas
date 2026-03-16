import { Construct } from 'constructs';

export interface EntraConfiguration {
  readonly tenantId: string;
  readonly audience: string;
  readonly jwksUrl: string;
  readonly issuer: string;
  readonly discoveryUrl: string;
  readonly tokenEndpoint: string;
}

function optionalContext(scope: Construct, name: string): string | undefined {
  const value = scope.node.tryGetContext(name);
  if (typeof value !== 'string' || value.trim() === '') {
    return undefined;
  }
  return value.trim();
}

export function resolveEntraConfiguration(scope: Construct): EntraConfiguration {
  const tenantId = optionalContext(scope, 'entraTenantId');
  if (!tenantId) {
    throw new Error(
      'entraTenantId context is required for all environments (e.g. --context entraTenantId=00000000-0000-0000-0000-000000000000). ' +
      'Check docs/entra-setup.md for how to find your Directory (tenant) ID.'
    );
  }

  const audience = optionalContext(scope, 'entraAudience') ?? 'platform-api';
  const issuer = optionalContext(scope, 'entraIssuer') ?? `https://login.microsoftonline.com/${tenantId}/v2.0`;
  const jwksUrl =
    optionalContext(scope, 'entraJwksUrl') ??
    `https://login.microsoftonline.com/${tenantId}/discovery/v2.0/keys`;
  const discoveryUrl =
    optionalContext(scope, 'entraDiscoveryUrl') ??
    `https://login.microsoftonline.com/${tenantId}/v2.0/.well-known/openid-configuration`;
  const tokenEndpoint =
    optionalContext(scope, 'entraTokenEndpoint') ??
    `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`;

  return {
    tenantId,
    audience,
    issuer,
    jwksUrl,
    discoveryUrl,
    tokenEndpoint,
  };
}
