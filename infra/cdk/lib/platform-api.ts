import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';

export interface PlatformApiProps {
  readonly envName: string;
  readonly spaAllowedOrigin: string;
  readonly apiDomainName?: string;
  readonly apiCertificateArn?: string;
  readonly authoriserAlias: lambda.IAlias;
  readonly tenantMgmtFn: lambda.IFunction;
  readonly webhookRegistryFn: lambda.IFunction;
  readonly agentRegistryFn: lambda.IFunction;
  readonly adminOpsFn: lambda.IFunction;
  readonly bridgeAlias: lambda.IAlias;
  readonly bffFn: lambda.IFunction;
}

export class PlatformApi extends Construct {
  public readonly api: apigateway.RestApi;

  constructor(scope: Construct, id: string, props: PlatformApiProps) {
    super(scope, id);

    const apiAccessLogGroup = new logs.LogGroup(this, 'ApiAccessLogGroup', {
      logGroupName: `/aws/apigateway/${cdk.Stack.of(this).stackName}-rest-api-access-logs`,
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
      restApiName: `${cdk.Stack.of(this).stackName}-rest-api`,
      description: 'Platform northbound REST API (ADR-003)',
      apiKeySourceType: apigateway.ApiKeySourceType.AUTHORIZER,
      cloudWatchRole: true,
      endpointConfiguration: {
        types: [apigateway.EndpointType.REGIONAL],
      },
      defaultCorsPreflightOptions: {
        allowOrigins: [props.spaAllowedOrigin],
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
          status: 0,
          protocol: '$context.protocol',
          responseLength: 0,
          tenantId: '$context.authorizer.tenantid',
          appId: '$context.authorizer.appid',
          sub: '$context.authorizer.sub',
          tier: '$context.authorizer.tier',
          latency: 0,
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

    this.api.addGatewayResponse('Default4xxResponse', {
      type: apigateway.ResponseType.DEFAULT_4XX,
      responseHeaders: {
        'gatewayresponses.header.Access-Control-Allow-Origin': `'${props.spaAllowedOrigin}'`,
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
        'gatewayresponses.header.Access-Control-Allow-Origin': `'${props.spaAllowedOrigin}'`,
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
        'gatewayresponses.header.Access-Control-Allow-Origin': `'${props.spaAllowedOrigin}'`,
        'gatewayresponses.header.Access-Control-Allow-Credentials': "'true'",
      },
      templates: {
        'application/json': '{"message":"Unauthorized","requestId":"$context.requestId"}',
      },
    });

    this.api.addGatewayResponse('AccessDeniedResponse', {
      type: apigateway.ResponseType.ACCESS_DENIED,
      responseHeaders: {
        'gatewayresponses.header.Access-Control-Allow-Origin': `'${props.spaAllowedOrigin}'`,
        'gatewayresponses.header.Access-Control-Allow-Credentials': "'true'",
      },
      templates: {
        'application/json': '{"message":"Access denied","requestId":"$context.requestId"}',
      },
    });

    const restAuthorizer = new apigateway.TokenAuthorizer(this, 'RestTokenAuthorizer', {
      handler: props.authoriserAlias,
      identitySource: apigateway.IdentitySource.header('Authorization'),
      resultsCacheTtl: cdk.Duration.minutes(5),
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
    const tenantUsersInvites = tenantUsers.addResource('invites');
    const platform = v1.addResource('platform');
    const failover = platform.addResource('failover');
    const quota = platform.addResource('quota');
    const splitAccounts = quota.addResource('split-accounts');
    const serviceHealth = platform.addResource('service-health');
    const billing = platform.addResource('billing');
    const billingStatus = billing.addResource('status');
    const ops = platform.addResource('ops');

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

    const tenantMgmtIntegration = new apigateway.LambdaIntegration(props.tenantMgmtFn, { proxy: true });
    const webhookRegistryIntegration = new apigateway.LambdaIntegration(props.webhookRegistryFn, {
      proxy: true,
    });
    const agentRegistryIntegration = new apigateway.LambdaIntegration(props.agentRegistryFn, {
      proxy: true,
    });
    const adminOpsIntegration = new apigateway.LambdaIntegration(props.adminOpsFn, { proxy: true });
    const bridgeIntegration = new apigateway.LambdaIntegration(props.bridgeAlias, { proxy: true });
    const bridgeStreamingIntegration = new apigateway.LambdaIntegration(props.bridgeAlias, {
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
    tenantUsersInvites.addMethod('GET', tenantMgmtIntegration, securedMethodOptions);

    failover.addMethod('POST', adminOpsIntegration, securedMethodOptions);
    quota.addMethod('GET', adminOpsIntegration, securedMethodOptions);
    splitAccounts.addMethod('POST', adminOpsIntegration, securedMethodOptions);
    serviceHealth.addMethod('GET', adminOpsIntegration, securedMethodOptions);
    billingStatus.addMethod('GET', adminOpsIntegration, securedMethodOptions);

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
    jobById.addMethod('GET', bridgeIntegration, securedMethodOptions);
    tokenRefresh.addMethod(
      'POST',
      new apigateway.LambdaIntegration(props.bffFn, { proxy: true }),
      securedMethodOptions,
    );
    sessionKeepalive.addMethod(
      'POST',
      new apigateway.LambdaIntegration(props.bffFn, { proxy: true }),
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
      parameterName: `/platform/core/${props.envName}/rest-api-id`,
      stringValue: this.api.restApiId,
      description: 'REST API ID for the platform northbound API',
    });

    for (const plan of usagePlanDefinitions) {
      const usagePlan = new apigateway.UsagePlan(this, plan.id, {
        name: `${cdk.Stack.of(this).stackName}-${plan.name}`,
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
        parameterName: `/platform/core/${props.envName}/usage-plan-${plan.name}-id`,
        stringValue: usagePlan.usagePlanId,
        description: `Usage plan ID for ${plan.name} tier`,
      });
    }

    if (props.apiDomainName && props.apiCertificateArn) {
      const apiCustomDomain = new apigateway.DomainName(this, 'ApiCustomDomain', {
        domainName: props.apiDomainName,
        certificate: acm.Certificate.fromCertificateArn(
          this,
          'ApiCertificate',
          props.apiCertificateArn,
        ),
        endpointType: apigateway.EndpointType.REGIONAL,
        securityPolicy: apigateway.SecurityPolicy.TLS_1_2,
      });

      new apigateway.BasePathMapping(this, 'ApiBasePathMapping', {
        domainName: apiCustomDomain,
        restApi: this.api,
      });

      new ssm.StringParameter(this, 'ApiDomainNameParam', {
        parameterName: `/platform/core/${props.envName}/api-domain-name`,
        stringValue: props.apiDomainName,
        description: 'Custom domain name for the platform REST API',
      });

      new ssm.StringParameter(this, 'ApiRegionalDomainNameParam', {
        parameterName: `/platform/core/${props.envName}/api-regional-domain-name`,
        stringValue: apiCustomDomain.domainNameAliasDomainName,
        description: 'Regional domain name for DNS CNAME/alias target',
      });

      const apiCustomDomainNameOutput = new cdk.CfnOutput(this, 'ApiCustomDomainName', {
        value: props.apiDomainName,
        description: 'Custom domain name for the platform REST API',
      });
      apiCustomDomainNameOutput.overrideLogicalId('ApiCustomDomainName');

      const apiRegionalDomainNameOutput = new cdk.CfnOutput(this, 'ApiRegionalDomainName', {
        value: apiCustomDomain.domainNameAliasDomainName,
        description: 'Regional domain name — point DNS here',
      });
      apiRegionalDomainNameOutput.overrideLogicalId('ApiRegionalDomainName');
    }
  }
}
