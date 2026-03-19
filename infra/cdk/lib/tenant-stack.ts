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
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { resolveTenantMemoryProperties } from './agentcore-memory-template';

export interface TenantStackProps extends cdk.StackProps {
  readonly authorizedRuntimeRegions?: readonly string[];
}

const DEFAULT_AUTHORIZED_RUNTIME_REGIONS = ['eu-west-1', 'eu-central-1'] as const;

function resolveAuthorizedRuntimeRegions(
  configuredRegions?: readonly string[],
): string[] {
  const regions = (configuredRegions ?? DEFAULT_AUTHORIZED_RUNTIME_REGIONS)
    .map((region) => region.trim())
    .filter((region) => region.length > 0);

  const uniqueRegions = Array.from(new Set(regions));
  if (uniqueRegions.length === 0) {
    throw new Error('authorizedRuntimeRegions must include at least one region');
  }

  return uniqueRegions;
}

export class TenantStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: TenantStackProps) {
    super(scope, id, props);

    const env = this.node.tryGetContext('env');
    if (!env) {
      throw new Error('env context is required');
    }

    const tenantIdParam = new cdk.CfnParameter(this, 'tenantId', {
      type: 'String',
      description: 'The unique identifier for the tenant',
      noEcho: false,
    });
    const tierParam = new cdk.CfnParameter(this, 'tier', {
      type: 'String',
      description: 'The service tier for the tenant (basic, standard, premium)',
      default: 'basic',
    });
    const accountIdParam = new cdk.CfnParameter(this, 'accountId', {
      type: 'String',
      description: 'The AWS account ID where tenant-scoped resources are authorized',
      default: cdk.Aws.ACCOUNT_ID,
    });

    const tenantId = tenantIdParam.valueAsString;
    const tier = tierParam.valueAsString;
    const accountId = accountIdParam.valueAsString;

    const authorizedRuntimeRegions = resolveAuthorizedRuntimeRegions(
      props?.authorizedRuntimeRegions,
    );

    // 1. Look up shared resources from SSM
    const tenantDataKeyArn = ssm.StringParameter.valueForStringParameter(
      this,
      `/platform/identity/${env}/tenant-data-kms-key-arn`,
    );
    const restApiId = ssm.StringParameter.valueForStringParameter(
      this,
      `/platform/core/${env}/rest-api-id`,
    );
    const usagePlanId = ssm.StringParameter.fromStringParameterAttributes(this, 'UsagePlanIdLookup', {
      parameterName: `/platform/core/${env}/usage-plan-${tier}-id`,
      simpleName: false,
    }).stringValue;
    const bridgeLambdaRoleArn = ssm.StringParameter.valueForStringParameter(
      this,
      `/platform/core/${env}/bridge-lambda-role-arn`,
    );
    const resultsBucketArn = ssm.StringParameter.valueForStringParameter(
      this,
      `/platform/core/${env}/results-bucket-arn`,
    );

    // 2. Tenant Execution Role (Layer 2 Isolation)
    // This role is assumed by the Bridge Lambda to act on behalf of the tenant.
    const executionRole = new iam.Role(this, 'TenantExecutionRole', {
      roleName: `platform-tenant-${tenantId}-execution-role`,
      description: `Execution role for tenant ${tenantId} (${tier} tier)`,
      assumedBy: new iam.ArnPrincipal(bridgeLambdaRoleArn),
    });

    // Scoped permissions for DynamoDB (ADR-012 isolation)
    executionRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'TenantDynamoDBAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:PutItem',
          'dynamodb:UpdateItem',
          'dynamodb:DeleteItem',
          'dynamodb:Query',
        ],
        resources: [
          `arn:aws:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/platform-invocations`,
          `arn:aws:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/platform-jobs`,
          `arn:aws:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/platform-sessions`,
        ],
        conditions: {
          'ForAllValues:StringLike': {
            'dynamodb:LeadingKeys': [
              `TENANT#${tenantId}*`,
            ],
          },
        },
      }),
    );

    // Scoped permissions for S3 (tenants/{tenant_id} prefix)
    executionRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'TenantS3Access',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket', 's3:DeleteObject'],
        resources: [
          resultsBucketArn,
          `${resultsBucketArn}/tenants/${tenantId}/*`,
        ],
      }),
    );

    // Permission to invoke AgentCore Runtime only in the approved primary/failover regions.
    executionRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AgentCoreRuntimeAccess',
        effect: iam.Effect.ALLOW,
        actions: ['bedrock-agentcore:InvokeRuntime'],
        resources: authorizedRuntimeRegions.map(
          (region) => `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/*`,
        ),
      }),
    );

    // 3. AgentCore Memory Store (Provisioned per-tenant)
    // Memory store requires its own service role for consolidation.
    const memoryServiceRole = new iam.Role(this, 'MemoryServiceRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: `Service role for tenant ${tenantId} memory store`,
    });
    // Add required permissions for memory consolidation (placeholder until exact actions confirmed)
    memoryServiceRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['kms:GenerateDataKey', 'kms:Decrypt'],
        resources: [tenantDataKeyArn],
      }),
    );
    const tenantMemory = resolveTenantMemoryProperties(tenantId);

    const memoryStore = new cdk.CfnResource(this, 'TenantMemoryStore', {
      type: 'AWS::BedrockAgentCore::Memory',
      properties: {
        Name: `platform-memory-${tenantId}`,
        Description: tenantMemory.description,
        EncryptionKeyArn: tenantDataKeyArn,
        EventExpiryDuration: tenantMemory.eventExpiryDuration,
        MemoryExecutionRoleArn: memoryServiceRole.roleArn,
        MemoryStrategies: tenantMemory.memoryStrategies,
        Tags: {
          TenantId: tenantId,
          Tier: tier,
        },
      },
    });

    // 4. API Gateway API Key for the tenant
    const apiKey = new apigateway.ApiKey(this, 'TenantApiKey', {
      apiKeyName: `platform-tenant-${tenantId}`, // pragma: allowlist secret
      description: `API key for tenant ${tenantId} (${tier} tier)`,
      enabled: true,
    });

    const usagePlan = apigateway.UsagePlan.fromUsagePlanId(this, 'TenantUsagePlan', usagePlanId);
    usagePlan.addApiKey(apiKey);

    // 5. SSM Parameters for tenant configuration (used by Bridge/Authoriser)
    new ssm.CfnParameter(this, 'TenantExecutionRoleArnParam', {
      name: `/platform/tenants/${tenantId}/execution-role-arn`,
      type: 'String',
      value: executionRole.roleArn,
      description: `Execution role ARN for tenant ${tenantId}`,
    });

    new ssm.CfnParameter(this, 'TenantMemoryStoreArnParam', {
      name: `/platform/tenants/${tenantId}/memory-store-arn`,
      type: 'String',
      value: memoryStore.getAtt('Arn').toString(),
      description: `Memory store ARN for tenant ${tenantId}`,
    });

    new ssm.CfnParameter(this, 'TenantApiKeyIdParam', {
      name: `/platform/tenants/${tenantId}/api-key-id`,
      type: 'String',
      value: apiKey.keyId,
      description: `API key ID for tenant ${tenantId}`,
    });

    // 6. Outputs
    new cdk.CfnOutput(this, 'ExecutionRoleArn', { value: executionRole.roleArn });
    new cdk.CfnOutput(this, 'MemoryStoreArn', { value: memoryStore.getAtt('Arn').toString() });
    new cdk.CfnOutput(this, 'ApiKeyId', { value: apiKey.keyId });
  }
}
