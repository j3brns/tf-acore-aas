from __future__ import annotations

from typing import Any

from src.tenant_api import tenant_lifecycle


def test_handle_create_delegates_to_tenant_records(monkeypatch) -> None:
    expected = {"statusCode": 201}

    def _fake_handle_create(event: dict[str, Any], caller: Any, deps: Any) -> dict[str, Any]:
        assert event["httpMethod"] == "POST"
        assert caller == "caller"
        assert deps == "deps"
        return expected

    monkeypatch.setattr(tenant_lifecycle.tenant_records, "handle_create", _fake_handle_create)
    assert tenant_lifecycle.handle_create({"httpMethod": "POST"}, "caller", "deps") is expected


def test_handle_audit_export_delegates_to_audit_module(monkeypatch) -> None:
    expected = {"statusCode": 200}

    def _fake_handle_audit_export(
        event: dict[str, Any], caller: Any, *, tenant_id: str
    ) -> dict[str, Any]:
        assert event["path"].endswith("/audit-export")
        assert caller == "caller"
        assert tenant_id == "t-001"
        return expected

    monkeypatch.setattr(
        tenant_lifecycle.tenant_audit_exports,
        "handle_audit_export",
        _fake_handle_audit_export,
    )
    assert (
        tenant_lifecycle.handle_audit_export(
            {"path": "/v1/tenants/t-001/audit-export"},
            "caller",
            tenant_id="t-001",
        )
        is expected
    )


def test_handle_list_invites_delegates_to_invite_module(monkeypatch) -> None:
    expected = {"statusCode": 200}

    def _fake_handle_list_invites(caller: Any, *, tenant_id: str) -> dict[str, Any]:
        assert caller == "caller"
        assert tenant_id == "t-001"
        return expected

    monkeypatch.setattr(
        tenant_lifecycle.tenant_invites,
        "handle_list_invites",
        _fake_handle_list_invites,
    )
    assert tenant_lifecycle.handle_list_invites("caller", tenant_id="t-001") is expected


def test_handle_sessions_delegates_to_session_module(monkeypatch) -> None:
    expected = {"statusCode": 501}

    def _fake_handle_sessions(event: dict[str, Any], caller: Any) -> dict[str, Any]:
        assert event["path"] == "/v1/sessions"
        assert caller == "caller"
        return expected

    monkeypatch.setattr(tenant_lifecycle.tenant_sessions, "handle_sessions", _fake_handle_sessions)
    assert tenant_lifecycle.handle_sessions({"path": "/v1/sessions"}, "caller") is expected
