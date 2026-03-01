import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as oam from 'aws-cdk-lib/aws-oam';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';

export interface ObservabilityStackProps extends cdk.StackProps {
  readonly api: apigateway.RestApi;
  readonly apiWebAcl: wafv2.CfnWebACL;
  readonly spaDistribution: cloudfront.CfnDistribution;
  readonly bridgeFn: lambda.IFunction;
  readonly bffFn: lambda.IFunction;
  readonly authoriserFn: lambda.IFunction;
  readonly requestInterceptorFn: lambda.IFunction;
  readonly responseInterceptorFn: lambda.IFunction;
  readonly tenantsTable: dynamodb.ITable;
  readonly agentsTable: dynamodb.ITable;
  readonly invocationsTable: dynamodb.ITable;
  readonly jobsTable: dynamodb.ITable;
  readonly sessionsTable: dynamodb.ITable;
  readonly toolsTable: dynamodb.ITable;
  readonly opsLocksTable: dynamodb.ITable;
  readonly dlqs: Record<string, sqs.IQueue>;
}

export class ObservabilityStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);

    // --- 1. Platform Operations Dashboard ---

    const dashboard = new cloudwatch.Dashboard(this, 'PlatformOpsDashboard', {
      dashboardName: `platform-ops-${this.stackName}`,
    });

    // Lambda Health Row
    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '# Lambda Health',
        width: 24,
        height: 1,
      }),
      this.createLambdaMetricWidget('Bridge', props.bridgeFn),
      this.createLambdaMetricWidget('Authoriser', props.authoriserFn),
      this.createLambdaMetricWidget('BFF', props.bffFn),
    );

    // API Health Row
    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '# API Health',
        width: 24,
        height: 1,
      }),
      new cloudwatch.GraphWidget({
        title: 'API Request Count',
        left: [props.api.metricCount()],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'API Latency (p99)',
        left: [props.api.metricLatency({ statistic: 'p99' })],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'API 4xx/5xx Errors',
        left: [props.api.metricClientError(), props.api.metricServerError()],
        width: 8,
      }),
    );

    // DynamoDB Health Row
    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '# DynamoDB Health',
        width: 24,
        height: 1,
      }),
      this.createTableMetricWidget('Invocations', props.invocationsTable),
      this.createTableMetricWidget('Tenants', props.tenantsTable),
      this.createTableMetricWidget('Agents', props.agentsTable),
    );

    // WAF & CloudFront Row
    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '# Edge & Security Health',
        width: 24,
        height: 1,
      }),
      new cloudwatch.GraphWidget({
        title: 'WAF Request Monitoring',
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/WAFV2',
            metricName: 'AllowedRequests',
            dimensionsMap: {
              WebACL: props.apiWebAcl.name!,
              Region: this.region,
              Rule: 'ALL',
            },
            statistic: 'Sum',
          }),
          new cloudwatch.Metric({
            namespace: 'AWS/WAFV2',
            metricName: 'BlockedRequests',
            dimensionsMap: {
              WebACL: props.apiWebAcl.name!,
              Region: this.region,
              Rule: 'ALL',
            },
            statistic: 'Sum',
          }),
        ],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'CloudFront SPA Performance',
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/CloudFront',
            metricName: 'Requests',
            dimensionsMap: {
              DistributionId: props.spaDistribution.ref,
              Region: 'Global',
            },
            statistic: 'Sum',
          }),
        ],
        right: [
          new cloudwatch.Metric({
            namespace: 'AWS/CloudFront',
            metricName: 'TotalErrorRate',
            dimensionsMap: {
              DistributionId: props.spaDistribution.ref,
              Region: 'Global',
            },
            statistic: 'Average',
          }),
        ],
        width: 12,
      }),
    );

    // AgentCore & Queues Row
    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '# AgentCore & Queue Health',
        width: 24,
        height: 1,
      }),
      new cloudwatch.GraphWidget({
        title: 'AgentCore Runtime (eu-west-1)',
        left: [
          new cloudwatch.Metric({
            namespace: 'AgentCore',
            metricName: 'ConcurrentSessions',
            region: 'eu-west-1',
            statistic: 'Maximum',
          }),
        ],
        right: [
          new cloudwatch.Metric({
            namespace: 'AgentCore',
            metricName: 'ExecutionErrors',
            region: 'eu-west-1',
            statistic: 'Sum',
          }),
        ],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Platform DLQ Backlog',
        left: Object.entries(props.dlqs).map(([name, queue]) =>
          queue.metricApproximateNumberOfMessagesVisible({
            label: name,
            statistic: 'Maximum',
          }),
        ),
        width: 12,
      }),
    );

    // --- 2. Cross-Region Observability Sink ---

    new oam.CfnSink(this, 'ObservabilitySink', {
      name: 'PlatformObservabilitySink',
      policy: {
        Version: '2012-10-17',
        Statement: [
          {
            Effect: 'Allow',
            Principal: {
              AWS: cdk.Aws.ACCOUNT_ID,
            },
            Action: ['oam:CreateLink', 'oam:UpdateLink'],
            Resource: '*',
            Condition: {
              StringEquals: {
                'oam:ResourceTypes': ['AWS::CloudWatch::Metric', 'AWS::Logs::LogGroup', 'AWS::XRay::Trace'],
              },
            },
          },
        ],
      },
    });

    // --- 3. Failure Mode (FM) Alarms ---

    // FM-1: Runtime region unavailable (ServiceUnavailableException)
    // Detected via Bridge Lambda 5xx errors or AgentCore direct metrics
    new cloudwatch.Alarm(this, 'Fm1RuntimeRegionUnavailableAlarm', {
      alarmName: 'FM-1-RuntimeRegionUnavailable',
      alarmDescription: 'AgentCore Runtime region is unavailable (ServiceUnavailableException)',
      metric: props.bridgeFn.metricErrors({
        period: cdk.Duration.minutes(1),
        statistic: 'Sum',
      }),
      threshold: 5,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // FM-2: Authoriser cold start spike (P99 > 500ms)
    new cloudwatch.Alarm(this, 'Fm2AuthoriserColdStartAlarm', {
      alarmName: 'FM-2-AuthoriserColdStartSpike',
      alarmDescription: 'Authoriser Lambda latency is high (likely cold starts)',
      metric: props.authoriserFn.metricDuration({
        period: cdk.Duration.minutes(1),
        statistic: 'p99',
      }),
      threshold: 500,
      evaluationPeriods: 3,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    });

    // FM-4: DynamoDB hot partition (Throttle events)
    new cloudwatch.Alarm(this, 'Fm4DynamoDbHotPartitionAlarm', {
      alarmName: 'FM-4-DynamoDbHotPartition',
      alarmDescription: 'DynamoDB throttling detected on invocations table',
      metric: props.invocationsTable.metric('ThrottledRequests', {
        period: cdk.Duration.minutes(1),
        statistic: 'Sum',
      }),
      threshold: 10,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    });

    // FM-5: Bridge Lambda timeout (15-min limit reached)
    new cloudwatch.Alarm(this, 'Fm5BridgeTimeoutAlarm', {
      alarmName: 'FM-5-BridgeTimeout',
      alarmDescription: 'Bridge Lambda reached 15-minute timeout',
      metric: props.bridgeFn.metricDuration({
        period: cdk.Duration.minutes(5),
        statistic: 'Maximum',
      }),
      threshold: cdk.Duration.minutes(15).toMilliseconds(),
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    });

    // FM-9: DLQ message arrival (General DLQ alarm)
    for (const [name, queue] of Object.entries(props.dlqs)) {
      new cloudwatch.Alarm(this, `Fm9DlqArrivalAlarm-${name}`, {
        alarmName: `FM-9-DLQ-Arrival-${name}`,
        alarmDescription: `Messages arriving in DLQ for ${name}`,
        metric: queue.metricApproximateNumberOfMessagesVisible({
          period: cdk.Duration.minutes(1),
          statistic: 'Sum',
        }),
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      });
    }

    // FM-7: AgentCore Memory unavailable (Degraded mode metric)
    // Note: AgentCore metrics are custom metrics from the SDK
    new cloudwatch.Alarm(this, 'Fm7AgentCoreMemoryDegradedAlarm', {
      alarmName: 'FM-7-AgentCoreMemoryDegraded',
      alarmDescription: 'AgentCore Memory is in degraded mode',
      metric: new cloudwatch.Metric({
        namespace: 'AgentCore',
        metricName: 'DegradedMode',
        dimensionsMap: { Service: 'Memory' },
        period: cdk.Duration.minutes(5),
        statistic: 'Maximum',
      }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    });

    // FM-8: Usage plan quota exhausted (429 from API Gateway)
    new cloudwatch.Alarm(this, 'Fm8UsagePlanQuotaExhaustedAlarm', {
      alarmName: 'FM-8-UsagePlanQuotaExhausted',
      alarmDescription: 'API Gateway returning 429 Too Many Requests (Usage Plan)',
      metric: props.api.metricClientError({
        period: cdk.Duration.minutes(5),
        statistic: 'Sum',
      }),
      threshold: 100, // Threshold for total 429s across all tenants
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    });

    // API 5xx Errors Alarm
    new cloudwatch.Alarm(this, 'Api5xxErrorsAlarm', {
      alarmName: 'Platform-API-5xx-Errors',
      alarmDescription: 'API Gateway returning 5xx Internal Server Errors',
      metric: props.api.metricServerError({
        period: cdk.Duration.minutes(1),
        statistic: 'Sum',
      }),
      threshold: 5,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    });

    // WAF Blocked Requests Alarm
    new cloudwatch.Alarm(this, 'WafBlockedRequestsAlarm', {
      alarmName: 'Platform-WAF-Blocked-Requests',
      alarmDescription: 'High number of requests blocked by WAF',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/WAFV2',
        metricName: 'BlockedRequests',
        dimensionsMap: {
          WebACL: props.apiWebAcl.name!,
          Region: this.region,
          Rule: 'ALL',
        },
        period: cdk.Duration.minutes(5),
        statistic: 'Sum',
      }),
      threshold: 50,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    });
  }

  private createLambdaMetricWidget(title: string, fn: lambda.IFunction): cloudwatch.GraphWidget {
    return new cloudwatch.GraphWidget({
      title: `${title} Lambda Health`,
      left: [fn.metricInvocations(), fn.metricErrors()],
      right: [fn.metricDuration()],
      width: 8,
    });
  }

  private createTableMetricWidget(title: string, table: dynamodb.ITable): cloudwatch.GraphWidget {
    return new cloudwatch.GraphWidget({
      title: `${title} Table Performance`,
      left: [table.metricConsumedReadCapacityUnits(), table.metricConsumedWriteCapacityUnits()],
      right: [table.metricThrottledRequests()],
      width: 8,
    });
  }
}
