import os
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from src.tenant_provisioner.handler import lambda_handler


class TestTenantProvisioner(unittest.TestCase):
    def setUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "PLATFORM_ENV": "dev",
                "TENANT_STACK_TEMPLATE_URL": "s3://bucket/template.json",
                "TENANTS_TABLE_NAME": "platform-tenants",
                "AWS_REGION": "eu-west-2",
            },
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_create_success(self, mock_sleep, mock_resource, mock_client):
        # Mock CloudFormation client
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        # describe_stacks fails first (stack doesn't exist)
        mock_cfn.describe_stacks.side_effect = [
            ClientError(
                {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
                "DescribeStacks",
            ),
            {"Stacks": [{"StackStatus": "CREATE_IN_PROGRESS"}]},  # poll 1
            {
                "Stacks": [
                    {
                        "StackStatus": "CREATE_COMPLETE",
                        "Outputs": [
                            {"OutputKey": "ExecutionRoleArn", "OutputValue": "arn:role"},
                            {"OutputKey": "MemoryStoreArn", "OutputValue": "arn:mem"},
                        ],
                    }
                ]
            },  # poll 2
        ]

        # Mock DynamoDB resource
        mock_ddb = MagicMock()
        mock_resource.return_value = mock_ddb
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table

        event = {
            "detail": {"tenantId": "t-test-001", "tier": "premium", "accountId": "123456789012"}
        }
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        result = lambda_handler(event, context)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["tenantId"], "t-test-001")

        # Verify CFN calls
        mock_cfn.create_stack.assert_called_once()
        args, kwargs = mock_cfn.create_stack.call_args
        self.assertEqual(kwargs["StackName"], "platform-tenant-t-test-001-dev")
        self.assertIn(
            {"ParameterKey": "tenantId", "ParameterValue": "t-test-001"}, kwargs["Parameters"]
        )
        self.assertIn({"ParameterKey": "tier", "ParameterValue": "premium"}, kwargs["Parameters"])

        # Verify DynamoDB update
        mock_table.update_item.assert_called_once()
        args, kwargs = mock_table.update_item.call_args
        self.assertEqual(kwargs["Key"], {"PK": "TENANT#t-test-001", "SK": "METADATA"})
        self.assertIn("executionRoleArn = :role", kwargs["UpdateExpression"])

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_update_success(self, mock_sleep, mock_resource, mock_client):
        # Mock CloudFormation client
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        # describe_stacks succeeds first (stack exists)
        mock_cfn.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]},  # describe (check exist)
            {"Stacks": [{"StackStatus": "UPDATE_IN_PROGRESS"}]},  # poll 1
            {
                "Stacks": [
                    {
                        "StackStatus": "UPDATE_COMPLETE",
                        "Outputs": [
                            {"OutputKey": "ExecutionRoleArn", "OutputValue": "arn:role"},
                            {"OutputKey": "MemoryStoreArn", "OutputValue": "arn:mem"},
                        ],
                    }
                ]
            },  # poll 2
        ]

        # Mock DynamoDB resource
        mock_ddb = MagicMock()
        mock_resource.return_value = mock_ddb
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table

        event = {"detail": {"tenantId": "t-test-001", "tier": "premium"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        result = lambda_handler(event, context)

        self.assertEqual(result["status"], "SUCCESS")
        mock_cfn.update_stack.assert_called_once()


if __name__ == "__main__":
    unittest.main()
