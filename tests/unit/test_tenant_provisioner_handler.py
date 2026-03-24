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
    def test_handle_start_create_success(self, mock_client):
        """START operation: stack doesn't exist → calls create_stack."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        # describe_stacks fails (stack doesn't exist)
        mock_cfn.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
            "DescribeStacks",
        )

        event = {
            "operation": "START",
            "tenantId": "t-test-001",
            "tier": "premium",
            "accountId": "123456789012",
        }
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        result = lambda_handler(event, context)

        self.assertEqual(result["status"], "IN_PROGRESS")
        self.assertEqual(result["stackName"], "platform-tenant-t-test-001-dev")
        mock_cfn.create_stack.assert_called_once()

    @patch("boto3.client")
    def test_handle_start_update_success(self, mock_client):
        """START operation: stack exists → calls update_stack."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        # describe_stacks succeeds (stack exists)
        mock_cfn.describe_stacks.return_value = {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}

        event = {"operation": "START", "tenantId": "t-test-001", "tier": "premium"}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        result = lambda_handler(event, context)

        self.assertEqual(result["status"], "IN_PROGRESS")
        mock_cfn.update_stack.assert_called_once()

    @patch("boto3.client")
    def test_handle_start_no_updates(self, mock_client):
        """START operation: update_stack raises 'No updates' → returns skipWait: True."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn
        mock_cfn.describe_stacks.return_value = {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
        mock_cfn.update_stack.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "No updates are to be performed"}},
            "UpdateStack",
        )

        event = {"operation": "START", "tenantId": "t-noop-001"}
        result = lambda_handler(event, MagicMock())

        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(result.get("skipWait"))

    @patch("boto3.client")
    @patch("boto3.resource")
    def test_handle_complete_success(self, mock_resource, mock_client):
        """COMPLETE operation: stack complete → updates DynamoDB status to active."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn
        mock_cfn.describe_stacks.return_value = {
            "Stacks": [
                {
                    "StackStatus": "CREATE_COMPLETE",
                    "Outputs": [
                        {"OutputKey": "ExecutionRoleArn", "OutputValue": "arn:role"},
                        {"OutputKey": "MemoryStoreArn", "OutputValue": "arn:mem"},
                    ],
                }
            ]
        }

        mock_ddb = MagicMock()
        mock_resource.return_value = mock_ddb
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table

        event = {
            "operation": "COMPLETE",
            "tenantId": "t-test-001",
            "stackName": "platform-tenant-t-test-001-dev",
        }
        result = lambda_handler(event, MagicMock())

        self.assertEqual(result["status"], "SUCCESS")

        # Verify DynamoDB update
        mock_table.update_item.assert_called_once()
        _, kwargs = mock_table.update_item.call_args
        self.assertEqual(kwargs["ExpressionAttributeValues"][":status"], "active")

    @patch("boto3.resource")
    def test_handle_fail(self, mock_resource):
        """FAIL operation: updates DynamoDB status to failed."""
        mock_ddb = MagicMock()
        mock_resource.return_value = mock_ddb
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table

        event = {"operation": "FAIL", "tenantId": "t-fail-001", "reason": "Stack failed"}
        result = lambda_handler(event, MagicMock())

        self.assertEqual(result["status"], "FAILED")

        # Verify DynamoDB update
        mock_table.update_item.assert_called_once()
        _, kwargs = mock_table.update_item.call_args
        self.assertEqual(kwargs["ExpressionAttributeValues"][":status"], "failed")
        self.assertEqual(kwargs["ExpressionAttributeValues"][":reason"], "Stack failed")

    def test_missing_tenant_id(self):
        result = lambda_handler({"operation": "START"}, MagicMock())
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("tenantId", result["reason"])

    @patch("boto3.client")
    def test_eventbridge_trigger_mapping(self, mock_client):
        """Direct EventBridge trigger (with 'detail') maps to START operation."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn
        mock_cfn.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
            "DescribeStacks",
        )

        event = {"detail": {"tenantId": "t-eb-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        result = lambda_handler(event, context)
        self.assertEqual(result["status"], "IN_PROGRESS")
        self.assertEqual(result["tenantId"], "t-eb-001")


if __name__ == "__main__":
    unittest.main()
