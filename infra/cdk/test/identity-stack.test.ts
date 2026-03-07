import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { IdentityStack } from '../lib/identity-stack';

function synthTemplate(): Template {
  const app = new cdk.App({
    context: {
      env: 'dev',
      gitlabProjectPath: 'j3brns/tf-acore-aas',
      entraTenantId: '00000000-0000-0000-0000-000000000000',
    },
  });

  const stack = new IdentityStack(app, 'platform-identity-dev');
  return Template.fromStack(stack);
}

describe('IdentityStack', () => {
  let template: Template;

  beforeAll(() => {
    template = synthTemplate();
  });

  test('creates GitLab OIDC provider with sts audience', () => {
    template.resourceCountIs('Custom::AWSCDKOpenIdConnectProvider', 1);
    template.hasResourceProperties('Custom::AWSCDKOpenIdConnectProvider', {
      Url: 'https://gitlab.com',
      ClientIDList: ['sts.amazonaws.com'],
    });
  });

  test('creates four pipeline roles with GitLab OIDC trust conditions', () => {
    const roleResources = Object.values(template.findResources('AWS::IAM::Role'));
    const pipelineRoles = roleResources.filter((resource) => {
      const roleName = resource.Properties?.RoleName;
      return typeof roleName === 'string' && roleName.startsWith('platform-pipeline-');
    });
    expect(pipelineRoles).toHaveLength(4);

    template.hasResourceProperties('AWS::IAM::Role', {
      RoleName: 'platform-pipeline-validate-dev',
      AssumeRolePolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 'sts:AssumeRoleWithWebIdentity',
            Condition: Match.objectLike({
              StringEquals: Match.objectLike({
                'gitlab.com:aud': 'sts.amazonaws.com',
              }),
              StringLike: Match.objectLike({
                'gitlab.com:sub': ['project_path:j3brns/tf-acore-aas:*'],
              }),
            }),
          }),
        ]),
      }),
    });

    for (const roleName of [
      'platform-pipeline-deploy-dev',
      'platform-pipeline-deploy-staging',
      'platform-pipeline-deploy-prod',
    ]) {
      template.hasResourceProperties('AWS::IAM::Role', {
        RoleName: roleName,
        AssumeRolePolicyDocument: Match.objectLike({
          Statement: Match.arrayWith([
            Match.objectLike({
              Condition: Match.objectLike({
                StringLike: Match.objectLike({
                  'gitlab.com:sub': [
                    'project_path:j3brns/tf-acore-aas:ref_type:branch:ref:main',
                  ],
                }),
              }),
            }),
          ]),
        }),
        Policies: Match.arrayWith([
          Match.objectLike({
            PolicyDocument: Match.objectLike({
              Statement: Match.arrayWith([
                Match.objectLike({
                  Action: 'sts:AssumeRole',
                }),
              ]),
            }),
          }),
        ]),
      });
    }
  });

  test('creates Entra JWKS layer and exposes resolved JWKS URL output', () => {
    template.resourceCountIs('AWS::Lambda::LayerVersion', 1);
    template.hasResourceProperties('AWS::Lambda::LayerVersion', {
      LayerName: 'platform-entra-jwks-dev',
      Description: 'Entra JWKS config layer (dev)',
      CompatibleRuntimes: ['python3.12'],
    });

    template.hasOutput('EntraJwksUrl', {
      Value:
        'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/discovery/v2.0/keys',
    });
  });

  test('creates three KMS keys and key policies do not use wildcard principals', () => {
    template.resourceCountIs('AWS::KMS::Key', 3);
    template.resourceCountIs('AWS::KMS::Alias', 3);
    template.hasResourceProperties('AWS::KMS::Alias', {
      AliasName: 'alias/platform-tenant-data-dev',
    });
    template.hasResourceProperties('AWS::KMS::Alias', {
      AliasName: 'alias/platform-config-dev',
    });
    template.hasResourceProperties('AWS::KMS::Alias', {
      AliasName: 'alias/platform-logs-dev',
    });

    const keys = template.findResources('AWS::KMS::Key');
    for (const resource of Object.values(keys)) {
      const statements = (resource.Properties?.KeyPolicy?.Statement ?? []) as Array<{
        Principal?: unknown;
      }>;

      for (const statement of statements) {
        const principal = statement.Principal;
        expect(principal).not.toBe('*');

        if (!principal || typeof principal !== 'object') {
          continue;
        }

        const awsPrincipal = (principal as Record<string, unknown>)['AWS'];
        if (Array.isArray(awsPrincipal)) {
          expect(awsPrincipal).not.toContain('*');
        } else {
          expect(awsPrincipal).not.toBe('*');
        }
      }
    }
  });
});
