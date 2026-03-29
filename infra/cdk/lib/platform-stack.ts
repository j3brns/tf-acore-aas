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
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as codedeploy from 'aws-cdk-lib/aws-codedeploy';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
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
import { createPlatformCompute } from './platform-compute';
import { resolveEntraConfiguration } from './entra-config';
import { createPlatformStorage } from './platform-storage';
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
  public readonly tenantMgmtFn: lambda.Function;
  public readonly webhookRegistryFn: lambda.Function;
  public readonly agentRegistryFn: lambda.Function;
  public readonly adminOpsFn: lambda.Function;
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

    // --- Custom domain configuration (issue #164) ---
    // When spaDomainName + spaCertificateArn are set in CDK context, the CloudFront
    // distribution uses a custom domain with an ACM certificate provisioned in
    // us-east-1 (CloudFront requirement). When absent, the distribution uses the
    // default *.cloudfront.net certificate — suitable for dev/test only.
    const spaDomainName = this.node.tryGetContext('spaDomainName') as string | undefined;
    const spaCertificateArn = this.node.tryGetContext('spaCertificateArn') as string | undefined;
    const apiDomainName = this.node.tryGetContext('apiDomainName') as string | undefined;
    const apiCertificateArn = this.node.tryGetContext('apiCertificateArn') as string | undefined;

    // --- Secrets ---

    const scopedTokenSigningKeySecret = new secretsmanager.Secret(this, 'ScopedTokenSigningKeySecret', {
      secretName: `platform/${env}/gateway/scoped-token-signing-key`, // pragma: allowlist secret
      description: 'Signing key for scoped act-on-behalf tokens issued by Gateway interceptor',
      generateSecretString: {
        passwordLength: 32,
        excludePunctuation: true,
      },
    });

    const storage = createPlatformStorage(this, { envName: env });
    this.tenantsTable = storage.tenantsTable;
    this.agentsTable = storage.agentsTable;
    this.toolsTable = storage.toolsTable;
    this.opsLocksTable = storage.opsLocksTable;
    this.gatewayIdempotencyTable = storage.gatewayIdempotencyTable;
    this.invocationsTable = storage.invocationsTable;
    this.jobsTable = storage.jobsTable;
    this.sessionsTable = storage.sessionsTable;

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
    const compute = createPlatformCompute(this, {
      envName: env,
      storage,
      entra,
      scopedTokenSigningKeySecret,
      tenantStackTemplateAsset,
      createPythonLambda: (lambdaProps) => this.createPythonLambda(lambdaProps),
    });
    this.tenantMgmtFn = compute.tenantMgmtFn;
    this.webhookRegistryFn = compute.webhookRegistryFn;
    this.agentRegistryFn = compute.agentRegistryFn;
    this.adminOpsFn = compute.adminOpsFn;
    this.bridgeFn = compute.bridgeFn;
    this.webhookDeliveryFn = compute.webhookDeliveryFn;
    this.bffFn = compute.bffFn;
    this.authoriserFn = compute.authoriserFn;
    this.requestInterceptorFn = compute.requestInterceptorFn;
    this.responseInterceptorFn = compute.responseInterceptorFn;
    this.billingFn = compute.billingFn;
    Object.assign(this.dlqs, compute.dlqs);

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
        ...(spaDomainName && spaCertificateArn
          ? {
              aliases: [spaDomainName],
              viewerCertificate: {
                acmCertificateArn: spaCertificateArn,
                minimumProtocolVersion: 'TLSv1.2_2021',
                sslSupportMethod: 'sni-only',
              },
            }
          : {
              viewerCertificate: {
                cloudFrontDefaultCertificate: true,
                minimumProtocolVersion: 'TLSv1.2_2021',
              },
            }),
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

    if (spaDomainName) {
      new ssm.StringParameter(this, 'SpaDomainNameParam', {
        parameterName: `/platform/spa/${env}/domain-name`,
        stringValue: spaDomainName,
        description: 'Custom domain name for the platform SPA CloudFront distribution',
      });

      new cdk.CfnOutput(this, 'SpaDomainName', {
        value: spaDomainName,
        description: 'Custom domain name for the platform SPA',
      });
    }

    const spaAllowedOrigin = spaDomainName
      ? `https://${spaDomainName}`
      : cdk.Fn.join('', ['https://', this.spaDistribution.attrDomainName]);

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

    const tenantMgmtIntegration = new apigateway.LambdaIntegration(this.tenantMgmtFn, { proxy: true });
    const webhookRegistryIntegration = new apigateway.LambdaIntegration(this.webhookRegistryFn, {
      proxy: true,
    });
    const agentRegistryIntegration = new apigateway.LambdaIntegration(this.agentRegistryFn, {
      proxy: true,
    });
    const adminOpsIntegration = new apigateway.LambdaIntegration(this.adminOpsFn, { proxy: true });
    const bridgeIntegration = new apigateway.LambdaIntegration(bridgeAlias, { proxy: true });
    const bridgeStreamingIntegration = new apigateway.LambdaIntegration(bridgeAlias, {
      proxy: true,
      responseTransferMode: apigateway.ResponseTransferMode.STREAM,
    });

    health.addMethod('GET', tenantMgmtIntegration, securedMethodOptions);
    sessions.addMethod('GET', tenantMgmtIntegration, securedMethodOptions);
    tenants.addMethod('POST', tenantMgmtIntegration, securedMethodOptions);
    tenants.addMethod('GET', tenantMgmtIntegration, securedMethodOptions);

    tenantById.addMethod('GET', tenantMgmtIntegration, securedMethodOptions);
    tenantById.addMethod('PATCH', tenantMgmtIntegration, securedMethodOptions);
    tenantById.addMethod('DELETE', tenantMgmtIntegration, securedMethodOptions);

    auditExport.addMethod('GET', tenantMgmtIntegration, securedMethodOptions);
    tenantApiKeyRotate.addMethod('POST', tenantMgmtIntegration, securedMethodOptions);
    tenantUsersInvite.addMethod('POST', tenantMgmtIntegration, securedMethodOptions);

    failover.addMethod('POST', adminOpsIntegration, securedMethodOptions);
    quota.addMethod('GET', adminOpsIntegration, securedMethodOptions);
    splitAccounts.addMethod('POST', adminOpsIntegration, securedMethodOptions);
    serviceHealth.addMethod('GET', adminOpsIntegration, securedMethodOptions);
    billingStatus.addMethod('GET', adminOpsIntegration, securedMethodOptions);

    // Wire all ops routes
    opsTopTenants.addMethod('GET', adminOpsIntegration, securedMethodOptions);
    opsSecurityEvents.addMethod('GET', adminOpsIntegration, securedMethodOptions);
    opsErrorRate.addMethod('GET', adminOpsIntegration, securedMethodOptions);
    opsSecurityPage.addMethod('POST', adminOpsIntegration, securedMethodOptions);
    opsDlqByName.addMethod('ANY', adminOpsIntegration, securedMethodOptions);
    opsTenantById.addMethod('ANY', adminOpsIntegration, securedMethodOptions);
    opsJobById.addMethod('ANY', adminOpsIntegration, securedMethodOptions);

    webhooks.addMethod('GET', webhookRegistryIntegration, securedMethodOptions);
    webhooks.addMethod('POST', webhookRegistryIntegration, securedMethodOptions);
    webhookById.addMethod('DELETE', webhookRegistryIntegration, securedMethodOptions);

    const platformAgents = platform.addResource('agents');
    const platformAgentByName = platformAgents.addResource('{agentName}');
    const platformAgentVersions = platformAgentByName.addResource('versions');
    const platformAgentVersion = platformAgentVersions.addResource('{version}');

    platformAgents.addMethod('GET', agentRegistryIntegration, securedMethodOptions);
    platformAgents.addMethod('POST', agentRegistryIntegration, securedMethodOptions);
    platformAgentVersion.addMethod('PATCH', agentRegistryIntegration, securedMethodOptions);

    agents.addMethod('GET', bridgeIntegration, securedMethodOptions);
    agentByName.addMethod('GET', bridgeIntegration, securedMethodOptions);
    agentInvoke.addMethod('POST', bridgeStreamingIntegration, securedMethodOptions);
    jobById.addMethod(
      'GET',
      bridgeIntegration,
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

    // --- API Gateway custom domain (issue #164) ---
    // When apiDomainName + apiCertificateArn are provided, a regional custom domain
    // is added to the REST API. The ACM certificate must be in the same region
    // (eu-west-2) for regional API Gateway endpoints. The caller is responsible for
    // creating a CNAME or alias DNS record pointing apiDomainName to the regional
    // domain name output.
    if (apiDomainName && apiCertificateArn) {
      const apiCustomDomain = new apigateway.DomainName(this, 'ApiCustomDomain', {
        domainName: apiDomainName,
        certificate: acm.Certificate.fromCertificateArn(this, 'ApiCertificate', apiCertificateArn),
        endpointType: apigateway.EndpointType.REGIONAL,
        securityPolicy: apigateway.SecurityPolicy.TLS_1_2,
      });

      new apigateway.BasePathMapping(this, 'ApiBasePathMapping', {
        domainName: apiCustomDomain,
        restApi: this.api,
      });

      new ssm.StringParameter(this, 'ApiDomainNameParam', {
        parameterName: `/platform/core/${env}/api-domain-name`,
        stringValue: apiDomainName,
        description: 'Custom domain name for the platform REST API',
      });

      new ssm.StringParameter(this, 'ApiRegionalDomainNameParam', {
        parameterName: `/platform/core/${env}/api-regional-domain-name`,
        stringValue: apiCustomDomain.domainNameAliasDomainName,
        description: 'Regional domain name for DNS CNAME/alias target',
      });

      new cdk.CfnOutput(this, 'ApiCustomDomainName', {
        value: apiDomainName,
        description: 'Custom domain name for the platform REST API',
      });

      new cdk.CfnOutput(this, 'ApiRegionalDomainName', {
        value: apiCustomDomain.domainNameAliasDomainName,
        description: 'Regional domain name — point DNS here',
      });
    }

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
