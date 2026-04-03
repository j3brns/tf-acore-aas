from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from data_access.models import TenantContext, TenantTier

from gateway.interceptors import response_redaction, response_tools


def test_load_patterns_uses_defaults_when_parameter_is_empty() -> None:
    get_parameter = MagicMock(return_value={"Parameter": {"Value": "[]"}})
    logger = MagicMock()

    patterns = response_redaction.load_patterns(
        get_parameter=get_parameter,
        parameter_name="/platform/gateway/pii-patterns/default",
        logger=logger,
    )

    assert patterns
    assert any(pattern.search("user@example.com") for pattern in patterns)


def test_filter_tools_respects_payload_tier_without_registry_lookup() -> None:
    context = TenantContext(
        tenant_id="t-001",
        app_id="app-001",
        tier=TenantTier.BASIC,
        sub="tester",
    )
    logger = MagicMock()

    body = {
        "tools": [
            {"name": "calculator", "tierMinimum": "basic"},
            {"name": "heavy-compute", "tierMinimum": "premium"},
        ]
    }

    filtered = response_tools.filter_tools(
        body,
        context,
        tools_table="platform-tools",
        logger=logger,
    )

    assert filtered["tools"] == [{"name": "calculator", "tierMinimum": "basic"}]
