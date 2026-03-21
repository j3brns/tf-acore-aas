"""Unit tests for package_agent.py, deploy_agent.py, and register_agent.py (TASK-035)."""

import importlib.util
import sys
import types
import zipfile
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
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


package_agent = _load_module("package_agent")
build_layer = _load_module("build_layer")
deploy_agent = _load_module("deploy_agent")
register_agent = _load_module("register_agent")
evaluate_agent = _load_module("evaluate_agent")

_REGION = "eu-west-2"


class _FakeAgentCoreClient:
    def __init__(self, *, response: dict[str, Any] | None = None, error: Exception | None = None):
        self._response = response or {}
        self._error = error

    def update_agent_code(self, **_: Any) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        return self._response


def _patch_deploy_agentcore(monkeypatch, fake_client: _FakeAgentCoreClient) -> None:
    real_client = boto3.client

    def _client(service_name: str, *args: Any, **kwargs: Any) -> Any:
        if service_name == "bedrock-agentcore":
            return fake_client
        return real_client(service_name, *args, **kwargs)

    monkeypatch.setattr(deploy_agent, "boto3", types.SimpleNamespace(client=_client))


def _makefile_recipe_lines(target: str) -> list[str]:
    makefile_lines = (REPO_ROOT / "Makefile").read_text().splitlines()
    recipe_lines: list[str] = []
    in_target = False

    for line in makefile_lines:
        if in_target:
            if line.startswith("\t"):
                recipe_lines.append(line.strip())
                continue
            break
        if line == f"{target}:":
            in_target = True

    if not recipe_lines:
        raise AssertionError(f"Target {target} not found in Makefile")

    return recipe_lines


# ---------------------------------------------------------------------------
# package_agent.py tests
# ---------------------------------------------------------------------------


def test_package_agent_creates_zip(tmp_path, monkeypatch):
    # Setup fake agent dir
    agent_name = "test-agent"
    # Monkeypatch REPO_ROOT in package_agent
    monkeypatch.setattr(package_agent, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(package_agent, "BUILD_DIR", tmp_path / ".build")

    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "handler.py").write_text("print('hello')")
    (agent_dir / "pyproject.toml").write_text("[project]\nname='test-agent'")
    (agent_dir / "__pycache__").mkdir()
    (agent_dir / "__pycache__" / "test.pyc").write_text("binary")

    package_agent.package_agent(agent_name)

    zip_path = tmp_path / ".build" / f"{agent_name}-code.zip"
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "handler.py" in names
        assert "pyproject.toml" in names
        assert "__pycache__/test.pyc" not in names


def test_agent_push_runs_tests_before_deploy_and_register():
    recipe_lines = _makefile_recipe_lines("agent-push")

    test_index = recipe_lines.index("$(MAKE) test-agent AGENT=$(AGENT)")
    deploy_index = recipe_lines.index("uv run python scripts/deploy_agent.py $(AGENT) --env $(ENV)")
    register_index = recipe_lines.index(
        "uv run python scripts/register_agent.py $(AGENT) --env $(ENV)"
    )

    assert test_index < deploy_index < register_index


# ---------------------------------------------------------------------------
# deploy_agent.py tests
# ---------------------------------------------------------------------------


def _prepare_deploy_agent_fixture(tmp_path, monkeypatch) -> tuple[str, str, str]:
    monkeypatch.setenv("AWS_REGION", _REGION)

    monkeypatch.setattr(deploy_agent, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(deploy_agent, "BUILD_DIR", tmp_path / ".build")

    agent_name = "echo-agent"
    env = "dev"
    bucket_name = "platform-artifacts-dev"
    monkeypatch.setenv("PLATFORM_LAYER_BUCKET", bucket_name)

    # Setup S3 bucket
    s3 = boto3.client("s3", region_name=_REGION)
    s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": _REGION})

    # Create fake zip
    build_dir = tmp_path / ".build"
    build_dir.mkdir()
    zip_path = build_dir / f"{agent_name}-code.zip"
    zip_path.write_text("fake zip content")

    # Setup SSM for deps-key
    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name=f"/platform/layers/{env}/{agent_name}/s3-key",
        Value="layers/echo-agent-deps-hash.zip",
        Type="String",
    )

    # Create fake pyproject.toml
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
name = "echo-agent"
owner_team = "platform"
tier_minimum = "basic"
handler = "handler:invoke"
invocation_mode = "sync"

