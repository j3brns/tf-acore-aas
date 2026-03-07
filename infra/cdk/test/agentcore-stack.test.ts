import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AgentCoreStack } from '../lib/agentcore-stack';

describe('AgentCoreStack (TASK-024)', () => {
  const synthTemplate = () => {
    const app = new cdk.App({
      context: {
        env: 'dev',
        entraTenantId: '00000000-0000-0000-0000-000000000000',
        entraAudience: 'api://platform-dev',
      },
    });

    const stack = new AgentCoreStack(app, 'platform-agentcore-dev', {
      env: { region: 'eu-west-1' },
      homeRegion: 'eu-west-2',
    });

    return Template.fromStack(stack);
  };

  const template = synthTemplate();

  test('creates runtime and endpoint with Entra JWT authorizer wiring', () => {
    template.resourceCountIs('AWS::BedrockAgentCore::Runtime', 1);
    template.resourceCountIs('AWS::BedrockAgentCore::RuntimeEndpoint', 1);

    template.hasResourceProperties('AWS::BedrockAgentCore::Runtime', {
      AgentRuntimeName: 'PlatformdevRuntime',
      NetworkConfiguration: {
        NetworkMode: 'PUBLIC',
      },
      ProtocolConfiguration: 'HTTP',
      AuthorizerConfiguration: {
        CustomJWTAuthorizer: {
          DiscoveryUrl:
            'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0/.well-known/openid-configuration',
          AllowedAudience: ['api://platform-dev'],
        },
      },
      RequestHeaderConfiguration: {
        RequestHeaderAllowlist: ['authorization', 'x-tenant-id', 'x-app-id'],
      },
    });
  });

  test('creates SSM parameters for memory template and Entra JWKS URL', () => {
    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/agentcore/memory/template/default',
      Type: 'String',
      Value: Match.serializedJson(
        Match.objectLike({
          provisionedBy: 'TenantStack',
          eventExpiryDurationDays: 90,
          strategy: 'SEMANTIC',
        }),
      ),
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/auth/jwks-url',
      Type: 'String',
      Value: 'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/discovery/v2.0/keys',
    });
  });

  test('creates AgentCore metric stream scoped to AgentCore namespace', () => {
    template.resourceCountIs('AWS::CloudWatch::MetricStream', 1);
    template.hasResourceProperties('AWS::CloudWatch::MetricStream', {
      OutputFormat: 'json',
      IncludeFilters: [
        {
          Namespace: 'AWS/BedrockAgentCore',
        },
      ],
      FirehoseArn: {
        Ref: 'AgentCoreMetricStreamFirehoseArn',
      },
      RoleArn: {
        Ref: 'AgentCoreMetricStreamRoleArn',
      },
    });
  });

  test('exports runtime region and memory template parameter name', () => {
    template.hasOutput('AgentCoreRuntimeRegion', {
      Value: 'eu-west-1',
    });

    template.hasOutput('TenantMemoryTemplateParameterName', {
      Value: Match.anyValue(),
    });
  });
});
