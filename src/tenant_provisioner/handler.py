import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

try:
    from aws_lambda_powertools import Logger

    logger = Logger()
except ImportError:
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

IN_PROGRESS_STATUSES = {
    "CREATE_IN_PROGRESS",
    "UPDATE_IN_PROGRESS",
    "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS",
    "UPDATE_ROLLBACK_IN_PROGRESS",
    "UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS",
    "IMPORT_IN_PROGRESS",
    "IMPORT_ROLLBACK_IN_PROGRESS",
    "REVIEW_IN_PROGRESS",
    "ROLLBACK_IN_PROGRESS",
}
FAILED_STATUSES = {
    "CREATE_FAILED",
    "ROLLBACK_COMPLETE",
    "ROLLBACK_FAILED",
    "UPDATE_ROLLBACK_COMPLETE",
    "UPDATE_ROLLBACK_FAILED",
    "DELETE_COMPLETE",
    "DELETE_FAILED",
}
READY_STATUSES = {"CREATE_COMPLETE", "UPDATE_COMPLETE"}

_cloudformation_client = None
_events_client = None


def _aws_region() -> str:
    return os.environ["AWS_REGION"]


def get_cloudformation():
    global _cloudformation_client
    if _cloudformation_client is None:
        _cloudformation_client = boto3.client("cloudformation", region_name=_aws_region())
    return _cloudformation_client


def get_events():
    global _events_client
    if _events_client is None:
        _events_client = boto3.client("events", region_name=_aws_region())
    return _events_client


