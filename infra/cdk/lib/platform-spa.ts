import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';

export interface PlatformSpaProps {
  readonly envName: string;
  readonly spaDomainName?: string;
  readonly spaCertificateArn?: string;
}

export class PlatformSpa extends Construct {
  public readonly spaDistribution: cloudfront.CfnDistribution;
  public readonly spaAllowedOrigin: string;

  constructor(scope: Construct, id: string, props: PlatformSpaProps) {
    super(scope, id);

    const spaBucket = new s3.Bucket(this, 'SpaBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
    });

    const spaLogBucket = new s3.Bucket(this, 'SpaLogBucket', {
      bucketName: `platform-spa-logs-${props.envName}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      accessControl: s3.BucketAccessControl.LOG_DELIVERY_WRITE,
    });

    const spaResponseHeadersPolicy = new cloudfront.CfnResponseHeadersPolicy(
      this,
      'SpaCspResponseHeadersPolicy',
      {
        responseHeadersPolicyConfig: {
          name: `${cdk.Stack.of(this).stackName}-spa-security-headers`,
          comment: 'Security headers for platform SPA',
          securityHeadersConfig: {
            contentSecurityPolicy: {
              contentSecurityPolicy:
                "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; connect-src 'self' https:; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self';",
              override: true,
            },
            frameOptions: {
              frameOption: 'DENY',
              override: true,
            },
            strictTransportSecurity: {
              accessControlMaxAgeSec: 31536000,
              includeSubdomains: true,
              preload: true,
              override: true,
            },
            contentTypeOptions: {
              override: true,
            },
            referrerPolicy: {
              referrerPolicy: 'same-origin',
              override: true,
            },
            xssProtection: {
              protection: true,
              modeBlock: true,
              override: true,
            },
          },
        },
      },
    );

    const spaOriginAccessControl = new cloudfront.CfnOriginAccessControl(this, 'SpaOriginAccessControl', {
      originAccessControlConfig: {
        name: `${cdk.Stack.of(this).stackName}-spa-oac`,
        description: 'OAC for SPA bucket origin',
        originAccessControlOriginType: 's3',
        signingBehavior: 'always',
        signingProtocol: 'sigv4',
      },
    });

    const spaRouteRewriteFunction = new cloudfront.Function(this, 'SpaRouteRewriteFunction', {
      comment: 'Rewrite SPA deep links to index.html without masking missing asset failures',
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var request = event.request;
  var uri = request.uri || '/';
  var lastSegment = uri.substring(uri.lastIndexOf('/') + 1);

  if (uri === '/' || lastSegment.indexOf('.') === -1) {
    request.uri = '/index.html';
  }

  return request;
}
      `),
    });

    this.spaDistribution = new cloudfront.CfnDistribution(this, 'SpaDistribution', {
      distributionConfig: {
        enabled: true,
        comment: 'Platform SPA distribution',
        defaultRootObject: 'index.html',
        httpVersion: 'http2',
        priceClass: 'PriceClass_100',
        ipv6Enabled: true,
        logging: {
          bucket: spaLogBucket.bucketRegionalDomainName,
          includeCookies: false,
          prefix: 'spa-cloudfront/',
        },
        origins: [
          {
            id: 'SpaS3Origin',
            domainName: spaBucket.bucketRegionalDomainName,
            originAccessControlId: spaOriginAccessControl.attrId,
            s3OriginConfig: {
              originAccessIdentity: '',
            },
          },
        ],
        defaultCacheBehavior: {
          targetOriginId: 'SpaS3Origin',
          viewerProtocolPolicy: 'redirect-to-https',
          compress: true,
          allowedMethods: ['GET', 'HEAD', 'OPTIONS'],
          cachedMethods: ['GET', 'HEAD', 'OPTIONS'],
          cachePolicyId: cloudfront.CachePolicy.CACHING_DISABLED.cachePolicyId,
          responseHeadersPolicyId: spaResponseHeadersPolicy.attrId,
          functionAssociations: [
            {
              eventType: 'viewer-request',
              functionArn: spaRouteRewriteFunction.functionArn,
            },
          ],
        },
        cacheBehaviors: [
          {
            pathPattern: 'assets/*',
            targetOriginId: 'SpaS3Origin',
            viewerProtocolPolicy: 'redirect-to-https',
            compress: true,
            allowedMethods: ['GET', 'HEAD', 'OPTIONS'],
            cachedMethods: ['GET', 'HEAD', 'OPTIONS'],
            cachePolicyId: cloudfront.CachePolicy.CACHING_OPTIMIZED.cachePolicyId,
            responseHeadersPolicyId: spaResponseHeadersPolicy.attrId,
          },
        ],
        restrictions: {
          geoRestriction: {
            restrictionType: 'none',
          },
        },
        ...(props.spaDomainName && props.spaCertificateArn
          ? {
              aliases: [props.spaDomainName],
              viewerCertificate: {
                acmCertificateArn: props.spaCertificateArn,
                minimumProtocolVersion: 'TLSv1.2_2021',
                sslSupportMethod: 'sni-only',
              },
            }
          : {
              viewerCertificate: {
                cloudFrontDefaultCertificate: true,
                minimumProtocolVersion: 'TLSv1.2_2021',
              },
            }),
      },
    });

    spaBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowCloudFrontOacRead',
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
        actions: ['s3:GetObject'],
        resources: [spaBucket.arnForObjects('*')],
        conditions: {
          StringEquals: {
            'AWS:SourceArn': cdk.Fn.join('', [
              'arn:',
              cdk.Aws.PARTITION,
              ':cloudfront::',
              cdk.Aws.ACCOUNT_ID,
              ':distribution/',
              this.spaDistribution.ref,
            ]),
          },
        },
      }),
    );

    new ssm.StringParameter(this, 'SpaBucketNameParam', {
      parameterName: `/platform/spa/${props.envName}/bucket-name`,
      stringValue: spaBucket.bucketName,
      description: 'S3 bucket name for the platform SPA',
    });

    new ssm.StringParameter(this, 'SpaDistributionIdParam', {
      parameterName: `/platform/spa/${props.envName}/distribution-id`,
      stringValue: this.spaDistribution.ref,
      description: 'CloudFront distribution ID for the platform SPA',
    });

    const spaBucketNameOutput = new cdk.CfnOutput(this, 'SpaBucketName', {
      value: spaBucket.bucketName,
      description: 'S3 bucket name for the platform SPA',
    });
    spaBucketNameOutput.overrideLogicalId('SpaBucketName');

    const spaDistributionIdOutput = new cdk.CfnOutput(this, 'SpaDistributionId', {
      value: this.spaDistribution.ref,
      description: 'CloudFront distribution ID for the platform SPA',
    });
    spaDistributionIdOutput.overrideLogicalId('SpaDistributionId');

    if (props.spaDomainName) {
      new ssm.StringParameter(this, 'SpaDomainNameParam', {
        parameterName: `/platform/spa/${props.envName}/domain-name`,
        stringValue: props.spaDomainName,
        description: 'Custom domain name for the platform SPA CloudFront distribution',
      });

      const spaDomainNameOutput = new cdk.CfnOutput(this, 'SpaDomainName', {
        value: props.spaDomainName,
        description: 'Custom domain name for the platform SPA',
      });
      spaDomainNameOutput.overrideLogicalId('SpaDomainName');
    }

    this.spaAllowedOrigin = props.spaDomainName
      ? `https://${props.spaDomainName}`
      : cdk.Fn.join('', ['https://', this.spaDistribution.attrDomainName]);
  }
}
