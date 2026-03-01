/**
 * Platform AaaS CDK Application entry point.
 *
 * Instantiates all platform stacks in deployment order (see ARCHITECTURE.md).
 * Run: npx cdk synth --context env=dev|staging|prod
 *
 * Stack order:
 *   1. NetworkStack    — VPC, subnets, endpoints         (TASK-021)
 *   2. IdentityStack   — OIDC, KMS keys                  (TASK-022)
 *   3. PlatformStack   — REST API, WAF, Lambdas, Gateway  (TASK-023)
 *   4. TenantStack     — per-tenant (EventBridge-triggered) (TASK-025)
 *   5. ObservabilityStack — dashboards, alarms            (TASK-026)
 *   6. AgentCoreStack  — Runtime config eu-west-1         (TASK-024)
 */
import * as cdk from 'aws-cdk-lib';
import { AgentCoreStack } from '../lib/agentcore-stack';
import { IdentityStack } from '../lib/identity-stack';
import { NetworkStack } from '../lib/network-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { PlatformStack } from '../lib/platform-stack';
import { TenantStack } from '../lib/tenant-stack';

const app = new cdk.App();

const env = app.node.tryGetContext('env') as string | undefined;
if (!env) {
  throw new Error('env context is required. Use: --context env=dev|staging|prod');
}

// eu-west-2 is the platform home region (see ARCHITECTURE.md, ADR-009).
// This is an architectural constant, not runtime configuration.
const HOME_REGION = 'eu-west-2';

const awsEnv: cdk.Environment = {
  account: process.env['CDK_DEFAULT_ACCOUNT'],
  region: HOME_REGION,
};

// 1. NetworkStack
new NetworkStack(app, `platform-network-${env}`, {
  env: awsEnv,
  description: `Platform network infrastructure — ${env}`,
});

// 2. IdentityStack
const identityStack = new IdentityStack(app, `platform-identity-${env}`, {
  env: awsEnv,
  description: `Platform identity and KMS keys — ${env}`,
});

// 3. PlatformStack
const platformStack = new PlatformStack(app, `platform-core-${env}`, {
  env: awsEnv,
  description: `Platform core services — ${env}`,
  tenantDataKey: identityStack.tenantDataKey,
  platformConfigKey: identityStack.platformConfigKey,
});

// 4. TenantStack (stub — real deployments triggered by EventBridge)
new TenantStack(app, `platform-tenant-stub-${env}`, {
  env: awsEnv,
  description: `Platform per-tenant resources stub — ${env}`,
});

// 5. ObservabilityStack
new ObservabilityStack(app, `platform-observability-${env}`, {
  env: awsEnv,
  description: `Platform observability — ${env}`,
  api: platformStack.api,
  apiWebAcl: platformStack.apiWebAcl,
  spaDistribution: platformStack.spaDistribution,
  bridgeFn: platformStack.bridgeFn,
  bffFn: platformStack.bffFn,
  authoriserFn: platformStack.authoriserFn,
  requestInterceptorFn: platformStack.requestInterceptorFn,
  responseInterceptorFn: platformStack.responseInterceptorFn,
  tenantsTable: platformStack.tenantsTable,
  agentsTable: platformStack.agentsTable,
  invocationsTable: platformStack.invocationsTable,
  jobsTable: platformStack.jobsTable,
  sessionsTable: platformStack.sessionsTable,
  toolsTable: platformStack.toolsTable,
  opsLocksTable: platformStack.opsLocksTable,
  dlqs: platformStack.dlqs,
});

// 6. AgentCoreStack (cross-region: deploys config for eu-west-1 Runtime)
new AgentCoreStack(app, `platform-agentcore-${env}`, {
  env: awsEnv,
  description: `Platform AgentCore configuration — ${env}`,
});
