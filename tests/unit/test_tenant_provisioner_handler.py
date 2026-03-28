import json
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
                "EVENT_BUS_NAME": "default",
                "AWS_REGION": "eu-west-2",
            },
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    @patch("boto3.client")
    def test_start_create_returns_in_progress(self, mock_client):
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn
        mock_cfn.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
            "DescribeStacks",
        )

        event = {
            "detail": {
                "tenantId": "t-test-001",
                "tier": "premium",
                "accountId": "123456789012",
            }
        }
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        result = lambda_handler(event, context)

        self.assertEqual(result["status"], "STARTED")
        self.assertEqual(result["provisioningState"], "IN_PROGRESS")
        self.assertEqual(result["stackName"], "platform-tenant-t-test-001-dev")
        mock_cfn.create_stack.assert_called_once()

    @patch("boto3.client")
    def test_start_update_noop_returns_ready(self, mock_client):
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn
        mock_cfn.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]},
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

        event = {"detail": {"tenantId": "t-noop-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:prov"

        result = lambda_handler(event, context)

        self.assertEqual(result["provisioningState"], "READY")
        self.assertEqual(result["outputs"]["ExecutionRoleArn"], "arn:role")
        mock_cfn.update_stack.assert_called_once()

    @patch("boto3.client")
    def test_poll_returns_ready_with_outputs(self, mock_client):
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

        result = lambda_handler(
            {
                "action": "poll",
                "tenantId": "t-test-001",
                "stackName": "platform-tenant-t-test-001-dev",
            },
            MagicMock(),
        )

        self.assertEqual(result["provisioningState"], "READY")
        self.assertEqual(result["stackStatus"], "CREATE_COMPLETE")
        self.assertEqual(result["outputs"]["MemoryStoreArn"], "arn:mem")

    @patch("boto3.client")
    def test_poll_returns_failed_for_terminal_failure(self, mock_client):
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn
        mock_cfn.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "ROLLBACK_COMPLETE", "Outputs": []}]
        }

        result = lambda_handler(
            {
                "action": "poll",
                "tenantId": "t-fail-001",
                "stackName": "platform-tenant-t-fail-001-dev",
            },
            MagicMock(),
        )

        self.assertEqual(result["provisioningState"], "FAILED")
        self.assertEqual(result["reason"], "ROLLBACK_COMPLETE")

    @patch("boto3.client")
    def test_emit_result_publishes_eventbridge_event(self, mock_client):
        mock_events = MagicMock()
        mock_client.return_value = mock_events

        result = lambda_handler(
            {
                "action": "emit-result",
                "resultType": "provisioned",
                "tenantId": "t-test-001",
                "appId": "app-001",
                "stackName": "platform-tenant-t-test-001-dev",
                "stackStatus": "CREATE_COMPLETE",
                "outputs": {"ExecutionRoleArn": "arn:role", "MemoryStoreArn": "arn:mem"},
            },
            MagicMock(),
        )

        self.assertEqual(result["status"], "EMITTED")
        kwargs = mock_events.put_events.call_args.kwargs
        detail = json.loads(kwargs["Entries"][0]["Detail"])
        self.assertEqual(kwargs["Entries"][0]["DetailType"], "tenant.provisioned")
        self.assertEqual(detail["tenantId"], "t-test-001")
        self.assertEqual(detail["ExecutionRoleArn"], "arn:role")

    @patch("boto3.client")
    def test_start_uses_context_account_when_missing_from_detail(self, mock_client):
        mock_cfn = MagicMock()
        mock_client.return_value = mock_cfn
        mock_cfn.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
            "DescribeStacks",
        )

        event = {"detail": {"tenantId": "t-acct-001", "tier": "basic"}}
        context = MagicMock()
        context.invoked_function_arn = "arn:aws:lambda:eu-west-2:999888777666:function:prov"

        lambda_handler(event, context)

        params = {
            p["ParameterKey"]: p["ParameterValue"]
            for p in mock_cfn.create_stack.call_args.kwargs["Parameters"]
        }
        self.assertEqual(params["accountId"], "999888777666")


if __name__ == "__main__":
    unittest.main()
