/**
 * TenantStack — Per-tenant provisioned resources.
 *
 * Triggered by EventBridge on platform.tenant.created event.
 * NOT deployed by the platform pipeline — only by tenant provisioning.
 *
 * Provisions per tenant:
 *   - AgentCore Memory store
 *   - Execution role (scoped to tenant S3 prefix and DynamoDB partition)
 *   - Usage plan API key
 *   - SSM parameters for tenant configuration
 *
 * CDK context input: tenantId, tier, accountId
 *
 * Implemented in TASK-025.
 * ADRs: ADR-012
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';

export class TenantStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    // TODO: Implemented in TASK-025
  }
}
