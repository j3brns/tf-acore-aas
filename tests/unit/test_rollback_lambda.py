"""Unit tests for rollback_lambda.py."""

import importlib.util
import json
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


rollback_lambda = _load_module("rollback_lambda")

_REGION = "eu-west-2"


def _create_role(iam_client) -> str:
    role_name = "lambda-role"
    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Effect": "Allow",
            }
        ],
    }
    role = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(assume_role_policy_document),
    )
    return role["Role"]["Arn"]


@mock_aws
def test_rollback_lambda_success(monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)

    func_base_name = "bridge"
    env = "dev"
    full_name = f"platform-{func_base_name}-{env}"
    alias_name = "live"

    client = boto3.client("lambda", region_name=_REGION)
    iam_client = boto3.client("iam", region_name=_REGION)
    role_arn = _create_role(iam_client)

    # 1. Create function
    client.create_function(
        FunctionName=full_name,
        Runtime="python3.12",
        Role=role_arn,
        Handler="handler.handler",
        Code={"ZipFile": b"print('hello')"},
    )

    # 2. Publish two versions
    client.publish_version(FunctionName=full_name)  # v1

    # Update code to create a distinct v2
    client.update_function_code(FunctionName=full_name, ZipFile=b"print('hello v2')")
    client.publish_version(FunctionName=full_name)  # v2

    # 3. Create alias pointing to v2
    client.create_alias(FunctionName=full_name, Name=alias_name, FunctionVersion="2")

    # 4. Run Rollback
    success = rollback_lambda.rollback_lambda(func_base_name, env, alias_name)
    assert success is True

    # 5. Verify alias points to v1
    alias_resp = client.get_alias(FunctionName=full_name, Name=alias_name)
    assert alias_resp["FunctionVersion"] == "1"


@mock_aws
def test_rollback_lambda_fails_no_previous(monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)

    func_base_name = "bridge"
    env = "dev"
    full_name = f"platform-{func_base_name}-{env}"
    alias_name = "live"

    client = boto3.client("lambda", region_name=_REGION)
    iam_client = boto3.client("iam", region_name=_REGION)
    role_arn = _create_role(iam_client)

    # 1. Create function
    client.create_function(
        FunctionName=full_name,
        Runtime="python3.12",
        Role=role_arn,
        Handler="handler.handler",
        Code={"ZipFile": b"print('hello')"},
    )

    # 2. Publish only ONE version
    client.publish_version(FunctionName=full_name)  # v1

    # 3. Create alias pointing to v1
    client.create_alias(FunctionName=full_name, Name=alias_name, FunctionVersion="1")

    # 4. Run Rollback - should fail as there is no version < 1
    success = rollback_lambda.rollback_lambda(func_base_name, env, alias_name)
    assert success is False
