from __future__ import annotations

import json
import secrets

from aws_lambda_powertools import Logger

from src.tenant_api import config
from src.tenant_api.models import TenantApiDependencies

logger = Logger(service="tenant-api-secrets")


def secret_prefix() -> str:
    return config.current_config().api_key_secret_prefix


def create_api_key_secret(
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
    app_id: str,
) -> str:
    secret_name = f"{secret_prefix().rstrip('/')}/{tenant_id}/api-key"
    secret_string = json.dumps(
        {
            "tenantId": tenant_id,
            "appId": app_id,
            "apiKey": secrets.token_urlsafe(32),
        }
    )
    response = deps.secretsmanager.create_secret(
        Name=secret_name,
        SecretString=secret_string,
        Description=f"Tenant API key for {tenant_id}",
        Tags=[
            {"Key": "tenantid", "Value": tenant_id},
            {"Key": "appid", "Value": app_id},
        ],
    )
    attach_tenant_api_key_secret_policy(
        deps,
        secret_arn=str(response["ARN"]),
        tenant_id=tenant_id,
        app_id=app_id,
    )
    return str(response["ARN"])


def attach_tenant_api_key_secret_policy(
    deps: TenantApiDependencies,
    *,
    secret_arn: str,
    tenant_id: str,
    app_id: str,
) -> None:
    tenant_mgmt_role_arn = (config.current_config().tenant_mgmt_role_arn or "").strip()
    if not tenant_mgmt_role_arn:
        logger.warning(
            "Skipping tenant API key secret resource policy: manager role ARN not configured",
            extra={"tenant_id": tenant_id, "app_id": app_id},
        )
        return

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyTenantMgmtReadback",
                "Effect": "Deny",
                "Principal": {"AWS": tenant_mgmt_role_arn},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": secret_arn,
            }
        ],
    }
    deps.secretsmanager.put_resource_policy(
        SecretId=secret_arn,
        ResourcePolicy=json.dumps(policy, separators=(",", ":")),
        BlockPublicPolicy=True,
    )
