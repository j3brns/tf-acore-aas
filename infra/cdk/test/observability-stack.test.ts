import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { ObservabilityStack } from '../lib/observability-stack';
import { PlatformStack } from '../lib/platform-stack';

describe('ObservabilityStack (TASK-026)', () => {
  const synthStack = () => {
    const app = new cdk.App({
      context: {
        env: 'dev',
        entraTenantId: '00000000-0000-0000-0000-000000000000',
        entraAudience: 'platform-api',
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

    const platformStack = new PlatformStack(app, 'PlatformStack', {
      env,
      vpc: mockVpc,
      tenantDataKey: mockKey,
      platformConfigKey: mockKey,
    });

    const observabilityStack = new ObservabilityStack(app, 'ObservabilityStack', {
      env,
      api: platformStack.api,
      apiWebAcl: platformStack.apiWebAcl,
      spaDistribution: platformStack.spaDistribution,
      bridgeFn: platformStack.bridgeFn,
      bffFn: platformStack.bffFn,
      authoriserFn: platformStack.authoriserFn,
      requestInterceptorFn: platformStack.requestInterceptorFn,
      responseInterceptorFn: platformStack.responseInterceptorFn,
      tenantsTable: platformStack.tenantsTable,
      agentsTable: platformStack.agentsTable,
      invocationsTable: platformStack.invocationsTable,
      jobsTable: platformStack.jobsTable,
      sessionsTable: platformStack.sessionsTable,
      toolsTable: platformStack.toolsTable,
      opsLocksTable: platformStack.opsLocksTable,
      billingFn: platformStack.billingFn,
      dlqs: platformStack.dlqs,
    });

    return Template.fromStack(observabilityStack);
  };

  test('creates a CloudWatch Dashboard', () => {
    const template = synthStack();
    template.resourceCountIs('AWS::CloudWatch::Dashboard', 1);
    template.hasResourceProperties('AWS::CloudWatch::Dashboard', {
      DashboardName: Match.stringLikeRegexp('platform-ops-ObservabilityStack'),
    });
  });

  test('creates FM-1 Runtime Region Unavailable alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-1-RuntimeRegionUnavailable',
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      Threshold: 5,
    });
  });

  test('creates FM-2 Authoriser Cold Start alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-2-AuthoriserColdStartSpike',
      Threshold: 500,
    });
  });

  test('creates FM-3 Secrets Manager Throttling alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-3-SecretsManagerThrottling',
      MetricName: 'SecretsManagerCacheMissCount',
    });
  });

  test('creates FM-4 DynamoDB Hot Partition alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-4-DynamoDbHotPartition',
      MetricName: 'ThrottledRequests',
    });
  });

  test('creates FM-5 Bridge Timeout alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-5-BridgeTimeout',
    });
  });

  test('creates FM-6 Interceptor Retry Storm alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-6-InterceptorRetryStorm',
    });
  });

  test('creates FM-7 AgentCore Memory Degraded alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-7-AgentCoreMemoryDegraded',
      MetricName: 'DegradedMode',
    });
  });

  test('creates FM-8 Usage Plan Quota Exhausted alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-8-UsagePlanQuotaExhausted',
    });
  });

  test('creates FM-9 DLQ Arrival alarms for each DLQ', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: Match.stringLikeRegexp('ObservabilityStack-FM-9-DLQ-Arrival-'),
    });
  });

  test('creates FM-10 Billing Lambda Failure alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-10-BillingLambdaFailure',
    });
  });

  test('creates Cross-Region Observability Sink', () => {
    const template = synthStack();
    template.resourceCountIs('AWS::Oam::Sink', 1);
    template.hasResourceProperties('AWS::Oam::Sink', {
      Name: 'PlatformObservabilitySink',
    });
  });

  test('exposes sink-only observability topology and no OAM links yet', () => {
    const template = synthStack();
    template.resourceCountIs('AWS::Oam::Link', 0);
    template.hasOutput('CrossRegionObservabilityTopology', {
      Value: 'SINK_ONLY_NO_OAM_LINKS',
    });
  });

  test('creates WAF and CloudFront alarms', () => {
    const template = synthStack();
    const api5xxAlarmName = ['ObservabilityStack', 'Platform', 'API', '5xx', 'Errors'].join('-');
    const wafBlockedAlarmName = [
      'ObservabilityStack',
      'Platform',
      'WAF',
      'Blocked',
      'Requests',
    ].join('-');

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: api5xxAlarmName,
    });
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: wafBlockedAlarmName,
    });
  });

  test('every documented FM (1-10) has a corresponding alarm', () => {
    const template = synthStack();
    const documentedFMs = [
      'FM-1-RuntimeRegionUnavailable',
      'FM-2-AuthoriserColdStartSpike',
      'FM-3-SecretsManagerThrottling',
      'FM-4-DynamoDbHotPartition',
      'FM-5-BridgeTimeout',
      'FM-6-InterceptorRetryStorm',
      'FM-7-AgentCoreMemoryDegraded',
      'FM-8-UsagePlanQuotaExhausted',
      'FM-9-DLQ-Arrival-',
      'FM-10-BillingLambdaFailure',
    ];
    for (const fm of documentedFMs) {
      template.hasResourceProperties('AWS::CloudWatch::Alarm', {
        AlarmName: Match.stringLikeRegexp(`ObservabilityStack-${fm}`),
      });
    }
  });
});
