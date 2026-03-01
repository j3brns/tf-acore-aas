/**
 * PlatformStack — REST API, WAF, CloudFront, Bridge Lambda, BFF Lambda,
 *                 Authoriser Lambda, AgentCore Gateway.
 *
 * REST API (not HTTP API) with usage plans, per-method throttling, WAF association.
 * Authoriser Lambda: provisioned concurrency 10.
 * AgentCore Gateway with REQUEST and RESPONSE interceptors wired.
 *
 * Implemented in TASK-023.
 * ADRs: ADR-003, ADR-004, ADR-011
 */
import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';
import * as path from 'path';

type PythonLambdaProps = {
  assetPath: string;
  handler: string;
  functionNameSuffix: string;
  timeout: cdk.Duration;
  memorySize: number;
  environment?: Record<string, string>;
};

export interface PlatformStackProps extends cdk.StackProps {
  readonly vpc: ec2.IVpc;
  readonly tenantDataKey: kms.IKey;
  readonly platformConfigKey: kms.IKey;
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

  public readonly bridgeFn: lambda.Function;
  public readonly bffFn: lambda.Function;
  public readonly authoriserFn: lambda.Function;
  public readonly tenantApiFn: lambda.Function;
  public readonly requestInterceptorFn: lambda.Function;
  public readonly responseInterceptorFn: lambda.Function;

  public readonly apiWebAcl: wafv2.CfnWebACL;
  public readonly spaDistribution: cloudfront.CfnDistribution;

  public readonly dlqs: Record<string, sqs.IQueue> = {};

  constructor(scope: Construct, id: string, props: PlatformStackProps) {
    super(scope, id, props);
    this.vpc = props.vpc;

    const env = this.node.tryGetContext('env') as string;

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
      pointInTimeRecovery: true,
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
        actions: ['secretsmanager:CreateSecret', 'secretsmanager:TagResource'],
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
      },
    });

    this.authoriserFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../src/authoriser'),
      handler: 'handler.handler',
      functionNameSuffix: 'authoriser',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'authoriser',
        ENTRA_JWKS_URL: 'https://login.microsoftonline.com/common/discovery/v2.0/keys',
        ENTRA_AUDIENCE: 'platform-api',
        ENTRA_ISSUER: 'https://login.microsoftonline.com/common/v2.0',
        TENANTS_TABLE: this.tenantsTable.tableName,
      },
    });

    this.tenantsTable.grantReadData(this.authoriserFn);

    this.requestInterceptorFn = this.createPythonLambda({
      assetPath: path.join(__dirname, '../../../gateway/interceptors'),
      handler: 'request_interceptor.handler',
      functionNameSuffix: 'interceptor-request',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        POWERTOOLS_SERVICE_NAME: 'gateway-request-interceptor',
      },
    });

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

    this.api = new apigateway.RestApi(this, 'PlatformRestApi', {
      restApiName: `${this.stackName}-rest-api`,
      description: 'Platform northbound REST API (ADR-003)',
      apiKeySourceType: apigateway.ApiKeySourceType.AUTHORIZER,
      deployOptions: {
        stageName: 'prod',
        tracingEnabled: true,
        metricsEnabled: true,
        methodOptions: {
          '/v1/invoke/POST': {
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

    const v1 = this.api.root.addResource('v1');
    const invoke = v1.addResource('invoke');
    const jobs = v1.addResource('jobs');
    const jobById = jobs.addResource('{jobId}');
    const bff = v1.addResource('bff');
    const tokenRefresh = bff.addResource('token-refresh');
    const sessionKeepalive = bff.addResource('session-keepalive');

    const tenants = v1.addResource('tenants');
    const tenantById = tenants.addResource('{tenantId}');
    const auditExport = tenantById.addResource('audit-export');
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

    tenants.addMethod('POST', tenantApiIntegration, securedMethodOptions);
    tenants.addMethod('GET', tenantApiIntegration, securedMethodOptions);

    tenantById.addMethod('GET', tenantApiIntegration, securedMethodOptions);
    tenantById.addMethod('PATCH', tenantApiIntegration, securedMethodOptions);
    tenantById.addMethod('DELETE', tenantApiIntegration, securedMethodOptions);

    auditExport.addMethod('GET', tenantApiIntegration, securedMethodOptions);

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

    invoke.addMethod(
      'POST',
      new apigateway.LambdaIntegration(this.bridgeFn, { proxy: true }),
      securedMethodOptions,
    );
    jobById.addMethod(
      'GET',
      new apigateway.LambdaIntegration(this.bridgeFn, { proxy: true }),
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

    new ssm.StringParameter(this, 'ResultsBucketArnParam', {
      parameterName: `/platform/core/${env}/results-bucket-arn`,
      stringValue: resultsBucket.bucketArn,
      description: 'ARN for the platform results S3 bucket',
    });

    const spaCspPolicy = new cloudfront.CfnResponseHeadersPolicy(this, 'SpaCspResponseHeadersPolicy', {
      responseHeadersPolicyConfig: {
        name: `${this.stackName}-spa-csp`,
        comment: 'CSP headers for platform SPA',
        customHeadersConfig: {
          items: [
            {
              header: 'Content-Security-Policy',
              value:
                "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; connect-src 'self' https:; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self';",
              override: true,
            },
          ],
        },
      },
    });

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
          responseHeadersPolicyId: spaCspPolicy.attrId,
        },
        restrictions: {
          geoRestriction: {
            restrictionType: 'none',
          },
        },
        viewerCertificate: {
          cloudFrontDefaultCertificate: true,
        },
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

    const agentCoreGatewayRole = new iam.Role(this, 'AgentCoreGatewayExecutionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'Execution role for AgentCore Gateway interceptors',
    });
    agentCoreGatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [this.requestInterceptorFn.functionArn, this.responseInterceptorFn.functionArn],
      }),
    );

    new cdk.CfnResource(this, 'AgentCoreGateway', {
      type: 'AWS::BedrockAgentCore::Gateway',
      properties: {
        Name: `${this.stackName.toLowerCase().replace(/[^a-z0-9-]/g, '-')}-gateway`,
        Description: 'Platform AgentCore Gateway with request/response interceptors',
        AuthorizerType: 'AWS_IAM',
        ProtocolType: 'MCP',
        RoleArn: agentCoreGatewayRole.roleArn,
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
}
