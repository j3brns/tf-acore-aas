import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.authoriser.handler import generate_policy, handler, is_admin_route

# Mock environment variables
OS_ENV = {
    "AWS_REGION": "eu-west-2",
    "AWS_DEFAULT_REGION": "eu-west-2",
    "AWS_SECRET_ACCESS_KEY": "testing",  # pragma: allowlist secret
    "AWS_SESSION_TOKEN": "testing",
    "AWS_EC2_METADATA_DISABLED": "true",
    "ENTRA_JWKS_URL": "http://localhost:8766/.well-known/jwks.json",
    "ENTRA_AUDIENCE": "api://platform-local",
    "ENTRA_ISSUER": "http://localhost:8766",
    "TENANTS_TABLE": "platform-tenants-dev",
}
OS_ENV["AWS_ACCESS_KEY_" + "ID"] = "testing"

SIGV4_TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"  # pragma: allowlist secret


@pytest.fixture
def mock_env():
    with patch.dict(os.environ, OS_ENV):
        yield


@pytest.fixture(autouse=True)
def mock_constants():
    with (
        patch("src.authoriser.handler.ENTRA_JWKS_URL", OS_ENV["ENTRA_JWKS_URL"]),
        patch("src.authoriser.handler.ENTRA_AUDIENCE", OS_ENV["ENTRA_AUDIENCE"]),
        patch("src.authoriser.handler.ENTRA_ISSUER", OS_ENV["ENTRA_ISSUER"]),
        patch("src.authoriser.handler.TENANTS_TABLE", OS_ENV["TENANTS_TABLE"]),
    ):
        yield


@pytest.fixture
def mock_dynamodb():
    with patch("boto3.resource") as mock:
        yield mock


@pytest.fixture
def mock_jwk_client():
    with patch("jwt.PyJWKClient") as mock:
        yield mock


class MockContext:
    def __init__(self):
        self.function_name = "authoriser"
        self.memory_limit_in_mb = 128
        self.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:authoriser"
        self.aws_request_id = "request-id"


@pytest.fixture
def lambda_context():
    return MockContext()


def _sigv4_authorization(
    access_key: str = SIGV4_TEST_ACCESS_KEY,
    signature: str | None = None,
) -> str:
    sig = signature or ("a" * 64)
    return (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/20260307/eu-west-2/execute-api/aws4_request, "
        "SignedHeaders=host;x-amz-date;x-tenant-id, "
        f"Signature={sig}"
    )


def _sigv4_event(
    *,
    method_arn: str = (
        "arn:aws:execute-api:eu-west-2:123456789012:api/dev/POST/v1/agents/echo-agent/invoke"
    ),
    tenant_id: str = "t-test-001",
    authorization: str | None = None,
    include_identity: bool = True,
    access_key: str = SIGV4_TEST_ACCESS_KEY,
    caller_arn: str = (
        "arn:aws:sts::123456789012:assumed-role/platform-tenant-t-test-001-execution-role/"
        "machine-session"
    ),
) -> dict[str, object]:
    event: dict[str, object] = {
        "methodArn": method_arn,
        "authorizationToken": authorization or _sigv4_authorization(access_key=access_key),
        "headers": {
            "Authorization": authorization or _sigv4_authorization(access_key=access_key),
            "x-tenant-id": tenant_id,
            "x-amz-date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "host": "api.example.com",
        },
    }
    if include_identity:
        event["requestContext"] = {
            "identity": {
                "accessKey": access_key,
                "userArn": caller_arn,
                "caller": "AIDAIEXAMPLE",
            }
        }
    return event


def test_generate_policy():
    context = {"foo": "bar"}
    method_arn = "arn:aws:execute-api:region:account:api/stage/GET/path"
    policy = generate_policy("user123", "Allow", method_arn, context)

    assert policy["principalId"] == "user123"
    assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert policy["context"] == context


