"""
tests/unit/test_models.py — Schema constraint tests for data_access.models.

Validates:
- PK/SK key patterns match architecture spec
- Enum values enforce constrained vocabulary
- TTL constants are correct
- InvocationRecord jitter constraint (exactly 2 chars when set)
- Optional vs required fields
- Frozen dataclass immutability
"""

import dataclasses
import time

import pytest
from data_access.models import (
    INVOCATION_TTL_SECONDS,
    JITTER_LENGTH,
    JOB_TTL_SECONDS,
    OPS_LOCK_TTL_SECONDS,
    SESSION_TTL_SECONDS,
    AgentRecord,
    InvocationMode,
    InvocationRecord,
    InvocationStatus,
    JobRecord,
    JobStatus,
    OpsLockRecord,
    SessionRecord,
    SessionStatus,
    TenantRecord,
    TenantStatus,
    TenantTier,
    ToolRecord,
)

# ---------------------------------------------------------------------------
# TTL constant sanity checks
# ---------------------------------------------------------------------------


class TestTtlConstants:
    def test_invocation_ttl_is_90_days(self):
        assert INVOCATION_TTL_SECONDS == 90 * 24 * 60 * 60

    def test_job_ttl_is_7_days(self):
        assert JOB_TTL_SECONDS == 7 * 24 * 60 * 60

    def test_session_ttl_is_24_hours(self):
        assert SESSION_TTL_SECONDS == 24 * 60 * 60

    def test_ops_lock_ttl_is_5_minutes(self):
        assert OPS_LOCK_TTL_SECONDS == 5 * 60

    def test_jitter_length_is_2(self):
        assert JITTER_LENGTH == 2


# ---------------------------------------------------------------------------
# TenantRecord
# ---------------------------------------------------------------------------


def _make_tenant(**overrides) -> TenantRecord:
    defaults = dict(
        tenant_id="t-abc123",
        app_id="app-001",
        display_name="Acme Corp",
        tier=TenantTier.STANDARD,
        status=TenantStatus.ACTIVE,
        created_at="2026-02-24T00:00:00Z",
        updated_at="2026-02-24T00:00:00Z",
        owner_email="admin@acme.example",
        owner_team="platform",
        account_id="123456789012",
    )
    defaults.update(overrides)
    return TenantRecord(**defaults)


class TestTenantRecord:
    def test_pk_format(self):
        tenant = _make_tenant(tenant_id="t-xyz")
        assert tenant.pk == "TENANT#t-xyz"

    def test_sk_is_metadata(self):
        assert _make_tenant().sk == "METADATA"

    def test_pk_starts_with_tenant_prefix(self):
        tenant = _make_tenant()
        assert tenant.pk.startswith("TENANT#")

    def test_optional_fields_default_none(self):
        tenant = _make_tenant()
        assert tenant.memory_store_arn is None
        assert tenant.runtime_region is None
        assert tenant.fallback_region is None
        assert tenant.api_key_secret_arn is None
        assert tenant.monthly_budget_usd is None

    def test_optional_fields_accept_values(self):
        tenant = _make_tenant(
            memory_store_arn="arn:aws:bedrock:eu-west-2:123:memory/m-1",
            runtime_region="eu-west-1",
            fallback_region="eu-central-1",
            api_key_secret_arn="arn:aws:secretsmanager:eu-west-2:123:secret:k",
            monthly_budget_usd=1000.0,
        )
        assert tenant.runtime_region == "eu-west-1"
        assert tenant.monthly_budget_usd == 1000.0

    def test_frozen(self):
        tenant = _make_tenant()
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            tenant.status = TenantStatus.SUSPENDED  # type: ignore[misc]

    def test_tier_enum_rejects_invalid(self):
        with pytest.raises(ValueError):
            TenantTier("gold")

    def test_status_enum_rejects_invalid(self):
        with pytest.raises(ValueError):
            TenantStatus("banned")

    def test_all_tiers_accepted(self):
        for tier in TenantTier:
            tenant = _make_tenant(tier=tier)
            assert tenant.tier == tier

    def test_all_statuses_accepted(self):
        for status in TenantStatus:
            tenant = _make_tenant(status=status)
            assert tenant.status == status


# ---------------------------------------------------------------------------
# AgentRecord
# ---------------------------------------------------------------------------


def _make_agent(**overrides) -> AgentRecord:
    defaults = dict(
        agent_name="echo-agent",
        version="1.0.0",
        owner_team="platform",
        tier_minimum=TenantTier.BASIC,
        layer_hash="abcdef1234567890",
        layer_s3_key="layers/echo-agent-abcdef12.zip",
        script_s3_key="agents/echo-agent/1.0.0.zip",
        deployed_at="2026-02-24T00:00:00Z",
        invocation_mode=InvocationMode.SYNC,
        streaming_enabled=False,
    )
    defaults.update(overrides)
    return AgentRecord(**defaults)


