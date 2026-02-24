/**
 * AgentCoreStack — AgentCore Runtime configuration and cross-region wiring.
 *
 * Runtime configuration: eu-west-1 (Dublin) — see ADR-009.
 * Memory template: provisioned per-tenant in TenantStack.
 * Identity configuration for Entra JWKS.
 * Observability metric stream eu-west-1 → eu-west-2.
 *
 * Implemented in TASK-024.
 * ADRs: ADR-001, ADR-009
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';

export class AgentCoreStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    // TODO: Implemented in TASK-024
  }
}
