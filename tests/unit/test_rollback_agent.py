"""Unit tests for rollback_agent.py."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import boto3
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rollback_agent = _load_module("rollback_agent")

_REGION = "eu-west-2"


@mock_aws
def test_rollback_agent_success(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("CI", "true")

    agent_name = "test-agent"
    env = "dev"
    bucket_name = "platform-artifacts-dev"
    monkeypatch.setenv("PLATFORM_LAYER_BUCKET", bucket_name)

    # Setup DynamoDB
    dynamodb = boto3.client("dynamodb", region_name=_REGION)
    dynamodb.create_table(
        TableName="platform-agents",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table = boto3.resource("dynamodb", region_name=_REGION).Table("platform-agents")

    # Seed two versions
    # v1.0.0 (good)
    table.put_item(
        Item={
            "PK": f"AGENT#{agent_name}",
            "SK": "VERSION#1.0.0",
            "agent_name": agent_name,
            "version": "1.0.0",
            "layer_hash": "hash100",
            "layer_s3_key": "layers/deps-100.zip",
            "script_s3_key": "scripts/code-100.zip",
            "deployed_at": "2026-03-01T10:00:00Z",
        }
    )
    # v1.1.0 (bad)
    table.put_item(
        Item={
            "PK": f"AGENT#{agent_name}",
            "SK": "VERSION#1.1.0",
            "agent_name": agent_name,
            "version": "1.1.0",
            "layer_hash": "hash110",
            "layer_s3_key": "layers/deps-110.zip",
            "script_s3_key": "scripts/code-110.zip",
            "deployed_at": "2026-03-01T11:00:00Z",
        }
    )

    # Setup S3
    s3 = boto3.client("s3", region_name=_REGION)
    s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": _REGION})

    # Setup SSM with current state (pointing to v1.1.0)
    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/latest-version", Value="1.1.0", Type="String"
    )
    ssm.put_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/script-s3-key",
        Value="scripts/code-110.zip",
        Type="String",
    )
    ssm.put_parameter(
        Name=f"/platform/layers/{env}/{agent_name}/hash", Value="hash110", Type="String"
    )
    ssm.put_parameter(
        Name=f"/platform/layers/{env}/{agent_name}/s3-key",
        Value="layers/deps-110.zip",
        Type="String",
    )

    # Run Rollback
    success = rollback_agent.rollback_agent(agent_name, env)
    assert success is True

    # Verify v1.1.0 is deleted
    response = table.get_item(Key={"PK": f"AGENT#{agent_name}", "SK": "VERSION#1.1.0"})
    assert "Item" not in response

    # Verify v1.0.0 still exists
    response = table.get_item(Key={"PK": f"AGENT#{agent_name}", "SK": "VERSION#1.0.0"})
    assert "Item" in response

    # Verify SSM points back to v1.0.0
    latest_version_param = ssm.get_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/latest-version"
    )
    assert latest_version_param["Parameter"]["Value"] == "1.0.0"

    script_s3_key_param = ssm.get_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/script-s3-key"
    )
    assert script_s3_key_param["Parameter"]["Value"] == "scripts/code-100.zip"

    hash_param = ssm.get_parameter(Name=f"/platform/layers/{env}/{agent_name}/hash")
    assert hash_param["Parameter"]["Value"] == "hash100"

    s3_key_param = ssm.get_parameter(Name=f"/platform/layers/{env}/{agent_name}/s3-key")
    assert s3_key_param["Parameter"]["Value"] == "layers/deps-100.zip"


@mock_aws
def test_rollback_agent_fails_no_previous(monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    agent_name = "test-agent"
    env = "dev"

    # Setup DynamoDB with only one version
    dynamodb = boto3.client("dynamodb", region_name=_REGION)
    dynamodb.create_table(
        TableName="platform-agents",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table = boto3.resource("dynamodb", region_name=_REGION).Table("platform-agents")
    table.put_item(
        Item={
            "PK": f"AGENT#{agent_name}",
            "SK": "VERSION#1.0.0",
            "agent_name": agent_name,
            "version": "1.0.0",
        }
    )

    # Run Rollback - should fail
    success = rollback_agent.rollback_agent(agent_name, env)
    assert success is False