class TestAgentRecord:
    def test_pk_format(self):
        agent = _make_agent(agent_name="my-agent")
        assert agent.pk == "AGENT#my-agent"

    def test_sk_format(self):
        agent = _make_agent(version="2.3.4")
        assert agent.sk == "VERSION#2.3.4"

    def test_pk_starts_with_agent_prefix(self):
        assert _make_agent().pk.startswith("AGENT#")

    def test_sk_starts_with_version_prefix(self):
        assert _make_agent().sk.startswith("VERSION#")

    def test_optional_fields_default_none(self):
        agent = _make_agent()
        assert agent.runtime_arn is None
        assert agent.estimated_duration_seconds is None

    def test_all_invocation_modes_accepted(self):
        for mode in InvocationMode:
            agent = _make_agent(invocation_mode=mode)
            assert agent.invocation_mode == mode

    def test_frozen(self):
        agent = _make_agent()
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            agent.version = "9.9.9"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# InvocationRecord
# ---------------------------------------------------------------------------


def _make_invocation(**overrides) -> InvocationRecord:
    defaults = dict(
        invocation_id="inv-001",
        tenant_id="t-abc123",
        app_id="app-001",
        agent_name="echo-agent",
        agent_version="1.0.0",
        session_id="sess-001",
        input_tokens=50,
        output_tokens=120,
        latency_ms=340,
        status=InvocationStatus.SUCCESS,
        runtime_region="eu-west-1",
        invocation_mode=InvocationMode.SYNC,
        timestamp="2026-02-24T12:00:00Z",
        ttl=int(time.time()) + INVOCATION_TTL_SECONDS,
    )
    defaults.update(overrides)
    return InvocationRecord(**defaults)


class TestInvocationRecord:
    def test_pk_format(self):
        inv = _make_invocation(tenant_id="t-xyz")
        assert inv.pk == "TENANT#t-xyz"

    def test_sk_format_without_jitter(self):
        inv = _make_invocation(
            timestamp="2026-02-24T12:00:00Z",
            invocation_id="inv-001",
            jitter=None,
        )
        assert inv.sk == "INV#2026-02-24T12:00:00Z#inv-001"

    def test_sk_format_with_jitter(self):
        inv = _make_invocation(
            timestamp="2026-02-24T12:00:00Z",
            invocation_id="inv-001",
            jitter="a3",
        )
        assert inv.sk == "INV#2026-02-24T12:00:00Z#inv-001#a3"

    def test_sk_starts_with_inv_prefix(self):
        assert _make_invocation().sk.startswith("INV#")

    def test_jitter_must_be_two_chars(self):
        with pytest.raises(ValueError):
            _make_invocation(jitter="x")  # too short

        with pytest.raises(ValueError):
            _make_invocation(jitter="abc")  # too long

    def test_jitter_exactly_two_chars_accepted(self):
        inv = _make_invocation(jitter="ff")
        assert inv.jitter == "ff"

    def test_jitter_none_accepted(self):
        inv = _make_invocation(jitter=None)
        assert inv.jitter is None

    def test_optional_fields_default(self):
        inv = _make_invocation()
        assert inv.jitter is None
        assert inv.error_code is None
        assert inv.job_id is None

    def test_all_invocation_statuses_accepted(self):
        for status in InvocationStatus:
            inv = _make_invocation(status=status)
            assert inv.status == status

    def test_frozen(self):
        inv = _make_invocation()
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            inv.status = InvocationStatus.ERROR  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JobRecord
# ---------------------------------------------------------------------------


def _make_job(**overrides) -> JobRecord:
    defaults = dict(
        job_id="job-001",
        tenant_id="t-abc123",
        agent_name="echo-agent",
        status=JobStatus.PENDING,
        created_at="2026-02-24T12:00:00Z",
        ttl=int(time.time()) + JOB_TTL_SECONDS,
    )
    defaults.update(overrides)
    return JobRecord(**defaults)


class TestJobRecord:
    def test_pk_format(self):
        job = _make_job(job_id="job-xyz")
        assert job.pk == "JOB#job-xyz"

    def test_sk_is_metadata(self):
        assert _make_job().sk == "METADATA"

    def test_pk_starts_with_job_prefix(self):
        assert _make_job().pk.startswith("JOB#")

    def test_optional_fields_default(self):
        job = _make_job()
        assert job.started_at is None
        assert job.completed_at is None
        assert job.result_s3_key is None
        assert job.error_message is None
        assert job.webhook_url is None
        assert job.webhook_delivered is False

    def test_all_job_statuses_accepted(self):
        for status in JobStatus:
            job = _make_job(status=status)
            assert job.status == status

    def test_frozen(self):
        job = _make_job()
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            job.status = JobStatus.RUNNING  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SessionRecord
# ---------------------------------------------------------------------------


def _make_session(**overrides) -> SessionRecord:
    defaults = dict(
        session_id="sess-001",
        tenant_id="t-abc123",
        runtime_session_id="rts-001",
        agent_name="echo-agent",
        started_at="2026-02-24T12:00:00Z",
        last_activity_at="2026-02-24T12:30:00Z",
        status=SessionStatus.ACTIVE,
        ttl=int(time.time()) + SESSION_TTL_SECONDS,
    )
    defaults.update(overrides)
    return SessionRecord(**defaults)


