import * as cdk from 'aws-cdk-lib';
import { resolveAppConfigExtensionLayerArn } from '../lib/platform-compute';

describe('platform compute AppConfig extension layer resolution', () => {
  test('uses the explicit ARM64 layer ARN map for eu-west-2 by default', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'PlatformComputeLayerArnTest', {
      env: {
        account: '123456789012',
        region: 'eu-west-2',
      },
    });

    expect(resolveAppConfigExtensionLayerArn(stack)).toBe(
      'arn:aws:lambda:eu-west-2:282860088358:layer:AWS-AppConfig-Extension-Arm64:190',
    );
  });

  test('allows an explicit context override for the AppConfig extension layer ARN', () => {
    const app = new cdk.App({
      context: {
        appConfigExtensionLayerArn:
          'arn:aws:lambda:eu-west-2:111122223333:layer:Custom-AppConfig-Extension-Arm64:7',
      },
    });
    const stack = new cdk.Stack(app, 'PlatformComputeLayerArnOverrideTest', {
      env: {
        account: '123456789012',
        region: 'eu-west-2',
      },
    });

    expect(resolveAppConfigExtensionLayerArn(stack)).toBe(
      'arn:aws:lambda:eu-west-2:111122223333:layer:Custom-AppConfig-Extension-Arm64:7',
    );
  });
});