[tool.agentcore.deployment]
type = "zip"
""")

    return agent_name, env, bucket_name


@mock_aws
def test_deploy_agent_zip_stores_runtime_arn_on_success(tmp_path, monkeypatch):
    agent_name, env, bucket_name = _prepare_deploy_agent_fixture(tmp_path, monkeypatch)
    _patch_deploy_agentcore(
        monkeypatch,
        _FakeAgentCoreClient(
            response={
                "agentArn": "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/echo-agent"
            }
        ),
    )

    assert deploy_agent.deploy_agent(agent_name, env) is True

    s3 = boto3.client("s3", region_name=_REGION)
    response = s3.list_objects_v2(Bucket=bucket_name)
    keys = [obj["Key"] for obj in response.get("Contents", [])]
    expected_script_key = f"scripts/{agent_name}/1.2.3.zip"
    assert expected_script_key in keys

    ssm = boto3.client("ssm", region_name=_REGION)
    s3_key_param = ssm.get_parameter(Name=f"/platform/agents/{env}/{agent_name}/script-s3-key")
    assert s3_key_param["Parameter"]["Value"] == expected_script_key
    runtime_arn_param = ssm.get_parameter(Name=f"/platform/agents/{env}/{agent_name}/runtime-arn")
    assert (
        runtime_arn_param["Parameter"]["Value"]
        == "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/echo-agent"
    )


@mock_aws
def test_deploy_agent_returns_false_when_runtime_update_fails(tmp_path, monkeypatch):
    agent_name, env, _ = _prepare_deploy_agent_fixture(tmp_path, monkeypatch)
    _patch_deploy_agentcore(
        monkeypatch,
        _FakeAgentCoreClient(error=RuntimeError("runtime update failed")),
    )

    assert deploy_agent.deploy_agent(agent_name, env) is False

    ssm = boto3.client("ssm", region_name=_REGION)
    s3_key_param = ssm.get_parameter(Name=f"/platform/agents/{env}/{agent_name}/script-s3-key")
    assert s3_key_param["Parameter"]["Value"] == f"scripts/{agent_name}/1.2.3.zip"
    with pytest.raises(ssm.exceptions.ParameterNotFound):
        ssm.get_parameter(Name=f"/platform/agents/{env}/{agent_name}/runtime-arn")


# ---------------------------------------------------------------------------
# register_agent.py tests
# ---------------------------------------------------------------------------


def test_register_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("CI", "true")

    monkeypatch.setattr(register_agent, "REPO_ROOT", tmp_path)

    agent_name = "echo-agent"
    env = "dev"
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
name = "echo-agent"
owner_team = "platform"
tier_minimum = "basic"
handler = "handler:invoke"
invocation_mode = "sync"
""")
    stored_items: list[dict[str, object]] = []
    latest_version_writes: list[dict[str, object]] = []

    def put_item(**kwargs):
        stored_items.append(kwargs["Item"])

    def put_parameter(**kwargs):
        latest_version_writes.append(kwargs)

    fake_table = types.SimpleNamespace(put_item=put_item)
    fake_resource = types.SimpleNamespace(Table=lambda table_name: fake_table)
    fake_ssm = types.SimpleNamespace(put_parameter=put_parameter)

    monkeypatch.setattr(
        register_agent,
        "boto3",
        types.SimpleNamespace(
            client=lambda service_name, **kwargs: fake_ssm,
            resource=lambda service_name, **kwargs: fake_resource,
        ),
    )
    monkeypatch.setattr(
        register_agent,
        "get_ssm_param",
        lambda ssm, name: {
            f"/platform/layers/{env}/{agent_name}/hash": "hash123",
            f"/platform/layers/{env}/{agent_name}/s3-key": "layers/key.zip",
            f"/platform/agents/{env}/{agent_name}/script-s3-key": "scripts/custom-key.zip",
            f"/platform/agents/{env}/{agent_name}/runtime-arn": None,
        }[name],
    )

    assert register_agent.register_agent(agent_name, env) is True

    assert len(stored_items) == 1
    item = stored_items[0]
    assert item["agent_name"] == agent_name
    assert item["version"] == "1.2.3"
    assert item["layer_hash"] == "hash123"
    assert item["script_s3_key"] == "scripts/custom-key.zip"

    assert latest_version_writes == [
        {
            "Name": f"/platform/agents/{env}/{agent_name}/latest-version",
            "Value": "1.2.3",
            "Type": "String",
            "Overwrite": True,
        }
    ]


