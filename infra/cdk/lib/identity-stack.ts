/**
 * IdentityStack — GitLab OIDC WIF provider and pipeline roles.
 *
 * Creates least-privilege pipeline roles (one per stage).
 *
 * Implemented in TASK-022.
 * ADRs: ADR-002
 */
import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { resolveEntraConfiguration } from './entra-config';

type PipelineRoleKey = 'validate' | 'deployDev' | 'deployStaging' | 'deployProd';

interface PipelineRoleDefinition {
  readonly id: string;
  readonly roleName: string;
  readonly allowedSubPatterns: string[];
  readonly assumableCdkBootstrapRoles: string[];
}

export class IdentityStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const envName = this.requiredContext('env');
    const gitlabProjectPath = this.optionalContext('gitlabProjectPath') ?? 'j3brns/tf-acore-aas';
    const gitlabOidcAudience = this.optionalContext('gitlabOidcAudience') ?? 'sts.amazonaws.com';
    const cdkBootstrapQualifier = this.optionalContext('cdkBootstrapQualifier') ?? 'hnb659fds';
    const deployBranch = this.optionalContext('gitlabDeployBranch') ?? 'main';
    const entra = resolveEntraConfiguration(this);

    const gitlabOidcProvider = new iam.OpenIdConnectProvider(this, 'GitLabOidcProvider', {
      url: 'https://gitlab.com',
      clientIds: [gitlabOidcAudience],
    });

    const cdkBootstrapRoles = {
      lookup: this.cdkBootstrapRoleArn(cdkBootstrapQualifier, 'lookup'),
      deploy: this.cdkBootstrapRoleArn(cdkBootstrapQualifier, 'deploy'),
      filePublishing: this.cdkBootstrapRoleArn(cdkBootstrapQualifier, 'file-publishing'),
      imagePublishing: this.cdkBootstrapRoleArn(cdkBootstrapQualifier, 'image-publishing'),
    };

    const pipelineRoleDefinitions: Record<PipelineRoleKey, PipelineRoleDefinition> = {
      validate: {
        id: 'PipelineValidateRole',
        roleName: `platform-pipeline-validate-${envName}`,
        allowedSubPatterns: [`project_path:${gitlabProjectPath}:*`],
        assumableCdkBootstrapRoles: [cdkBootstrapRoles.lookup],
      },
      deployDev: {
        id: 'PipelineDeployDevRole',
        roleName: 'platform-pipeline-deploy-dev',
        allowedSubPatterns: [
          `project_path:${gitlabProjectPath}:ref_type:branch:ref:${deployBranch}`,
        ],
        assumableCdkBootstrapRoles: [
          cdkBootstrapRoles.lookup,
          cdkBootstrapRoles.deploy,
          cdkBootstrapRoles.filePublishing,
          cdkBootstrapRoles.imagePublishing,
        ],
      },
      deployStaging: {
        id: 'PipelineDeployStagingRole',
        roleName: 'platform-pipeline-deploy-staging',
        allowedSubPatterns: [
          `project_path:${gitlabProjectPath}:ref_type:branch:ref:${deployBranch}`,
        ],
        assumableCdkBootstrapRoles: [
          cdkBootstrapRoles.lookup,
          cdkBootstrapRoles.deploy,
          cdkBootstrapRoles.filePublishing,
          cdkBootstrapRoles.imagePublishing,
        ],
      },
      deployProd: {
        id: 'PipelineDeployProdRole',
        roleName: 'platform-pipeline-deploy-prod',
        allowedSubPatterns: [
          `project_path:${gitlabProjectPath}:ref_type:branch:ref:${deployBranch}`,
        ],
        assumableCdkBootstrapRoles: [
          cdkBootstrapRoles.lookup,
          cdkBootstrapRoles.deploy,
          cdkBootstrapRoles.filePublishing,
          cdkBootstrapRoles.imagePublishing,
        ],
      },
    };

    const pipelineRoles = Object.fromEntries(
      (Object.entries(pipelineRoleDefinitions) as Array<[PipelineRoleKey, PipelineRoleDefinition]>).map(
        ([key, definition]) => [
          key,
          this.createGitLabPipelineRole({
            definition,
            provider: gitlabOidcProvider,
            audience: gitlabOidcAudience,
          }),
        ],
      ),
    ) as Record<PipelineRoleKey, iam.Role>;

    new cdk.CfnOutput(this, 'GitLabOidcProviderArn', {
      description: 'IAM OIDC provider ARN for GitLab WIF',
      value: gitlabOidcProvider.openIdConnectProviderArn,
    });
    new cdk.CfnOutput(this, 'PipelineValidateRoleArn', {
      description: 'GitLab CI validate stage role ARN',
      value: pipelineRoles.validate.roleArn,
    });
    new cdk.CfnOutput(this, 'PipelineDeployDevRoleArn', {
      description: 'GitLab CI deploy-dev stage role ARN',
      value: pipelineRoles.deployDev.roleArn,
    });
    new cdk.CfnOutput(this, 'PipelineDeployStagingRoleArn', {
      description: 'GitLab CI deploy-staging stage role ARN',
      value: pipelineRoles.deployStaging.roleArn,
    });
    new cdk.CfnOutput(this, 'PipelineDeployProdRoleArn', {
      description: 'GitLab CI deploy-prod stage role ARN',
      value: pipelineRoles.deployProd.roleArn,
    });
    new cdk.CfnOutput(this, 'EntraJwksUrl', {
      description: 'Resolved Entra JWKS URL for runtime configuration',
      value: entra.jwksUrl,
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

  private cdkBootstrapRoleArn(qualifier: string, roleType: string): string {
    return `arn:${cdk.Aws.PARTITION}:iam::${cdk.Aws.ACCOUNT_ID}:role/cdk-${qualifier}-${roleType}-role-${cdk.Aws.ACCOUNT_ID}-${cdk.Aws.REGION}`;
  }

  private createGitLabPipelineRole(args: {
    definition: PipelineRoleDefinition;
    provider: iam.OpenIdConnectProvider;
    audience: string;
  }): iam.Role {
    const { definition, provider, audience } = args;

    const assumedBy = new iam.OpenIdConnectPrincipal(provider).withConditions({
      StringEquals: {
        'gitlab.com:aud': audience,
      },
      StringLike: {
        'gitlab.com:sub': definition.allowedSubPatterns,
      },
    });

    const role = new iam.Role(this, definition.id, {
      roleName: definition.roleName,
      description: `GitLab CI pipeline role for ${definition.roleName}`,
      assumedBy,
      inlinePolicies: {
        AssumeCdkBootstrapRoles: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              sid: 'AssumeCdkBootstrapRoles',
              effect: iam.Effect.ALLOW,
              actions: ['sts:AssumeRole'],
              resources: definition.assumableCdkBootstrapRoles,
            }),
          ],
        }),
      },
    });

    return role;
  }
}
