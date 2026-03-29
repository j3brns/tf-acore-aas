import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AgentCoreStack } from '../lib/agentcore-stack';
import {
  DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE,
  TENANT_MEMORY_TEMPLATE_PARAMETER_NAME,
} from '../lib/agentcore-memory-template';

describe('AgentCoreStack (TASK-024)', () => {
  const synthTemplate = () => {
    const app = new cdk.App({
      context: {
        env: 'dev',
        entraTenantId: '00000000-0000-0000-0000-000000000000',
        entraAudience: 'api://platform-dev',
      },
    });

    const homeStack = new cdk.Stack(app, 'home-stack', {
      env: { region: 'eu-west-2' },
    });
    const metricsBucket = new s3.Bucket(homeStack, 'TestMetricsBucket');

    const stack = new AgentCoreStack(app, 'platform-agentcore-dev', {
      env: { region: 'eu-west-1' },
      homeRegion: 'eu-west-2',
      runtimeNetworkPosture: 'PUBLIC_WITH_COMPENSATING_CONTROLS',
      metricsBucketName: 'platform-metrics-dev-eu-west-2',
      metricsBucketArn: 'arn:aws:s3:::platform-metrics-dev-eu-west-2',
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

  test('records the public runtime posture as an explicit, reviewable exception', () => {
    template.hasResource('AWS::BedrockAgentCore::Runtime', {
      Metadata: {
        RuntimeNetworkPosture: {
          Decision: 'PUBLIC_WITH_COMPENSATING_CONTROLS',
          Justification: 'ADR-009_NO_RUNTIME_REGION_VPC',
          RevisitTrigger: Match.stringLikeRegexp('NetworkMode=VPC'),
        },
      },
      Properties: {
        NetworkConfiguration: {
          NetworkMode: 'PUBLIC',
        },
        Tags: Match.objectLike({
          networkMode: 'PUBLIC',
          networkPosture: 'PUBLIC_WITH_COMPENSATING_CONTROLS',
        }),
      },
    });
  });

  test('creates SSM parameters for memory template and Entra JWKS URL', () => {
    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: TENANT_MEMORY_TEMPLATE_PARAMETER_NAME,
      Type: 'String',
      Value: Match.serializedJson(
        Match.objectLike({
          provisionedBy: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.provisionedBy,
          eventExpiryDurationDays: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.eventExpiryDurationDays,
          semanticMemory: Match.objectLike({
            strategy: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.semanticMemory.strategy,
            namespaceTemplate:
              DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.semanticMemory.namespaceTemplate,
          }),
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
      FirehoseArn: Match.anyValue(),
      RoleArn: Match.anyValue(),
    });

    template.resourceCountIs('AWS::KinesisFirehose::DeliveryStream', 1);
    template.hasResourceProperties('AWS::KinesisFirehose::DeliveryStream', {
      DeliveryStreamType: 'DirectPut',
      S3DestinationConfiguration: {
        CompressionFormat: 'GZIP',
        Prefix: 'metrics/',
      },
    });

    // Verify roles
    template.hasResourceProperties('AWS::IAM::Role', {
      AssumeRolePolicyDocument: {
        Statement: [
          {
            Action: 'sts:AssumeRole',
            Effect: 'Allow',
            Principal: {
              Service: 'streams.metrics.cloudwatch.amazonaws.com',
            },
          },
        ],
      },
    });
    template.hasResourceProperties('AWS::IAM::Role', {
      AssumeRolePolicyDocument: {
        Statement: [
          {
            Action: 'sts:AssumeRole',
            Effect: 'Allow',
            Principal: {
              Service: 'firehose.amazonaws.com',
            },
          },
        ],
      },
    });
  });

  test('exports runtime region and memory template parameter name', () => {
    template.hasOutput('AgentCoreRuntimeRegion', {
      Value: 'eu-west-1',
    });
    template.hasOutput('AgentCoreRuntimeNetworkMode', {
      Value: 'PUBLIC',
    });
    template.hasOutput('AgentCoreRuntimeNetworkPostureDecision', {
      Value: 'PUBLIC_WITH_COMPENSATING_CONTROLS',
    });

    template.hasOutput('TenantMemoryTemplateParameterName', {
      Value: Match.anyValue(),
    });
  });
});