def _event_detail(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")
    if isinstance(detail, dict):
        return detail
    return event


def _tenant_id(detail: dict[str, Any]) -> str:
    tenant_id = str(detail.get("tenantId") or "").strip()
    if not tenant_id:
        raise ValueError("Missing tenantId")
    return tenant_id


def _account_id(detail: dict[str, Any], context: Any) -> str:
    provided = str(detail.get("accountId") or "").strip()
    if provided:
        return provided
    return str(context.invoked_function_arn).split(":")[4]


def _stack_name(tenant_id: str) -> str:
    env = os.environ["PLATFORM_ENV"]
    return f"platform-tenant-{tenant_id}-{env}"


def _stack_parameters(detail: dict[str, Any], context: Any) -> list[dict[str, str]]:
    tenant_id = _tenant_id(detail)
    tier = str(detail.get("tier") or "basic").strip() or "basic"
    account_id = _account_id(detail, context)
    params = [
        {"ParameterKey": "tenantId", "ParameterValue": tenant_id},
        {"ParameterKey": "tier", "ParameterValue": tier},
        {"ParameterKey": "accountId", "ParameterValue": account_id},
    ]
    monthly_budget = detail.get("monthlyBudgetUsd")
    if monthly_budget is not None:
        params.append({"ParameterKey": "monthlyBudgetUsd", "ParameterValue": str(monthly_budget)})
    return params


def _stack_outputs(stack: dict[str, Any]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for output in stack.get("Outputs", []):
        key = output.get("OutputKey")
        val = output.get("OutputValue")
        if key and val:
            outputs[str(key)] = str(val)
    return outputs


def _describe_stack(cfn: Any, stack_name: str) -> dict[str, Any]:
    response = cfn.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks") or []
    if not stacks:
        raise RuntimeError(f"Stack {stack_name} not found")
    return stacks[0]


def _start_provisioning(event: dict[str, Any], context: Any) -> dict[str, Any]:
    detail = _event_detail(event)
    tenant_id = _tenant_id(detail)
    stack_name = _stack_name(tenant_id)
    template_url = os.environ["TENANT_STACK_TEMPLATE_URL"]
    cfn = get_cloudformation()
    params = _stack_parameters(detail, context)
    operation = "UPDATE"

    logger.info(
        "Starting tenant provisioning",
        extra={"tenant_id": tenant_id, "stack_name": stack_name, "operation": "upsert"},
    )

    try:
        _describe_stack(cfn, stack_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ValidationError":
            operation = "CREATE"
        else:
            raise

    try:
        if operation == "CREATE":
            cfn.create_stack(
                StackName=stack_name,
                TemplateURL=template_url,
                Parameters=params,  # type: ignore[arg-type]
                Capabilities=["CAPABILITY_NAMED_IAM"],
                OnFailure="ROLLBACK",
                Tags=[
                    {"Key": "tenantid", "Value": tenant_id},
                    {"Key": "platform:env", "Value": os.environ["PLATFORM_ENV"]},
                ],
            )
            provisioning_state = "IN_PROGRESS"
        else:
            cfn.update_stack(
                StackName=stack_name,
                TemplateURL=template_url,
                Parameters=params,  # type: ignore[arg-type]
                Capabilities=["CAPABILITY_NAMED_IAM"],
            )
            provisioning_state = "IN_PROGRESS"
    except ClientError as exc:
        if "No updates are to be performed" in str(exc):
            stack = _describe_stack(cfn, stack_name)
            return {
                "status": "SUCCESS",
                "tenantId": tenant_id,
                "appId": detail.get("appId"),
                "tier": detail.get("tier"),
                "accountId": _account_id(detail, context),
                "stackName": stack_name,
                "operation": operation,
                "provisioningState": "READY",
                "stackStatus": stack["StackStatus"],
                "outputs": _stack_outputs(stack),
            }
        raise

    return {
        "status": "STARTED",
        "tenantId": tenant_id,
        "appId": detail.get("appId"),
        "tier": detail.get("tier"),
        "accountId": _account_id(detail, context),
        "stackName": stack_name,
        "operation": operation,
        "provisioningState": provisioning_state,
    }


def _poll_provisioning(event: dict[str, Any]) -> dict[str, Any]:
    stack_name = str(event.get("stackName") or "").strip()
    tenant_id = str(event.get("tenantId") or "").strip()
    if not stack_name or not tenant_id:
        raise ValueError("stackName and tenantId are required")
    cfn = get_cloudformation()
    stack = _describe_stack(cfn, stack_name)
    status = str(stack["StackStatus"])
    outputs = _stack_outputs(stack)
    result = {
        "tenantId": tenant_id,
        "appId": event.get("appId"),
        "tier": event.get("tier"),
        "accountId": event.get("accountId"),
        "stackName": stack_name,
        "stackStatus": status,
        "outputs": outputs,
    }
    if status in READY_STATUSES:
        result["provisioningState"] = "READY"
        return result
    if status in FAILED_STATUSES:
        result["provisioningState"] = "FAILED"
        result["reason"] = status
        return result
    if status in IN_PROGRESS_STATUSES:
        result["provisioningState"] = "IN_PROGRESS"
        return result
    result["provisioningState"] = "FAILED"
    result["reason"] = status
    return result


def _emit_completion(event: dict[str, Any]) -> dict[str, Any]:
    result_type = str(event.get("resultType") or "").strip()
    detail_type = {
        "provisioned": "tenant.provisioned",
        "failed": "tenant.provisioning_failed",
    }.get(result_type)
    if detail_type is None:
        raise ValueError("resultType must be 'provisioned' or 'failed'")

    detail = {
        "tenantId": event.get("tenantId"),
        "appId": event.get("appId"),
        "tier": event.get("tier"),
        "accountId": event.get("accountId"),
        "stackName": event.get("stackName"),
        "stackStatus": event.get("stackStatus"),
    }
    outputs = event.get("outputs")
    if isinstance(outputs, dict):
        detail.update(outputs)
    if event.get("reason") is not None:
        detail["reason"] = event.get("reason")

    events = get_events()
    events.put_events(
        Entries=[
            {
                "Source": "platform.tenant_provisioner",
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
                "EventBusName": os.environ.get("EVENT_BUS_NAME", "default"),
            }
        ]
    )
    return {"status": "EMITTED", "detailType": detail_type, "tenantId": detail.get("tenantId")}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    action = str(event.get("action") or "").strip().lower()
    if not action and isinstance(event.get("detail"), dict):
        action = "start"
    if action in {"start", "poll"}:
        try:
            if action == "start":
                return _start_provisioning(event, context)
            return _poll_provisioning(event)
        except Exception as exc:
            detail = _event_detail(event)
            fallback_tenant_id = str(detail.get("tenantId") or event.get("tenantId") or "").strip()
            return {
                "tenantId": fallback_tenant_id,
                "appId": detail.get("appId") or event.get("appId"),
                "tier": detail.get("tier") or event.get("tier"),
                "accountId": detail.get("accountId") or event.get("accountId"),
                "stackName": event.get("stackName") or _stack_name(fallback_tenant_id),
                "provisioningState": "FAILED",
                "reason": str(exc),
                "stackStatus": "ERROR",
                "outputs": {},
            }
    if action == "emit-result":
        return _emit_completion(event)
    raise ValueError("Unsupported action")
