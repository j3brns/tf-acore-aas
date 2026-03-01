import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { TenantStack } from '../lib/tenant-stack';

describe('TenantStack (TASK-025)', () => {
  const synthTemplate = (context: Record<string, string>) => {
    const app = new cdk.App({ context });
    const stack = new TenantStack(app, 'platform-tenant-test', {
      env: { region: 'eu-west-2' },
    });
    return Template.fromStack(stack);
  };

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
                'dynamodb:LeadingKeys': Match.arrayWith(['TENANT#t-test123*', 'JOB#*']),
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

  test('creates AgentCore Memory Store with service role and SUMMARY strategy', () => {
    const template = synthTemplate(defaultContext);

    template.hasResourceProperties('AWS::BedrockAgentCore::Memory', {
      Name: 'platform-memory-t-test123',
      EncryptionKeyArn: Match.anyValue(),
      MemoryExecutionRoleArn: Match.anyValue(),
      MemoryStrategies: Match.arrayWith([
        Match.objectLike({ StrategyType: 'SUMMARY' }),
        Match.objectLike({ StrategyType: 'USER_PREFERENCES' }),
      ]),
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

  test('fails if context is missing', () => {
    const app = new cdk.App();
    expect(() => {
      new TenantStack(app, 'platform-tenant-fail', {
        env: { region: 'eu-west-2' },
      });
    }).toThrow();
  });
});
