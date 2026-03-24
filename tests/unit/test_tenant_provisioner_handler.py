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


    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_missing_tenant_id_returns_failed(
        self, mock_sleep, mock_resource, mock_client
    ):
        event = {"detail": {"tier": "basic"}}  # No tenantId
        context = MagicMock()

        result = lambda_handler(event, context)

        self.assertEqual(result["status"], "FAILED")
        self.assertIn("tenantId", result["reason"])
        mock_client.assert_not_called()

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_stack_create_failed_raises(
        self, mock_sleep, mock_resource, mock_client
    ):
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        mock_cfn.describe_stacks.side_effect = [
            ClientError(
                {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
                "DescribeStacks",
            ),
            {
                "Stacks": [{"StackStatus": "CREATE_FAILED", "Outputs": []}]
            },
        ]

        event = {"detail": {"tenantId": "t-fail-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        with self.assertRaises(RuntimeError) as cm:
            lambda_handler(event, context)

        self.assertIn("CREATE_FAILED", str(cm.exception))

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_rollback_complete_raises(
        self, mock_sleep, mock_resource, mock_client
    ):
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        mock_cfn.describe_stacks.side_effect = [
            ClientError(
                {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
                "DescribeStacks",
            ),
            {
                "Stacks": [{"StackStatus": "ROLLBACK_COMPLETE", "Outputs": []}]
            },
        ]

        event = {"detail": {"tenantId": "t-rollback-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        with self.assertRaises(RuntimeError) as cm:
            lambda_handler(event, context)

        self.assertIn("ROLLBACK_COMPLETE", str(cm.exception))

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_update_rollback_raises(
        self, mock_sleep, mock_resource, mock_client
    ):
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        mock_cfn.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]},  # stack exists
            {
                "Stacks": [{"StackStatus": "UPDATE_ROLLBACK_COMPLETE", "Outputs": []}]
            },
        ]

        event = {"detail": {"tenantId": "t-urollback-001", "tier": "premium"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        with self.assertRaises(RuntimeError) as cm:
            lambda_handler(event, context)

        self.assertIn("UPDATE_ROLLBACK_COMPLETE", str(cm.exception))

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_completes_with_no_outputs_raises(
        self, mock_sleep, mock_resource, mock_client
    ):
        """Stack reaches CREATE_COMPLETE but has no outputs → RuntimeError (same 'no outputs' path)."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        mock_cfn.describe_stacks.side_effect = [
            ClientError(
                {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
                "DescribeStacks",
            ),
            # Stack reaches terminal state with no outputs
            {"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": []}]},
        ]

        event = {"detail": {"tenantId": "t-noout-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        with self.assertRaises(RuntimeError) as cm:
            lambda_handler(event, context)

        self.assertIn("no outputs", str(cm.exception).lower())

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_no_updates_to_perform_succeeds(
        self, mock_sleep, mock_resource, mock_client
    ):
        """'No updates are to be performed' is treated as a no-op success."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        # Stack exists (update path)
        mock_cfn.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]},  # check exist
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
            },
        ]
        mock_cfn.update_stack.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "No updates are to be performed"}},
            "UpdateStack",
        )

        mock_ddb = MagicMock()
        mock_resource.return_value = mock_ddb
        mock_ddb.Table.return_value = MagicMock()

        event = {"detail": {"tenantId": "t-noop-001", "tier": "premium"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        result = lambda_handler(event, context)

        self.assertEqual(result["status"], "SUCCESS")

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_cfn_client_error_during_poll_propagates(
        self, mock_sleep, mock_resource, mock_client
    ):
        """ClientError during poll loop is re-raised."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        mock_cfn.describe_stacks.side_effect = [
            ClientError(
                {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
                "DescribeStacks",
            ),
            ClientError(
                {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
                "DescribeStacks",
            ),
        ]

        event = {"detail": {"tenantId": "t-error-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        with self.assertRaises(ClientError):
            lambda_handler(event, context)

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_uses_context_account_when_not_in_event(
        self, mock_sleep, mock_resource, mock_client
    ):
        """accountId falls back to Lambda function ARN account segment."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        mock_cfn.describe_stacks.side_effect = [
            ClientError(
                {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
                "DescribeStacks",
            ),
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
            },
        ]

        mock_ddb = MagicMock()
        mock_resource.return_value = mock_ddb
        mock_ddb.Table.return_value = MagicMock()

        # No accountId in event — must use context ARN
        event = {"detail": {"tenantId": "t-acct-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:999888777666:function:prov"

        result = lambda_handler(event, context)
        self.assertEqual(result["status"], "SUCCESS")

        # Check accountId param was populated from context
        _, kwargs = mock_cfn.create_stack.call_args
        params = {p["ParameterKey"]: p["ParameterValue"] for p in kwargs["Parameters"]}
        self.assertEqual(params["accountId"], "999888777666")

    @patch("boto3.client")
    @patch("boto3.resource")
    @patch("time.sleep", return_value=None)
    def test_lambda_handler_non_validation_cfn_error_on_describe_propagates(
        self, mock_sleep, mock_resource, mock_client
    ):
        """ClientError with a code other than ValidationError during initial describe is re-raised."""
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn

        mock_cfn.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "User is not authorized"}},
            "DescribeStacks",
        )

        event = {"detail": {"tenantId": "t-authz-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        with self.assertRaises(ClientError) as cm:
            lambda_handler(event, context)

        self.assertEqual(cm.exception.response["Error"]["Code"], "AccessDenied")


if __name__ == "__main__":
    unittest.main()