class TestSessionRecord:
    def test_pk_format(self):
        sess = _make_session(tenant_id="t-xyz")
        assert sess.pk == "TENANT#t-xyz"

    def test_sk_format(self):
        sess = _make_session(session_id="sess-abc")
        assert sess.sk == "SESSION#sess-abc"

    def test_pk_starts_with_tenant_prefix(self):
        assert _make_session().pk.startswith("TENANT#")

    def test_sk_starts_with_session_prefix(self):
        assert _make_session().sk.startswith("SESSION#")

    def test_all_session_statuses_accepted(self):
        for status in SessionStatus:
            sess = _make_session(status=status)
            assert sess.status == status

    def test_frozen(self):
        sess = _make_session()
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            sess.status = SessionStatus.EXPIRED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ToolRecord
# ---------------------------------------------------------------------------


def _make_tool(**overrides) -> ToolRecord:
    defaults = dict(
        tool_name="web-search",
        tier_minimum=TenantTier.STANDARD,
        lambda_arn="arn:aws:lambda:eu-west-2:123:function:platform-web-search-dev",
        gateway_target_id="tgt-001",
        enabled=True,
    )
    defaults.update(overrides)
    return ToolRecord(**defaults)


class TestToolRecord:
    def test_pk_format(self):
        tool = _make_tool(tool_name="code-exec")
        assert tool.pk == "TOOL#code-exec"

    def test_sk_global_when_no_tenant(self):
        tool = _make_tool(tenant_id=None)
        assert tool.sk == "GLOBAL"

    def test_sk_tenant_scoped_when_tenant_set(self):
        tool = _make_tool(tenant_id="t-abc123")
        assert tool.sk == "TENANT#t-abc123"

    def test_pk_starts_with_tool_prefix(self):
        assert _make_tool().pk.startswith("TOOL#")

    def test_global_tool_tenant_id_is_none(self):
        tool = _make_tool()
        assert tool.tenant_id is None

    def test_all_tier_minimums_accepted(self):
        for tier in TenantTier:
            tool = _make_tool(tier_minimum=tier)
            assert tool.tier_minimum == tier

    def test_frozen(self):
        tool = _make_tool()
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            tool.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# OpsLockRecord
# ---------------------------------------------------------------------------


def _make_lock(**overrides) -> OpsLockRecord:
    defaults = dict(
        lock_name="region-failover",
        lock_id="550e8400-e29b-41d4-a716-446655440000",
        acquired_by="ops/failover@ops-host",
        acquired_at="2026-02-24T12:00:00Z",
        ttl=int(time.time()) + OPS_LOCK_TTL_SECONDS,
    )
    defaults.update(overrides)
    return OpsLockRecord(**defaults)


class TestOpsLockRecord:
    def test_pk_format(self):
        lock = _make_lock(lock_name="region-failover")
        assert lock.pk == "LOCK#region-failover"

    def test_sk_is_metadata(self):
        assert _make_lock().sk == "METADATA"

    def test_pk_starts_with_lock_prefix(self):
        assert _make_lock().pk.startswith("LOCK#")

    def test_ttl_is_approximately_5_minutes_from_now(self):
        now = int(time.time())
        lock = _make_lock(ttl=now + OPS_LOCK_TTL_SECONDS)
        assert lock.ttl > now
        assert lock.ttl <= now + OPS_LOCK_TTL_SECONDS + 1

    def test_frozen(self):
        lock = _make_lock()
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            lock.lock_id = "different-id"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Cross-table key prefix uniqueness
# (ensures no two tables share a PK prefix — prevents accidental cross-table reads)
# ---------------------------------------------------------------------------


class TestKeyPrefixUniqueness:
    """All PK prefixes must be distinct to prevent scan/query collisions."""

    def test_pk_prefixes_are_unique(self):
        records = [
            _make_tenant(),
            _make_agent(),
            _make_invocation(),
            _make_job(),
            _make_session(),
            _make_tool(),
            _make_lock(),
        ]
        prefixes = [r.pk.split("#")[0] for r in records]
        # TenantRecord and InvocationRecord/SessionRecord share TENANT# prefix
        # intentionally (GSI queries by tenant). Others must be unique.
        non_tenant_prefixes = [p for p in prefixes if p != "TENANT"]
        assert len(non_tenant_prefixes) == len(set(non_tenant_prefixes)), (
            "Non-tenant PK prefixes must be unique across tables"
        )

    def test_tenant_records_share_pk_prefix_by_design(self):
        """TenantRecord, InvocationRecord, and SessionRecord all use TENANT# PK
        so tenant-scoped queries return all data for a tenant via single partition."""
        tenant = _make_tenant()
        inv = _make_invocation()
        sess = _make_session()
        assert tenant.pk.startswith("TENANT#")
        assert inv.pk.startswith("TENANT#")
        assert sess.pk.startswith("TENANT#")
