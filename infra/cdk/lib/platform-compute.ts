import * as cdk from 'aws-cdk-lib';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as path from 'path';
import { Construct } from 'constructs';
import { EntraConfiguration } from './entra-config';
import { PlatformStorageResources } from './platform-storage';

export type PythonLambdaFactoryProps = {
  assetPath: string;
  handler: string;
  functionNameSuffix: string;
  timeout: cdk.Duration;
  memorySize: number;
  environment?: Record<string, string>;
};

export interface PlatformComputeProps {
  readonly envName: string;
  readonly storage: PlatformStorageResources;
  readonly entra: EntraConfiguration;
  readonly scopedTokenSigningKeySecret: secretsmanager.ISecret;
  readonly tenantStackTemplateAsset: s3assets.Asset;
  readonly createPythonLambda: (props: PythonLambdaFactoryProps) => lambda.Function;
}

export interface PlatformComputeResources {
  readonly tenantMgmtFn: lambda.Function;
  readonly webhookRegistryFn: lambda.Function;
  readonly agentRegistryFn: lambda.Function;
  readonly adminOpsFn: lambda.Function;
  readonly bridgeFn: lambda.Function;
  readonly webhookDeliveryFn: lambda.Function;
  readonly bffFn: lambda.Function;
  readonly authoriserFn: lambda.Function;
  readonly requestInterceptorFn: lambda.Function;
  readonly responseInterceptorFn: lambda.Function;
  readonly billingFn: lambda.Function;
  readonly dlqs: Record<string, sqs.IQueue>;
}

