"""Tests for scripts/validate_agent_manifest.py — tier and invocation mode validation."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validate_agent_manifest import VALID_INVOCATION_MODES, VALID_TIERS, validate_manifest


def _write_manifest(tmp_path: Path, agent_name: str, toml_content: str) -> None:
    """Write a pyproject.toml for the given agent under a fake agents/ dir."""
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "pyproject.toml").write_text(textwrap.dedent(toml_content))


# ---------------------------------------------------------------------------
# Tier enum: canonical set is {basic, standard, premium}
# ---------------------------------------------------------------------------


class TestTierEnum:
    """The supported tier set must match ARCHITECTURE.md and the scaling model."""

    def test_canonical_tiers(self) -> None:
        assert VALID_TIERS == {"basic", "standard", "premium"}

    def test_enterprise_is_not_a_valid_tier(self) -> None:
        assert "enterprise" not in VALID_TIERS

    @pytest.mark.parametrize("tier", sorted(VALID_TIERS))
    def test_valid_tier_accepted(self, tmp_path: Path, tier: str) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            f"""\
            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "{tier}"
            invocation_mode = "sync"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is True

    @pytest.mark.parametrize("tier", ["enterprise", "free", "gold", ""])
    def test_invalid_tier_rejected(self, tmp_path: Path, tier: str) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            f"""\
            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "{tier}"
            invocation_mode = "sync"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False


# ---------------------------------------------------------------------------
# Invocation mode enum
# ---------------------------------------------------------------------------


class TestInvocationModeEnum:
    """Invocation modes must match ARCHITECTURE.md."""

    def test_canonical_modes(self) -> None:
        assert VALID_INVOCATION_MODES == {"sync", "streaming", "async"}

    @pytest.mark.parametrize("mode", sorted(VALID_INVOCATION_MODES))
    def test_valid_mode_accepted(self, tmp_path: Path, mode: str) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            f"""\
            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "basic"
            invocation_mode = "{mode}"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is True

    def test_invalid_mode_rejected(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "basic"
            invocation_mode = "batch"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


class TestRequiredFields:
    def test_missing_agentcore_section(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [project]
            name = "test-agent"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False

    def test_missing_required_field(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            invocation_mode = "sync"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False

    def test_agent_name_mismatch(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [tool.agentcore]
            name = "different-agent"
            owner_team = "team-test"
            tier_minimum = "basic"
            invocation_mode = "sync"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False

    def test_nonexistent_agent(self, tmp_path: Path) -> None:
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("no-such-agent") is False
