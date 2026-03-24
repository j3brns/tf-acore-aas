/**
 * PlatformStack — REST API, WAF, CloudFront, Bridge Lambda, BFF Lambda,
 *                 Authoriser Lambda, AgentCore Gateway.
 *
 * REST API (not HTTP API) with usage plans, per-method throttling, WAF association.
 * Public SPA CloudFront distribution currently has an explicit no-WebACL exception:
 * the platform has not yet introduced an approved global/us-east-1 edge security stack,
 * so only the regional API surface is WAF-protected in this stack.
 * Authoriser Lambda: provisioned concurrency 10.
 * AgentCore Gateway with REQUEST and RESPONSE interceptors wired.
 *
 * Implemented in TASK-023.
 * ADRs: ADR-003, ADR-004, ADR-009, ADR-011
 */
import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as codedeploy from 'aws-cdk-lib/aws-codedeploy';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import { Template } from 'aws-cdk-lib/assertions';
import { Construct } from 'constructs';
import * as fs from 'fs';
import * as path from 'path';
import { resolveEntraConfiguration } from './entra-config';
import { TenantStack } from './tenant-stack';

const TENANT_AUTHORIZED_RUNTIME_REGIONS = ['eu-west-1', 'eu-central-1'] as const;

type PythonLambdaProps = {
  assetPath: string;
  handler: string;
  functionNameSuffix: string;
  timeout: cdk.Duration;
  memorySize: number;
  environment?: Record<string, string>;
};

type BridgeCanaryPolicy = {
  readonly deploymentConfig: codedeploy.ILambdaDeploymentConfig;
  readonly summary: string;
};

type GatewayPolicyConfiguration = {
  readonly enforcementMode: 'LOG_ONLY' | 'ENFORCE';
  readonly policyEngineName: string;
  readonly policyName: string;
};

export interface PlatformStackProps extends cdk.StackProps {
  readonly vpc: ec2.IVpc;
  readonly tenantDataKey: kms.IKey;
  readonly platformConfigKey: kms.IKey;
}

function ensureTenantStubTemplate(
  env: string,
  stackEnv: cdk.Environment,
  authorizedRuntimeRegions: readonly string[],
): string {
  const generatedDir = path.join(__dirname, '../generated');
  const stackId = `platform-tenant-stub-${env}`;
  const templatePath = path.join(generatedDir, `${stackId}.template.json`);

  if (fs.existsSync(templatePath)) {
    return templatePath;
  }

  fs.mkdirSync(generatedDir, { recursive: true });

  const tempApp = new cdk.App({
    outdir: generatedDir,
    context: {
      env,
    },
  });

  const tenantStubStack = new TenantStack(tempApp, stackId, {
    env: stackEnv,
    description: `Platform per-tenant resources stub — ${env}`,
    authorizedRuntimeRegions,
  });

  const tenantTemplate = Template.fromStack(tenantStubStack).toJSON();
  fs.writeFileSync(templatePath, JSON.stringify(tenantTemplate, null, 2));

  if (!fs.existsSync(templatePath)) {
    throw new Error(`Failed to synthesize tenant stub template at ${templatePath}`);
  }

  return templatePath;
}

export class PlatformStack extends cdk.Stack {
  public readonly vpc: ec2.IVpc;
  public readonly api: apigateway.RestApi;
  public readonly tenantsTable: dynamodb.Table;
  public readonly agentsTable: dynamodb.Table;
  public readonly invocationsTable: dynamodb.Table;
  public readonly jobsTable: dynamodb.Table;
  public readonly sessionsTable: dynamodb.Table;
  public readonly toolsTable: dynamodb.Table;
  public readonly opsLocksTable: dynamodb.Table;
  public readonly gatewayIdempotencyTable: dynamodb.Table;

  public readonly bridgeFn: lambda.Function;
  public readonly bffFn: lambda.Function;
  public readonly authoriserFn: lambda.Function;
  public readonly tenantApiFn: lambda.Function;
  public readonly webhookDeliveryFn: lambda.Function;
  public readonly requestInterceptorFn: lambda.Function;
  public readonly responseInterceptorFn: lambda.Function;
  public readonly billingFn: lambda.Function;

  public readonly apiWebAcl: wafv2.CfnWebACL;
  public readonly spaDistribution: cloudfront.CfnDistribution;

  public readonly dlqs: Record<string, sqs.IQueue> = {};