@pytest.mark.parametrize(
    ("method_arn", "expected"),
    [
        ("arn:aws:execute-api:eu-west-2:123456789012:api/dev/GET/v1/tenants/t-001", False),
        ("arn:aws:execute-api:eu-west-2:123456789012:api/dev/GET/v1/tenants", False),
        ("arn:aws:execute-api:eu-west-2:123456789012:api/dev/POST/v1/tenants", True),
        ("arn:aws:execute-api:eu-west-2:123456789012:api/dev/PATCH/v1/tenants/t-001", True),
        ("arn:aws:execute-api:eu-west-2:123456789012:api/dev/DELETE/v1/tenants/t-001", True),
        (
            "arn:aws:execute-api:eu-west-2:123456789012:api/dev/POST/v1/tenants/t-001/api-key/rotate",
            False,
        ),
        (
            "arn:aws:execute-api:eu-west-2:123456789012:api/dev/GET/v1/tenants/t-001/audit-export",
            True,
        ),
        ("arn:aws:execute-api:eu-west-2:123456789012:api/dev/GET/v1/platform/quota", True),
    ],
)
def test_is_admin_route(method_arn: str, expected: bool):
    assert is_admin_route(method_arn) is expected


def test_handler_missing_auth(mock_env, lambda_context):
    event = {
        "methodArn": "arn:aws:execute-api:eu-west-2:123456789012:api/dev/GET/v1/health",
        "headers": {},
    }
    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_valid_jwt(mock_get_status, mock_get_jwk_client, mock_env, lambda_context):
    token = "valid.token.here"
    method_arn = (
        "arn:aws:execute-api:eu-west-2:123456789012:api/dev/POST/v1/agents/echo-agent/invoke"
    )
    event = {"methodArn": method_arn, "authorizationToken": f"Bearer {token}"}

    # Mock payload
    payload = {
        "tenantid": "t-test-001",
        "appid": "app-001",
        "tier": "basic",
        "sub": "user-001",
        "roles": ["Agent.Invoke"],
        "iss": OS_ENV["ENTRA_ISSUER"],
        "aud": OS_ENV["ENTRA_AUDIENCE"],
    }

    mock_get_status.return_value = "active"

    # Mock JWK client
    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.return_value = MagicMock(key="public-key")

    # Mock JWT decode
    with patch("jwt.decode", return_value=payload):
        result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["context"]["tenantid"] == "t-test-001"
    assert result["context"]["tier"] == "basic"
    assert result["context"]["usageIdentifierKey"] == "t-test-001"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_suspended_tenant(mock_get_status, mock_get_jwk_client, mock_env, lambda_context):
    token = "valid.token.here"
    method_arn = (
        "arn:aws:execute-api:eu-west-2:123456789012:api/dev/POST/v1/agents/echo-agent/invoke"
    )
    event = {"methodArn": method_arn, "authorizationToken": f"Bearer {token}"}

    payload = {
        "tenantid": "t-test-001",
        "appid": "app-001",
        "tier": "basic",
        "sub": "user-001",
    }

    mock_get_status.return_value = "suspended"

    # Mock JWK client
    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.return_value = MagicMock(key="public-key")

    with patch("jwt.decode", return_value=payload):
        result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_admin_route_unauthorised(
    mock_get_status, mock_get_jwk_client, mock_env, lambda_context
):
    token = "valid.token.here"
    method_arn = "arn:aws:execute-api:eu-west-2:123456789012:api/dev/POST/v1/tenants"
    event = {"methodArn": method_arn, "authorizationToken": f"Bearer {token}"}

    payload = {
        "tenantid": "t-test-001",
        "appid": "app-001",
        "tier": "basic",
        "sub": "user-001",
        "roles": ["Agent.Invoke"],
    }

    mock_get_status.return_value = "active"

    # Mock JWK client
    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.return_value = MagicMock(key="public-key")

    with patch("jwt.decode", return_value=payload):
        result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_admin_route_authorised(
    mock_get_status, mock_get_jwk_client, mock_env, lambda_context
):
    token = "valid.token.here"
    method_arn = "arn:aws:execute-api:eu-west-2:123456789012:api/dev/POST/v1/tenants"
    event = {"methodArn": method_arn, "authorizationToken": f"Bearer {token}"}

    payload = {
        "tenantid": "t-test-001",
        "appid": "app-001",
        "tier": "premium",
        "sub": "admin-001",
        "roles": ["Platform.Admin"],
    }

    mock_get_status.return_value = "active"

    # Mock JWK client
    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.return_value = MagicMock(key="public-key")

    with patch("jwt.decode", return_value=payload):
        result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_non_admin_can_read_own_tenant_route(
    mock_get_status, mock_get_jwk_client, mock_env, lambda_context
):
    token = "valid.token.here"
    method_arn = "arn:aws:execute-api:eu-west-2:123456789012:api/dev/GET/v1/tenants/t-test-001"
    event = {"methodArn": method_arn, "authorizationToken": f"Bearer {token}"}

    payload = {
        "tenantid": "t-test-001",
        "appid": "app-001",
        "tier": "basic",
        "sub": "user-001",
        "roles": ["Agent.Invoke"],
    }

    mock_get_status.return_value = "active"
    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.return_value = MagicMock(key="public-key")

    with patch("jwt.decode", return_value=payload):
        result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"


