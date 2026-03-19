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
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
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
    const monthlyBudgetUsdParam = new cdk.CfnParameter(this, 'monthlyBudgetUsd', {
      type: 'Number',
      description: 'Monthly budget in USD for the tenant',
      default: 100,
    });

    const tenantId = this.node.tryGetContext('tenantId') || tenantIdParam.valueAsString;
    const tier = this.node.tryGetContext('tier') || tierParam.valueAsString;
    const accountId = this.node.tryGetContext('accountId') || accountIdParam.valueAsString;
    const monthlyBudgetUsdContext = this.node.tryGetContext('monthlyBudgetUsd');
    const monthlyBudgetUsd = monthlyBudgetUsdContext ? parseFloat(monthlyBudgetUsdContext) : monthlyBudgetUsdParam.valueAsNumber;

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

    // 6. Per-Tenant Observability (TASK-026/TASK-290)

    const dashboard = new cloudwatch.Dashboard(this, 'TenantDashboard', {
      dashboardName: `platform-tenant-${tenantId}`,
    });

    const tenantDimensions = { TenantId: tenantId };

    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: `# Tenant Usage: ${tenantId} (${tier} tier)`,
        width: 24,
        height: 1,
      }),
      new cloudwatch.GraphWidget({
        title: 'API Request Count',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/API',
            metricName: 'RequestCount',
            dimensionsMap: tenantDimensions,
            statistic: 'Sum',
            period: cdk.Duration.minutes(5),
          }),
        ],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'API Latency (p99)',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/API',
            metricName: 'Latency',
            dimensionsMap: tenantDimensions,
            statistic: 'p99',
            period: cdk.Duration.minutes(5),
          }),
        ],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'API Errors (4xx/5xx)',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/API',
            metricName: 'ErrorCount',
            dimensionsMap: tenantDimensions,
            statistic: 'Sum',
            period: cdk.Duration.minutes(5),
          }),
        ],
        width: 8,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '# Agent Performance (Bridge Real-time)',
        width: 24,
        height: 1,
      }),
      new cloudwatch.GraphWidget({
        title: 'Invocations (Count)',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/Bridge',
            metricName: 'Invocations',
            dimensionsMap: tenantDimensions,
            statistic: 'Sum',
            period: cdk.Duration.minutes(5),
          }),
        ],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'Bridge Latency (Avg)',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/Bridge',
            metricName: 'Latency',
            dimensionsMap: tenantDimensions,
            statistic: 'Average',
            period: cdk.Duration.minutes(5),
          }),
        ],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'Bridge Errors (Count)',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/Bridge',
            metricName: 'Errors',
            dimensionsMap: tenantDimensions,
            statistic: 'Sum',
            period: cdk.Duration.minutes(5),
          }),
        ],
        width: 8,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '# Token Usage',
        width: 24,
        height: 1,
      }),
      new cloudwatch.GraphWidget({
        title: 'Daily Token Usage (Cumulative Monthly)',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/Billing',
            metricName: 'InputTokens',
            dimensionsMap: { ...tenantDimensions, Tier: tier },
            statistic: 'Maximum',
            period: cdk.Duration.days(1),
            label: 'Input Tokens',
          }),
          new cloudwatch.Metric({
            namespace: 'Platform/Billing',
            metricName: 'OutputTokens',
            dimensionsMap: { ...tenantDimensions, Tier: tier },
            statistic: 'Maximum',
            period: cdk.Duration.days(1),
            label: 'Output Tokens',
          }),
        ],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Real-time Token Throughput',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/Bridge',
            metricName: 'InputTokens',
            dimensionsMap: tenantDimensions,
            statistic: 'Sum',
            period: cdk.Duration.minutes(5),
            label: 'Input (5m Sum)',
          }),
          new cloudwatch.Metric({
            namespace: 'Platform/Bridge',
            metricName: 'OutputTokens',
            dimensionsMap: tenantDimensions,
            statistic: 'Sum',
            period: cdk.Duration.minutes(5),
            label: 'Output (5m Sum)',
          }),
        ],
        width: 12,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '# Billing & Budget',
        width: 24,
        height: 1,
      }),
      new cloudwatch.SingleValueWidget({
        title: 'Current Monthly Cost (USD)',
        metrics: [
          new cloudwatch.Metric({
            namespace: 'Platform/Billing',
            metricName: 'MonthlyCost',
            dimensionsMap: { ...tenantDimensions, Tier: tier },
            statistic: 'Maximum',
            period: cdk.Duration.days(1),
          }),
        ],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Daily Cost Trend (USD)',
        left: [
          new cloudwatch.Metric({
            namespace: 'Platform/Billing',
            metricName: 'DailyCost',
            dimensionsMap: { ...tenantDimensions, Tier: tier },
            statistic: 'Sum',
            period: cdk.Duration.days(1),
          }),
        ],
        width: 12,
      }),
    );

    // Budget Alarm: Triggers if MonthlyCost exceeds monthlyBudgetUsd
    const budgetAlarm = new cloudwatch.Alarm(this, 'TenantBudgetAlarm', {
      alarmName: `platform-tenant-${tenantId}-budget-exceeded`,
      alarmDescription: `Monthly budget exceeded for tenant ${tenantId} (Limit: $${monthlyBudgetUsd})`,
      metric: new cloudwatch.Metric({
        namespace: 'Platform/Billing',
        metricName: 'MonthlyCost',
        dimensionsMap: { ...tenantDimensions, Tier: tier },
        statistic: 'Maximum',
        period: cdk.Duration.hours(6), // Check every 6 hours
      }),
      threshold: monthlyBudgetUsd,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // 7. Outputs
    new cdk.CfnOutput(this, 'ExecutionRoleArn', { value: executionRole.roleArn });
    new cdk.CfnOutput(this, 'MemoryStoreArn', { value: memoryStore.getAtt('Arn').toString() });
    new cdk.CfnOutput(this, 'ApiKeyId', { value: apiKey.keyId });
    new cdk.CfnOutput(this, 'DashboardName', { value: dashboard.dashboardName });
    new cdk.CfnOutput(this, 'BudgetAlarmArn', { value: budgetAlarm.alarmArn });
  }
}