  constructor(scope: Construct, id: string, props: PlatformStackProps) {
    super(scope, id, props);
    this.vpc = props.vpc;

    const env = ((this.node.tryGetContext('env') as string | undefined) ?? 'dev').toLowerCase();
    const bridgeCanaryPolicy = this.resolveBridgeCanaryPolicy(env);
    const gatewayPolicyConfiguration = this.resolveGatewayPolicyConfiguration(env);
    const entra = resolveEntraConfiguration(this);

    // --- Secrets ---

    const scopedTokenSigningKeySecret = new secretsmanager.Secret(this, 'ScopedTokenSigningKeySecret', {
      secretName: `platform/${env}/gateway/scoped-token-signing-key`, // pragma: allowlist secret
      description: 'Signing key for scoped act-on-behalf tokens issued by Gateway interceptor',
      generateSecretString: {
        passwordLength: 32,
        excludePunctuation: true,
      },
    });

    // --- DynamoDB Tables (ADR-012) ---

    this.tenantsTable = new dynamodb.Table(this, 'TenantsTable', {
      tableName: 'platform-tenants',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PROVISIONED,
      readCapacity: 5,
      writeCapacity: 5,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.platformConfigKey,
      pointInTimeRecovery: true,
      deletionProtection: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.agentsTable = new dynamodb.Table(this, 'AgentsTable', {
      tableName: 'platform-agents',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PROVISIONED,
      readCapacity: 5,
      writeCapacity: 5,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.platformConfigKey,
      pointInTimeRecovery: true,
      deletionProtection: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.toolsTable = new dynamodb.Table(this, 'ToolsTable', {
      tableName: 'platform-tools',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PROVISIONED,
      readCapacity: 5,
      writeCapacity: 5,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.platformConfigKey,
      pointInTimeRecovery: true,
      deletionProtection: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.opsLocksTable = new dynamodb.Table(this, 'OpsLocksTable', {
      tableName: 'platform-ops-locks',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PROVISIONED,
      readCapacity: 1,
      writeCapacity: 1,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.platformConfigKey,
      timeToLiveAttribute: 'ttl',
      pointInTimeRecovery: true,
      deletionProtection: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.gatewayIdempotencyTable = new dynamodb.Table(this, 'GatewayIdempotencyTable', {
      tableName: 'platform-gateway-idempotency',
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.platformConfigKey,
      timeToLiveAttribute: 'expiration',
      pointInTimeRecovery: true,
      deletionProtection: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.invocationsTable = new dynamodb.Table(this, 'InvocationsTable', {
      tableName: 'platform-invocations',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.tenantDataKey,
      timeToLiveAttribute: 'ttl',
      pointInTimeRecovery: true,
      deletionProtection: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.jobsTable = new dynamodb.Table(this, 'JobsTable', {
      tableName: 'platform-jobs',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.tenantDataKey,
      timeToLiveAttribute: 'ttl',
      stream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
      pointInTimeRecovery: true,
      deletionProtection: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.sessionsTable = new dynamodb.Table(this, 'SessionsTable', {
      tableName: 'platform-sessions',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.tenantDataKey,
      timeToLiveAttribute: 'ttl',
      pointInTimeRecovery: true,
      deletionProtection: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // --- Lambdas ---

    this.tenantApiFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../src/tenant_api'),
      handler: 'handler.lambda_handler',
      functionNameSuffix: 'tenant-api',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'tenant-api',
        TENANTS_TABLE_NAME: this.tenantsTable.tableName,
        EVENT_BUS_NAME: 'default',
        TENANT_API_KEY_SECRET_PREFIX: 'platform/tenants', // pragma: allowlist secret
      },
    });

    this.tenantsTable.grantReadWriteData(this.tenantApiFn);
    this.tenantApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'secretsmanager:CreateSecret',
          'secretsmanager:TagResource',
          'secretsmanager:PutSecretValue',
        ],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:platform/tenants/*`,
        ],
      }),
    );
    this.tenantApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['events:PutEvents'],
        resources: [`arn:aws:events:${this.region}:${this.account}:event-bus/default`],
      }),
    );

    this.tenantApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'lambda:ListVersionsByFunction',
          'lambda:UpdateAlias',
          'lambda:GetAlias',
          'lambda:GetFunctionConfiguration',
        ],
        resources: [
          `arn:aws:lambda:${this.region}:${this.account}:function:platform-*-${env}`,
          `arn:aws:lambda:${this.region}:${this.account}:function:platform-*-${env}:*`,
        ],
      }),
    );

    this.bridgeFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../src/bridge'),
      handler: 'handler.handler',
      functionNameSuffix: 'bridge',
      timeout: cdk.Duration.minutes(15),
      memorySize: 1024,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'bridge',
        AGENTS_TABLE: this.agentsTable.tableName,
        INVOCATIONS_TABLE: this.invocationsTable.tableName,
        JOBS_TABLE: this.jobsTable.tableName,
        TENANTS_TABLE: this.tenantsTable.tableName,
      },
    });

    this.tenantsTable.grantReadData(this.bridgeFn);
    this.agentsTable.grantReadData(this.bridgeFn);
    this.invocationsTable.grantReadWriteData(this.bridgeFn);
    this.jobsTable.grantReadWriteData(this.bridgeFn);

    const webhookDeliveryRetryDlq = new sqs.Queue(this, 'webhookDeliveryRetryDlq', {
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      retentionPeriod: cdk.Duration.days(14),
    });
    this.dlqs['webhook-delivery-retry'] = webhookDeliveryRetryDlq;

    const webhookDeliveryRetryQueue = new sqs.Queue(this, 'webhookDeliveryRetryQueue', {
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      retentionPeriod: cdk.Duration.days(14),
      visibilityTimeout: cdk.Duration.seconds(60),
      deadLetterQueue: {
        maxReceiveCount: 1,
        queue: webhookDeliveryRetryDlq,
      },
    });

    this.webhookDeliveryFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../src/webhook_delivery'),
      handler: 'handler.handler',
      functionNameSuffix: 'webhook-delivery',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'webhook-delivery',
        JOBS_TABLE: this.jobsTable.tableName,
        WEBHOOK_RETRY_QUEUE_URL: webhookDeliveryRetryQueue.queueUrl,
        WEBHOOK_DLQ_URL: webhookDeliveryRetryDlq.queueUrl,
        WEBHOOK_MAX_RETRY_ATTEMPTS: '3',
        WEBHOOK_HTTP_TIMEOUT_SECONDS: '10',
      },
    });

    this.bffFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../src/bff'),
      handler: 'handler.handler',
      functionNameSuffix: 'bff',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'bff',
        ENTRA_TENANT_ID: entra.tenantId,
        ENTRA_AUDIENCE: entra.audience,
        ENTRA_TOKEN_ENDPOINT: entra.tokenEndpoint,
        ENTRA_CLIENT_ID_SECRET_ARN: `arn:aws:secretsmanager:${this.region}:${this.account}:secret:platform/${env}/entra/client-id`,
        ENTRA_CLIENT_SECRET_SECRET_ARN: `arn:aws:secretsmanager:${this.region}:${this.account}:secret:platform/${env}/entra/client-secret`, // pragma: allowlist secret
      },
    });

    const entraClientIdSecret = secretsmanager.Secret.fromSecretNameV2(this, 'EntraClientIdSecret', `platform/${env}/entra/client-id`);
    const entraClientSecretSecret = secretsmanager.Secret.fromSecretNameV2(this, 'EntraClientSecretSecret', `platform/${env}/entra/client-secret`);
    entraClientIdSecret.grantRead(this.bffFn);
    entraClientSecretSecret.grantRead(this.bffFn);

    new lambda.EventSourceMapping(this, 'webhookDeliveryJobsStreamMapping', {
      target: this.webhookDeliveryFn,
      eventSourceArn: this.jobsTable.tableStreamArn,
      startingPosition: lambda.StartingPosition.LATEST,
      batchSize: 10,
      bisectBatchOnError: true,
      retryAttempts: 3,
    });
    new lambda.EventSourceMapping(this, 'webhookDeliveryRetryQueueMapping', {
      target: this.webhookDeliveryFn,
      eventSourceArn: webhookDeliveryRetryQueue.queueArn,
      batchSize: 10,
    });

    this.authoriserFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../src/authoriser'),
      handler: 'handler.handler',
      functionNameSuffix: 'authoriser',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'authoriser',
        ENTRA_JWKS_URL: entra.jwksUrl,
        ENTRA_AUDIENCE: entra.audience,
        ENTRA_ISSUER: entra.issuer,
        TENANTS_TABLE: this.tenantsTable.tableName,
      },
    });

    this.tenantsTable.grantReadData(this.authoriserFn);
    this.jobsTable.grantReadWriteData(this.webhookDeliveryFn);
    this.webhookDeliveryFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'dynamodb:DescribeStream',
          'dynamodb:GetRecords',
          'dynamodb:GetShardIterator',
          'dynamodb:ListStreams',
        ],
        resources: [`${this.jobsTable.tableArn}/stream/*`],
      }),
    );
    webhookDeliveryRetryQueue.grantConsumeMessages(this.webhookDeliveryFn);
    webhookDeliveryRetryQueue.grantSendMessages(this.webhookDeliveryFn);
    webhookDeliveryRetryDlq.grantSendMessages(this.webhookDeliveryFn);

    this.requestInterceptorFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../gateway/interceptors'),
      handler: 'request_interceptor.handler',
      functionNameSuffix: 'interceptor-request',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'gateway-request-interceptor',
        TOOLS_TABLE: this.toolsTable.tableName,
        ENTRA_JWKS_URL: entra.jwksUrl,
        ENTRA_AUDIENCE: entra.audience,
        ENTRA_ISSUER: entra.issuer,
        SCOPED_TOKEN_ISSUER: 'platform-gateway',
        IDEMPOTENCY_TABLE: this.gatewayIdempotencyTable.tableName,
        SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN: scopedTokenSigningKeySecret.secretArn,
        PLATFORM_ENV: env,
      },
    });

    scopedTokenSigningKeySecret.grantRead(this.requestInterceptorFn);

    this.responseInterceptorFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../gateway/interceptors'),
      handler: 'response_interceptor.handler',
      functionNameSuffix: 'interceptor-response',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'gateway-response-interceptor',
      },
    });

    this.billingFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../src/billing'),
      handler: 'handler.lambda_handler',
      functionNameSuffix: 'billing',
      timeout: cdk.Duration.minutes(15),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'billing',
        TENANTS_TABLE_NAME: this.tenantsTable.tableName,
        INVOCATIONS_TABLE_NAME: this.invocationsTable.tableName,
        EVENT_BUS_NAME: 'default',
      },
    });

    this.tenantsTable.grantReadWriteData(this.billingFn);
    this.invocationsTable.grantReadData(this.billingFn);
    this.billingFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter/platform/billing/pricing/*`,
        ],
      }),
    );
    this.billingFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
      }),
    );
    this.billingFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['events:PutEvents'],
        resources: [`arn:aws:events:${this.region}:${this.account}:event-bus/default`],
      }),
    );

    // Daily billing schedule (midnight UTC)
    new events.Rule(this, 'DailyBillingRule', {
      schedule: events.Schedule.cron({ hour: '0', minute: '0' }),
      targets: [new targets.LambdaFunction(this.billingFn)],
    });

    // --- Tenant Provisioner (Issue #291) ---

    // The TenantStack template is synthesized during 'cdk synth' and needs to be
    // available to the provisioner Lambda via S3.
    // NOTE: This assumes 'cdk synth' has run and populated cdk.out.
    const tenantStackTemplatePath = ensureTenantStubTemplate(
      env,
      this.env,
      TENANT_AUTHORIZED_RUNTIME_REGIONS,
    );
    const tenantStackTemplateAsset = new s3assets.Asset(this, 'TenantStackTemplateAsset', {
      path: tenantStackTemplatePath,
    });

    const tenantProvisionerFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../src/tenant_provisioner'),
      handler: 'handler.lambda_handler',
      functionNameSuffix: 'tenant-provisioner',
      timeout: cdk.Duration.minutes(11), // Must be > stack deployment poll (10m)
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'tenant-provisioner',
        PLATFORM_ENV: env,
        TENANTS_TABLE_NAME: this.tenantsTable.tableName,
        TENANT_STACK_TEMPLATE_URL: tenantStackTemplateAsset.bucket.s3UrlForObject(tenantStackTemplateAsset.s3ObjectKey),
      },
    });

    this.tenantsTable.grantReadWriteData(tenantProvisionerFn);
    tenantStackTemplateAsset.grantRead(tenantProvisionerFn);

    tenantProvisionerFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TenantStackCloudFormationAccess',
        actions: [
          'cloudformation:CreateStack',
          'cloudformation:UpdateStack',
          'cloudformation:DescribeStacks',
          'cloudformation:GetTemplate',
        ],
        resources: [`arn:aws:cloudformation:${this.region}:${this.account}:stack/platform-tenant-*/*`],
      }),
    );

    tenantProvisionerFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TenantStackIamAccess',
        actions: [
          'iam:CreateRole',
          'iam:DeleteRole',
          'iam:PutRolePolicy',
          'iam:DeleteRolePolicy',
          'iam:GetRole',
          'iam:PassRole',
          'iam:TagRole',
        ],
        resources: [`arn:aws:iam::${this.account}:role/platform-tenant-*`],
      }),
    );

    tenantProvisionerFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TenantStackSsmAccess',
        actions: [
          'ssm:PutParameter',
          'ssm:GetParameter',
          'ssm:DeleteParameter',
          'ssm:AddTagsToResource',
        ],
        resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/platform/tenants/*`],
      }),
    );

    tenantProvisionerFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TenantStackBedrockAccess',
        actions: [
          'bedrock-agentcore:CreateMemory',
          'bedrock-agentcore:UpdateMemory',
          'bedrock-agentcore:DeleteMemory',
          'bedrock-agentcore:GetMemory',
          'bedrock-agentcore:TagResource',
        ],
        resources: ['*'], // Resource-level permissions not fully supported for all AgentCore resources yet
      }),
    );

    new events.Rule(this, 'TenantCreatedRule', {
      ruleName: `platform-tenant-created-${env}`,
      description: 'Trigger tenant provisioning when a new tenant is created',
      eventPattern: {
        source: ['platform.tenant_api'],
        detailType: ['tenant.created'],
      },
      targets: [new targets.LambdaFunction(tenantProvisionerFn)],
    });

    this.toolsTable.grantReadData(this.requestInterceptorFn);
    this.gatewayIdempotencyTable.grantReadWriteData(this.requestInterceptorFn);

    const bridgeAlias = new lambda.Alias(this, 'BridgeLiveAlias', {
      aliasName: 'live',
      version: this.bridgeFn.currentVersion,
    });

    const bridgeErrorRateHighAlarm = new cloudwatch.Alarm(this, 'BridgeErrorRateHighAlarm', {
      alarmName: `${this.stackName}-error_rate_high`,
      alarmDescription: 'Bridge live alias error rate exceeded threshold during canary deployment',
      metric: new cloudwatch.MathExpression({
        expression: 'IF(invocations > 0, (errors / invocations) * 100, 0)',
        period: cdk.Duration.minutes(1),
        usingMetrics: {
          errors: bridgeAlias.metricErrors({
            statistic: 'Sum',
            period: cdk.Duration.minutes(1),
          }),
          invocations: bridgeAlias.metricInvocations({
            statistic: 'Sum',
            period: cdk.Duration.minutes(1),
          }),
        },
        label: 'BridgeAliasErrorRatePercent',
      }),
      threshold: 5,
      evaluationPeriods: 1,
      datapointsToAlarm: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new codedeploy.LambdaDeploymentGroup(this, 'BridgeCanaryDeploymentGroup', {
      alias: bridgeAlias,
      deploymentConfig: bridgeCanaryPolicy.deploymentConfig,
      alarms: [bridgeErrorRateHighAlarm],
      autoRollback: {
        deploymentInAlarm: true,
        failedDeployment: true,
        stoppedDeployment: true,
      },
    });

    new cdk.CfnOutput(this, 'BridgeCanaryPolicy', {
      description: 'Environment-specific bridge rollout policy',
      value: bridgeCanaryPolicy.summary,
    });

    const authoriserAlias = new lambda.Alias(this, 'AuthoriserLiveAlias', {
      aliasName: 'live',
      version: this.authoriserFn.currentVersion,
      provisionedConcurrentExecutions: 10,
    });

    const restAuthorizer = new apigateway.TokenAuthorizer(this, 'RestTokenAuthorizer', {
      handler: authoriserAlias,
      identitySource: apigateway.IdentitySource.header('Authorization'),
      resultsCacheTtl: cdk.Duration.minutes(5),
    });

    const spaBucket = new s3.Bucket(this, 'SpaBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
    });

    const resultsBucket = new s3.Bucket(this, 'ResultsBucket', {
      bucketName: `platform-results-${env}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const spaLogBucket = new s3.Bucket(this, 'SpaLogBucket', {
      bucketName: `platform-spa-logs-${env}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      accessControl: s3.BucketAccessControl.LOG_DELIVERY_WRITE,
    });

    new ssm.StringParameter(this, 'ResultsBucketArnParam', {
      parameterName: `/platform/core/${env}/results-bucket-arn`,
      stringValue: resultsBucket.bucketArn,
      description: 'ARN for the platform results S3 bucket',
    });

    const spaResponseHeadersPolicy = new cloudfront.CfnResponseHeadersPolicy(
      this,
      'SpaCspResponseHeadersPolicy',
      {
        responseHeadersPolicyConfig: {
          name: `${this.stackName}-spa-security-headers`,
          comment: 'Security headers for platform SPA',
          securityHeadersConfig: {
            contentSecurityPolicy: {
              contentSecurityPolicy:
                "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; connect-src 'self' https:; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self';",
              override: true,
            },
            frameOptions: {
              frameOption: 'DENY',
              override: true,
            },
            strictTransportSecurity: {
              accessControlMaxAgeSec: 31536000,
              includeSubdomains: true,
              preload: true,
              override: true,
            },
            contentTypeOptions: {
              override: true,
            },
            referrerPolicy: {
              referrerPolicy: 'same-origin',
              override: true,
            },
            xssProtection: {
              protection: true,
              modeBlock: true,
              override: true,
            },
          },
        },
      },
    );

    const spaOriginAccessControl = new cloudfront.CfnOriginAccessControl(this, 'SpaOriginAccessControl', {
      originAccessControlConfig: {
        name: `${this.stackName}-spa-oac`,
        description: 'OAC for SPA bucket origin',
        originAccessControlOriginType: 's3',
        signingBehavior: 'always',
        signingProtocol: 'sigv4',
      },
    });

    this.spaDistribution = new cloudfront.CfnDistribution(this, 'SpaDistribution', {
      distributionConfig: {
        enabled: true,
        comment: 'Platform SPA distribution',
        defaultRootObject: 'index.html',
        httpVersion: 'http2',
        priceClass: 'PriceClass_100',
        ipv6Enabled: true,
        logging: {
          bucket: spaLogBucket.bucketRegionalDomainName,
          includeCookies: false,
          prefix: 'spa-cloudfront/',
        },
        customErrorResponses: [
          {
            errorCode: 403,
            responsePagePath: '/index.html',
            responseCode: 200,
            errorCachingMinTtl: 0,
          },
          {
            errorCode: 404,
            responsePagePath: '/index.html',
            responseCode: 200,
            errorCachingMinTtl: 0,
          },
        ],
        origins: [
          {
            id: 'SpaS3Origin',
            domainName: spaBucket.bucketRegionalDomainName,
            originAccessControlId: spaOriginAccessControl.attrId,
            s3OriginConfig: {
              originAccessIdentity: '',
            },
          },
        ],
        defaultCacheBehavior: {
          targetOriginId: 'SpaS3Origin',
          viewerProtocolPolicy: 'redirect-to-https',
          compress: true,
          allowedMethods: ['GET', 'HEAD', 'OPTIONS'],
          cachedMethods: ['GET', 'HEAD', 'OPTIONS'],
          cachePolicyId: cloudfront.CachePolicy.CACHING_OPTIMIZED.cachePolicyId,
          responseHeadersPolicyId: spaResponseHeadersPolicy.attrId,
        },
        restrictions: {
          geoRestriction: {
            restrictionType: 'none',
          },
        },
        viewerCertificate: {
          cloudFrontDefaultCertificate: true,
        },
        // No WebACLId is wired here by design. A CloudFront-scope WAF must be managed
        // via a dedicated global/us-east-1 path, which this repository has not yet
        // approved under the current ADR-009 region topology.
      },
    });

    spaBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowCloudFrontOacRead',
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
        actions: ['s3:GetObject'],
        resources: [spaBucket.arnForObjects('*')],
        conditions: {
          StringEquals: {
            'AWS:SourceArn': cdk.Fn.join('', [
              'arn:',
              cdk.Aws.PARTITION,
              ':cloudfront::',
              cdk.Aws.ACCOUNT_ID,
              ':distribution/',
              this.spaDistribution.ref,
            ]),
          },
        },
      }),
    );

    new ssm.StringParameter(this, 'SpaBucketNameParam', {
      parameterName: `/platform/spa/${env}/bucket-name`,
      stringValue: spaBucket.bucketName,
      description: 'S3 bucket name for the platform SPA',
    });

    new ssm.StringParameter(this, 'SpaDistributionIdParam', {
      parameterName: `/platform/spa/${env}/distribution-id`,
      stringValue: this.spaDistribution.ref,
      description: 'CloudFront distribution ID for the platform SPA',
    });

    new cdk.CfnOutput(this, 'SpaBucketName', {
      value: spaBucket.bucketName,
      description: 'S3 bucket name for the platform SPA',
    });

    new cdk.CfnOutput(this, 'SpaDistributionId', {
      value: this.spaDistribution.ref,
      description: 'CloudFront distribution ID for the platform SPA',
    });

    const spaAllowedOrigin = cdk.Fn.join('', ['https://', this.spaDistribution.attrDomainName]);

    // API Access Log Group (TASK-165)
    const apiAccessLogGroup = new logs.LogGroup(this, 'ApiAccessLogGroup', {
      logGroupName: `/aws/apigateway/${this.stackName}-rest-api-access-logs`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    apiAccessLogGroup.addMetricFilter('TenantRequestCountFilter', {
      metricName: 'RequestCount',
      metricNamespace: 'Platform/API',
      metricValue: '1',
      filterPattern: logs.FilterPattern.exists('$.tenantId'),
      dimensions: {
        TenantId: '$.tenantId',
      },
    });

    apiAccessLogGroup.addMetricFilter('TenantErrorCountFilter', {
      metricName: 'ErrorCount',
      metricNamespace: 'Platform/API',
      metricValue: '1',
      filterPattern: logs.FilterPattern.numberValue('$.status', '>=', 400),
      dimensions: {
        TenantId: '$.tenantId',
      },
    });

    apiAccessLogGroup.addMetricFilter('TenantLatencyFilter', {
      metricName: 'Latency',
      metricNamespace: 'Platform/API',
      metricValue: '$.latency',
      filterPattern: logs.FilterPattern.exists('$.latency'),
      dimensions: {
        TenantId: '$.tenantId',
      },
    });

    this.api = new apigateway.RestApi(this, 'PlatformRestApi', {
      restApiName: `${this.stackName}-rest-api`,
      description: 'Platform northbound REST API (ADR-003)',
      apiKeySourceType: apigateway.ApiKeySourceType.AUTHORIZER,
      cloudWatchRole: true,
      endpointConfiguration: {
        types: [apigateway.EndpointType.REGIONAL],
      },
      defaultCorsPreflightOptions: {
        allowOrigins: [spaAllowedOrigin],
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: [
          'Authorization',
          'Content-Type',
          'X-Api-Key',
          'X-Amz-Date',
          'X-Amz-Security-Token',
          'X-Amz-User-Agent',
        ],
      },
      deployOptions: {
        stageName: 'prod',
        tracingEnabled: true,
        metricsEnabled: true,
        throttlingRateLimit: 100,
        throttlingBurstLimit: 200,
        accessLogDestination: new apigateway.LogGroupLogDestination(apiAccessLogGroup),
        accessLogFormat: apigateway.AccessLogFormat.custom(JSON.stringify({
          requestId: '$context.requestId',
          extendedRequestId: '$context.extendedRequestId',
          ip: '$context.identity.sourceIp',
          caller: '$context.identity.caller',
          user: '$context.identity.user',
          requestTime: '$context.requestTime',
          httpMethod: '$context.httpMethod',
          resourcePath: '$context.resourcePath',
          status: 0, // Placeholder for numeric type in JSON.stringify
          protocol: '$context.protocol',
          responseLength: 0, // Placeholder
          tenantId: '$context.authorizer.tenantid',
          appId: '$context.authorizer.appid',
          sub: '$context.authorizer.sub',
          tier: '$context.authorizer.tier',
          latency: 0, // Placeholder
        }).replace(':0', ':$context.status').replace(':0', ':$context.responseLength').replace(':0', ':$context.responseLatency')),
        dataTraceEnabled: false,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        methodOptions: {
          '/v1/agents/{agentName}/invoke/POST': {
            throttlingRateLimit: 50,
            throttlingBurstLimit: 100,
            metricsEnabled: true,
          },
          '/v1/jobs/{jobId}/GET': {
            throttlingRateLimit: 100,
            throttlingBurstLimit: 200,
            metricsEnabled: true,
          },
          '/v1/bff/token-refresh/POST': {
            throttlingRateLimit: 30,
            throttlingBurstLimit: 60,
            metricsEnabled: true,
          },
          '/v1/bff/session-keepalive/POST': {
            throttlingRateLimit: 120,
            throttlingBurstLimit: 240,
            metricsEnabled: true,
          },
        },
      },
    });

    // Add Gateway Responses for CORS and custom errors (TASK-165)
    // We use quotes for the header values as they are passed to CloudFormation
    this.api.addGatewayResponse('Default4xxResponse', {
      type: apigateway.ResponseType.DEFAULT_4XX,
      responseHeaders: {
        'gatewayresponses.header.Access-Control-Allow-Origin': `'${spaAllowedOrigin}'`,
        'gatewayresponses.header.Access-Control-Allow-Credentials': "'true'",
        'gatewayresponses.header.Access-Control-Allow-Headers': "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'",
        'gatewayresponses.header.Access-Control-Allow-Methods': "'GET,POST,OPTIONS'",
      },
      templates: {
        'application/json': '{"message":$context.error.messageString,"requestId":"$context.requestId"}',
      },
    });

    this.api.addGatewayResponse('Default5xxResponse', {
      type: apigateway.ResponseType.DEFAULT_5XX,
      responseHeaders: {
        'gatewayresponses.header.Access-Control-Allow-Origin': `'${spaAllowedOrigin}'`,
        'gatewayresponses.header.Access-Control-Allow-Credentials': "'true'",
        'gatewayresponses.header.Access-Control-Allow-Headers': "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'",
        'gatewayresponses.header.Access-Control-Allow-Methods': "'GET,POST,OPTIONS'",
      },
      templates: {
        'application/json': '{"message":"Internal server error","requestId":"$context.requestId"}',
      },
    });

    this.api.addGatewayResponse('UnauthorizedResponse', {
      type: apigateway.ResponseType.UNAUTHORIZED,
      responseHeaders: {
        'gatewayresponses.header.Access-Control-Allow-Origin': `'${spaAllowedOrigin}'`,
        'gatewayresponses.header.Access-Control-Allow-Credentials': "'true'",
      },
      templates: {
        'application/json': '{"message":"Unauthorized","requestId":"$context.requestId"}',
      },
    });

    this.api.addGatewayResponse('AccessDeniedResponse', {
      type: apigateway.ResponseType.ACCESS_DENIED,
      responseHeaders: {
        'gatewayresponses.header.Access-Control-Allow-Origin': `'${spaAllowedOrigin}'`,
        'gatewayresponses.header.Access-Control-Allow-Credentials': "'true'",
      },
      templates: {
        'application/json': '{"message":"Access denied","requestId":"$context.requestId"}',
      },
    });

    const v1 = this.api.root.addResource('v1');
    const health = v1.addResource('health');
    const sessions = v1.addResource('sessions');
    const agents = v1.addResource('agents');
    const agentByName = agents.addResource('{agentName}');
    const agentInvoke = agentByName.addResource('invoke');
    const jobs = v1.addResource('jobs');
    const jobById = jobs.addResource('{jobId}');
    const webhooks = v1.addResource('webhooks');
    const webhookById = webhooks.addResource('{webhookId}');
    const bff = v1.addResource('bff');
    const tokenRefresh = bff.addResource('token-refresh');
    const sessionKeepalive = bff.addResource('session-keepalive');

    const tenants = v1.addResource('tenants');
    const tenantById = tenants.addResource('{tenantId}');
    const auditExport = tenantById.addResource('audit-export');
    const tenantApiKey = tenantById.addResource('api-key');
    const tenantApiKeyRotate = tenantApiKey.addResource('rotate');
    const tenantUsers = tenantById.addResource('users');
    const tenantUsersInvite = tenantUsers.addResource('invite');
    const platform = v1.addResource('platform');
    const failover = platform.addResource('failover');
    const quota = platform.addResource('quota');
    const splitAccounts = quota.addResource('split-accounts');
    const serviceHealth = platform.addResource('service-health');
    const billing = platform.addResource('billing');
    const billingStatus = billing.addResource('status');
    const ops = platform.addResource('ops');

    // Ops sub-resources
    const opsTopTenants = ops.addResource('top-tenants');
    const opsSecurityEvents = ops.addResource('security-events');
    const opsErrorRate = ops.addResource('error-rate');
    const opsSecurity = ops.addResource('security');
    const opsSecurityPage = opsSecurity.addResource('page');

    const opsDlq = ops.addResource('dlq');
    const opsDlqByName = opsDlq.addResource('{proxy+}');

    const opsTenants = ops.addResource('tenants');
    const opsTenantById = opsTenants.addResource('{proxy+}');

    const opsJobs = ops.addResource('jobs');
    const opsJobById = opsJobs.addResource('{proxy+}');

    const securedMethodOptions: apigateway.MethodOptions = {
      authorizer: restAuthorizer,
      authorizationType: apigateway.AuthorizationType.CUSTOM,
      apiKeyRequired: true,
    };

    const tenantApiIntegration = new apigateway.LambdaIntegration(this.tenantApiFn, { proxy: true });
    const bridgeIntegration = new apigateway.LambdaIntegration(bridgeAlias, { proxy: true });
    const bridgeStreamingIntegration = new apigateway.LambdaIntegration(bridgeAlias, {
      proxy: true,
      responseTransferMode: apigateway.ResponseTransferMode.STREAM,
    });

    health.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    sessions.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    tenants.addMethod('POST', tenantApiIntegration, securedMethodOptions);
    tenants.addMethod('GET', tenantApiIntegration, securedMethodOptions);

    tenantById.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    tenantById.addMethod('PATCH', tenantApiIntegration, securedMethodOptions);
    tenantById.addMethod('DELETE', tenantApiIntegration, securedMethodOptions);

    auditExport.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    tenantApiKeyRotate.addMethod('POST', tenantApiIntegration, securedMethodOptions);
    tenantUsersInvite.addMethod('POST', tenantApiIntegration, securedMethodOptions);

    failover.addMethod('POST', tenantApiIntegration, securedMethodOptions);
    quota.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    splitAccounts.addMethod('POST', tenantApiIntegration, securedMethodOptions);
    serviceHealth.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    billingStatus.addMethod('GET', tenantApiIntegration, securedMethodOptions);

    // Wire all ops routes
    opsTopTenants.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    opsSecurityEvents.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    opsErrorRate.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    opsSecurityPage.addMethod('POST', tenantApiIntegration, securedMethodOptions);
    opsDlqByName.addMethod('ANY', tenantApiIntegration, securedMethodOptions);
    opsTenantById.addMethod('ANY', tenantApiIntegration, securedMethodOptions);
    opsJobById.addMethod('ANY', tenantApiIntegration, securedMethodOptions);

    agents.addMethod('GET', bridgeIntegration, securedMethodOptions);
    agentByName.addMethod('GET', bridgeIntegration, securedMethodOptions);
    agentInvoke.addMethod('POST', bridgeStreamingIntegration, securedMethodOptions);
    jobById.addMethod(
      'GET',
      bridgeIntegration,
      securedMethodOptions,
    );
    webhooks.addMethod(
      'GET',
      tenantApiIntegration,
      securedMethodOptions,
    );
    webhooks.addMethod(
      'POST',
      tenantApiIntegration,
      securedMethodOptions,
    );
    webhookById.addMethod(
      'DELETE',
      tenantApiIntegration,
      securedMethodOptions,
    );
    tokenRefresh.addMethod(
      'POST',
      new apigateway.LambdaIntegration(this.bffFn, { proxy: true }),
      securedMethodOptions,
    );
    sessionKeepalive.addMethod(
      'POST',
      new apigateway.LambdaIntegration(this.bffFn, { proxy: true }),
      securedMethodOptions,
    );

    const usagePlanDefinitions = [
      {
        id: 'BasicUsagePlan',
        name: 'basic',
        rateLimit: 10,
        burstLimit: 100,
        quotaLimit: 1000,
      },
      {
        id: 'StandardUsagePlan',
        name: 'standard',
        rateLimit: 50,
        burstLimit: 500,
        quotaLimit: 10_000,
      },
      {
        id: 'PremiumUsagePlan',
        name: 'premium',
        rateLimit: 500,
        burstLimit: 2_000,
      },
    ];

    new ssm.StringParameter(this, 'RestApiIdParam', {
      parameterName: `/platform/core/${env}/rest-api-id`,
      stringValue: this.api.restApiId,
      description: 'REST API ID for the platform northbound API',
    });

    for (const plan of usagePlanDefinitions) {
      const usagePlan = new apigateway.UsagePlan(this, plan.id, {
        name: `${this.stackName}-${plan.name}`,
        throttle: {
          rateLimit: plan.rateLimit,
          burstLimit: plan.burstLimit,
        },
        quota:
          plan.quotaLimit === undefined
            ? undefined
            : {
                limit: plan.quotaLimit,
                period: apigateway.Period.DAY,
              },
        apiStages: [
          {
            api: this.api,
            stage: this.api.deploymentStage,
          },
        ],
      });

      new ssm.StringParameter(this, `${plan.id}IdParam`, {
        parameterName: `/platform/core/${env}/usage-plan-${plan.name}-id`,
        stringValue: usagePlan.usagePlanId,
        description: `Usage plan ID for ${plan.name} tier`,
      });
    }

    new ssm.StringParameter(this, 'BridgeLambdaRoleArnParam', {
      parameterName: `/platform/core/${env}/bridge-lambda-role-arn`,
      stringValue: this.bridgeFn.role!.roleArn,
      description: 'IAM role ARN for the Bridge Lambda function',
    });

    this.apiWebAcl = new wafv2.CfnWebACL(this, 'ApiWebAcl', {
      name: `${this.stackName}-api-waf`,
      defaultAction: { allow: {} },
      scope: 'REGIONAL',
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: `${this.stackName}-api-waf`,
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          name: 'AWSManagedRulesCommonRuleSet',
          priority: 0,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesCommonRuleSet',
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'aws-managed-common',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'UkIpRateLimit',
          priority: 1,
          action: { block: {} },
          statement: {
            rateBasedStatement: {
              aggregateKeyType: 'IP',
              limit: 2000,
              scopeDownStatement: {
                geoMatchStatement: {
                  countryCodes: ['GB'],
                },
              },
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'uk-ip-rate-limit',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'BlockSqlmapUserAgent',
          priority: 2,
          action: { block: {} },
          statement: {
            byteMatchStatement: {
              fieldToMatch: {
                singleHeader: {
                  Name: 'user-agent',
                },
              },
              positionalConstraint: 'CONTAINS',
              searchString: 'sqlmap',
              textTransformations: [
                {
                  priority: 0,
                  type: 'LOWERCASE',
                },
              ],
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'block-sqlmap-user-agent',
            sampledRequestsEnabled: true,
          },
        },
      ],
    });

    new wafv2.CfnWebACLAssociation(this, 'ApiWebAclAssociation', {
      resourceArn: this.api.deploymentStage.stageArn,
      webAclArn: this.apiWebAcl.attrArn,
    });

    const agentCoreGatewayRole = new iam.Role(this, 'AgentCoreGatewayExecutionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'Execution role for AgentCore Gateway interceptors',
    });

    const gatewayPolicyEngine = new cdk.CfnResource(this, 'AgentCoreGatewayPolicyEngine', {
      type: 'AWS::BedrockAgentCore::PolicyEngine',
      properties: {
        Name: gatewayPolicyConfiguration.policyEngineName,
        Description: 'Cedar policy engine for AgentCore Gateway tool authorization',
        Tags: [
          {
            Key: 'stack',
            Value: this.stackName,
          },
          {
            Key: 'component',
            Value: 'platform-gateway-policy',
          },
        ],
      },
    });

    const gatewayDefaultPolicy = new cdk.CfnResource(this, 'AgentCoreGatewayDefaultPolicy', {
      type: 'AWS::BedrockAgentCore::Policy',
      properties: {
        Name: gatewayPolicyConfiguration.policyName,
        Description: 'Baseline Cedar policy for AgentCore Gateway',
        PolicyEngineId: gatewayPolicyEngine.getAtt('PolicyEngineId').toString(),
        ValidationMode: 'FAIL_ON_ANY_FINDINGS',
        Definition: {
          Cedar: {
            Statement: [
              'permit (',
              '  principal,',
              '  action,',
              '  resource',
              ') when {',
              '  true',
              '};',
            ].join('\n'),
          },
        },
      },
    });
    gatewayDefaultPolicy.addDependency(gatewayPolicyEngine);

    agentCoreGatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [this.requestInterceptorFn.functionArn, this.responseInterceptorFn.functionArn],
      }),
    );
    agentCoreGatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock-agentcore:AuthorizeAction',
          'bedrock-agentcore:PartiallyAuthorizeActions',
          'bedrock-agentcore:GetPolicyEngine',
        ],
        resources: [gatewayPolicyEngine.ref],
      }),
    );

    const agentCoreGateway = new cdk.CfnResource(this, 'AgentCoreGateway', {
      type: 'AWS::BedrockAgentCore::Gateway',
      properties: {
        Name: `${this.stackName.toLowerCase().replace(/[^a-z0-9-]/g, '-')}-gateway`,
        Description: 'Platform AgentCore Gateway with request/response interceptors',
        AuthorizerType: 'AWS_IAM',
        ProtocolType: 'MCP',
        RoleArn: agentCoreGatewayRole.roleArn,
        PolicyEngineConfiguration: {
          Arn: gatewayPolicyEngine.ref,
          Mode: gatewayPolicyConfiguration.enforcementMode,
        },
        InterceptorConfigurations: [
          {
            InterceptionPoints: ['REQUEST'],
            InputConfiguration: {
              PassRequestHeaders: true,
            },
            Interceptor: {
              Lambda: {
                Arn: this.requestInterceptorFn.functionArn,
              },
            },
          },
          {
            InterceptionPoints: ['RESPONSE'],
            InputConfiguration: {
              PassRequestHeaders: true,
            },
            Interceptor: {
              Lambda: {
                Arn: this.responseInterceptorFn.functionArn,
              },
            },
          },
        ],
        Tags: {
          stack: this.stackName,
          component: 'platform-gateway',
        },
      },
    });
    agentCoreGateway.addDependency(gatewayDefaultPolicy);

    new cdk.CfnOutput(this, 'AgentCoreGatewayPolicyMode', {
      value: gatewayPolicyConfiguration.enforcementMode,
      description: 'Policy enforcement mode for the AgentCore Gateway',
    });
  }

  private createPythonLambda(props: PythonLambdaProps): lambda.Function {
    const dlq = new sqs.Queue(this, `${props.functionNameSuffix}Dlq`, {
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      retentionPeriod: cdk.Duration.days(14),
    });
    this.dlqs[props.functionNameSuffix] = dlq;

    return new lambda.Function(this, `${props.functionNameSuffix}Lambda`, {
      functionName: `${this.stackName}-${props.functionNameSuffix}`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: props.handler,
      code: lambda.Code.fromAsset(props.assetPath),
      tracing: lambda.Tracing.ACTIVE,
      deadLetterQueueEnabled: true,
      deadLetterQueue: dlq,
      timeout: props.timeout,
      memorySize: props.memorySize,
      vpc: this.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      environment: {
        LOG_LEVEL: 'INFO',
        ...props.environment,
      },
    });
  }

  private resolveBridgeCanaryPolicy(env: string): BridgeCanaryPolicy {
    switch (env) {
      case 'dev':
        return {
          deploymentConfig: codedeploy.LambdaDeploymentConfig.ALL_AT_ONCE,
          summary: 'dev=all-at-once',
        };
      case 'staging':
        return {
          deploymentConfig: codedeploy.LambdaDeploymentConfig.CANARY_10PERCENT_30MINUTES,
          summary: 'staging=canary-10%-30m',
        };
      case 'prod':
        return {
          deploymentConfig: codedeploy.LambdaDeploymentConfig.CANARY_10PERCENT_15MINUTES,
          summary: 'prod=canary-10%-15m',
        };
      default:
        throw new Error(`Unsupported env context for canary policy: ${env}`);
    }
  }

  private resolveGatewayPolicyConfiguration(env: string): GatewayPolicyConfiguration {
    switch (env) {
      case 'dev':
        return {
          enforcementMode: 'LOG_ONLY',
          policyEngineName: 'PlatformGatewayPolicyEngineDev',
          policyName: 'PlatformGatewayAllowAllDev',
        };
      case 'staging':
        return {
          enforcementMode: 'LOG_ONLY',
          policyEngineName: 'PlatformGatewayPolicyEngineStaging',
          policyName: 'PlatformGatewayAllowAllStaging',
        };
      case 'prod':
        return {
          enforcementMode: 'ENFORCE',
          policyEngineName: 'PlatformGatewayPolicyEngineProd',
          policyName: 'PlatformGatewayAllowAllProd',
        };
      default:
        throw new Error(`Unsupported env context for gateway policy mode: ${env}`);
    }
  }
}