export function createPlatformCompute(
  scope: Construct,
  props: PlatformComputeProps,
): PlatformComputeResources {
  const {
    envName,
    storage,
    entra,
    scopedTokenSigningKeySecret,
    tenantStackTemplateAsset,
    createPythonLambda,
  } = props;

  const dlqs: Record<string, sqs.IQueue> = {};
  const stack = cdk.Stack.of(scope);

  const tenantMgmtFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../src/tenant_api'),
    handler: 'tenant_mgmt_handler.lambda_handler',
    functionNameSuffix: 'tenant-mgmt',
    timeout: cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'tenant-mgmt-service',
      TENANTS_TABLE_NAME: storage.tenantsTable.tableName,
      INVOCATIONS_TABLE_NAME: storage.invocationsTable.tableName,
      EVENT_BUS_NAME: 'default',
      TENANT_API_KEY_SECRET_PREFIX: 'platform/tenants', // pragma: allowlist secret
    },
  });
  storage.tenantsTable.grantReadWriteData(tenantMgmtFn);
  storage.invocationsTable.grantReadData(tenantMgmtFn);
  tenantMgmtFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['secretsmanager:CreateSecret', 'secretsmanager:TagResource', 'secretsmanager:PutSecretValue'],
      resources: [`arn:aws:secretsmanager:${stack.region}:${stack.account}:secret:platform/tenants/*`],
    }),
  );
  tenantMgmtFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['events:PutEvents'],
      resources: [`arn:aws:events:${stack.region}:${stack.account}:event-bus/default`],
    }),
  );
  tenantMgmtFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [`arn:aws:ssm:${stack.region}:${stack.account}:parameter/platform/config/runtime-region`],
    }),
  );

  const webhookRegistryFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../src/tenant_api'),
    handler: 'webhook_registry_handler.lambda_handler',
    functionNameSuffix: 'webhook-registry',
    timeout: cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'webhook-registry-service',
      TENANTS_TABLE_NAME: storage.tenantsTable.tableName,
      EVENT_BUS_NAME: 'default',
    },
  });
  storage.tenantsTable.grantReadWriteData(webhookRegistryFn);
  webhookRegistryFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['events:PutEvents'],
      resources: [`arn:aws:events:${stack.region}:${stack.account}:event-bus/default`],
    }),
  );

  const agentRegistryFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../src/tenant_api'),
    handler: 'agent_registry_handler.lambda_handler',
    functionNameSuffix: 'agent-registry',
    timeout: cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'agent-registry-service',
      AGENTS_TABLE_NAME: storage.agentsTable.tableName,
      EVENT_BUS_NAME: 'default',
    },
  });
  storage.agentsTable.grantReadWriteData(agentRegistryFn);
  agentRegistryFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['events:PutEvents'],
      resources: [`arn:aws:events:${stack.region}:${stack.account}:event-bus/default`],
    }),
  );
  agentRegistryFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['lambda:ListVersionsByFunction', 'lambda:UpdateAlias', 'lambda:GetAlias', 'lambda:GetFunctionConfiguration'],
      resources: [
        `arn:aws:lambda:${stack.region}:${stack.account}:function:platform-*-${envName}`,
        `arn:aws:lambda:${stack.region}:${stack.account}:function:platform-*-${envName}:*`,
      ],
    }),
  );

  const adminOpsFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../src/tenant_api'),
    handler: 'admin_ops_handler.lambda_handler',
    functionNameSuffix: 'admin-ops',
    timeout: cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'admin-ops-service',
      TENANTS_TABLE_NAME: storage.tenantsTable.tableName,
      OPS_LOCKS_TABLE: storage.opsLocksTable.tableName,
      RUNTIME_REGION_PARAM: '/platform/config/runtime-region',
      FALLBACK_REGION_PARAM: '/platform/config/fallback-region',
    },
  });
  storage.tenantsTable.grantReadWriteData(adminOpsFn);
  storage.opsLocksTable.grantReadData(adminOpsFn);
  adminOpsFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:PutParameter'],
      resources: [
        `arn:aws:ssm:${stack.region}:${stack.account}:parameter/platform/config/runtime-region`,
        `arn:aws:ssm:${stack.region}:${stack.account}:parameter/platform/config/fallback-region`,
      ],
    }),
  );
  adminOpsFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['servicequotas:ListServiceQuotas', 'cloudwatch:GetMetricStatistics'],
      resources: ['*'],
    }),
  );
  adminOpsFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['lambda:ListVersionsByFunction', 'lambda:UpdateAlias', 'lambda:GetAlias', 'lambda:GetFunctionConfiguration'],
      resources: [
        `arn:aws:lambda:${stack.region}:${stack.account}:function:platform-*-${envName}`,
        `arn:aws:lambda:${stack.region}:${stack.account}:function:platform-*-${envName}:*`,
      ],
    }),
  );

  const bridgeFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../src/bridge'),
    handler: 'handler.handler',
    functionNameSuffix: 'bridge',
    timeout: cdk.Duration.minutes(15),
    memorySize: 1024,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'bridge',
      AGENTS_TABLE: storage.agentsTable.tableName,
      INVOCATIONS_TABLE: storage.invocationsTable.tableName,
      JOBS_TABLE: storage.jobsTable.tableName,
      TENANTS_TABLE: storage.tenantsTable.tableName,
      APPCONFIG_APPLICATION_ID: storage.appconfigApp.ref,
      APPCONFIG_ENVIRONMENT_ID: storage.appconfigEnv.ref,
      APPCONFIG_PROFILE_ID: storage.capabilityProfile.ref,
    },
  });
  storage.tenantsTable.grantReadData(bridgeFn);
  storage.agentsTable.grantReadData(bridgeFn);
  storage.invocationsTable.grantReadWriteData(bridgeFn);
  storage.jobsTable.grantReadWriteData(bridgeFn);
  bridgeFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['appconfig:GetLatestConfiguration', 'appconfig:StartConfigurationSession'],
      resources: [
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}`,
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}/environment/${storage.appconfigEnv.ref}`,
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}/configurationprofile/${storage.capabilityProfile.ref}`,
      ],
    }),
  );

  const webhookDeliveryRetryDlq = new sqs.Queue(scope, 'webhookDeliveryRetryDlq', {
    encryption: sqs.QueueEncryption.SQS_MANAGED,
    retentionPeriod: cdk.Duration.days(14),
  });
  dlqs['webhook-delivery-retry'] = webhookDeliveryRetryDlq;

  const webhookDeliveryRetryQueue = new sqs.Queue(scope, 'webhookDeliveryRetryQueue', {
    encryption: sqs.QueueEncryption.SQS_MANAGED,
    retentionPeriod: cdk.Duration.days(14),
    visibilityTimeout: cdk.Duration.seconds(60),
    deadLetterQueue: {
      maxReceiveCount: 1,
      queue: webhookDeliveryRetryDlq,
    },
  });

  const webhookDeliveryFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../src/webhook_delivery'),
    handler: 'handler.handler',
    functionNameSuffix: 'webhook-delivery',
    timeout: cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'webhook-delivery',
      JOBS_TABLE: storage.jobsTable.tableName,
      TENANTS_TABLE: storage.tenantsTable.tableName,
      WEBHOOK_RETRY_QUEUE_URL: webhookDeliveryRetryQueue.queueUrl,
      WEBHOOK_DLQ_URL: webhookDeliveryRetryDlq.queueUrl,
      WEBHOOK_MAX_RETRY_ATTEMPTS: '3',
      WEBHOOK_HTTP_TIMEOUT_SECONDS: '10',
    },
  });

  const bffFn = createPythonLambda({
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
      ENTRA_CLIENT_ID_SECRET_ARN: `arn:aws:secretsmanager:${stack.region}:${stack.account}:secret:platform/${envName}/entra/client-id`,
      ENTRA_CLIENT_SECRET_SECRET_ARN: `arn:aws:secretsmanager:${stack.region}:${stack.account}:secret:platform/${envName}/entra/client-secret`, // pragma: allowlist secret
    },
  });

  const entraClientIdSecret = secretsmanager.Secret.fromSecretNameV2(scope, 'EntraClientIdSecret', `platform/${envName}/entra/client-id`);
  const entraClientSecretSecret = secretsmanager.Secret.fromSecretNameV2(scope, 'EntraClientSecretSecret', `platform/${envName}/entra/client-secret`);
  entraClientIdSecret.grantRead(bffFn);
  entraClientSecretSecret.grantRead(bffFn);

  new lambda.EventSourceMapping(scope, 'webhookDeliveryJobsStreamMapping', {
    target: webhookDeliveryFn,
    eventSourceArn: storage.jobsTable.tableStreamArn,
    startingPosition: lambda.StartingPosition.LATEST,
    batchSize: 10,
    bisectBatchOnError: true,
    retryAttempts: 3,
  });
  new lambda.EventSourceMapping(scope, 'webhookDeliveryRetryQueueMapping', {
    target: webhookDeliveryFn,
    eventSourceArn: webhookDeliveryRetryQueue.queueArn,
    batchSize: 10,
  });

  const authoriserFn = createPythonLambda({
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
      TENANTS_TABLE: storage.tenantsTable.tableName,
      APPCONFIG_APPLICATION_ID: storage.appconfigApp.ref,
      APPCONFIG_ENVIRONMENT_ID: storage.appconfigEnv.ref,
      APPCONFIG_PROFILE_ID: storage.capabilityProfile.ref,
    },
  });
  storage.tenantsTable.grantReadData(authoriserFn);
  authoriserFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['appconfig:GetLatestConfiguration', 'appconfig:StartConfigurationSession'],
      resources: [
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}`,
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}/environment/${storage.appconfigEnv.ref}`,
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}/configurationprofile/${storage.capabilityProfile.ref}`,
      ],
    }),
  );
  storage.tenantsTable.grantReadData(webhookDeliveryFn);
  storage.jobsTable.grantReadWriteData(webhookDeliveryFn);
  webhookDeliveryFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['dynamodb:DescribeStream', 'dynamodb:GetRecords', 'dynamodb:GetShardIterator', 'dynamodb:ListStreams'],
      resources: [`${storage.jobsTable.tableArn}/stream/*`],
    }),
  );
  webhookDeliveryRetryQueue.grantConsumeMessages(webhookDeliveryFn);
  webhookDeliveryRetryQueue.grantSendMessages(webhookDeliveryFn);
  webhookDeliveryRetryDlq.grantSendMessages(webhookDeliveryFn);

  const requestInterceptorFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../gateway/interceptors'),
    handler: 'request_interceptor.handler',
    functionNameSuffix: 'interceptor-request',
    timeout: cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'gateway-request-interceptor',
      TOOLS_TABLE: storage.toolsTable.tableName,
      ENTRA_JWKS_URL: entra.jwksUrl,
      ENTRA_AUDIENCE: entra.audience,
      ENTRA_ISSUER: entra.issuer,
      SCOPED_TOKEN_ISSUER: 'platform-gateway',
      IDEMPOTENCY_TABLE: storage.gatewayIdempotencyTable.tableName,
      SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN: scopedTokenSigningKeySecret.secretArn,
      PLATFORM_ENV: envName,
      APPCONFIG_APPLICATION_ID: storage.appconfigApp.ref,
      APPCONFIG_ENVIRONMENT_ID: storage.appconfigEnv.ref,
      APPCONFIG_PROFILE_ID: storage.capabilityProfile.ref,
    },
  });
  scopedTokenSigningKeySecret.grantRead(requestInterceptorFn);
  requestInterceptorFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['appconfig:GetLatestConfiguration', 'appconfig:StartConfigurationSession'],
      resources: [
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}`,
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}/environment/${storage.appconfigEnv.ref}`,
        `arn:aws:appconfig:${stack.region}:${stack.account}:application/${storage.appconfigApp.ref}/configurationprofile/${storage.capabilityProfile.ref}`,
      ],
    }),
  );
  storage.toolsTable.grantReadData(requestInterceptorFn);
  storage.gatewayIdempotencyTable.grantReadWriteData(requestInterceptorFn);

  const responseInterceptorFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../gateway/interceptors'),
    handler: 'response_interceptor.handler',
    functionNameSuffix: 'interceptor-response',
    timeout: cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'gateway-response-interceptor',
    },
  });

  const billingFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../src/billing'),
    handler: 'handler.lambda_handler',
    functionNameSuffix: 'billing',
    timeout: cdk.Duration.minutes(15),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'billing',
      TENANTS_TABLE_NAME: storage.tenantsTable.tableName,
      INVOCATIONS_TABLE_NAME: storage.invocationsTable.tableName,
      EVENT_BUS_NAME: 'default',
    },
  });
  storage.tenantsTable.grantReadWriteData(billingFn);
  storage.invocationsTable.grantReadData(billingFn);
  billingFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [`arn:aws:ssm:${stack.region}:${stack.account}:parameter/platform/billing/pricing/*`],
    }),
  );
  billingFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
    }),
  );
  billingFn.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ['events:PutEvents'],
      resources: [`arn:aws:events:${stack.region}:${stack.account}:event-bus/default`],
    }),
  );
  new events.Rule(scope, 'DailyBillingRule', {
    schedule: events.Schedule.cron({ hour: '0', minute: '0' }),
    targets: [new targets.LambdaFunction(billingFn)],
  });

  const tenantProvisionerFn = createPythonLambda({
    assetPath: path.join(__dirname, '../../../src/tenant_provisioner'),
    handler: 'handler.lambda_handler',
    functionNameSuffix: 'tenant-provisioner',
    timeout: cdk.Duration.minutes(11),
    memorySize: 512,
    environment: {
      POWERTOOLS_SERVICE_NAME: 'tenant-provisioner',
      PLATFORM_ENV: envName,
      TENANT_STACK_TEMPLATE_URL: tenantStackTemplateAsset.bucket.s3UrlForObject(tenantStackTemplateAsset.s3ObjectKey),
      EVENT_BUS_NAME: 'default',
    },
  });
  tenantStackTemplateAsset.grantRead(tenantProvisionerFn);
  tenantProvisionerFn.addToRolePolicy(
    new iam.PolicyStatement({
      sid: 'TenantStackCloudFormationAccess',
      actions: ['cloudformation:CreateStack', 'cloudformation:UpdateStack', 'cloudformation:DescribeStacks', 'cloudformation:GetTemplate'],
      resources: [`arn:aws:cloudformation:${stack.region}:${stack.account}:stack/platform-tenant-*/*`],
    }),
  );
  tenantProvisionerFn.addToRolePolicy(
    new iam.PolicyStatement({
      sid: 'TenantStackIamAccess',
      actions: ['iam:CreateRole', 'iam:DeleteRole', 'iam:PutRolePolicy', 'iam:DeleteRolePolicy', 'iam:GetRole', 'iam:PassRole', 'iam:TagRole'],
      resources: [`arn:aws:iam::${stack.account}:role/platform-tenant-*`],
    }),
  );
  tenantProvisionerFn.addToRolePolicy(
    new iam.PolicyStatement({
      sid: 'TenantStackSsmAccess',
      actions: ['ssm:PutParameter', 'ssm:GetParameter', 'ssm:DeleteParameter', 'ssm:AddTagsToResource'],
      resources: [`arn:aws:ssm:${stack.region}:${stack.account}:parameter/platform/tenants/*`],
    }),
  );
  tenantProvisionerFn.addToRolePolicy(
    new iam.PolicyStatement({
      sid: 'TenantStackBedrockAccess',
      actions: ['bedrock-agentcore:CreateMemory', 'bedrock-agentcore:UpdateMemory', 'bedrock-agentcore:DeleteMemory', 'bedrock-agentcore:GetMemory', 'bedrock-agentcore:TagResource'],
      resources: ['*'],
    }),
  );
  tenantProvisionerFn.addToRolePolicy(
    new iam.PolicyStatement({
      sid: 'TenantProvisionerEventBridgeAccess',
      actions: ['events:PutEvents'],
      resources: [`arn:aws:events:${stack.region}:${stack.account}:event-bus/default`],
    }),
  );

  const startTenantProvisioning = new tasks.LambdaInvoke(scope, 'StartTenantProvisioning', {
    lambdaFunction: tenantProvisionerFn,
    payload: sfn.TaskInput.fromObject({
      action: 'start',
      detail: sfn.JsonPath.objectAt('$.detail'),
    }),
    payloadResponseOnly: true,
  });
  const waitForTenantProvisioning = new sfn.Wait(scope, 'WaitForTenantProvisioning', {
    time: sfn.WaitTime.duration(cdk.Duration.seconds(10)),
  });
  const pollTenantProvisioning = new tasks.LambdaInvoke(scope, 'PollTenantProvisioning', {
    lambdaFunction: tenantProvisionerFn,
    payload: sfn.TaskInput.fromObject({
      action: 'poll',
      tenantId: sfn.JsonPath.stringAt('$.tenantId'),
      appId: sfn.JsonPath.stringAt('$.appId'),
      tier: sfn.JsonPath.stringAt('$.tier'),
      accountId: sfn.JsonPath.stringAt('$.accountId'),
      stackName: sfn.JsonPath.stringAt('$.stackName'),
    }),
    payloadResponseOnly: true,
  });
  const emitTenantProvisioned = new tasks.LambdaInvoke(scope, 'EmitTenantProvisioned', {
    lambdaFunction: tenantProvisionerFn,
    payload: sfn.TaskInput.fromObject({
      action: 'emit-result',
      resultType: 'provisioned',
      tenantId: sfn.JsonPath.stringAt('$.tenantId'),
      appId: sfn.JsonPath.stringAt('$.appId'),
      tier: sfn.JsonPath.stringAt('$.tier'),
      accountId: sfn.JsonPath.stringAt('$.accountId'),
      stackName: sfn.JsonPath.stringAt('$.stackName'),
      stackStatus: sfn.JsonPath.stringAt('$.stackStatus'),
      outputs: sfn.JsonPath.objectAt('$.outputs'),
    }),
    payloadResponseOnly: true,
  });
  const emitTenantProvisioningFailed = new tasks.LambdaInvoke(scope, 'EmitTenantProvisioningFailed', {
    lambdaFunction: tenantProvisionerFn,
    payload: sfn.TaskInput.fromObject({
      action: 'emit-result',
      resultType: 'failed',
      tenantId: sfn.JsonPath.stringAt('$.tenantId'),
      appId: sfn.JsonPath.stringAt('$.appId'),
      tier: sfn.JsonPath.stringAt('$.tier'),
      accountId: sfn.JsonPath.stringAt('$.accountId'),
      stackName: sfn.JsonPath.stringAt('$.stackName'),
      stackStatus: sfn.JsonPath.stringAt('$.stackStatus'),
      reason: sfn.JsonPath.stringAt('$.reason'),
      outputs: sfn.JsonPath.objectAt('$.outputs'),
    }),
    payloadResponseOnly: true,
  });
  const tenantProvisioningStateMachine = new sfn.StateMachine(scope, 'TenantProvisioningStateMachine', {
    stateMachineName: `platform-tenant-provisioning-${envName}`,
    timeout: cdk.Duration.minutes(30),
    definitionBody: sfn.DefinitionBody.fromChainable(
      startTenantProvisioning.next(
        new sfn.Choice(scope, 'TenantProvisioningStarted?')
          .when(sfn.Condition.stringEquals('$.provisioningState', 'READY'), emitTenantProvisioned)
          .when(sfn.Condition.stringEquals('$.provisioningState', 'FAILED'), emitTenantProvisioningFailed)
          .otherwise(
            waitForTenantProvisioning.next(
              pollTenantProvisioning.next(
                new sfn.Choice(scope, 'TenantProvisioningComplete?')
                  .when(sfn.Condition.stringEquals('$.provisioningState', 'READY'), emitTenantProvisioned)
                  .when(sfn.Condition.stringEquals('$.provisioningState', 'FAILED'), emitTenantProvisioningFailed)
                  .otherwise(waitForTenantProvisioning),
              ),
            ),
          ),
      ),
    ),
  });
  new events.Rule(scope, 'TenantCreatedRule', {
    ruleName: `platform-tenant-created-${envName}`,
    description: 'Trigger tenant provisioning when a new tenant is created',
    eventPattern: {
      source: ['platform.tenant_api'],
      detailType: ['tenant.created'],
    },
    targets: [new targets.SfnStateMachine(tenantProvisioningStateMachine)],
  });
  new events.Rule(scope, 'TenantProvisioningCompletedRule', {
    ruleName: `platform-tenant-provisioning-completed-${envName}`,
    description: 'Update tenant metadata when tenant provisioning completes',
    eventPattern: {
      source: ['platform.tenant_provisioner'],
      detailType: ['tenant.provisioned', 'tenant.provisioning_failed'],
    },
    targets: [new targets.LambdaFunction(tenantMgmtFn)],
  });

  return {
    tenantMgmtFn,
    webhookRegistryFn,
    agentRegistryFn,
    adminOpsFn,
    bridgeFn,
    webhookDeliveryFn,
    bffFn,
    authoriserFn,
    requestInterceptorFn,
    responseInterceptorFn,
    billingFn,
    dlqs,
  };
}
