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
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';

export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const region = cdk.Stack.of(this).region;
    const privateSubnetSelection: ec2.SubnetSelection = {
      subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
    };

    this.vpc = new ec2.Vpc(this, 'PlatformVpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.42.0.0/16'),
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          name: 'private',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    });

    const lambdaSecurityGroup = new ec2.SecurityGroup(this, 'LambdaSecurityGroup', {
      vpc: this.vpc,
      allowAllOutbound: false,
      description: 'Security group for platform Lambdas running in private subnets',
    });

    const endpointSecurityGroup = new ec2.SecurityGroup(this, 'InterfaceEndpointSecurityGroup', {
      vpc: this.vpc,
      allowAllOutbound: false,
      description: 'Interface VPC endpoints for platform control-plane services',
    });

    endpointSecurityGroup.addIngressRule(
      lambdaSecurityGroup,
      ec2.Port.tcp(443),
      'Allow HTTPS from platform Lambdas',
    );
    lambdaSecurityGroup.addEgressRule(
      endpointSecurityGroup,
      ec2.Port.tcp(443),
      'Allow HTTPS to interface VPC endpoints',
    );
    lambdaSecurityGroup.addEgressRule(
      ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
      ec2.Port.udp(53),
      'Allow DNS (UDP) to VPC resolver',
    );
    lambdaSecurityGroup.addEgressRule(
      ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
      ec2.Port.tcp(53),
      'Allow DNS (TCP) to VPC resolver',
    );

    this.vpc.addGatewayEndpoint('S3GatewayEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
      subnets: [privateSubnetSelection],
    });
    this.vpc.addGatewayEndpoint('DynamoDbGatewayEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
      subnets: [privateSubnetSelection],
    });

    this.vpc.addInterfaceEndpoint('SsmInterfaceEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SSM,
      privateDnsEnabled: true,
      securityGroups: [endpointSecurityGroup],
      subnets: privateSubnetSelection,
    });
    this.vpc.addInterfaceEndpoint('SecretsManagerInterfaceEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
      privateDnsEnabled: true,
      securityGroups: [endpointSecurityGroup],
      subnets: privateSubnetSelection,
    });
    this.vpc.addInterfaceEndpoint('AgentCoreInterfaceEndpoint', {
      service: new ec2.InterfaceVpcEndpointService(
        `com.amazonaws.${region}.bedrock-agentcore`,
        443,
      ),
      privateDnsEnabled: true,
      securityGroups: [endpointSecurityGroup],
      subnets: privateSubnetSelection,
    });
    this.vpc.addInterfaceEndpoint('AgentCoreGatewayInterfaceEndpoint', {
      service: new ec2.InterfaceVpcEndpointService(
        `com.amazonaws.${region}.bedrock-agentcore.gateway`,
        443,
      ),
      privateDnsEnabled: true,
      securityGroups: [endpointSecurityGroup],
      subnets: privateSubnetSelection,
    });

    const publicNetworkAcl = new ec2.NetworkAcl(this, 'PublicSubnetNetworkAcl', { vpc: this.vpc });
    const privateNetworkAcl = new ec2.NetworkAcl(this, 'PrivateSubnetNetworkAcl', { vpc: this.vpc });

    for (const [index, subnet] of this.vpc.publicSubnets.entries()) {
      new ec2.SubnetNetworkAclAssociation(this, `PublicSubnetAclAssociation${index + 1}`, {
        subnet,
        networkAcl: publicNetworkAcl,
      });
    }
    for (const [index, subnet] of this.vpc.isolatedSubnets.entries()) {
      new ec2.SubnetNetworkAclAssociation(this, `PrivateSubnetAclAssociation${index + 1}`, {
        subnet,
        networkAcl: privateNetworkAcl,
      });
    }

    this.addPublicSubnetAclRules(publicNetworkAcl, this.vpc.vpcCidrBlock);
    this.addPrivateSubnetAclRules(privateNetworkAcl, this.vpc.vpcCidrBlock);

    const enableRuntimeVpcPeering = new cdk.CfnParameter(this, 'EnableRuntimeVpcPeering', {
      type: 'String',
      allowedValues: ['true', 'false'],
      default: 'false',
      description:
        'Enable optional inter-region VPC peering to a eu-west-1 runtime egress VPC for AgentCore Runtime traffic',
    });
    const runtimePeerVpcId = new cdk.CfnParameter(this, 'RuntimePeerVpcId', {
      type: 'String',
      default: '',
      description: 'Peer VPC ID in eu-west-1 (required when EnableRuntimeVpcPeering=true)',
    });
    const runtimePeerAccountId = new cdk.CfnParameter(this, 'RuntimePeerAccountId', {
      type: 'String',
      default: '',
      description: 'Peer AWS account ID in eu-west-1 (required when EnableRuntimeVpcPeering=true)',
    });
    const runtimePeerCidr = new cdk.CfnParameter(this, 'RuntimePeerCidr', {
      type: 'String',
      default: '',
      description: 'CIDR of the eu-west-1 runtime egress VPC (required when peering is enabled)',
    });

    const runtimePeeringConfigured = new cdk.CfnCondition(this, 'RuntimeVpcPeeringConfigured', {
      expression: cdk.Fn.conditionAnd(
        cdk.Fn.conditionEquals(enableRuntimeVpcPeering.valueAsString, 'true'),
        cdk.Fn.conditionNot(cdk.Fn.conditionEquals(runtimePeerVpcId.valueAsString, '')),
        cdk.Fn.conditionNot(cdk.Fn.conditionEquals(runtimePeerAccountId.valueAsString, '')),
        cdk.Fn.conditionNot(cdk.Fn.conditionEquals(runtimePeerCidr.valueAsString, '')),
      ),
    });

    const runtimeVpcPeering = new ec2.CfnVPCPeeringConnection(this, 'RuntimeVpcPeeringConnection', {
      vpcId: this.vpc.vpcId,
      peerVpcId: runtimePeerVpcId.valueAsString,
      peerOwnerId: runtimePeerAccountId.valueAsString,
      peerRegion: 'eu-west-1',
      tags: [{ key: 'Name', value: `${cdk.Stack.of(this).stackName}-runtime-peering` }],
    });
    runtimeVpcPeering.cfnOptions.condition = runtimePeeringConfigured;

    for (const [index, subnet] of this.vpc.isolatedSubnets.entries()) {
      const isolatedSubnet = subnet as ec2.Subnet;
      const route = new ec2.CfnRoute(this, `RuntimePeerRoute${index + 1}`, {
        routeTableId: isolatedSubnet.routeTable.routeTableId,
        destinationCidrBlock: runtimePeerCidr.valueAsString,
        vpcPeeringConnectionId: runtimeVpcPeering.ref,
      });
      route.cfnOptions.condition = runtimePeeringConfigured;
      route.addDependency(runtimeVpcPeering);
    }

    const runtimePeerEgress = new ec2.CfnSecurityGroupEgress(this, 'RuntimePeerHttpsEgress', {
      groupId: lambdaSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 443,
      toPort: 443,
      cidrIp: runtimePeerCidr.valueAsString,
      description: 'HTTPS egress to eu-west-1 runtime VPC over optional inter-region peering',
    });
    runtimePeerEgress.cfnOptions.condition = runtimePeeringConfigured;

    new cdk.CfnOutput(this, 'VpcId', {
      value: this.vpc.vpcId,
      description: 'Primary platform VPC ID',
      exportName: `${this.stackName}-VpcId`,
    });
    new cdk.CfnOutput(this, 'PrivateSubnetIds', {
      value: cdk.Fn.join(',', this.vpc.isolatedSubnets.map((subnet) => subnet.subnetId)),
      description: 'Private isolated subnet IDs for Lambda placement',
      exportName: `${this.stackName}-PrivateSubnetIds`,
    });
    new cdk.CfnOutput(this, 'PublicSubnetIds', {
      value: cdk.Fn.join(',', this.vpc.publicSubnets.map((subnet) => subnet.subnetId)),
      description: 'Public subnet IDs (reserved for edge/public-facing infra)',
      exportName: `${this.stackName}-PublicSubnetIds`,
    });
    new cdk.CfnOutput(this, 'LambdaSecurityGroupId', {
      value: lambdaSecurityGroup.securityGroupId,
      description: 'Security group for platform Lambdas in the VPC',
      exportName: `${this.stackName}-LambdaSecurityGroupId`,
    });
    new cdk.CfnOutput(this, 'InterfaceEndpointSecurityGroupId', {
      value: endpointSecurityGroup.securityGroupId,
      description: 'Security group attached to interface VPC endpoints',
      exportName: `${this.stackName}-InterfaceEndpointSecurityGroupId`,
    });
  }

  private addPublicSubnetAclRules(networkAcl: ec2.NetworkAcl, vpcCidr: string): void {
    new ec2.NetworkAclEntry(this, 'PublicAclInboundVpc', {
      networkAcl,
      ruleNumber: 100,
      cidr: ec2.AclCidr.ipv4(vpcCidr),
      traffic: ec2.AclTraffic.allTraffic(),
      direction: ec2.TrafficDirection.INGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PublicAclInboundHttps', {
      networkAcl,
      ruleNumber: 110,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPort(443),
      direction: ec2.TrafficDirection.INGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PublicAclInboundHttp', {
      networkAcl,
      ruleNumber: 120,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPort(80),
      direction: ec2.TrafficDirection.INGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PublicAclInboundEphemeral', {
      networkAcl,
      ruleNumber: 130,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPortRange(1024, 65535),
      direction: ec2.TrafficDirection.INGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PublicAclOutboundVpc', {
      networkAcl,
      ruleNumber: 100,
      cidr: ec2.AclCidr.ipv4(vpcCidr),
      traffic: ec2.AclTraffic.allTraffic(),
      direction: ec2.TrafficDirection.EGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PublicAclOutboundHttps', {
      networkAcl,
      ruleNumber: 110,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPort(443),
      direction: ec2.TrafficDirection.EGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PublicAclOutboundHttp', {
      networkAcl,
      ruleNumber: 120,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPort(80),
      direction: ec2.TrafficDirection.EGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PublicAclOutboundEphemeral', {
      networkAcl,
      ruleNumber: 130,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPortRange(1024, 65535),
      direction: ec2.TrafficDirection.EGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
  }

  private addPrivateSubnetAclRules(networkAcl: ec2.NetworkAcl, vpcCidr: string): void {
    new ec2.NetworkAclEntry(this, 'PrivateAclInboundVpc', {
      networkAcl,
      ruleNumber: 100,
      cidr: ec2.AclCidr.ipv4(vpcCidr),
      traffic: ec2.AclTraffic.allTraffic(),
      direction: ec2.TrafficDirection.INGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PrivateAclInboundEphemeral', {
      networkAcl,
      ruleNumber: 110,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPortRange(1024, 65535),
      direction: ec2.TrafficDirection.INGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PrivateAclOutboundVpc', {
      networkAcl,
      ruleNumber: 100,
      cidr: ec2.AclCidr.ipv4(vpcCidr),
      traffic: ec2.AclTraffic.allTraffic(),
      direction: ec2.TrafficDirection.EGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PrivateAclOutboundHttps', {
      networkAcl,
      ruleNumber: 110,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPort(443),
      direction: ec2.TrafficDirection.EGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PrivateAclOutboundHttp', {
      networkAcl,
      ruleNumber: 120,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPort(80),
      direction: ec2.TrafficDirection.EGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
    new ec2.NetworkAclEntry(this, 'PrivateAclOutboundEphemeral', {
      networkAcl,
      ruleNumber: 130,
      cidr: ec2.AclCidr.anyIpv4(),
      traffic: ec2.AclTraffic.tcpPortRange(1024, 65535),
      direction: ec2.TrafficDirection.EGRESS,
      ruleAction: ec2.Action.ALLOW,
    });
  }
}
