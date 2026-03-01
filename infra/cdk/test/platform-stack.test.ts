import * as cdk from 'aws-cdk-lib';
import * as kms from 'aws-cdk-lib/aws-kms';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { PlatformStack } from '../lib/platform-stack';

describe('PlatformStack (TASK-023)', () => {
  const synthTemplate = () => {
    const app = new cdk.App();
    const env = { account: '123456789012', region: 'eu-west-2' };
    const identityStack = new cdk.Stack(app, 'IdentityStack', { env });
    const mockKey = new kms.Key(identityStack, 'MockKey');
    const stack = new PlatformStack(app, 'platform-core-dev', {
      env,
      tenantDataKey: mockKey,
      platformConfigKey: mockKey,
    });
    return Template.fromStack(stack);
  };
  const template = synthTemplate();

  test('creates all required DynamoDB tables with PITR and encryption', () => {
    template.resourceCountIs('AWS::DynamoDB::Table', 7);

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
  });

  test('creates REST API with authorizer-backed API key source and usage plans', () => {
    template.hasResourceProperties('AWS::ApiGateway::RestApi', {
      ApiKeySourceType: 'AUTHORIZER', // pragma: allowlist secret
    });

    template.resourceCountIs('AWS::ApiGateway::UsagePlan', 3);

    template.hasResourceProperties('AWS::Lambda::Alias', {
      Name: 'live',
      ProvisionedConcurrencyConfig: {
        ProvisionedConcurrentExecutions: 10,
      },
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
        CustomHeadersConfig: Match.objectLike({
          Items: Match.arrayWith([
            Match.objectLike({
              Header: 'Content-Security-Policy',
              Override: true,
            }),
          ]),
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

  test('creates AgentCore Gateway with request and response interceptor wiring', () => {
    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      AuthorizerType: 'AWS_IAM',
      ProtocolType: 'MCP',
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
});
