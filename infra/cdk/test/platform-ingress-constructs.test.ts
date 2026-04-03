import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { PlatformApi } from '../lib/platform-api';
import { PlatformGateway } from '../lib/platform-gateway';
import { PlatformSpa } from '../lib/platform-spa';
import { PlatformWaf } from '../lib/platform-waf';

const TEST_ENV = {
  account: '123456789012',
  region: 'eu-west-2',
};

function createNodejsFunction(scope: cdk.Stack, id: string): lambda.Function {
  return new lambda.Function(scope, id, {
    runtime: lambda.Runtime.NODEJS_20_X,
    handler: 'index.handler',
    code: lambda.Code.fromInline(
      'exports.handler = async () => ({ statusCode: 200, body: "ok" });',
    ),
  });
}

function synthSpa(extraProps: Partial<{ spaDomainName: string; spaCertificateArn: string }> = {}) {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformSpaTestStack', { env: TEST_ENV });
  new PlatformSpa(stack, 'PlatformSpa', {
    envName: 'dev',
    ...extraProps,
  });
  return Template.fromStack(stack);
}

function synthApi(
  extraProps: Partial<{ apiDomainName: string; apiCertificateArn: string }> = {},
) {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformApiTestStack', { env: TEST_ENV });

  const authoriserFn = createNodejsFunction(stack, 'AuthoriserFn');
  const bridgeFn = createNodejsFunction(stack, 'BridgeFn');

  const authoriserAlias = new lambda.Alias(stack, 'AuthoriserAlias', {
    aliasName: 'live',
    version: authoriserFn.currentVersion,
  });
  const bridgeAlias = new lambda.Alias(stack, 'BridgeAlias', {
    aliasName: 'live',
    version: bridgeFn.currentVersion,
  });

  new PlatformApi(stack, 'PlatformApi', {
    envName: 'dev',
    spaAllowedOrigin: 'https://spa.example.com',
    authoriserAlias,
    tenantMgmtFn: createNodejsFunction(stack, 'TenantMgmtFn'),
    webhookRegistryFn: createNodejsFunction(stack, 'WebhookRegistryFn'),
    agentRegistryFn: createNodejsFunction(stack, 'AgentRegistryFn'),
    adminOpsFn: createNodejsFunction(stack, 'AdminOpsFn'),
    bridgeAlias,
    bffFn: createNodejsFunction(stack, 'BffFn'),
    ...extraProps,
  });

  return Template.fromStack(stack);
}

function synthGateway(enforcementMode: 'LOG_ONLY' | 'ENFORCE' = 'LOG_ONLY') {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformGatewayTestStack', { env: TEST_ENV });

  new PlatformGateway(stack, 'PlatformGateway', {
    enforcementMode,
    policyEngineName: `PlatformGatewayPolicyEngine${enforcementMode}`,
    policyName: `PlatformGatewayAllowAll${enforcementMode}`,
    requestInterceptorFn: createNodejsFunction(stack, 'RequestInterceptorFn'),
    responseInterceptorFn: createNodejsFunction(stack, 'ResponseInterceptorFn'),
  });

  return Template.fromStack(stack);
}

function synthWaf() {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformWafTestStack', { env: TEST_ENV });
  const api = new apigateway.RestApi(stack, 'TestApi');
  api.root.addMethod(
    'GET',
    new apigateway.MockIntegration({
      integrationResponses: [
        {
          statusCode: '200',
        },
      ],
      passthroughBehavior: apigateway.PassthroughBehavior.NEVER,
      requestTemplates: {
        'application/json': '{"statusCode": 200}',
      },
    }),
    {
      methodResponses: [
        {
          statusCode: '200',
        },
      ],
    },
  );

  new PlatformWaf(stack, 'PlatformWaf', {
    api,
  });

  return Template.fromStack(stack);
}

describe('PlatformSpa', () => {
  test('keeps OAC, CSP headers, SPA rewrite, and cache separation intact', () => {
    const template = synthSpa();

    template.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);
    template.resourceCountIs('AWS::CloudFront::Function', 1);

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
        DefaultCacheBehavior: Match.objectLike({
          CachePolicyId: cloudfront.CachePolicy.CACHING_DISABLED.cachePolicyId,
          FunctionAssociations: Match.arrayWith([
            Match.objectLike({
              EventType: 'viewer-request',
              FunctionARN: Match.anyValue(),
            }),
          ]),
        }),
        CacheBehaviors: Match.arrayWith([
          Match.objectLike({
            PathPattern: 'assets/*',
            CachePolicyId: cloudfront.CachePolicy.CACHING_OPTIMIZED.cachePolicyId,
          }),
        ]),
      }),
    });

    const distributions = template.findResources('AWS::CloudFront::Distribution');
    const [distribution] = Object.values(distributions) as Array<{
      Properties?: { DistributionConfig?: Record<string, unknown> };
    }>;

    expect(distribution.Properties?.DistributionConfig).not.toHaveProperty('WebACLId');
    expect(distribution.Properties?.DistributionConfig).not.toHaveProperty('CustomErrorResponses');
  });

  test('preserves custom-domain TLS posture when domain inputs are provided', () => {
    const template = synthSpa({
      spaDomainName: 'spa.example.com',
      spaCertificateArn:
        'arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111',
    });

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Aliases: ['spa.example.com'],
        ViewerCertificate: Match.objectLike({
          AcmCertificateArn:
            'arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111',
          MinimumProtocolVersion: 'TLSv1.2_2021',
          SslSupportMethod: 'sni-only',
        }),
      }),
    });
  });
});

