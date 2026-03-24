import json
import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

# Use powertools if available, else standard logging
try:
    from aws_lambda_powertools import Logger

    logger = Logger()
except ImportError:
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Tenant Provisioner — Triggers CloudFormation deployment for TenantStack.

    Workflow-aware handler.
    """
    operation = event.get("operation", "START")
    tenant_id = event.get("tenantId")
    tier = event.get("tier", "basic")
    account_id = event.get("accountId")

    # If it's the direct EventBridge trigger, it won't have "operation"
    if not event.get("operation") and "detail" in event:
        detail = event.get("detail", {})
        tenant_id = detail.get("tenantId")
        tier = detail.get("tier", "basic")
        account_id = detail.get("accountId")
        operation = "START"

    if not tenant_id:
        logger.error("Missing tenantId in event")
        return {"status": "FAILED", "reason": "Missing tenantId"}

    if operation == "START":
        return _handle_start(tenant_id, tier, account_id, context)
    elif operation == "COMPLETE":
        return _handle_complete(tenant_id, event.get("stackName"))
    elif operation == "FAIL":
        return _handle_fail(tenant_id, event.get("reason", "Unknown error"))
    else:
        raise ValueError(f"Unknown operation: {operation}")


def _handle_start(
    tenant_id: str, tier: str, account_id: str | None, context: Any
) -> dict[str, Any]:
    env = os.environ["PLATFORM_ENV"]
    template_url = os.environ["TENANT_STACK_TEMPLATE_URL"]
    cfn = boto3.client("cloudformation")
    stack_name = f"platform-tenant-{tenant_id}-{env}"

    account_id = account_id or context.invoked_function_arn.split(":")[4]
    params = [
        {"ParameterKey": "tenantId", "ParameterValue": tenant_id},
        {"ParameterKey": "tier", "ParameterValue": tier},
        {"ParameterKey": "accountId", "ParameterValue": account_id},
    ]

    logger.info(
        f"Starting TenantStack deployment for {tenant_id}",
        extra={"tenant_id": tenant_id, "stack_name": stack_name},
    )

    try:
        cfn.describe_stacks(StackName=stack_name)
        cfn_op = "UPDATE"
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ValidationError":
            cfn_op = "CREATE"
        else:
            raise

    try:
        if cfn_op == "CREATE":
            cfn.create_stack(
                StackName=stack_name,
                TemplateURL=template_url,
                Parameters=params,  # type: ignore
                Capabilities=["CAPABILITY_NAMED_IAM"],
                OnFailure="ROLLBACK",
                Tags=[
                    {"Key": "tenantid", "Value": tenant_id},
                    {"Key": "platform:env", "Value": env},
                ],
            )
        else:
            cfn.update_stack(
                StackName=stack_name,
                TemplateURL=template_url,
                Parameters=params,  # type: ignore
                Capabilities=["CAPABILITY_NAMED_IAM"],
            )
    except ClientError as e:
        if "No updates are to be performed" in str(e):
            logger.info(f"No changes for stack {stack_name}")
            # If no changes, we can proceed to complete immediately
            return {
                "status": "SUCCESS",
                "tenantId": tenant_id,
                "stackName": stack_name,
                "skipWait": True,
            }
        else:
            logger.error(f"CloudFormation operation failed: {e}")
            raise

    return {
        "status": "IN_PROGRESS",
        "tenantId": tenant_id,
        "stackName": stack_name,
        "tier": tier,
        "accountId": account_id,
    }


def _handle_complete(tenant_id: str, stack_name: str | None) -> dict[str, Any]:
    if not stack_name:
        raise ValueError("stackName is required for COMPLETE operation")

    cfn = boto3.client("cloudformation")
    table_name = os.environ["TENANTS_TABLE_NAME"]

    logger.info(f"Finalizing provisioning for {tenant_id} (stack: {stack_name})")

    resp = cfn.describe_stacks(StackName=stack_name)
    stack = resp["Stacks"][0]
    status = stack["StackStatus"]

    if status not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError(f"Stack {stack_name} in unexpected state: {status}")

    outputs: dict[str, str] = {}
    for output in stack.get("Outputs", []):
        key = output.get("OutputKey")
        val = output.get("OutputValue")
        if key and val:
            outputs[key] = val

    if not outputs:
        raise RuntimeError(f"Stack {stack_name} has no outputs")

    # Write back to DynamoDB
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)

    update_expr = (
        "SET executionRoleArn = :role, execution_role_arn = :role, "
        "memoryStoreArn = :mem, memory_store_arn = :mem, "
        "updatedAt = :now, #s = :status"
    )
    expr_names = {"#s": "status"}
    expr_values = {
        ":role": outputs.get("ExecutionRoleArn"),
        ":mem": outputs.get("MemoryStoreArn"),
        ":now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ":status": "active",
    }
    table.update_item(
        Key={"PK": f"TENANT#{tenant_id}", "SK": "METADATA"},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

    logger.info(f"Provisioning complete for {tenant_id}")
    return {"status": "SUCCESS", "tenantId": tenant_id, "outputs": outputs}


def _handle_fail(tenant_id: str, reason: str) -> dict[str, Any]:
    table_name = os.environ["TENANTS_TABLE_NAME"]
    logger.error(f"Provisioning failed for {tenant_id}: {reason}")

    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)

    table.update_item(
        Key={"PK": f"TENANT#{tenant_id}", "SK": "METADATA"},
        UpdateExpression="SET #s = :status, failureReason = :reason, updatedAt = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":status": "failed",
            ":reason": reason,
            ":now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )

    return {"status": "FAILED", "tenantId": tenant_id, "reason": reason}
