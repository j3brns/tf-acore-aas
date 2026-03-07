/**
 * AgentCoreStack — AgentCore Runtime configuration and cross-region wiring.
 *
 * Runtime configuration: eu-west-1 (Dublin) — see ADR-009.
 * Memory template: provisioned per-tenant in TenantStack.
 * Identity configuration for Entra JWKS.
 * Observability metric stream eu-west-1 → eu-west-2.
 *
 * Implemented in TASK-024.
 * ADRs: ADR-001, ADR-009
 */
import * as cdk from 'aws-cdk-lib';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';

export interface AgentCoreStackProps extends cdk.StackProps {
  readonly homeRegion: string;
}

export class AgentCoreStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const envName = this.requiredContext('env');
    const entraTenantId = this.optionalContext('entraTenantId') ?? 'common';
    const entraAudience = this.optionalContext('entraAudience') ?? 'platform-api';
    const runtimeRegion = cdk.Stack.of(this).region;

    if (!cdk.Token.isUnresolved(runtimeRegion) && runtimeRegion !== 'eu-west-1') {
      throw new Error('AgentCoreStack must be deployed in eu-west-1');
    }

    const runtimeExecutionRoleArn = new cdk.CfnParameter(this, 'RuntimeExecutionRoleArn', {
      type: 'String',
      description: 'IAM role ARN used by AWS::BedrockAgentCore::Runtime',
    });
    const runtimeArtifactBucketName = new cdk.CfnParameter(this, 'RuntimeArtifactBucketName', {
      type: 'String',
      description: 'S3 bucket containing zipped AgentCore runtime artifacts',
    });
    const runtimeArtifactPrefix = new cdk.CfnParameter(this, 'RuntimeArtifactPrefix', {
      type: 'String',
      description: 'S3 key prefix for the runtime artifact object',
    });
    const metricStreamFirehoseArn = new cdk.CfnParameter(this, 'AgentCoreMetricStreamFirehoseArn', {
      type: 'String',
      description:
        'Firehose delivery stream ARN in eu-west-1 that forwards AgentCore metrics to eu-west-2 observability sinks',
    });
    const metricStreamRoleArn = new cdk.CfnParameter(this, 'AgentCoreMetricStreamRoleArn', {
      type: 'String',
      description:
        'IAM role ARN assumed by CloudWatch metric streams for firehose:PutRecord and firehose:PutRecordBatch',
    });

    const runtimeName = this.runtimeName(envName);
    const runtimeEndpointName = this.runtimeEndpointName(envName);
    const entraJwksUrl = this.resolveEntraJwksUrl(entraTenantId);

    const runtime = new cdk.CfnResource(this, 'AgentCoreRuntime', {
      type: 'AWS::BedrockAgentCore::Runtime',
      properties: {
        AgentRuntimeName: runtimeName,
        Description: `Primary AgentCore runtime for ${envName} (${runtimeRegion})`,
        RoleArn: runtimeExecutionRoleArn.valueAsString,
        AgentRuntimeArtifact: {
          CodeConfiguration: {
            Runtime: 'PYTHON_3_12',
            EntryPoint: ['handler.py'],
            Code: {
              S3: {
                Bucket: runtimeArtifactBucketName.valueAsString,
                Prefix: runtimeArtifactPrefix.valueAsString,
              },
            },
          },
        },
        NetworkConfiguration: {
          NetworkMode: 'PUBLIC',
        },
        ProtocolConfiguration: 'HTTP',
        AuthorizerConfiguration: {
          CustomJWTAuthorizer: {
            DiscoveryUrl: this.resolveEntraDiscoveryUrl(entraTenantId),
            AllowedAudience: [entraAudience],
          },
        },
        RequestHeaderConfiguration: {
          RequestHeaderAllowlist: ['authorization', 'x-tenant-id', 'x-app-id'],
        },
        EnvironmentVariables: {
          HOME_REGION: props.homeRegion,
          RUNTIME_REGION: runtimeRegion,
        },
        Tags: {
          component: 'agentcore-runtime',
          environment: envName,
          homeRegion: props.homeRegion,
        },
      },
    });

    const runtimeEndpoint = new cdk.CfnResource(this, 'AgentCoreRuntimeEndpoint', {
      type: 'AWS::BedrockAgentCore::RuntimeEndpoint',
      properties: {
        Name: runtimeEndpointName,
        Description: `Live endpoint for ${runtimeName}`,
        AgentRuntimeId: runtime.getAtt('AgentRuntimeId').toString(),
        AgentRuntimeVersion: runtime.getAtt('AgentRuntimeVersion').toString(),
        Tags: {
          component: 'agentcore-runtime-endpoint',
          environment: envName,
        },
      },
    });
    runtimeEndpoint.addDependency(runtime);

    const memoryTemplateParameter = new ssm.StringParameter(this, 'TenantMemoryTemplateParameter', {
      parameterName: '/platform/agentcore/memory/template/default',
      description:
        'Template used by TenantStack when provisioning per-tenant AWS::BedrockAgentCore::Memory resources',
      stringValue: JSON.stringify({
        provisionedBy: 'TenantStack',
        eventExpiryDurationDays: 90,
        strategy: 'SEMANTIC',
        namespaceTemplate: 'tenant/{tenantId}',
        descriptionTemplate: 'Per-tenant AgentCore memory',
      }),
      tier: ssm.ParameterTier.STANDARD,
    });

    new ssm.StringParameter(this, 'EntraJwksUrlParameter', {
      parameterName: '/platform/auth/jwks-url',
      description: 'Entra JWKS URL consumed by platform identity and runtime integrations',
      stringValue: entraJwksUrl,
      tier: ssm.ParameterTier.STANDARD,
    });

    const metricStream = new cdk.CfnResource(this, 'AgentCoreMetricStream', {
      type: 'AWS::CloudWatch::MetricStream',
      properties: {
        Name: `${this.stackName}-agentcore-metrics`,
        OutputFormat: 'json',
        FirehoseArn: metricStreamFirehoseArn.valueAsString,
        RoleArn: metricStreamRoleArn.valueAsString,
        IncludeFilters: [
          {
            Namespace: 'AWS/BedrockAgentCore',
          },
        ],
        Tags: [
          {
            Key: 'component',
            Value: 'agentcore-observability',
          },
          {
            Key: 'source-region',
            Value: runtimeRegion,
          },
          {
            Key: 'destination-region',
            Value: props.homeRegion,
          },
        ],
      },
    });

    new cdk.CfnOutput(this, 'AgentCoreRuntimeRegion', {
      value: runtimeRegion,
      description: 'Runtime compute region for AgentCore execution',
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeName', {
      value: runtimeName,
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeEndpointName', {
      value: runtimeEndpointName,
    });
    new cdk.CfnOutput(this, 'TenantMemoryTemplateParameterName', {
      value: memoryTemplateParameter.parameterName,
    });
    new cdk.CfnOutput(this, 'AgentCoreMetricStreamName', {
      value: metricStream.ref,
    });
    new cdk.CfnOutput(this, 'EntraJwksUrl', {
      value: entraJwksUrl,
    });
  }

  private requiredContext(name: string): string {
    const value = this.node.tryGetContext(name);
    if (typeof value !== 'string' || value.trim() === '') {
      throw new Error(`CDK context "${name}" is required`);
    }
    return value;
  }

  private optionalContext(name: string): string | undefined {
    const value = this.node.tryGetContext(name);
    if (typeof value !== 'string' || value.trim() === '') {
      return undefined;
    }
    return value;
  }

  private resolveEntraJwksUrl(entraTenantId: string): string {
    return `https://login.microsoftonline.com/${entraTenantId}/discovery/v2.0/keys`;
  }

  private resolveEntraDiscoveryUrl(entraTenantId: string): string {
    return `https://login.microsoftonline.com/${entraTenantId}/v2.0/.well-known/openid-configuration`;
  }

  private runtimeName(envName: string): string {
    const clean = envName.replace(/[^a-zA-Z0-9]/g, '');
    return `Platform${clean}Runtime`;
  }

  private runtimeEndpointName(envName: string): string {
    const clean = envName.replace(/[^a-zA-Z0-9]/g, '');
    return `Platform${clean}Endpoint`;
  }
}