def test_register_agent_returns_false_when_dynamodb_write_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(register_agent, "REPO_ROOT", tmp_path)

    agent_name = "echo-agent"
    env = "dev"
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
name = "echo-agent"
owner_team = "platform"
tier_minimum = "basic"
handler = "handler:invoke"
invocation_mode = "sync"
""")

    def failing_put_item(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "ddb write failed"}},
            "PutItem",
        )

    fake_table = types.SimpleNamespace(put_item=failing_put_item)
    fake_resource = types.SimpleNamespace(Table=lambda table_name: fake_table)
    fake_ssm = types.SimpleNamespace(put_parameter=lambda **kwargs: None)

    monkeypatch.setattr(
        register_agent,
        "boto3",
        types.SimpleNamespace(
            client=lambda service_name, **kwargs: fake_ssm,
            resource=lambda service_name, **kwargs: fake_resource,
        ),
    )
    monkeypatch.setattr(
        register_agent,
        "get_ssm_param",
        lambda ssm, name: {
            f"/platform/layers/{env}/{agent_name}/hash": "hash123",
            f"/platform/layers/{env}/{agent_name}/s3-key": "layers/key.zip",
            f"/platform/agents/{env}/{agent_name}/script-s3-key": "scripts/custom-key.zip",
            f"/platform/agents/{env}/{agent_name}/runtime-arn": None,
        }[name],
    )

    assert register_agent.register_agent(agent_name, env) is False


def test_register_agent_returns_false_when_latest_version_write_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(register_agent, "REPO_ROOT", tmp_path)

    agent_name = "echo-agent"
    env = "dev"
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
name = "echo-agent"
owner_team = "platform"
tier_minimum = "basic"
handler = "handler:invoke"
invocation_mode = "sync"
""")
    put_item_calls: list[dict[str, object]] = []

    def failing_put_parameter(*args, **kwargs):
        if kwargs.get("Name") == f"/platform/agents/{env}/{agent_name}/latest-version":
            raise ClientError(
                {"Error": {"Code": "InternalServerError", "Message": "ssm write failed"}},
                "PutParameter",
            )
        return None

    fake_table = types.SimpleNamespace(put_item=lambda **kwargs: put_item_calls.append(kwargs))
    fake_resource = types.SimpleNamespace(Table=lambda table_name: fake_table)
    fake_ssm = types.SimpleNamespace(put_parameter=failing_put_parameter)

    monkeypatch.setattr(
        register_agent,
        "boto3",
        types.SimpleNamespace(
            client=lambda service_name, **kwargs: fake_ssm,
            resource=lambda service_name, **kwargs: fake_resource,
        ),
    )
    monkeypatch.setattr(
        register_agent,
        "get_ssm_param",
        lambda ssm, name: {
            f"/platform/layers/{env}/{agent_name}/hash": "hash123",
            f"/platform/layers/{env}/{agent_name}/s3-key": "layers/key.zip",
            f"/platform/agents/{env}/{agent_name}/script-s3-key": "scripts/custom-key.zip",
            f"/platform/agents/{env}/{agent_name}/runtime-arn": None,
        }[name],
    )

    assert register_agent.register_agent(agent_name, env) is False
    assert len(put_item_calls) == 1


def test_register_agent_returns_false_when_manifest_invalid_before_aws(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setattr(register_agent, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(register_agent, "boto3", types.SimpleNamespace(client=None, resource=None))

    agent_dir = tmp_path / "agents" / "echo-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
name = "echo-agent"
owner_team = "platform"
tier_minimum = "basic"
invocation_mode = "sync"
""")

    assert register_agent.register_agent("echo-agent", "dev") is False


def test_deploy_agent_returns_false_when_manifest_invalid_before_aws(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setattr(deploy_agent, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(deploy_agent, "BUILD_DIR", tmp_path / ".build")

    agent_dir = tmp_path / "agents" / "echo-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
name = "echo-agent"
owner_team = "platform"
tier_minimum = "basic"
invocation_mode = "sync"
""")

    def _unexpected_client(*_args, **_kwargs):
        raise AssertionError("AWS client should not be created for invalid manifests")

    monkeypatch.setattr(deploy_agent, "boto3", types.SimpleNamespace(client=_unexpected_client))

    assert deploy_agent.deploy_agent("echo-agent", "dev") is False


def test_evaluate_agent_returns_false_when_manifest_invalid_before_aws(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate_agent, "REPO_ROOT", tmp_path)

    agent_dir = tmp_path / "agents" / "echo-agent"
    (agent_dir / "tests" / "golden").mkdir(parents=True)
    (agent_dir / "tests" / "golden" / "invoke_cases.json").write_text('{"sync": []}')
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
name = "echo-agent"
owner_team = "platform"
tier_minimum = "basic"
invocation_mode = "sync"
""")

    def _unexpected_client(*_args, **_kwargs):
        raise AssertionError("AWS client should not be created for invalid manifests")

    monkeypatch.setattr(evaluate_agent, "boto3", types.SimpleNamespace(client=_unexpected_client))

    assert evaluate_agent.evaluate_agent("echo-agent", "dev") is False
