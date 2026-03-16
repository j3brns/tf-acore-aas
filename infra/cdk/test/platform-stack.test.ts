import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as kms from 'aws-cdk-lib/aws-kms';
import { PlatformStack } from '../lib/platform-stack';

describe('PlatformStack (TASK-023)', () => {
  const synthTemplate = (
    environment: 'dev' | 'staging' | 'prod' = 'dev',
    extraContext: Record<string, string> = {},
  ) => {
    const app = new cdk.App({
      context: {
        env: environment,
        entraTenantId: '00000000-0000-0000-0000-000000000000',
        ...extraContext,
      },
    });
    const env = { account: '123456789012', region: 'eu-west-2' };
    const identityStack = new cdk.Stack(app, 'IdentityStack', { env });
    const mockKey = new kms.Key(identityStack, 'MockKey');

    const networkStack = new cdk.Stack(app, 'NetworkStack', { env });
    const mockVpc = new ec2.Vpc(networkStack, 'MockVpc', {
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
        },
        {
          name: 'Isolated',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
        },
      ],
    });

    const stack = new PlatformStack(app, `platform-core-${environment}`, {
      env,
      vpc: mockVpc,
      tenantDataKey: mockKey,
      platformConfigKey: mockKey,
    });
    return Template.fromStack(stack);
  };
  const template = synthTemplate('dev');

  test('creates all required DynamoDB tables with PITR and encryption', () => {
    template.resourceCountIs('AWS::DynamoDB::Table', 8);

    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'platform-tenants',
      ProvisionedThroughput: {
        ReadCapacityUnits: 5,
        WriteCapacityUnits: 5,
      },
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    });

    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'platform-invocations',
      BillingMode: 'PAY_PER_REQUEST',
      TimeToLiveSpecification: {
        AttributeName: 'ttl',
        Enabled: true,
      },
    });

    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'platform-jobs',
      StreamSpecification: {
        StreamViewType: 'NEW_AND_OLD_IMAGES',
      },
    });
  });

  test('creates REST API with authorizer-backed API key source and usage plans', () => {
    template.hasResourceProperties('AWS::ApiGateway::RestApi', {
      ApiKeySourceType: 'AUTHORIZER', // pragma: allowlist secret
    });

    template.resourceCountIs('AWS::ApiGateway::UsagePlan', 3);
    template.resourceCountIs('AWS::Lambda::Alias', 2);

    template.hasResourceProperties('AWS::Lambda::Alias', {
      Name: 'live',
      ProvisionedConcurrencyConfig: {
        ProvisionedConcurrentExecutions: 10,
      },
    });
  });

  test('wires canonical invoke and jobs routes and removes legacy /v1/invoke', () => {
    const resources = template.findResources('AWS::ApiGateway::Resource');
    const pathParts = Object.values(resources).map((resource) => {
      const properties = (resource as { Properties?: { PathPart?: string } }).Properties;
      return properties?.PathPart;
    });

    expect(pathParts).toContain('agents');
    expect(pathParts).toContain('{agentName}');
    expect(pathParts).toContain('invoke');
    expect(pathParts).toContain('jobs');
    expect(pathParts).toContain('{jobId}');

    const stages = template.findResources('AWS::ApiGateway::Stage');
    const methodSettings = Object.values(stages).flatMap((stage) => {
      const properties = (stage as { Properties?: { MethodSettings?: Array<unknown> } }).Properties;
      return properties?.MethodSettings ?? [];
    });

    expect(methodSettings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          HttpMethod: 'POST',
          ResourcePath: '/~1v1~1agents~1{agentName}~1invoke',
        }),
        expect.objectContaining({
          HttpMethod: 'GET',
          ResourcePath: '/~1v1~1jobs~1{jobId}',
        }),
      ]),
    );
    expect(methodSettings).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          HttpMethod: 'POST',
          ResourcePath: '/~1v1~1invoke',
        }),
      ]),
    );
  });

  test('creates environment-aware bridge rollout policy with auto-rollback alarm', () => {
    const devTemplate = synthTemplate('dev');
    const stagingTemplate = synthTemplate('staging');
    const prodTemplate = synthTemplate('prod');

    devTemplate.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      DeploymentConfigName: 'CodeDeployDefault.LambdaAllAtOnce',
    });
    stagingTemplate.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      DeploymentConfigName: 'CodeDeployDefault.LambdaCanary10Percent30Minutes',
    });
    prodTemplate.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      DeploymentConfigName: 'CodeDeployDefault.LambdaCanary10Percent15Minutes',
    });

    stagingTemplate.hasOutput('BridgeCanaryPolicy', {
      Value: 'staging=canary-10%-30m',
    });
    prodTemplate.hasOutput('BridgeCanaryPolicy', {
      Value: 'prod=canary-10%-15m',
    });

    prodTemplate.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'platform-core-prod-error_rate_high',
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      Threshold: 5,
    });
    prodTemplate.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      AutoRollbackConfiguration: {
        Enabled: true,
        Events: Match.arrayWith([
          'DEPLOYMENT_FAILURE',
          'DEPLOYMENT_STOP_ON_REQUEST',
          'DEPLOYMENT_STOP_ON_ALARM',
        ]),
      },
      AlarmConfiguration: Match.objectLike({
        Enabled: true,
      }),
    });
  });

  test('creates WAF WebACL with managed rules, UK rate limit, and API association', () => {
    template.hasResourceProperties('AWS::WAFv2::WebACL', {
      Scope: 'REGIONAL',
      Rules: Match.arrayWith([
        Match.objectLike({
          Name: 'AWSManagedRulesCommonRuleSet',
          Statement: Match.objectLike({
            ManagedRuleGroupStatement: {
              VendorName: 'AWS',
              Name: 'AWSManagedRulesCommonRuleSet',
            },
          }),
        }),
        Match.objectLike({
          Name: 'UkIpRateLimit',
          Statement: Match.objectLike({
            RateBasedStatement: Match.objectLike({
              AggregateKeyType: 'IP',
              ScopeDownStatement: Match.objectLike({
                GeoMatchStatement: {
                  CountryCodes: ['GB'],
                },
              }),
            }),
          }),
        }),
        Match.objectLike({
          Name: 'BlockSqlmapUserAgent',
        }),
      ]),
    });

    template.resourceCountIs('AWS::WAFv2::WebACLAssociation', 1);
  });

  test('creates CloudFront distribution with OAC and CSP response headers policy', () => {
    template.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);

    template.hasResourceProperties('AWS::CloudFront::ResponseHeadersPolicy', {
      ResponseHeadersPolicyConfig: Match.objectLike({
        SecurityHeadersConfig: Match.objectLike({
          ContentSecurityPolicy: Match.objectLike({
            Override: true,
          }),
          FrameOptions: Match.objectLike({
            FrameOption: 'DENY',
            Override: true,
          }),
          StrictTransportSecurity: Match.objectLike({
            AccessControlMaxAgeSec: 31536000,
            IncludeSubdomains: true,
            Preload: true,
            Override: true,
          }),
        }),
      }),
    });

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Origins: Match.arrayWith([
          Match.objectLike({
            OriginAccessControlId: Match.anyValue(),
          }),
        ]),
        DefaultCacheBehavior: Match.objectLike({
          ResponseHeadersPolicyId: Match.anyValue(),
        }),
      }),
    });
  });

  test('configures CloudFront custom error responses for SPA route fallback', () => {
    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        CustomErrorResponses: [
          {
            ErrorCode: 403,
            ResponseCode: 200,
            ResponsePagePath: '/index.html',
            ErrorCachingMinTTL: 0,
          },
          {
            ErrorCode: 404,
            ResponseCode: 200,
            ResponsePagePath: '/index.html',
            ErrorCachingMinTTL: 0,
          },
        ],
      }),
    });
  });

  test('configures API Gateway CORS preflight to CloudFront origin only', () => {
    const optionsMethods = template.findResources('AWS::ApiGateway::Method', {
      Properties: {
        HttpMethod: 'OPTIONS',
      },
    });

    expect(Object.keys(optionsMethods).length).toBeGreaterThan(0);

    for (const method of Object.values(optionsMethods) as Array<{ Properties?: unknown }>) {
      const properties = method.Properties as {
        Integration?: { IntegrationResponses?: Array<{ ResponseParameters?: Record<string, unknown> }> };
      };
      const responseParameters =
        properties.Integration?.IntegrationResponses?.[0]?.ResponseParameters ?? {};
      const allowOrigin = responseParameters['method.response.header.Access-Control-Allow-Origin'];
      const allowMethods = responseParameters['method.response.header.Access-Control-Allow-Methods'];

      expect(allowOrigin).toBeDefined();
      expect(JSON.stringify(allowOrigin)).toContain('DomainName');
      expect(JSON.stringify(allowOrigin)).not.toContain("'*'");
      expect(JSON.stringify(allowMethods)).toContain('OPTIONS');
    }
  });

  test('creates AgentCore Gateway with request and response interceptor wiring', () => {
    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      AuthorizerType: 'AWS_IAM',
      ProtocolType: 'MCP',
      PolicyEngineConfiguration: Match.objectLike({
        Arn: Match.anyValue(),
        Mode: 'LOG_ONLY',
      }),
      InterceptorConfigurations: Match.arrayWith([
        Match.objectLike({
          InterceptionPoints: ['REQUEST'],
          InputConfiguration: {
            PassRequestHeaders: true,
          },
          Interceptor: Match.objectLike({
            Lambda: Match.objectLike({
              Arn: Match.anyValue(),
            }),
          }),
        }),
        Match.objectLike({
          InterceptionPoints: ['RESPONSE'],
          InputConfiguration: {
            PassRequestHeaders: true,
          },
        }),
      ]),
    });
  });

  test('creates AgentCore Policy Engine and Cedar policy resources', () => {
    template.resourceCountIs('AWS::BedrockAgentCore::PolicyEngine', 1);
    template.resourceCountIs('AWS::BedrockAgentCore::Policy', 1);

    template.hasResourceProperties('AWS::BedrockAgentCore::PolicyEngine', {
      Name: 'PlatformGatewayPolicyEngineDev',
    });

    template.hasResourceProperties('AWS::BedrockAgentCore::Policy', {
      Name: 'PlatformGatewayAllowAllDev',
      ValidationMode: 'FAIL_ON_ANY_FINDINGS',
      Definition: Match.objectLike({
        Cedar: Match.objectLike({
          Statement: Match.stringLikeRegexp('permit'),
        }),
      }),
    });

    template.hasOutput('AgentCoreGatewayPolicyMode', {
      Value: 'LOG_ONLY',
    });
  });

  test('uses LOG_ONLY for non-prod and ENFORCE for prod gateway policy mode', () => {
    const stagingTemplate = synthTemplate('staging');
    const prodTemplate = synthTemplate('prod');

    stagingTemplate.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      PolicyEngineConfiguration: Match.objectLike({
        Mode: 'LOG_ONLY',
      }),
    });
    prodTemplate.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      PolicyEngineConfiguration: Match.objectLike({
        Mode: 'ENFORCE',
      }),
    });
  });

  test('grants gateway role policy-engine authorization actions without wildcard resource', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const allStatements = Object.values(policies).flatMap((resource) => {
      const properties = (resource as { Properties?: { PolicyDocument?: { Statement?: Array<Record<string, unknown>> } } })
        .Properties;
      return properties?.PolicyDocument?.Statement ?? [];
    });

    const gatewayPolicyStatement = allStatements.find((statement) => {
      const actions = statement.Action;
      if (!Array.isArray(actions)) {
        return false;
      }
      return (
        actions.includes('bedrock-agentcore:AuthorizeAction') &&
        actions.includes('bedrock-agentcore:PartiallyAuthorizeActions') &&
        actions.includes('bedrock-agentcore:GetPolicyEngine')
      );
    });

    expect(gatewayPolicyStatement).toBeDefined();
    const resources = Array.isArray(gatewayPolicyStatement?.Resource)
      ? gatewayPolicyStatement?.Resource
      : [gatewayPolicyStatement?.Resource];
    expect(resources).not.toContain('*');
  });

  test('does not synthesize a standalone async-runner lambda', () => {
    const functions = template.findResources('AWS::Lambda::Function');
    const names = Object.values(functions).map((resource) => {
      const properties = (resource as { Properties?: { FunctionName?: string } }).Properties;
      return String(properties?.FunctionName ?? '');
    });

    expect(names.some((name) => name.includes('async-runner'))).toBe(false);
  });

  test('provisions webhook delivery lambda with jobs stream and retry queue wiring', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-webhook-delivery',
      Handler: 'handler.handler',
      Environment: {
        Variables: Match.objectLike({
          JOBS_TABLE: Match.anyValue(),
          WEBHOOK_MAX_RETRY_ATTEMPTS: '3',
        }),
      },
    });

    template.hasResourceProperties('AWS::Lambda::EventSourceMapping', {
      StartingPosition: 'LATEST',
      BatchSize: 10,
    });

    template.hasResourceProperties('AWS::SQS::Queue', {
      RedrivePolicy: Match.objectLike({
        maxReceiveCount: 1,
      }),
    });
  });

  test('configures the BFF lambda with canonical Entra config and secret references', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-bff',
      Environment: {
        Variables: Match.objectLike({
          ENTRA_TENANT_ID: '00000000-0000-0000-0000-000000000000',
          ENTRA_AUDIENCE: 'platform-api',
          ENTRA_TOKEN_ENDPOINT:
            'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/oauth2/v2.0/token',
          ENTRA_CLIENT_ID_SECRET_ARN:
            'arn:aws:secretsmanager:eu-west-2:123456789012:secret:platform/dev/entra/client-id',
          ENTRA_CLIENT_SECRET_SECRET_ARN:
            'arn:aws:secretsmanager:eu-west-2:123456789012:secret:platform/dev/entra/client-secret',
          POWERTOOLS_SERVICE_NAME: 'bff',
        }),
      },
    });
  });

  test('provisions SPA resources: S3 bucket, CloudFront distribution, and identifiers', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      BucketEncryption: {
        ServerSideEncryptionConfiguration: [
          {
            ServerSideEncryptionByDefault: {
              SSEAlgorithm: 'AES256',
            },
          },
        ],
      },
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Enabled: true,
        DefaultCacheBehavior: Match.objectLike({
          ViewerProtocolPolicy: 'redirect-to-https',
        }),
      }),
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/spa/dev/bucket-name',
      Type: 'String',
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/spa/dev/distribution-id',
      Type: 'String',
    });

    template.hasOutput('SpaBucketName', {
      Description: 'S3 bucket name for the platform SPA',
    });

    template.hasOutput('SpaDistributionId', {
      Description: 'CloudFront distribution ID for the platform SPA',
    });
  });

  test('wires Entra config from CDK context instead of hardcoded common endpoints', () => {
    const customTemplate = synthTemplate('dev', {
      entraTenantId: '00000000-0000-0000-0000-000000000000',
      entraAudience: 'api://platform-dev',
    });

    const expectedJwksUrl =
      'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/discovery/v2.0/keys';
    const expectedIssuer =
      'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0';

    customTemplate.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-authoriser',
      Environment: {
        Variables: Match.objectLike({
          ENTRA_JWKS_URL: expectedJwksUrl,
          ENTRA_AUDIENCE: 'api://platform-dev',
          ENTRA_ISSUER: expectedIssuer,
        }),
      },
    });

    customTemplate.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-interceptor-request',
      Environment: {
        Variables: Match.objectLike({
          ENTRA_JWKS_URL: expectedJwksUrl,
          ENTRA_AUDIENCE: 'api://platform-dev',
          ENTRA_ISSUER: expectedIssuer,
        }),
      },
    });

    customTemplate.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-bff',
      Environment: {
        Variables: Match.objectLike({
          ENTRA_TENANT_ID: '00000000-0000-0000-0000-000000000000',
          ENTRA_AUDIENCE: 'api://platform-dev',
          ENTRA_TOKEN_ENDPOINT:
            'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/oauth2/v2.0/token',
        }),
      },
    });
  });
});
