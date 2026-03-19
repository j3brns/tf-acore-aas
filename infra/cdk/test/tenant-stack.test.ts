import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE } from '../lib/agentcore-memory-template';
import { TenantStack } from '../lib/tenant-stack';

describe('TenantStack (TASK-025)', () => {
  const synthTemplate = (
    context: Record<string, string>,
    authorizedRuntimeRegions = ['eu-west-1', 'eu-central-1'],
  ) => {
    const app = new cdk.App({ context });
    const stack = new TenantStack(app, 'platform-tenant-test', {
      env: { region: 'eu-west-2' },
      authorizedRuntimeRegions,
    });
    return Template.fromStack(stack);
  };

  const runtimeAccessStatement = (template: Template) => {
    const policies = template.findResources('AWS::IAM::Policy') as Record<
      string,
      { Properties?: { PolicyDocument?: { Statement?: Array<Record<string, unknown>> } } }
    >;
    const statement = Object.values(policies)
      .flatMap((policy) => policy.Properties?.PolicyDocument?.Statement ?? [])
      .find((candidate) => candidate.Sid === 'AgentCoreRuntimeAccess');

    expect(statement).toBeDefined();
    return statement as { Action: string[]; Resource: string[]; Sid: string };
  };

  const asArray = (value: string | string[]) => (Array.isArray(value) ? value : [value]);

  const defaultContext = {
    env: 'dev',
    tenantId: 't-test123',
    tier: 'basic',
    accountId: '123456789012',
  };

  test('creates tenant execution role with scoped DynamoDB and S3 permissions', () => {
    const template = synthTemplate(defaultContext);

    template.hasResourceProperties('AWS::IAM::Role', {
      RoleName: 'platform-tenant-t-test123-execution-role',
      AssumeRolePolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 'sts:AssumeRole',
            Principal: {
              AWS: Match.anyValue(),
            },
          }),
        ]),
      }),
    });

    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Sid: 'TenantDynamoDBAccess',
            Action: Match.arrayWith(['dynamodb:GetItem', 'dynamodb:Query']),
            Resource: Match.anyValue(),
            Condition: Match.objectLike({
              'ForAllValues:StringLike': {
                'dynamodb:LeadingKeys': ['TENANT#t-test123*'],
              },
            }),
          }),
          Match.objectLike({
            Sid: 'TenantS3Access',
            Action: Match.arrayWith(['s3:GetObject', 's3:PutObject']),
            Resource: Match.anyValue(),
          }),
        ]),
      }),
    });
  });

  test('creates AgentCore Memory Store from the canonical semantic memory template', () => {
    const template = synthTemplate(defaultContext);

    template.hasResourceProperties('AWS::BedrockAgentCore::Memory', {
      Name: 'platform-memory-t-test123',
      Description: 'Per-tenant AgentCore memory for t-test123',
      EncryptionKeyArn: Match.anyValue(),
      EventExpiryDuration: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.eventExpiryDurationDays,
      MemoryExecutionRoleArn: Match.anyValue(),
      MemoryStrategies: [
        Match.objectLike({
          SemanticMemoryStrategy: Match.objectLike({
            Name: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.semanticMemory.strategyNameTemplate,
            Description: 'Per-tenant AgentCore memory for t-test123',
            Namespaces: ['tenant/t-test123'],
            Type: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.semanticMemory.strategy,
          }),
        }),
      ],
    });

    template.hasResourceProperties('AWS::IAM::Role', {
      Description: 'Service role for tenant t-test123 memory store',
      AssumeRolePolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 'sts:AssumeRole',
            Principal: {
              Service: 'bedrock-agentcore.amazonaws.com',
            },
          }),
        ]),
      }),
    });
  });

  test('creates API key and associates it with usage plan', () => {
    const template = synthTemplate(defaultContext);

    template.hasResourceProperties('AWS::ApiGateway::ApiKey', {
      Name: 'platform-tenant-t-test123',
      Enabled: true,
    });

    template.hasResourceProperties('AWS::ApiGateway::UsagePlanKey', {
      KeyId: Match.anyValue(),
      UsagePlanId: Match.anyValue(),
      KeyType: 'API_KEY',
    });
  });

  test('creates SSM parameters for tenant configuration', () => {
    const template = synthTemplate(defaultContext);

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/tenants/t-test123/execution-role-arn',
      Type: 'String',
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/tenants/t-test123/memory-store-arn',
      Type: 'String',
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/tenants/t-test123/api-key-id',
      Type: 'String',
    });
  });

  test('limits runtime invocation permissions to the approved primary and failover regions', () => {
    const template = synthTemplate(defaultContext);
    const statement = runtimeAccessStatement(template);

    expect(asArray(statement.Action)).toEqual(['bedrock-agentcore:InvokeRuntime']);
    expect(asArray(statement.Resource)).toEqual([
      'arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/*',
      'arn:aws:bedrock-agentcore:eu-central-1:123456789012:runtime/*',
    ]);
    expect(asArray(statement.Resource)).not.toContain(
      'arn:aws:bedrock-agentcore:*:123456789012:runtime/*',
    );
  });

  test('supports explicit runtime allowlists without widening beyond the configured regions', () => {
    const template = synthTemplate(defaultContext, ['eu-west-1']);
    const statement = runtimeAccessStatement(template);

    expect(asArray(statement.Resource)).toEqual([
      'arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/*',
    ]);
  });

  test('creates per-tenant CloudWatch dashboard and budget alarm', () => {
    const template = synthTemplate({
      ...defaultContext,
      monthlyBudgetUsd: '500',
    });

    template.hasResourceProperties('AWS::CloudWatch::Dashboard', {
      DashboardName: 'platform-tenant-t-test123',
      DashboardBody: Match.anyValue(),
    });

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'platform-tenant-t-test123-budget-exceeded',
      AlarmDescription: 'Monthly budget exceeded for tenant t-test123 (Limit: $500)',
      Threshold: 500,
      ComparisonOperator: 'GreaterThanThreshold',
      MetricName: 'MonthlyCost',
      Namespace: 'Platform/Billing',
      Dimensions: Match.arrayWith([
        Match.objectLike({ Name: 'TenantId', Value: 't-test123' }),
        Match.objectLike({ Name: 'Tier', Value: 'basic' }),
      ]),
    });
  });

  test('uses default budget if context is missing', () => {
    const template = synthTemplate(defaultContext);

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'platform-tenant-t-test123-budget-exceeded',
      Threshold: 100, // Default in code
    });
  });

  test('fails if context is missing', () => {
    const app = new cdk.App();
    expect(() => {
      new TenantStack(app, 'platform-tenant-fail', {
        env: { region: 'eu-west-2' },
      });
    }).toThrow();
  });

  test('keeps tenant memory expiry aligned with the published AgentCore default contract', () => {
    const template = synthTemplate(defaultContext);

    template.hasResourceProperties('AWS::BedrockAgentCore::Memory', {
      EventExpiryDuration: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.eventExpiryDurationDays,
      MemoryStrategies: [
        Match.objectLike({
          SemanticMemoryStrategy: Match.objectLike({
            Type: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.semanticMemory.strategy,
          }),
        }),
      ],
    });
  });
});
