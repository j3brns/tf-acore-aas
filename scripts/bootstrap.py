"""
bootstrap.py — First-time platform bootstrap script.

Ordered steps with validation at each. Idempotent and safe to re-run.

Usage:
    uv run python scripts/bootstrap.py --step <step> --env <env>

Implemented in TASK-028.
ADRs: ADR-007
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("bootstrap")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CDK_DIR = REPO_ROOT / "infra" / "cdk"

DEFAULT_HOME_REGION = "eu-west-2"
DEFAULT_RUNTIME_REGION = "eu-west-1"
DEFAULT_FALLBACK_REGION = "eu-central-1"

STEP_ORDER: tuple[str, ...] = (
    "cdk-bootstrap",
    "seed-secrets",
    "gitlab-oidc",
    "first-deploy",
    "post-deploy",
    "verify",
    "delete-bootstrap-user",
)

STEP_CHOICES: tuple[str, ...] = ("all", *STEP_ORDER)

STACKS_HOME: tuple[str, ...] = (
    "platform-network-{env}",
    "platform-identity-{env}",
    "platform-core-{env}",
    "platform-tenant-stub-{env}",
    "platform-observability-{env}",
)
STACKS_RUNTIME: tuple[str, ...] = ("platform-agentcore-{env}",)

SECRET_TEMPLATES: dict[str, str] = {
    "entra_client_id": "platform/{env}/entra/client-id",
    "entra_tenant_id": "platform/{env}/entra/tenant-id",
    "entra_client_secret": "platform/{env}/entra/client-secret",  # pragma: allowlist secret
    "platform_private_key_passphrase": (
        "platform/{env}/platform/private-key-passphrase"  # pragma: allowlist secret
    ),
}

REPORT_CONTENT_TYPE = "application/json"
REPORT_KEY_DEFAULT = "bootstrap-report.json"


class StepExecutionError(RuntimeError):
    """Raised when a bootstrap step command fails."""


@dataclass(frozen=True)
class BootstrapContext:
    env: str
    aws_region: str
    home_region: str
    runtime_region: str
    fallback_region: str
    account_id: str
    caller_arn: str
    report_bucket: str
    report_key: str


StepHandler = Callable[[BootstrapContext], dict[str, Any]]


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(tz=UTC).isoformat()


def require_aws_region() -> str:
    """Read AWS_REGION from environment and fail fast if missing."""
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def _input_value(env_name: str, prompt: str, *, secret: bool) -> str:
    """Read from env first; fall back to interactive prompt."""
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env.strip()

    if not sys.stdin.isatty():
        raise RuntimeError(f"{env_name} must be set when stdin is not interactive")

    if secret:
        value = getpass.getpass(prompt)
    else:
        value = input(prompt).strip()

    if not value:
        raise RuntimeError(f"No value provided for {env_name}")
    return value


def run_command(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    """Run a command and return redacted execution metadata."""
    cmd_display = " ".join(command)
    logger.info("Running: %s", cmd_display)
    result = subprocess.run(
        command,
        cwd=str(cwd or REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )

    details: dict[str, Any] = {
        "command": cmd_display,
        "returnCode": result.returncode,
    }
    if result.stdout.strip():
        details["stdoutTail"] = result.stdout.strip()[-8000:]
    if result.stderr.strip():
        details["stderrTail"] = result.stderr.strip()[-8000:]

    if result.returncode != 0:
        raise StepExecutionError(f"Command failed ({result.returncode}): {cmd_display}")

    return details


def build_context(env_name: str) -> BootstrapContext:
    """Create runtime context from environment + caller identity."""
    aws_region = require_aws_region()
    home_region = os.environ.get("PLATFORM_HOME_REGION", DEFAULT_HOME_REGION)
    runtime_region = os.environ.get("PLATFORM_RUNTIME_REGION", DEFAULT_RUNTIME_REGION)
    fallback_region = os.environ.get("PLATFORM_FALLBACK_REGION", DEFAULT_FALLBACK_REGION)
    report_bucket = os.environ.get(
        "BOOTSTRAP_REPORT_BUCKET",
        f"platform-bootstrap-reports-{env_name}",
    )
    report_key = os.environ.get("BOOTSTRAP_REPORT_KEY", REPORT_KEY_DEFAULT)

    sts = boto3.client("sts", region_name=aws_region)
    identity = sts.get_caller_identity()
    account_id = str(identity["Account"])
    caller_arn = str(identity["Arn"])

    return BootstrapContext(
        env=env_name,
        aws_region=aws_region,
        home_region=home_region,
        runtime_region=runtime_region,
        fallback_region=fallback_region,
        account_id=account_id,
        caller_arn=caller_arn,
        report_bucket=report_bucket,
        report_key=report_key,
    )


def expected_bootstrap_regions(ctx: BootstrapContext) -> tuple[str, ...]:
    """Ordered unique list of target bootstrap regions."""
    ordered = (ctx.home_region, ctx.runtime_region, ctx.fallback_region)
    deduped = tuple(dict.fromkeys(ordered))
    return deduped


def _stack_status(cloudformation_client: Any, stack_name: str) -> str:
    """Read a CloudFormation stack status, raising when missing."""
    response = cloudformation_client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", [])
    if not stacks:
        raise RuntimeError(f"Stack missing: {stack_name}")
    status = str(stacks[0].get("StackStatus", ""))
    if not status:
        raise RuntimeError(f"Stack status missing: {stack_name}")
    return status


def _validate_stack_complete(status: str, stack_name: str, region: str) -> None:
    """Validate a CloudFormation stack reached a complete status."""
    if not status.endswith("_COMPLETE"):
        raise RuntimeError(f"Stack {stack_name} in {region} is not complete (status={status})")


def ensure_report_bucket(s3_client: Any, ctx: BootstrapContext) -> None:
    """Create report bucket if missing; enforce encryption and public-blocking."""
    try:
        s3_client.head_bucket(Bucket=ctx.report_bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            create_args: dict[str, Any] = {"Bucket": ctx.report_bucket}
            if ctx.aws_region != "us-east-1":
                create_args["CreateBucketConfiguration"] = {"LocationConstraint": ctx.aws_region}
            s3_client.create_bucket(**create_args)
            logger.info("Created report bucket %s", ctx.report_bucket)
        else:
            raise

    s3_client.put_public_access_block(
        Bucket=ctx.report_bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3_client.put_bucket_encryption(
        Bucket=ctx.report_bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "AES256",
                    }
                }
            ]
        },
    )


def initial_report(ctx: BootstrapContext) -> dict[str, Any]:
    """Build an empty bootstrap report object."""
    return {
        "taskId": "TASK-028",
        "environment": ctx.env,
        "accountId": ctx.account_id,
        "callerArn": ctx.caller_arn,
        "awsRegion": ctx.aws_region,
        "updatedAt": utc_now_iso(),
        "steps": [],
    }


def load_report(s3_client: Any, ctx: BootstrapContext) -> dict[str, Any]:
    """Load existing report from S3 or return a fresh report."""
    try:
        response = s3_client.get_object(Bucket=ctx.report_bucket, Key=ctx.report_key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}:
            return initial_report(ctx)
        raise

    body = response["Body"].read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        return initial_report(ctx)
    if "steps" not in parsed or not isinstance(parsed["steps"], list):
        parsed["steps"] = []
    return parsed


def persist_report(s3_client: Any, ctx: BootstrapContext, report: dict[str, Any]) -> str:
    """Write the bootstrap report JSON to S3."""
    report["updatedAt"] = utc_now_iso()
    payload = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
    s3_client.put_object(
        Bucket=ctx.report_bucket,
        Key=ctx.report_key,
        Body=payload,
        ContentType=REPORT_CONTENT_TYPE,
    )
    s3_uri = f"s3://{ctx.report_bucket}/{ctx.report_key}"
    logger.info("Wrote bootstrap report: %s", s3_uri)
    return s3_uri


def upsert_secret(
    secrets_client: Any,
    *,
    secret_name: str,
    secret_value: str,
    description: str,
) -> str:
    """Create or update a secret. Returns 'created' or 'updated'."""
    try:
        secrets_client.create_secret(
            Name=secret_name,
            SecretString=secret_value,
            Description=description,
        )
        return "created"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code != "ResourceExistsException":
            raise

    secrets_client.put_secret_value(
        SecretId=secret_name,
        SecretString=secret_value,
    )
    return "updated"


def step_cdk_bootstrap(ctx: BootstrapContext) -> dict[str, Any]:
    """Run CDK bootstrap in all required regions."""
    regions = expected_bootstrap_regions(ctx)
    commands = []
    for region in regions:
        command = [
            "npx",
            "cdk",
            "bootstrap",
            f"aws://{ctx.account_id}/{region}",
            "--context",
            f"env={ctx.env}",
        ]
        commands.append(run_command(command, cwd=CDK_DIR))
    return {"regions": list(regions), "commands": commands}


def validate_cdk_bootstrap(ctx: BootstrapContext) -> dict[str, Any]:
    """Validate CDKToolkit stack exists and is complete in target regions."""
    regions = expected_bootstrap_regions(ctx)
    statuses: dict[str, str] = {}
    for region in regions:
        cfn = boto3.client("cloudformation", region_name=region)
        status = _stack_status(cfn, "CDKToolkit")
        _validate_stack_complete(status, "CDKToolkit", region)
        statuses[region] = status
    return {"cdkToolkit": statuses}


def step_seed_secrets(ctx: BootstrapContext) -> dict[str, Any]:
    """Write initial Entra/platform bootstrap secrets."""
    values = {
        "entra_client_id": _input_value(
            "BOOTSTRAP_ENTRA_CLIENT_ID",
            "Entra client ID: ",
            secret=False,
        ),
        "entra_tenant_id": _input_value(
            "BOOTSTRAP_ENTRA_TENANT_ID",
            "Entra tenant ID: ",
            secret=False,
        ),
        "entra_client_secret": _input_value(
            "BOOTSTRAP_ENTRA_CLIENT_SECRET",
            "Entra client secret: ",
            secret=True,
        ),
        "platform_private_key_passphrase": _input_value(
            "BOOTSTRAP_PLATFORM_PRIVATE_KEY_PASSPHRASE",
            "Platform private key passphrase: ",
            secret=True,
        ),
    }

    secrets_client = boto3.client("secretsmanager", region_name=ctx.aws_region)
    secret_names: list[str] = []
    created = 0
    updated = 0

    for key, template in SECRET_TEMPLATES.items():
        name = template.format(env=ctx.env)
        result = upsert_secret(
            secrets_client,
            secret_name=name,
            secret_value=values[key],
            description=f"Bootstrap secret ({ctx.env}): {key}",
        )
        secret_names.append(name)
        if result == "created":
            created += 1
        else:
            updated += 1

    return {
        "secretNames": secret_names,
        "created": created,
        "updated": updated,
    }


def validate_seed_secrets(ctx: BootstrapContext) -> dict[str, Any]:
    """Validate required secrets exist."""
    secrets_client = boto3.client("secretsmanager", region_name=ctx.aws_region)
    found: list[str] = []
    for template in SECRET_TEMPLATES.values():
        secret_name = template.format(env=ctx.env)
        secrets_client.describe_secret(SecretId=secret_name)
        found.append(secret_name)
    return {"secretNames": found}


def _find_gitlab_oidc_provider_arn(iam_client: Any) -> str | None:
    """Return OIDC provider ARN for gitlab.com if present."""
    providers = iam_client.list_open_id_connect_providers().get("OpenIDConnectProviderList", [])
    for provider in providers:
        arn = provider.get("Arn")
        if not arn:
            continue
        details = iam_client.get_open_id_connect_provider(OpenIDConnectProviderArn=arn)
        url = str(details.get("Url", "")).strip()
        if url in {"gitlab.com", "https://gitlab.com"}:
            return str(arn)
    return None


def step_gitlab_oidc(ctx: BootstrapContext) -> dict[str, Any]:
    """Deploy identity stack to wire GitLab OIDC + pipeline roles."""
    command = [
        "npx",
        "cdk",
        "deploy",
        f"platform-identity-{ctx.env}",
        "--context",
        f"env={ctx.env}",
        "--require-approval",
        "never",
    ]
    command_details = run_command(command, cwd=CDK_DIR)

    role_names = [
        f"platform-pipeline-validate-{ctx.env}",
        "platform-pipeline-deploy-dev",
        "platform-pipeline-deploy-staging",
        "platform-pipeline-deploy-prod",
    ]
    iam_client = boto3.client("iam", region_name=ctx.aws_region)
    role_arns = {
        role_name: str(iam_client.get_role(RoleName=role_name)["Role"]["Arn"])
        for role_name in role_names
    }

    logger.info("MANUAL STEP: set these GitLab CI/CD variables:")
    logger.info("  PLATFORM_PIPELINE_VALIDATE_ROLE_ARN=%s", role_arns[role_names[0]])
    logger.info("  PLATFORM_PIPELINE_DEPLOY_DEV_ROLE_ARN=%s", role_arns[role_names[1]])
    logger.info("  PLATFORM_PIPELINE_DEPLOY_STAGING_ROLE_ARN=%s", role_arns[role_names[2]])
    logger.info("  PLATFORM_PIPELINE_DEPLOY_PROD_ROLE_ARN=%s", role_arns[role_names[3]])

    provider_arn = _find_gitlab_oidc_provider_arn(iam_client)
    return {
        "command": command_details,
        "gitlabOidcProviderArn": provider_arn,
        "roleArns": role_arns,
    }


def validate_gitlab_oidc(ctx: BootstrapContext) -> dict[str, Any]:
    """Validate GitLab OIDC provider and pipeline roles exist."""
    iam_client = boto3.client("iam", region_name=ctx.aws_region)
    provider_arn = _find_gitlab_oidc_provider_arn(iam_client)
    if not provider_arn:
        raise RuntimeError("GitLab OIDC provider not found")

    role_names = [
        f"platform-pipeline-validate-{ctx.env}",
        "platform-pipeline-deploy-dev",
        "platform-pipeline-deploy-staging",
        "platform-pipeline-deploy-prod",
    ]

    role_arns: dict[str, str] = {}
    for role_name in role_names:
        role = iam_client.get_role(RoleName=role_name)["Role"]
        role_arns[role_name] = str(role["Arn"])

    return {
        "gitlabOidcProviderArn": provider_arn,
        "roleArns": role_arns,
    }


def step_first_deploy(ctx: BootstrapContext) -> dict[str, Any]:
    """Deploy all CDK stacks from local workstation (non-prod only)."""
    if ctx.env == "prod":
        raise RuntimeError("first-deploy is disabled for prod; use CI/CD pipeline")

    command = [
        "npx",
        "cdk",
        "deploy",
        "--all",
        "--context",
        f"env={ctx.env}",
        "--require-approval",
        "never",
    ]
    return {"command": run_command(command, cwd=CDK_DIR)}


def _validate_expected_stacks(ctx: BootstrapContext) -> dict[str, dict[str, str]]:
    """Validate all expected stacks are complete in home/runtime regions."""
    statuses_home: dict[str, str] = {}
    statuses_runtime: dict[str, str] = {}

    cfn_home = boto3.client("cloudformation", region_name=ctx.home_region)
    for template in STACKS_HOME:
        stack_name = template.format(env=ctx.env)
        status = _stack_status(cfn_home, stack_name)
        _validate_stack_complete(status, stack_name, ctx.home_region)
        statuses_home[stack_name] = status

    cfn_runtime = boto3.client("cloudformation", region_name=ctx.runtime_region)
    for template in STACKS_RUNTIME:
        stack_name = template.format(env=ctx.env)
        status = _stack_status(cfn_runtime, stack_name)
        _validate_stack_complete(status, stack_name, ctx.runtime_region)
        statuses_runtime[stack_name] = status

    return {
        "homeRegion": statuses_home,
        "runtimeRegion": statuses_runtime,
    }


def validate_first_deploy(ctx: BootstrapContext) -> dict[str, Any]:
    """Validate all expected stacks are deployed and complete."""
    return _validate_expected_stacks(ctx)


def _upsert_ssm_parameter(ssm_client: Any, *, name: str, value: str) -> None:
    """Put/overwrite string SSM parameter."""
    ssm_client.put_parameter(
        Name=name,
        Value=value,
        Type="String",
        Overwrite=True,
    )


def step_post_deploy(ctx: BootstrapContext) -> dict[str, Any]:
    """Seed bootstrap config + first tenant/agent records."""
    ssm_client = boto3.client("ssm", region_name=ctx.home_region)
    ddb_resource = boto3.resource("dynamodb", region_name=ctx.home_region)

    admin_tenant_id = os.environ.get("BOOTSTRAP_ADMIN_TENANT_ID", "t-admin-001")
    app_id = os.environ.get("BOOTSTRAP_ADMIN_APP_ID", f"platform-{ctx.env}")
    owner_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "platform-admin@example.invalid")
    owner_team = os.environ.get("BOOTSTRAP_ADMIN_TEAM", "platform")
    now = utc_now_iso()

    ssm_params = {
        "/platform/config/runtime-region": ctx.runtime_region,
        "/platform/config/fallback-region": ctx.fallback_region,
        "/platform/config/env": ctx.env,
    }
    for name, value in ssm_params.items():
        _upsert_ssm_parameter(ssm_client, name=name, value=value)

    tenants_table = ddb_resource.Table("platform-tenants")
    tenants_table.put_item(
        Item={
            "PK": f"TENANT#{admin_tenant_id}",
            "SK": "METADATA",
            "tenant_id": admin_tenant_id,
            "tenantId": admin_tenant_id,
            "app_id": app_id,
            "appId": app_id,
            "display_name": "Platform Admin Tenant",
            "displayName": "Platform Admin Tenant",
            "tier": "premium",
            "status": "active",
            "created_at": now,
            "createdAt": now,
            "updated_at": now,
            "updatedAt": now,
            "owner_email": owner_email,
            "ownerEmail": owner_email,
            "owner_team": owner_team,
            "ownerTeam": owner_team,
            "account_id": ctx.account_id,
            "accountId": ctx.account_id,
            "runtime_region": ctx.runtime_region,
            "runtimeRegion": ctx.runtime_region,
            "fallback_region": ctx.fallback_region,
            "fallbackRegion": ctx.fallback_region,
            "monthly_budget_usd": Decimal("10000"),
            "monthlyBudgetUsd": Decimal("10000"),
        }
    )

    agents_table = ddb_resource.Table("platform-agents")
    agents_table.put_item(
        Item={
            "PK": "AGENT#echo-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "echo-agent",
            "version": "1.0.0",
            "owner_team": owner_team,
            "tier_minimum": "basic",
            "layer_hash": os.environ.get("BOOTSTRAP_ECHO_LAYER_HASH", "bootstrap-seeded"),
            "layer_s3_key": os.environ.get(
                "BOOTSTRAP_ECHO_LAYER_S3_KEY",
                "layers/echo-agent/1.0.0-bootstrap-seeded.zip",
            ),
            "script_s3_key": os.environ.get(
                "BOOTSTRAP_ECHO_SCRIPT_S3_KEY",
                "scripts/echo-agent/1.0.0.zip",
            ),
            "runtime_arn": os.environ.get("BOOTSTRAP_ECHO_RUNTIME_ARN", ""),
            "deployed_at": now,
            "invocation_mode": "sync",
            "streaming_enabled": True,
            "estimated_duration_seconds": 30,
        }
    )

    return {
        "tenantId": admin_tenant_id,
        "agentName": "echo-agent",
        "ssmParameters": sorted(ssm_params.keys()),
    }


def validate_post_deploy(ctx: BootstrapContext) -> dict[str, Any]:
    """Validate seeded config + records are present."""
    ssm_client = boto3.client("ssm", region_name=ctx.home_region)
    ddb_resource = boto3.resource("dynamodb", region_name=ctx.home_region)
    admin_tenant_id = os.environ.get("BOOTSTRAP_ADMIN_TENANT_ID", "t-admin-001")

    param_names = [
        "/platform/config/runtime-region",
        "/platform/config/fallback-region",
        "/platform/config/env",
    ]
    params = ssm_client.get_parameters(Names=param_names).get("Parameters", [])
    values: dict[str, str] = {}
    for item in params:
        name = item.get("Name")
        value = item.get("Value")
        if name is None or value is None:
            continue
        values[str(name)] = str(value)

    if values.get("/platform/config/runtime-region") != ctx.runtime_region:
        raise RuntimeError("runtime-region SSM parameter mismatch")

    if values.get("/platform/config/fallback-region") != ctx.fallback_region:
        raise RuntimeError("fallback-region SSM parameter mismatch")

    if values.get("/platform/config/env") != ctx.env:
        raise RuntimeError("env SSM parameter mismatch")

    tenants_table = ddb_resource.Table("platform-tenants")
    tenant_item = tenants_table.get_item(
        Key={"PK": f"TENANT#{admin_tenant_id}", "SK": "METADATA"}
    ).get("Item")
    if not tenant_item:
        raise RuntimeError("Admin tenant seed record missing")

    agents_table = ddb_resource.Table("platform-agents")
    agent_item = agents_table.get_item(Key={"PK": "AGENT#echo-agent", "SK": "VERSION#1.0.0"}).get(
        "Item"
    )
    if not agent_item:
        raise RuntimeError("Echo-agent registry record missing")

    return {
        "tenantId": admin_tenant_id,
        "agentName": "echo-agent",
        "ssmParameters": values,
    }


def _try_smoke_invoke(ctx: BootstrapContext) -> dict[str, Any]:
    """Invoke echo-agent when BOOTSTRAP_ADMIN_JWT is provided.

    This is optional because some environments may not have Entra/JWT setup at
    the moment verify is run.
    """
    token = os.environ.get("BOOTSTRAP_ADMIN_JWT", "").strip()
    if not token:
        return {
            "status": "skipped",
            "reason": "BOOTSTRAP_ADMIN_JWT not set",
        }

    ssm_client = boto3.client("ssm", region_name=ctx.home_region)
    param_name = f"/platform/core/{ctx.env}/rest-api-id"
    response = ssm_client.get_parameter(Name=param_name)
    parameter = response.get("Parameter", {})
    rest_api_value = parameter.get("Value")
    if not rest_api_value:
        raise RuntimeError(f"Missing SSM parameter value: {param_name}")
    rest_api_id = str(rest_api_value)

    payload = json.dumps({"input": "bootstrap smoke test"}).encode("utf-8")
    paths = [
        "/prod/v1/agents/echo-agent/invoke",
        "/prod/v1/invoke",
    ]

    attempts: list[dict[str, Any]] = []
    for path in paths:
        url = f"https://{rest_api_id}.execute-api.{ctx.home_region}.amazonaws.com{path}"
        request = urllib.request.Request(
            url=url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as result:
                code = result.getcode()
                body = result.read().decode("utf-8")
            attempts.append({"url": url, "statusCode": code})
            if code in {200, 202}:
                return {
                    "status": "passed",
                    "statusCode": code,
                    "url": url,
                    "responseTail": body[-2000:],
                    "attempts": attempts,
                }
        except urllib.error.HTTPError as exc:
            attempts.append({"url": url, "statusCode": exc.code})
            continue

    raise RuntimeError(f"Smoke invoke failed for all candidate paths: {attempts}")


def step_verify(ctx: BootstrapContext) -> dict[str, Any]:
    """Run smoke checks for the bootstrapped environment."""
    stack_details = _validate_expected_stacks(ctx)
    seed_details = validate_post_deploy(ctx)
    smoke = _try_smoke_invoke(ctx)
    return {
        "stacks": stack_details,
        "seeded": seed_details,
        "smokeInvoke": smoke,
    }


def validate_verify(ctx: BootstrapContext) -> dict[str, Any]:
    """Verify validation stage returns healthy status."""
    return step_verify(ctx)


def _resolve_bootstrap_user(ctx: BootstrapContext) -> str:
    """Resolve bootstrap IAM username from env or caller ARN."""
    explicit = os.environ.get("BOOTSTRAP_IAM_USER", "").strip()
    if explicit:
        return explicit

    prefix = f"arn:aws:iam::{ctx.account_id}:user/"
    if ctx.caller_arn.startswith(prefix):
        return ctx.caller_arn[len(prefix) :]

    raise RuntimeError(
        "Set BOOTSTRAP_IAM_USER to delete the bootstrap IAM user "
        "when current caller is not an IAM user ARN"
    )


def step_delete_bootstrap_user(ctx: BootstrapContext) -> dict[str, Any]:
    """Delete temporary bootstrap IAM user and credentials."""
    iam_client = boto3.client("iam", region_name=ctx.aws_region)
    user_name = _resolve_bootstrap_user(ctx)

    try:
        iam_client.get_user(UserName=user_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "NoSuchEntity":
            return {
                "userName": user_name,
                "deleted": False,
                "reason": "already missing",
            }
        raise

    deleted_access_keys = 0
    for key in iam_client.list_access_keys(UserName=user_name).get("AccessKeyMetadata", []):
        access_key_id = str(key["AccessKeyId"])
        iam_client.delete_access_key(UserName=user_name, AccessKeyId=access_key_id)
        deleted_access_keys += 1

    try:
        iam_client.delete_login_profile(UserName=user_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code", "") != "NoSuchEntity":
            raise

    for policy in iam_client.list_attached_user_policies(UserName=user_name).get(
        "AttachedPolicies", []
    ):
        iam_client.detach_user_policy(
            UserName=user_name,
            PolicyArn=str(policy["PolicyArn"]),
        )

    for policy_name in iam_client.list_user_policies(UserName=user_name).get("PolicyNames", []):
        iam_client.delete_user_policy(UserName=user_name, PolicyName=str(policy_name))

    for group in iam_client.list_groups_for_user(UserName=user_name).get("Groups", []):
        iam_client.remove_user_from_group(
            UserName=user_name,
            GroupName=str(group["GroupName"]),
        )

    iam_client.delete_user(UserName=user_name)

    return {
        "userName": user_name,
        "deleted": True,
        "deletedAccessKeys": deleted_access_keys,
    }


def validate_delete_bootstrap_user(ctx: BootstrapContext) -> dict[str, Any]:
    """Validate bootstrap IAM user no longer exists."""
    iam_client = boto3.client("iam", region_name=ctx.aws_region)
    user_name = _resolve_bootstrap_user(ctx)
    try:
        iam_client.get_user(UserName=user_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "NoSuchEntity":
            return {"userName": user_name, "deleted": True}
        raise

    raise RuntimeError(f"Bootstrap IAM user still exists: {user_name}")


STEP_HANDLERS: dict[str, tuple[StepHandler, StepHandler]] = {
    "cdk-bootstrap": (step_cdk_bootstrap, validate_cdk_bootstrap),
    "seed-secrets": (step_seed_secrets, validate_seed_secrets),
    "gitlab-oidc": (step_gitlab_oidc, validate_gitlab_oidc),
    "first-deploy": (step_first_deploy, validate_first_deploy),
    "post-deploy": (step_post_deploy, validate_post_deploy),
    "verify": (step_verify, validate_verify),
    "delete-bootstrap-user": (step_delete_bootstrap_user, validate_delete_bootstrap_user),
}


def execute_step(
    *,
    step_name: str,
    ctx: BootstrapContext,
    report: dict[str, Any],
    s3_client: Any,
) -> None:
    """Execute one step, run validation, and persist report entry."""
    handler, validator = STEP_HANDLERS[step_name]
    started_at = utc_now_iso()
    status = "passed"
    details: dict[str, Any] = {}

    try:
        action_details = handler(ctx)
        validation_details = validator(ctx)
        details = {
            "action": action_details,
            "validation": validation_details,
        }
    except Exception as exc:
        status = "failed"
        details = {
            "errorType": exc.__class__.__name__,
            "errorMessage": str(exc),
        }
        raise
    finally:
        report.setdefault("steps", []).append(
            {
                "step": step_name,
                "status": status,
                "startedAt": started_at,
                "completedAt": utc_now_iso(),
                "details": details,
            }
        )
        persist_report(s3_client, ctx, report)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Platform bootstrap runner")
    parser.add_argument(
        "--step",
        required=True,
        choices=STEP_CHOICES,
        help="Bootstrap step to run",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["dev", "staging", "prod"],
        help="Target environment",
    )
    return parser.parse_args(argv)


def run_bootstrap(step: str, env_name: str) -> str:
    """Run selected bootstrap step(s); returns report S3 URI."""
    ctx = build_context(env_name)
    s3_client = boto3.client("s3", region_name=ctx.aws_region)

    ensure_report_bucket(s3_client, ctx)
    report = load_report(s3_client, ctx)

    steps = STEP_ORDER if step == "all" else (step,)
    for step_name in steps:
        logger.info("==> Step: %s", step_name)
        execute_step(step_name=step_name, ctx=ctx, report=report, s3_client=s3_client)

    return f"s3://{ctx.report_bucket}/{ctx.report_key}"


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    try:
        report_uri = run_bootstrap(step=args.step, env_name=args.env)
    except Exception as exc:
        logger.error("Bootstrap failed: %s", exc)
        return 1

    logger.info("Bootstrap step(s) completed. Report: %s", report_uri)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
