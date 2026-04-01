from unittest.mock import MagicMock, patch

import pytest

from src.platform_tools.diagnostics_handler import lambda_handler


@pytest.fixture
def mock_db():
    with patch("src.platform_tools.diagnostics_handler.ControlPlaneDynamoDB") as mock:
        yield mock


def test_get_platform_health(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health", "arguments": {}},
        "headers": {"x-tenant-id": "platform", "x-app-id": "admin-ui"},
        "id": "1",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "1"
    assert "result" in response
    assert response["result"]["status"] == "healthy"
    assert len(response["result"]["regions"]) == 2


def test_get_tenant_status(mock_db):
    # Setup mock DB response
    mock_instance = mock_db.return_value
    mock_instance.get_item.return_value = {
        "tenant_id": "t-test-001",
        "display_name": "Test Tenant",
        "status": "active",
        "tier": "basic",
    }
    mock_instance.query.return_value = MagicMock(items=[])

    event = {
        "method": "tools/call",
        "params": {"name": "get_tenant_status", "arguments": {"tenant_id": "t-test-001"}},
        "headers": {"x-tenant-id": "platform", "x-app-id": "admin-ui"},
        "id": "2",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "2"
    assert response["result"]["tenantId"] == "t-test-001"
    assert response["result"]["status"] == "active"
    mock_instance.get_item.assert_called_once()


def test_get_runbook_guidance(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_runbook_guidance", "arguments": {"runbook_id": "RUNBOOK-001"}},
        "headers": {"x-tenant-id": "platform", "x-app-id": "admin-ui"},
        "id": "3",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "3"
    assert response["result"]["runbookId"] == "RUNBOOK-001"
    assert "steps" in response["result"]


def test_access_denied_for_non_platform_tenant(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health"},
        "headers": {"x-tenant-id": "t-test-001", "x-app-id": "some-app"},
        "id": "4",
    }

    response = lambda_handler(event, None)

    assert "error" in response
    assert response["error"]["code"] == -32003
    assert "Access denied" in response["error"]["message"]
