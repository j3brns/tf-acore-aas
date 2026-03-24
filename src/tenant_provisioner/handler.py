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

    Trigger: EventBridge platform.tenant.created
    Source: platform.tenant_api
    """
    detail = event.get("detail", {})
    tenant_id = detail.get("tenantId")
    tier = detail.get("tier", "basic")
    account_id = detail.get("accountId")

    if not tenant_id:
        logger.error("Missing tenantId in event detail")
        return {"status": "FAILED", "reason": "Missing tenantId"}

    env = os.environ["PLATFORM_ENV"]
    template_url = os.environ["TENANT_STACK_TEMPLATE_URL"]
    table_name = os.environ["TENANTS_TABLE_NAME"]

    cfn = boto3.client("cloudformation")
    stack_name = f"platform-tenant-{tenant_id}-{env}"

    # 1. Resolve parameters
    account_id = account_id or context.invoked_function_arn.split(":")[4]
    params = [
        {"ParameterKey": "tenantId", "ParameterValue": tenant_id},
        {"ParameterKey": "tier", "ParameterValue": tier},
        {"ParameterKey": "accountId", "ParameterValue": account_id},
    ]

    # 2. Start deployment
    logger.info(
        f"Deploying TenantStack for {tenant_id} (tier: {tier})",
        extra={"tenant_id": tenant_id, "stack_name": stack_name, "operation": "upsert"},
    )

    try:
        cfn.describe_stacks(StackName=stack_name)
        operation = "UPDATE"
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "ValidationError":
            operation = "CREATE"
        else:
            raise

    try:
        if operation == "CREATE":
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
        else:
            logger.error(f"CloudFormation operation failed: {e}")
            raise

    # 3. Wait for completion (poll)
    # TenantStack usually takes 1-3 minutes.
    max_wait_seconds = 600  # 10 minutes
    start_time = time.time()

    logger.info(f"Waiting for stack {stack_name} to complete...")

    outputs: dict[str, str] = {}
    while time.time() - start_time < max_wait_seconds:
        try:
            resp = cfn.describe_stacks(StackName=stack_name)
            stack = resp["Stacks"][0]
            status = stack["StackStatus"]

            if status in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                for output in stack.get("Outputs", []):
                    key = output.get("OutputKey")
                    val = output.get("OutputValue")
                    if key and val:
                        outputs[key] = val
                break
            elif status in (
                "CREATE_FAILED",
                "ROLLBACK_COMPLETE",
                "UPDATE_ROLLBACK_COMPLETE",
                "DELETE_COMPLETE",
            ):
                raise RuntimeError(f"Stack deployment failed with status: {status}")

            time.sleep(10)
        except ClientError as e:
            logger.error(f"Error polling stack: {e}")
            raise

    if not outputs:
        raise RuntimeError(f"Stack {stack_name} did not complete within timeout or has no outputs")

    # 4. Write back to DynamoDB
    logger.info(f"Writing resource refs back to {table_name} for {tenant_id}")
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)

    # Use update_item to be safe
    update_expr = (
        "SET executionRoleArn = :role, execution_role_arn = :role, "
        "memoryStoreArn = :mem, memory_store_arn = :mem, updatedAt = :now"
    )
    expr_values = {
        ":role": outputs.get("ExecutionRoleArn"),
        ":mem": outputs.get("MemoryStoreArn"),
        ":now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    table.update_item(
        Key={"PK": f"TENANT#{tenant_id}", "SK": "METADATA"},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )

    logger.info(f"Provisioning complete for {tenant_id}")
    return {"status": "SUCCESS", "tenantId": tenant_id, "stackName": stack_name, "outputs": outputs}
