"""
data_access.client — TenantScopedDynamoDB and TenantScopedS3.

Enforces tenant partition on every DynamoDB and S3 operation.
Raises TenantAccessViolation on any cross-tenant access attempt.

Security guarantees:
  - DynamoDB: any PK prefixed with TENANT# must equal TENANT#{tenant_id}.
  - S3: every object key must be under tenants/{tenant_id}/.
  - On violation: log with tenant_id/caller_tenant_id, emit CW metric, raise.

100% test coverage required (security-critical path).
See TASK-013 for coverage assertions.

Implemented in TASK-013.
ADRs: ADR-012
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.parameters import AppConfigProvider
from boto3.dynamodb.conditions import ConditionBase, Key

from data_access.exceptions import TenantAccessViolation
from data_access.models import (
    CapabilityRollout,
    PaginatedItems,
    TenantCapabilityPolicy,
    TenantContext,
    TenantTier,
)

logger = Logger(service="data-access-lib")

_TENANT_PK_PREFIX = "TENANT#"
_S3_TENANT_DIR = "tenants/"


# ---------------------------------------------------------------------------
# Internal helper — metric emission (shared between DynamoDB and S3 clients)
# ---------------------------------------------------------------------------


def _emit_tenant_violation_metric(
    cloudwatch_client: Any,
    *,
    caller_tenant_id: str,
    target_tenant_id: str,
) -> None:
    """Publish a TenantAccessViolation count metric to CloudWatch.

    Never raises — metric emission failure must not suppress the exception.
    Logs at ERROR level if emission fails.
    """
    try:
        cloudwatch_client.put_metric_data(
            Namespace="platform/security",
            MetricData=[
                {
                    "MetricName": "TenantAccessViolation",
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "caller_tenant_id", "Value": caller_tenant_id},
                        {"Name": "target_tenant_id", "Value": target_tenant_id},
                    ],
                }
            ],
        )
    except Exception:
        logger.exception(
            "Failed to emit TenantAccessViolation metric",
            caller_tenant_id=caller_tenant_id,
            target_tenant_id=target_tenant_id,
        )


# ---------------------------------------------------------------------------
# TenantScopedDynamoDB
# ---------------------------------------------------------------------------


class TenantScopedDynamoDB:
    """
    DynamoDB client scoped to a single tenant partition.

    Enforces that any PK starting with TENANT# must equal TENANT#{tenant_id}.
    Access to non-tenant-prefixed tables (AGENT#, JOB#, LOCK#, TOOL#) is
    permitted; tenant isolation on those tables is enforced at the IAM layer.

    On violation:
      1. Logs structured error with tenant_id, app_id, attempted_key.
      2. Emits CloudWatch metric: namespace=platform/security, TenantAccessViolation.
      3. Raises TenantAccessViolation.

    Implemented in TASK-013.
    """

    def __init__(
        self,
        context: TenantContext,
        *,
        dynamodb_resource: Any = None,
        cloudwatch_client: Any = None,
    ) -> None:
        self._tenant_id = context.tenant_id
        self._app_id = context.app_id
        region = os.environ["AWS_REGION"]
        self._dynamodb: Any = dynamodb_resource or boto3.resource("dynamodb", region_name=region)
        self._cloudwatch: Any = cloudwatch_client or boto3.client("cloudwatch", region_name=region)

    def _validate_pk(self, key: dict[str, Any]) -> None:
        """Raise TenantAccessViolation if a TENANT#-prefixed PK doesn't match caller.

        Convention: all platform DynamoDB tables use "PK" as the partition key
        attribute name (single-table design standard).  Non-TENANT# prefixed PKs
        (AGENT#, LOCK#, TOOL#) bypass this check; IAM governs those tables.
        """
        pk = key.get("PK", "")
        if isinstance(pk, str) and pk.startswith(_TENANT_PK_PREFIX):
            expected = f"{_TENANT_PK_PREFIX}{self._tenant_id}"
            if pk != expected:
                target_tenant_id = pk.removeprefix(_TENANT_PK_PREFIX)
                self._raise_violation(
                    target_tenant_id=target_tenant_id,
                    attempted_key=repr(key),
                )

    def _raise_violation(self, *, target_tenant_id: str, attempted_key: str) -> None:
        """Log, emit metric, then raise TenantAccessViolation. Never returns."""
        logger.error(
            "TenantAccessViolation: cross-tenant DynamoDB access attempt",
            tenant_id=self._tenant_id,
            app_id=self._app_id,
            caller_tenant_id=self._tenant_id,
            target_tenant_id=target_tenant_id,
            attempted_key=attempted_key,
        )
        _emit_tenant_violation_metric(
            self._cloudwatch,
            caller_tenant_id=self._tenant_id,
            target_tenant_id=target_tenant_id,
        )
        raise TenantAccessViolation(
            tenant_id=target_tenant_id,
            caller_tenant_id=self._tenant_id,
            attempted_key=attempted_key,
        )

    def get_item(self, table_name: str, key: dict[str, Any]) -> dict[str, Any] | None:
        """Get a single item, enforcing tenant partition on PK.

        Returns the item dict, or None if the item does not exist.
        """
        self._validate_pk(key)
        table = self._dynamodb.Table(table_name)
        response = table.get_item(Key=key)
        return response.get("Item")

    def put_item(
        self,
        table_name: str,
        item: dict[str, Any],
        *,
        condition_expression: str | None = None,
    ) -> None:
        """Write an item, enforcing tenant partition on PK."""
        self._validate_pk(item)
        table = self._dynamodb.Table(table_name)
        kwargs: dict[str, Any] = {"Item": item}
        if condition_expression:
            kwargs["ConditionExpression"] = condition_expression
        table.put_item(**kwargs)

    def update_item(
        self,
        table_name: str,
        key: dict[str, Any],
        update_expression: str,
        expression_attribute_values: dict[str, Any],
        *,
        expression_attribute_names: dict[str, str] | None = None,
        condition_expression: str | None = None,
    ) -> dict[str, Any]:
        """Update an item, enforcing tenant partition on PK.

        Returns the raw boto3 response dict.  The updated attributes are
        under the "Attributes" key (ReturnValues=ALL_NEW).
        """
        self._validate_pk(key)
        table = self._dynamodb.Table(table_name)
        kwargs: dict[str, Any] = {
            "Key": key,
            "UpdateExpression": update_expression,
            "ExpressionAttributeValues": expression_attribute_values,
            "ReturnValues": "ALL_NEW",
        }
        if expression_attribute_names is not None:
            kwargs["ExpressionAttributeNames"] = expression_attribute_names
        if condition_expression is not None:
            kwargs["ConditionExpression"] = condition_expression
        return table.update_item(**kwargs)

    def delete_item(self, table_name: str, key: dict[str, Any]) -> None:
        """Delete an item, enforcing tenant partition on PK."""
        self._validate_pk(key)
        table = self._dynamodb.Table(table_name)
        table.delete_item(Key=key)

    def query(
        self,
        table_name: str,
        *,
        sk_condition: ConditionBase | None = None,
        filter_expression: ConditionBase | None = None,
        index_name: str | None = None,
        limit: int | None = None,
        scan_index_forward: bool = True,
        exclusive_start_key: dict[str, Any] | None = None,
    ) -> PaginatedItems:
        """Query the caller's tenant partition.

        The PK is always forced to TENANT#{tenant_id}; the caller cannot
        supply a different partition key.  Assumes the table's partition key
        attribute name is "PK" (platform single-table design convention).
        An optional SK condition is ANDed onto the key condition.

        Returns a PaginatedItems object. Pass result.last_evaluated_key
        to exclusive_start_key for subsequent pages.
        """
        table = self._dynamodb.Table(table_name)
        pk_condition = Key("PK").eq(f"{_TENANT_PK_PREFIX}{self._tenant_id}")
        key_condition = pk_condition & sk_condition if sk_condition is not None else pk_condition

        kwargs: dict[str, Any] = {
            "KeyConditionExpression": key_condition,
            "ScanIndexForward": scan_index_forward,
        }
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression
        if index_name is not None:
            kwargs["IndexName"] = index_name
        if limit is not None:
            kwargs["Limit"] = limit
        if exclusive_start_key is not None:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = table.query(**kwargs)
        return PaginatedItems(
            items=response.get("Items", []),
            last_evaluated_key=response.get("LastEvaluatedKey"),
        )

    def query_all(
        self,
        table_name: str,
        *,
        sk_condition: ConditionBase | None = None,
        filter_expression: ConditionBase | None = None,
        index_name: str | None = None,
        scan_index_forward: bool = True,
    ) -> list[dict[str, Any]]:
        """Query all items in the caller's tenant partition, handling pagination.

        The PK is always forced to TENANT#{tenant_id}.
        Returns a flat list of all items.
        """
        items: list[dict[str, Any]] = []
        exclusive_start_key = None
        while True:
            result = self.query(
                table_name,
                sk_condition=sk_condition,
                filter_expression=filter_expression,
                index_name=index_name,
                scan_index_forward=scan_index_forward,
                exclusive_start_key=exclusive_start_key,
            )
            items.extend(result.items)
            exclusive_start_key = result.last_evaluated_key
            if not exclusive_start_key:
                break
        return items

    def scan(
        self,
        table_name: str,
        *,
        filter_expression: ConditionBase | str | None = None,
        limit: int | None = None,
        exclusive_start_key: dict[str, Any] | None = None,
        expression_attribute_names: dict[str, str] | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
    ) -> PaginatedItems:
        """Scan a table.

        SECURITY: Scanning is an administrative operation.  Isolation is
        NOT enforced by this method — it will return items from all
        tenants if they exist in the scanned table.

        Lambda handlers must perform their own authorization (e.g. roles claim
        check) before calling this method.

        Returns a PaginatedItems object. Pass result.last_evaluated_key
        to exclusive_start_key for subsequent pages.
        """
        table = self._dynamodb.Table(table_name)
        kwargs: dict[str, Any] = {}
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression
        if limit is not None:
            kwargs["Limit"] = limit
        if exclusive_start_key is not None:
            kwargs["ExclusiveStartKey"] = exclusive_start_key
        if expression_attribute_names is not None:
            kwargs["ExpressionAttributeNames"] = expression_attribute_names
        if expression_attribute_values is not None:
            kwargs["ExpressionAttributeValues"] = expression_attribute_values

        response = table.scan(**kwargs)
        return PaginatedItems(
            items=response.get("Items", []),
            last_evaluated_key=response.get("LastEvaluatedKey"),
        )

    def scan_all(
        self,
        table_name: str,
        *,
        filter_expression: ConditionBase | str | None = None,
        expression_attribute_names: dict[str, str] | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Scan all items in a table, handling pagination.

        SECURITY: Scanning is an administrative operation. Isolation is
        NOT enforced by this method.

        Returns a flat list of all items.
        """
        items: list[dict[str, Any]] = []
        exclusive_start_key = None
        while True:
            result = self.scan(
                table_name,
                filter_expression=filter_expression,
                exclusive_start_key=exclusive_start_key,
                expression_attribute_names=expression_attribute_names,
                expression_attribute_values=expression_attribute_values,
            )
            items.extend(result.items)
            exclusive_start_key = result.last_evaluated_key
            if not exclusive_start_key:
                break
        return items


# ---------------------------------------------------------------------------
# TenantScopedS3
# ---------------------------------------------------------------------------


class TenantScopedS3:
    """
    S3 client scoped to a single tenant prefix.

    Enforces that all object keys are under tenants/{tenant_id}/.
    Raises TenantAccessViolation if the caller attempts to access a different
    tenant's prefix or a path outside the tenant directory entirely.

    On violation:
      1. Logs structured error with tenant_id, app_id, attempted_key.
      2. Emits CloudWatch metric: namespace=platform/security, TenantAccessViolation.
      3. Raises TenantAccessViolation.

    Implemented in TASK-013.
    """

    def __init__(
        self,
        context: TenantContext,
        *,
        s3_client: Any = None,
        cloudwatch_client: Any = None,
    ) -> None:
        self._tenant_id = context.tenant_id
        self._app_id = context.app_id
        self._prefix = f"{_S3_TENANT_DIR}{self._tenant_id}/"
        region = os.environ["AWS_REGION"]
        self._s3: Any = s3_client or boto3.client("s3", region_name=region)
        self._cloudwatch: Any = cloudwatch_client or boto3.client("cloudwatch", region_name=region)

    def _validate_key(self, key: str) -> None:
        """Raise TenantAccessViolation if key is outside the tenant prefix."""
        if not key.startswith(self._prefix):
            # Best-effort: extract the target tenant from the key if possible.
            target_tenant_id = "unknown"
            if key.startswith(_S3_TENANT_DIR):
                parts = key.split("/")
                if len(parts) >= 2:
                    target_tenant_id = parts[1]
            self._raise_violation(
                target_tenant_id=target_tenant_id,
                attempted_key=key,
            )

    def _raise_violation(self, *, target_tenant_id: str, attempted_key: str) -> None:
        """Log, emit metric, then raise TenantAccessViolation. Never returns."""
        logger.error(
            "TenantAccessViolation: cross-tenant S3 access attempt",
            tenant_id=self._tenant_id,
            app_id=self._app_id,
            caller_tenant_id=self._tenant_id,
            target_tenant_id=target_tenant_id,
            attempted_key=attempted_key,
        )
        _emit_tenant_violation_metric(
            self._cloudwatch,
            caller_tenant_id=self._tenant_id,
            target_tenant_id=target_tenant_id,
        )
        raise TenantAccessViolation(
            tenant_id=target_tenant_id,
            caller_tenant_id=self._tenant_id,
            attempted_key=attempted_key,
        )

    def get_object(self, bucket: str, key: str) -> dict[str, Any]:
        """Get an S3 object body, enforcing tenant prefix."""
        self._validate_key(key)
        return self._s3.get_object(Bucket=bucket, Key=key)

    def put_object(self, bucket: str, key: str, body: bytes, **kwargs: Any) -> None:
        """Put an S3 object, enforcing tenant prefix."""
        self._validate_key(key)
        self._s3.put_object(Bucket=bucket, Key=key, Body=body, **kwargs)

    def delete_object(self, bucket: str, key: str) -> None:
        """Delete an S3 object, enforcing tenant prefix."""
        self._validate_key(key)
        self._s3.delete_object(Bucket=bucket, Key=key)

    def list_objects(self, bucket: str, prefix: str = "") -> list[dict[str, Any]]:
        """List objects under the tenant prefix.

        The optional prefix is appended to the tenant prefix, keeping
        the listing inside the tenant's directory.
        """
        full_prefix = self._prefix + prefix
        response = self._s3.list_objects_v2(Bucket=bucket, Prefix=full_prefix)
        return response.get("Contents", [])

    def generate_presigned_url(
        self,
        bucket: str,
        key: str,
        *,
        expires_in: int = 3600,
        client_method: str = "get_object",
    ) -> str:
        """Generate a presigned URL for an object, enforcing tenant prefix."""
        self._validate_key(key)
        return self._s3.generate_presigned_url(
            ClientMethod=client_method,
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )


# ---------------------------------------------------------------------------
# TenantCapabilityClient (ADR-017)
# ---------------------------------------------------------------------------


class TenantCapabilityClient:
    """
    Client for fetching and evaluating tenant capability policy from AppConfig.

    Implements ADR-017: AppConfig owns dynamic tenant capability policy only.
    evaluates it with deny-by-default fallback semantics: use the last known
    good cached document when available; otherwise fall back to empty policy.
    """

    def __init__(
        self,
        application: str | None = None,
        environment: str | None = None,
        profile: str | None = None,
        *,
        provider: AppConfigProvider | None = None,
    ) -> None:
        self._application = application or os.environ.get("APPCONFIG_APPLICATION_ID")
        self._environment = environment or os.environ.get("APPCONFIG_ENVIRONMENT_ID")
        self._profile = profile or os.environ.get("APPCONFIG_PROFILE_ID")

        self._provider = provider or (
            AppConfigProvider(application=self._application, environment=self._environment)
            if self._application and self._environment
            else None
        )

    def fetch_policy(self) -> TenantCapabilityPolicy:
        """Fetch the active capability policy from AppConfig.

        Returns TenantCapabilityPolicy.safe_fallback() on any error.
        Policy is cached for 60 seconds (max_age) to minimize AppConfig calls.
        """
        if not self._provider or not self._profile:
            logger.warning("AppConfig provider or profile not configured; using fallback policy")
            return TenantCapabilityPolicy.safe_fallback()

        try:
            # get() returns a dict if the profile is JSON and parsed by AppConfig.
            # Local caching and session state handled by Powertools AppConfigProvider.
            raw_policy = self._provider.get(self._profile, max_age=60)
            if not isinstance(raw_policy, dict):
                logger.error(
                    "AppConfig policy is not a JSON object",
                    extra={"type": str(type(raw_policy)), "profile": self._profile},
                )
                return TenantCapabilityPolicy.safe_fallback()

            # Map raw dict to TenantCapabilityPolicy dataclass
            # NOTE: Any malformed field will result in fallback for that specific rollout.
            capabilities = {}
            for cap_name, rollout_dict in raw_policy.get("capabilities", {}).items():
                if not isinstance(rollout_dict, dict):
                    logger.error(
                        "Capability rollout must be a JSON object; skipping",
                        extra={"capability": cap_name, "type": str(type(rollout_dict))},
                    )
                    continue

                try:
                    capabilities[cap_name] = CapabilityRollout(
                        enabled=rollout_dict.get("enabled", False),
                        rollout_percentage=rollout_dict.get("rollout_percentage", 100),
                        tier_allow_list=frozenset(
                            TenantTier(t) for t in rollout_dict.get("tier_allow_list", [])
                        ),
                        tenant_allow_list=frozenset(rollout_dict.get("tenant_allow_list", [])),
                    )
                except (ValueError, TypeError):
                    logger.error(
                        "Malformed capability rollout in policy; skipping",
                        extra={"capability": cap_name, "rollout_dict": rollout_dict},
                    )

            return TenantCapabilityPolicy(
                schema_version=str(raw_policy.get("schema_version", "unknown")),
                capabilities=capabilities,
                killed_capabilities=frozenset(raw_policy.get("killed_capabilities", [])),
            )
        except Exception:
            logger.exception("Failed to fetch AppConfig capability policy; using fallback")
            return TenantCapabilityPolicy.safe_fallback()
