/**
 * IdentityStack — GitLab OIDC WIF provider, pipeline roles, KMS keys.
 *
 * Creates least-privilege pipeline roles (one per stage).
 * Creates KMS keys: one per data classification (tenant-data, platform-config, logs).
 * No wildcard principals in KMS key policies.
 *
 * Implemented in TASK-022.
 * ADRs: ADR-002
 */
import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

type PipelineRoleKey = 'validate' | 'deployDev' | 'deployStaging' | 'deployProd';

interface PipelineRoleDefinition {
  readonly id: string;
  readonly roleName: string;
  readonly allowedSubPatterns: string[];
  readonly assumableCdkBootstrapRoles: string[];
}

export class IdentityStack extends cdk.Stack {
  public readonly tenantDataKey: kms.IKey;
  public readonly platformConfigKey: kms.IKey;
  public readonly logsKey: kms.IKey;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const envName = this.requiredContext('env');
    const gitlabProjectPath = this.optionalContext('gitlabProjectPath') ?? 'j3brns/tf-acore-aas';
    const gitlabOidcAudience = this.optionalContext('gitlabOidcAudience') ?? 'sts.amazonaws.com';
    const cdkBootstrapQualifier = this.optionalContext('cdkBootstrapQualifier') ?? 'hnb659fds';
    const deployBranch = this.optionalContext('gitlabDeployBranch') ?? 'main';
    const entraJwksUrl = this.resolveEntraJwksUrl();

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

    const entraJwksLayer = this.createEntraJwksLayer({
      envName,
      jwksUrl: entraJwksUrl,
    });

    this.tenantDataKey = this.createPlatformKey({
      id: 'TenantDataKey',
      aliasName: `alias/platform-tenant-data-${envName}`,
      description: `Platform tenant data KMS key (${envName})`,
    });
    this.platformConfigKey = this.createPlatformKey({
      id: 'PlatformConfigKey',
      aliasName: `alias/platform-config-${envName}`,
      description: `Platform config KMS key (${envName})`,
    });
    this.logsKey = this.createPlatformKey({
      id: 'LogsKey',
      aliasName: `alias/platform-logs-${envName}`,
      description: `Platform logs KMS key (${envName})`,
    });

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
    new cdk.CfnOutput(this, 'EntraJwksLayerArn', {
      description: 'Lambda layer ARN containing baked Entra JWKS config',
      value: entraJwksLayer.layerVersionArn,
    });
    new cdk.CfnOutput(this, 'EntraJwksUrl', {
      description: 'Resolved Entra JWKS URL baked into the Lambda layer',
      value: entraJwksUrl,
    });
    new cdk.CfnOutput(this, 'TenantDataKmsKeyArn', {
      value: this.tenantDataKey.keyArn,
    });
    new cdk.CfnOutput(this, 'PlatformConfigKmsKeyArn', {
      value: this.platformConfigKey.keyArn,
    });
    new cdk.CfnOutput(this, 'LogsKmsKeyArn', {
      value: this.logsKey.keyArn,
    });

    new ssm.StringParameter(this, 'TenantDataKmsKeyArnParam', {
      parameterName: `/platform/identity/${envName}/tenant-data-kms-key-arn`,
      stringValue: this.tenantDataKey.keyArn,
      description: 'KMS key ARN for tenant data encryption',
    });

    new ssm.StringParameter(this, 'PlatformConfigKmsKeyArnParam', {
      parameterName: `/platform/identity/${envName}/platform-config-kms-key-arn`,
      stringValue: this.platformConfigKey.keyArn,
      description: 'KMS key ARN for platform configuration encryption',
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

  private resolveEntraJwksUrl(): string {
    const explicitJwksUrl = this.optionalContext('entraJwksUrl');
    if (explicitJwksUrl) {
      return explicitJwksUrl;
    }

    const entraTenantId = this.optionalContext('entraTenantId') ?? 'common';
    return `https://login.microsoftonline.com/${entraTenantId}/discovery/v2.0/keys`;
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

  private createEntraJwksLayer(args: { envName: string; jwksUrl: string }): lambda.LayerVersion {
    const { envName, jwksUrl } = args;
    const assetDir = fs.mkdtempSync(path.join(os.tmpdir(), 'platform-entra-jwks-layer-'));
    const pythonDir = path.join(assetDir, 'python');
    fs.mkdirSync(pythonDir, { recursive: true });

    const moduleSource = [
      '"""Generated by IdentityStack (TASK-022)."""',
      `JWKS_URL = ${JSON.stringify(jwksUrl)}`,
      '',
      'def get_jwks_url() -> str:',
      '    return JWKS_URL',
      '',
    ].join('\n');

    fs.writeFileSync(path.join(pythonDir, 'platform_entra_jwks_config.py'), moduleSource, 'utf8');

    return new lambda.LayerVersion(this, 'EntraJwksConfigLayer', {
      layerVersionName: `platform-entra-jwks-${envName}`,
      description: `Entra JWKS config layer (${envName})`,
      code: lambda.Code.fromAsset(assetDir),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
    });
  }

  private createPlatformKey(args: { id: string; aliasName: string; description: string }): kms.Key {
    const key = new kms.Key(this, args.id, {
      description: args.description,
      enableKeyRotation: true,
      pendingWindow: cdk.Duration.days(30),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    new kms.Alias(this, `${args.id}Alias`, {
      aliasName: args.aliasName,
      targetKey: key,
    });

    return key;
  }
}
