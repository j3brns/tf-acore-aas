"""
tests/test_capability_client.py — Tests for AppConfig-backed TenantCapabilityClient.

Coverage assertions:
  - fetch_policy() returns fallback on missing environment.
  - fetch_policy() returns fallback on AppConfig error.
  - fetch_policy() retains last known good policy after provider failure.
  - fetch_policy() parses valid JSON policy correctly.
  - fetch_policy() handles malformed rollout dicts gracefully.
  - fetch_policy() uses max_age caching.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from data_access.client import TenantCapabilityClient
from data_access.models import TenantCapabilityPolicy, TenantTier

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_ID = "platform-config-dev"
ENV_ID = "dev"
PROFILE_ID = "tenant-capabilities"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_appconfig_provider():
    """Mock the aws-lambda-powertools AppConfigProvider."""
    with patch("data_access.client.AppConfigProvider") as mock:
        yield mock


@pytest.fixture
def capability_client(mock_appconfig_provider):
    """Return a TenantCapabilityClient with mocked provider."""
    # We pass explicit IDs to avoid depending on env vars in tests
    return TenantCapabilityClient(application=APP_ID, environment=ENV_ID, profile=PROFILE_ID)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTenantCapabilityClientInit:
    def test_init_with_env_vars(self, monkeypatch):
        monkeypatch.setenv("APPCONFIG_APPLICATION_ID", APP_ID)
        monkeypatch.setenv("APPCONFIG_ENVIRONMENT_ID", ENV_ID)
        monkeypatch.setenv("APPCONFIG_PROFILE_ID", PROFILE_ID)

        with patch("data_access.client.AppConfigProvider") as mock_provider:
            client = TenantCapabilityClient()
            assert client._application == APP_ID
            assert client._environment == ENV_ID
            assert client._profile == PROFILE_ID
            mock_provider.assert_called_once_with(application=APP_ID, environment=ENV_ID)

    def test_init_without_env_vars_allows_none(self, monkeypatch):
        monkeypatch.delenv("APPCONFIG_APPLICATION_ID", raising=False)
        monkeypatch.delenv("APPCONFIG_ENVIRONMENT_ID", raising=False)
        monkeypatch.delenv("APPCONFIG_PROFILE_ID", raising=False)

        client = TenantCapabilityClient()
        assert client._application is None
        assert client._environment is None
        assert client._profile is None
        assert client._provider is None


class TestTenantCapabilityClientFetch:
    def test_fetch_policy_returns_fallback_if_not_configured(self, monkeypatch):
        monkeypatch.delenv("APPCONFIG_APPLICATION_ID", raising=False)
        client = TenantCapabilityClient()
        policy = client.fetch_policy()
        assert policy == TenantCapabilityPolicy.safe_fallback()

    def test_fetch_policy_success(self, capability_client, mock_appconfig_provider):
        mock_provider_instance = mock_appconfig_provider.return_value
        mock_provider_instance.get.return_value = {
            "schema_version": "2026-03-21",
            "capabilities": {
                "agents.invoke": {
                    "enabled": True,
                    "rollout_percentage": 100,
                    "tier_allow_list": ["basic", "standard", "premium"],
                },
                "tools.browser": {
                    "enabled": True,
                    "rollout_percentage": 50,
                    "tier_allow_list": ["premium"],
                },
            },
            "killed_capabilities": ["agents.unsafe"],
        }

        policy = capability_client.fetch_policy()

        assert policy.schema_version == "2026-03-21"
        assert "agents.invoke" in policy.capabilities
        assert policy.capabilities["agents.invoke"].enabled is True
        assert policy.capabilities["agents.invoke"].rollout_percentage == 100
        assert TenantTier.BASIC in policy.capabilities["agents.invoke"].tier_allow_list

        assert policy.capabilities["tools.browser"].rollout_percentage == 50
        assert TenantTier.PREMIUM in policy.capabilities["tools.browser"].tier_allow_list
        assert TenantTier.BASIC not in policy.capabilities["tools.browser"].tier_allow_list

        assert "agents.unsafe" in policy.killed_capabilities
        mock_provider_instance.get.assert_called_once_with(PROFILE_ID, max_age=60)

    def test_fetch_policy_handles_malformed_json_gracefully(
        self, capability_client, mock_appconfig_provider
    ):
        mock_provider_instance = mock_appconfig_provider.return_value
        # Return something that isn't a dict
        mock_provider_instance.get.return_value = "not a dict"

        policy = capability_client.fetch_policy()
        assert policy == TenantCapabilityPolicy.safe_fallback()

    def test_fetch_policy_handles_powertools_error(
        self, capability_client, mock_appconfig_provider
    ):
        mock_provider_instance = mock_appconfig_provider.return_value
        mock_provider_instance.get.side_effect = Exception("AppConfig boom")

        policy = capability_client.fetch_policy()
        assert policy == TenantCapabilityPolicy.safe_fallback()

    def test_fetch_policy_retains_last_known_good_on_provider_error(
        self, capability_client, mock_appconfig_provider
    ):
        mock_provider_instance = mock_appconfig_provider.return_value
        mock_provider_instance.get.return_value = {
            "schema_version": "2026-03-21",
            "capabilities": {
                "agents.invoke": {
                    "enabled": True,
                    "rollout_percentage": 100,
                    "tier_allow_list": ["basic", "standard", "premium"],
                }
            },
            "killed_capabilities": [],
        }

        first = capability_client.fetch_policy()
        assert "agents.invoke" in first.capabilities

        mock_provider_instance.get.side_effect = Exception("AppConfig boom")
        second = capability_client.fetch_policy()

        assert second == first

    def test_fetch_policy_skips_malformed_rollouts(
        self, capability_client, mock_appconfig_provider
    ):
        mock_provider_instance = mock_appconfig_provider.return_value
        mock_provider_instance.get.return_value = {
            "schema_version": "2026-03-21",
            "capabilities": {
                "valid": {"enabled": True},
                "invalid_tier": {"enabled": True, "tier_allow_list": ["not-a-tier"]},
                "invalid_type": "should be a dict",
            },
        }

        policy = capability_client.fetch_policy()
        assert "valid" in policy.capabilities
        # invalid_tier should be skipped if TenantTier(t) raises ValueError
        assert "invalid_tier" not in policy.capabilities
        # invalid_type should be skipped because rollout_dict.get() will fail or return None
        assert "invalid_type" not in policy.capabilities


class TestCapabilityEvaluation:
    """End-to-end evaluation tests using the client and policy models."""

    def test_is_enabled_logic(self, capability_client, mock_appconfig_provider):
        mock_provider_instance = mock_appconfig_provider.return_value
        mock_provider_instance.get.return_value = {
            "schema_version": "2026-03-21",
            "capabilities": {
                "feature.rollout": {
                    "enabled": True,
                    "rollout_percentage": 100,
                    "tier_allow_list": ["standard", "premium"],
                },
                "feature.limited": {
                    "enabled": True,
                    "rollout_percentage": 0,
                    "tenant_allow_list": ["t-allowed"],
                },
            },
            "killed_capabilities": ["feature.killed"],
        }

        policy = capability_client.fetch_policy()

        # Tier allowed
        assert (
            policy.is_enabled("feature.rollout", tenant_id="t-any", tenant_tier=TenantTier.STANDARD)
            is True
        )
        # Tier NOT allowed
        assert (
            policy.is_enabled("feature.rollout", tenant_id="t-any", tenant_tier=TenantTier.BASIC)
            is False
        )

        # Tenant explicitly allowed
        assert (
            policy.is_enabled(
                "feature.limited", tenant_id="t-allowed", tenant_tier=TenantTier.BASIC
            )
            is True
        )
        # Tenant NOT allowed (0% rollout)
        assert (
            policy.is_enabled("feature.limited", tenant_id="t-other", tenant_tier=TenantTier.BASIC)
            is False
        )

    def test_kill_switch_overrides_everything(self, capability_client, mock_appconfig_provider):
        mock_provider_instance = mock_appconfig_provider.return_value
        mock_provider_instance.get.return_value = {
            "schema_version": "2026-03-21",
            "capabilities": {
                "feature.killed": {
                    "enabled": True,
                    "rollout_percentage": 100,
                    "tier_allow_list": ["basic", "standard", "premium"],
                }
            },
            "killed_capabilities": ["feature.killed"],
        }

        policy = capability_client.fetch_policy()
        assert (
            policy.is_enabled("feature.killed", tenant_id="t-any", tenant_tier=TenantTier.PREMIUM)
            is False
        )

    def test_unknown_capability_is_denied(self, capability_client, mock_appconfig_provider):
        mock_provider_instance = mock_appconfig_provider.return_value
        mock_provider_instance.get.return_value = {
            "schema_version": "1.0",
            "capabilities": {},
        }

        policy = capability_client.fetch_policy()
        assert (
            policy.is_enabled("unknown", tenant_id="t-any", tenant_tier=TenantTier.PREMIUM) is False
        )
