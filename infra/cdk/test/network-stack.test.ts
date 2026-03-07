import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { NetworkStack } from '../lib/network-stack';

describe('NetworkStack', () => {
  function synthTemplate(): Template {
    const app = new cdk.App();
    const stack = new NetworkStack(app, 'platform-network-dev', {
      env: {
        region: 'eu-west-2',
      },
    });

    return Template.fromStack(stack);
  }

  test('creates VPC, subnets, security groups, and NACLs', () => {
    const template = synthTemplate();

    template.resourceCountIs('AWS::EC2::VPC', 1);
    template.resourceCountIs('AWS::EC2::Subnet', 4);
    template.resourceCountIs('AWS::EC2::SecurityGroup', 2);
    template.resourceCountIs('AWS::EC2::NetworkAcl', 2);
    template.resourceCountIs('AWS::EC2::SubnetNetworkAclAssociation', 4);
  });

  test('creates required VPC endpoints', () => {
    const template = synthTemplate();
    const gatewayEndpoints = template.findResources('AWS::EC2::VPCEndpoint', {
      Properties: {
        VpcEndpointType: 'Gateway',
      },
    });
    const interfaceEndpoints = template.findResources('AWS::EC2::VPCEndpoint', {
      Properties: {
        VpcEndpointType: 'Interface',
      },
    });

    template.resourceCountIs('AWS::EC2::VPCEndpoint', 6);
    expect(Object.keys(gatewayEndpoints)).toHaveLength(2);
    expect(Object.keys(interfaceEndpoints)).toHaveLength(4);
    expect(JSON.stringify(gatewayEndpoints)).toContain('.s3');
    expect(JSON.stringify(gatewayEndpoints)).toContain('.dynamodb');
    template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
      ServiceName: 'com.amazonaws.eu-west-2.ssm',
      VpcEndpointType: 'Interface',
    });
    template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
      ServiceName: 'com.amazonaws.eu-west-2.secretsmanager',
      VpcEndpointType: 'Interface',
    });
    template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
      ServiceName: 'com.amazonaws.eu-west-2.bedrock-agentcore',
      VpcEndpointType: 'Interface',
    });
    template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
      ServiceName: 'com.amazonaws.eu-west-2.bedrock-agentcore.gateway',
      VpcEndpointType: 'Interface',
    });
  });

  test('includes optional eu-west-1 peering scaffolding for runtime connectivity', () => {
    const template = synthTemplate();

    template.hasResourceProperties('AWS::EC2::VPCPeeringConnection', {
      PeerRegion: 'eu-west-1',
    });
    template.hasResourceProperties(
      'AWS::EC2::SecurityGroupEgress',
      Match.objectLike({
        IpProtocol: 'tcp',
        FromPort: 443,
        ToPort: 443,
      }),
    );
  });
});
