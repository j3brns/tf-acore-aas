"""Unit tests for package_agent.py, deploy_agent.py, and register_agent.py (TASK-035)."""

import importlib.util
import sys
import zipfile
from pathlib import Path
from typing import Any

import boto3
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


package_agent = _load_module("package_agent")
build_layer = _load_module("build_layer")
deploy_agent = _load_module("deploy_agent")
register_agent = _load_module("register_agent")

_REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# package_agent.py tests
# ---------------------------------------------------------------------------


def test_package_agent_creates_zip(tmp_path):
    # Setup fake agent dir
    agent_name = "test-agent"
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "handler.py").write_text("print('hello')")
    (agent_dir / "pyproject.toml").write_text("[project]\nname='test-agent'")
    (agent_dir / "__pycache__").mkdir()
    (agent_dir / "__pycache__" / "test.pyc").write_text("binary")

    package_agent.package_agent(agent_name, repo_root=tmp_path)

    zip_path = tmp_path / ".build" / f"{agent_name}-code.zip"
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "handler.py" in names
        assert "pyproject.toml" in names
        assert "__pycache__/test.pyc" not in names


# ---------------------------------------------------------------------------
# deploy_agent.py tests
# ---------------------------------------------------------------------------


@mock_aws
def test_deploy_agent_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("MOCK_RUNTIME", "true")
    monkeypatch.setenv("LOCALSTACK_ENDPOINT", "mock")

    agent_name = "echo-agent"
    # Create fake zip
    build_dir = tmp_path / ".build"
    build_dir.mkdir()
    zip_path = build_dir / f"{agent_name}-code.zip"
    zip_path.write_text("fake zip content")

    # Create fake pyproject.toml
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore.deployment]
type = "zip"
""")

    bucket_name = "platform-artifacts-dev-local"
    s3 = boto3.client("s3", region_name=_REGION)
    s3.create_bucket(
        Bucket=bucket_name,
        CreateBucketConfiguration={"LocationConstraint": _REGION},
    )
    monkeypatch.setenv("PLATFORM_LAYER_BUCKET", bucket_name)

    # Setup SSM for deploy_agent
    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name=f"/platform/layers/{agent_name}/s3-key", Value="layers/fake-deps.zip", Type="String"
    )

    deploy_agent.deploy_agent(agent_name, "dev", repo_root=tmp_path)

    # Verify S3 upload
    response = s3.list_objects_v2(Bucket=bucket_name)
    keys = [obj["Key"] for obj in response.get("Contents", [])]
    assert f"scripts/{agent_name}/1.2.3.zip" in keys

    # Verify SSM parameters
    ssm = boto3.client("ssm", region_name=_REGION)
    s3_key_param = ssm.get_parameter(Name=f"/platform/agents/{agent_name}/script-s3-key")
    assert s3_key_param["Parameter"]["Value"] == f"scripts/{agent_name}/1.2.3.zip"
    runtime_arn_param = ssm.get_parameter(Name=f"/platform/agents/{agent_name}/runtime-arn")
    assert "runtime/echo-agent" in runtime_arn_param["Parameter"]["Value"]


# ---------------------------------------------------------------------------
# register_agent.py tests
# ---------------------------------------------------------------------------


@mock_aws
def test_register_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("LOCALSTACK_ENDPOINT", "mock")

    agent_name = "echo-agent"
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
owner_team = "platform"
tier_minimum = "basic"
invocation_mode = "sync"
""")

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

    # Setup SSM values
    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(Name=f"/platform/layers/{agent_name}/hash", Value="hash123", Type="String")
    ssm.put_parameter(
        Name=f"/platform/layers/{agent_name}/s3-key", Value="layers/key.zip", Type="String"
    )
    ssm.put_parameter(
        Name=f"/platform/agents/{agent_name}/script-s3-key", Value="scripts/key.zip", Type="String"
    )
    ssm.put_parameter(
        Name=f"/platform/agents/{agent_name}/runtime-arn", Value="arn:runtime", Type="String"
    )

    register_agent.register_agent(agent_name, "dev", repo_root=tmp_path)

    # Verify DynamoDB record
    ddb = boto3.resource("dynamodb", region_name=_REGION)
    table = ddb.Table("platform-agents")
    response = table.get_item(Key={"PK": f"AGENT#{agent_name}", "SK": "VERSION#1.2.3"})
    item = response["Item"]
    assert item["agent_name"] == agent_name
    assert item["version"] == "1.2.3"
    assert item["layer_hash"] == "hash123"
    assert item["runtime_arn"] == "arn:runtime"

    # Verify SSM latest-version
    latest_version_param = ssm.get_parameter(Name=f"/platform/agents/{agent_name}/latest-version")
    assert latest_version_param["Parameter"]["Value"] == "1.2.3"