describe('PlatformApi', () => {
  test('keeps authorizer-backed usage plans, canonical routes, and CORS responses', () => {
    const template = synthApi();

    template.hasResourceProperties('AWS::ApiGateway::RestApi', {
      ApiKeySourceType: 'AUTHORIZER',
    });
    template.resourceCountIs('AWS::ApiGateway::UsagePlan', 3);

    template.hasResourceProperties('AWS::ApiGateway::GatewayResponse', {
      ResponseType: 'DEFAULT_4XX',
      ResponseParameters: Match.objectLike({
        'gatewayresponse.header.gatewayresponses.header.Access-Control-Allow-Origin':
          "'https://spa.example.com'",
      }),
    });

    const resources = template.findResources('AWS::ApiGateway::Resource');
    const pathParts = Object.values(resources).map((resource) => {
      const properties = (resource as { Properties?: { PathPart?: string } }).Properties;
      return properties?.PathPart;
    });

    expect(pathParts).toEqual(
      expect.arrayContaining(['agents', '{agentName}', 'invoke', 'jobs', '{jobId}', 'webhooks']),
    );
  });

  test('adds the documented TLS 1.2 regional custom domain when configured', () => {
    const template = synthApi({
      apiDomainName: 'api.example.com',
      apiCertificateArn:
        'arn:aws:acm:eu-west-2:123456789012:certificate/22222222-2222-2222-2222-222222222222',
    });

    template.hasResourceProperties('AWS::ApiGateway::DomainName', {
      DomainName: 'api.example.com',
      EndpointConfiguration: {
        Types: ['REGIONAL'],
      },
      SecurityPolicy: 'TLS_1_2',
      RegionalCertificateArn:
        'arn:aws:acm:eu-west-2:123456789012:certificate/22222222-2222-2222-2222-222222222222',
    });
  });
});

describe('PlatformWaf', () => {
  test('keeps the API WebACL rules and association intact', () => {
    const template = synthWaf();

    template.hasResourceProperties('AWS::WAFv2::WebACL', {
      Scope: 'REGIONAL',
      Rules: Match.arrayWith([
        Match.objectLike({
          Name: 'AWSManagedRulesCommonRuleSet',
        }),
        Match.objectLike({
          Name: 'UkIpRateLimit',
          Statement: Match.objectLike({
            RateBasedStatement: Match.objectLike({
              AggregateKeyType: 'IP',
              Limit: 2000,
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
});

describe('PlatformGateway', () => {
  test('keeps MCP gateway, Cedar policy, and non-wildcard policy-engine access', () => {
    const template = synthGateway();

    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      AuthorizerType: 'AWS_IAM',
      ProtocolType: 'MCP',
      PolicyEngineConfiguration: Match.objectLike({
        Mode: 'LOG_ONLY',
      }),
      InterceptorConfigurations: Match.arrayWith([
        Match.objectLike({
          InterceptionPoints: ['REQUEST'],
          InputConfiguration: {
            PassRequestHeaders: true,
          },
        }),
        Match.objectLike({
          InterceptionPoints: ['RESPONSE'],
          InputConfiguration: {
            PassRequestHeaders: true,
          },
        }),
      ]),
    });

    template.hasResourceProperties('AWS::BedrockAgentCore::PolicyEngine', {
      Name: 'PlatformGatewayPolicyEngineLOG_ONLY',
    });
    template.hasResourceProperties('AWS::BedrockAgentCore::Policy', {
      Name: 'PlatformGatewayAllowAllLOG_ONLY',
      ValidationMode: 'FAIL_ON_ANY_FINDINGS',
    });

    const policies = template.findResources('AWS::IAM::Policy');
    const allStatements = Object.values(policies).flatMap((resource) => {
      const properties = (
        resource as {
          Properties?: { PolicyDocument?: { Statement?: Array<Record<string, unknown>> } };
        }
      ).Properties;
      return properties?.PolicyDocument?.Statement ?? [];
    });

    const gatewayPolicyStatement = allStatements.find((statement) => {
      const actions = statement.Action;
      return Array.isArray(actions) && actions.includes('bedrock-agentcore:GetPolicyEngine');
    });

    expect(gatewayPolicyStatement).toBeDefined();
    expect(gatewayPolicyStatement?.Resource).not.toBe('*');
  });

  test('switches the policy engine mode to ENFORCE for prod posture', () => {
    const template = synthGateway('ENFORCE');

    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      PolicyEngineConfiguration: Match.objectLike({
        Mode: 'ENFORCE',
      }),
    });
  });
});
