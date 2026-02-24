/**
 * NetworkStack — VPC, subnets, VPC endpoints, security groups, NACLs.
 *
 * eu-west-2 London only. Provides network isolation for all Lambda functions
 * and VPC endpoints for: S3, DynamoDB, SSM, Secrets Manager, AgentCore.
 *
 * Implemented in TASK-021.
 * ADRs: ADR-009
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';

export class NetworkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    // TODO: Implemented in TASK-021
  }
}