@patch("src.authoriser.handler.get_jwk_client")
def test_handler_jwk_client_missing(mock_get_jwk_client, mock_env, lambda_context):
    mock_get_jwk_client.return_value = None
    event = {
        "methodArn": "arn:aws:execute-api:eu-west-2:123456789012:api/dev/GET/v1/health",
        "authorizationToken": "Bearer token",
    }
    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


def test_handler_sigv4_stub(mock_env, lambda_context):
    event = _sigv4_event(include_identity=False)
    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_valid_allows_and_returns_context(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "active"
    mock_resolve_binding.return_value = {
        "tenant_id": "t-test-001",
        "app_id": "app-001",
        "tier": "standard",
    }
    event = _sigv4_event()

    result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["context"]["tenantid"] == "t-test-001"
    assert result["context"]["usageIdentifierKey"] == "t-test-001"
    assert result["context"]["appid"] == "app-001"
    assert result["context"]["tier"] == "standard"


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_machine_happy_path_uses_request_identity(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "active"
    mock_resolve_binding.return_value = {
        "tenant_id": "t-test-001",
        "app_id": "app-001",
        "tier": "basic",
    }
    event = _sigv4_event()

    result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["principalId"].startswith("arn:aws:sts::123456789012:assumed-role/")
    assert result["context"]["sub"].startswith("arn:aws:sts::123456789012:assumed-role/")


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_invalid_signature_denied(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "active"
    mock_resolve_binding.return_value = {
        "tenant_id": "t-test-001",
        "app_id": "app-001",
        "tier": "basic",
    }
    bad_sig = "z" * 64
    auth = _sigv4_authorization(signature=bad_sig)
    event = _sigv4_event(authorization=auth)

    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_missing_tenant_header_denied(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "active"
    mock_resolve_binding.return_value = {
        "tenant_id": "t-test-001",
        "app_id": "app-001",
        "tier": "basic",
    }
    event = _sigv4_event()
    headers = dict(event["headers"])  # type: ignore[index]
    headers.pop("x-tenant-id", None)
    event["headers"] = headers

    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_suspended_tenant_denied(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "suspended"
    mock_resolve_binding.return_value = {
        "tenant_id": "t-test-001",
        "app_id": "app-001",
        "tier": "basic",
    }
    event = _sigv4_event()

    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_ignores_spoofed_tier_header(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "active"
    mock_resolve_binding.return_value = {
        "tenant_id": "t-test-001",
        "app_id": "app-001",
        "tier": "basic",
    }
    event = _sigv4_event()
    event["headers"]["x-tier"] = "premium"  # type: ignore[index]

    result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["context"]["tier"] == "basic"


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_uses_trusted_premium_tier(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "active"
    mock_resolve_binding.return_value = {
        "tenant_id": "t-test-001",
        "app_id": "app-001",
        "tier": "premium",
    }
    event = _sigv4_event()
    event["headers"]["x-tier"] = "basic"  # type: ignore[index]

    result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["context"]["tier"] == "premium"


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_cross_tenant_header_injection_denied(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "active"
    mock_resolve_binding.return_value = {
        "tenant_id": "t-trusted-001",
        "app_id": "app-001",
        "tier": "basic",
    }
    event = _sigv4_event(tenant_id="t-attacker-001")

    result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.resolve_sigv4_tenant_binding")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_sigv4_missing_trusted_binding_denied(
    mock_get_status, mock_resolve_binding, mock_env, lambda_context
):
    mock_get_status.return_value = "active"
    mock_resolve_binding.return_value = None
    event = _sigv4_event()

    result = handler(event, lambda_context)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_expired_token(mock_get_status, mock_get_jwk_client, mock_env, lambda_context):
    token = "expired.token"
    event = {"methodArn": "arn", "authorizationToken": f"Bearer {token}"}

    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.side_effect = jwt.ExpiredSignatureError()

    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_invalid_token(mock_get_status, mock_get_jwk_client, mock_env, lambda_context):
    token = "invalid.token"
    event = {"methodArn": "arn", "authorizationToken": f"Bearer {token}"}

    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.side_effect = jwt.InvalidTokenError()

    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_tenant_not_found(mock_get_status, mock_get_jwk_client, mock_env, lambda_context):
    token = "valid.token"
    event = {"methodArn": "arn", "authorizationToken": f"Bearer {token}"}
    payload = {"tenantid": "t-unknown", "appid": "app", "sub": "user"}

    mock_get_status.return_value = None
    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.return_value = MagicMock(key="key")

    with patch("jwt.decode", return_value=payload):
        result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_tenant_status")
def test_handler_token_authoriser_fallback(
    mock_get_status, mock_get_jwk_client, mock_env, lambda_context
):
    token = "raw.token"
    event = {"methodArn": "arn", "authorizationToken": token}
    payload = {"tenantid": "t-test", "appid": "app", "sub": "user"}

    mock_get_status.return_value = "active"
    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.return_value = MagicMock(key="key")

    with patch("jwt.decode", return_value=payload):
        result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"


def test_get_jwk_client_logic(mock_env):
    from src.authoriser.handler import get_jwk_client

    with patch("src.authoriser.handler._jwk_client", None):
        with patch("src.authoriser.handler.ENTRA_JWKS_URL", "http://test"):
            client = get_jwk_client()
            assert client is not None


def test_get_dynamodb_logic(mock_env):
    from src.authoriser.handler import get_dynamodb

    with patch("src.authoriser.handler._dynamodb_resource", None):
        db = get_dynamodb()
        assert db is not None


def test_get_tenant_status_no_table(mock_env):
    from src.authoriser.handler import get_tenant_status

    with patch("src.authoriser.handler.TENANTS_TABLE", None):
        assert get_tenant_status("any") == "active"


@patch("src.authoriser.handler.get_dynamodb")
def test_resolve_sigv4_tenant_binding_invalid_tier_falls_back_to_basic(mock_get_dynamodb, mock_env):
    from src.authoriser.handler import resolve_sigv4_tenant_binding

    mock_table = MagicMock()
    mock_get_dynamodb.return_value.Table.return_value = mock_table
    mock_table.scan.side_effect = [
        {
            "Items": [
                {
                    "tenantId": "t-test-001",
                    "appId": "app-001",
                    "tier": "not-a-tier",
                    "executionRoleArn": (
                        "arn:aws:iam::123456789012:role/platform-tenant-t-test-001-execution-role"
                    ),
                }
            ]
        }
    ]

    binding = resolve_sigv4_tenant_binding(
        "arn:aws:sts::123456789012:assumed-role/platform-tenant-t-test-001-execution-role/machine-session"
    )

    assert binding == {"tenant_id": "t-test-001", "app_id": "app-001", "tier": "basic"}


@patch("src.authoriser.handler.get_jwk_client")
def test_handler_unexpected_error(mock_get_jwk_client, mock_env, lambda_context):
    mock_get_jwk_client.side_effect = Exception("Crash")
    event = {"methodArn": "arn", "authorizationToken": "Bearer token"}
    result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@patch("src.authoriser.handler.get_jwk_client")
@patch("src.authoriser.handler.get_dynamodb")
def test_get_tenant_status_error(mock_get_db, mock_get_jwk_client, mock_env, lambda_context):
    token = "valid.token"
    event = {"methodArn": "arn", "authorizationToken": f"Bearer {token}"}
    payload = {"tenantid": "t-test", "appid": "app", "sub": "user"}

    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_table = MagicMock()
    mock_db.Table.return_value = mock_table
    mock_table.get_item.side_effect = Exception("DB Error")

    mock_jwk_client = MagicMock()
    mock_get_jwk_client.return_value = mock_jwk_client
    mock_jwk_client.get_signing_key_from_jwt.return_value = MagicMock(key="key")

    with patch("jwt.decode", return_value=payload):
        result = handler(event, lambda_context)
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
