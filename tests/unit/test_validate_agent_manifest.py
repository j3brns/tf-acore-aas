"""Tests for scripts/validate_agent_manifest.py and the shared manifest contract."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.agent_manifest import load_agent_manifest
from scripts.validate_agent_manifest import VALID_INVOCATION_MODES, VALID_TIERS, validate_manifest


def _write_manifest(tmp_path: Path, agent_name: str, toml_content: str) -> None:
    """Write a pyproject.toml for the given agent under a fake agents/ dir."""
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "pyproject.toml").write_text(textwrap.dedent(toml_content))


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
            [project]
            name = "test-agent"
            version = "1.0.0"

            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "{tier}"
            handler = "handler:invoke"
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
            [project]
            name = "test-agent"
            version = "1.0.0"

            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "{tier}"
            handler = "handler:invoke"
            invocation_mode = "sync"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False


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
            [project]
            name = "test-agent"
            version = "1.0.0"

            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "basic"
            handler = "handler:invoke"
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
            [project]
            name = "test-agent"
            version = "1.0.0"

            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "basic"
            handler = "handler:invoke"
            invocation_mode = "batch"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False


class TestRequiredFields:
    def test_missing_agentcore_section(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [project]
            name = "test-agent"
            version = "1.0.0"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False

    def test_missing_required_field(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [project]
            name = "test-agent"
            version = "1.0.0"

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
            [project]
            name = "test-agent"
            version = "1.0.0"

            [tool.agentcore]
            name = "different-agent"
            owner_team = "team-test"
            tier_minimum = "basic"
            handler = "handler:invoke"
            invocation_mode = "sync"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False

    def test_nonexistent_agent(self, tmp_path: Path) -> None:
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("no-such-agent") is False

    def test_project_name_mismatch(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [project]
            name = "different-agent"
            version = "1.0.0"

            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "basic"
            handler = "handler:invoke"
            invocation_mode = "sync"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False

    def test_unknown_manifest_key_rejected(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [project]
            name = "test-agent"
            version = "1.0.0"

            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "basic"
            handler = "handler:invoke"
            invocation_mode = "sync"
            release_channel = "beta"
        """,
        )
        with patch("scripts.validate_agent_manifest.REPO_ROOT", tmp_path):
            assert validate_manifest("test-agent") is False

    def test_loader_reads_optional_sections(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "test-agent",
            """\
            [project]
            name = "test-agent"
            version = "1.2.3"

            [tool.agentcore]
            name = "test-agent"
            owner_team = "team-test"
            tier_minimum = "premium"
            handler = "handler:invoke"
            invocation_mode = "async"
            streaming_enabled = true
            estimated_duration_seconds = 42

            [tool.agentcore.llm]
            model_id = "anthropic.claude-sonnet-4-6"
            max_tokens = 2048

            [tool.agentcore.deployment]
            type = "container"

            [tool.agentcore.evaluations]
            threshold = 0.9
            evaluation_region = "eu-west-1"
        """,
        )

        manifest = load_agent_manifest("test-agent", tmp_path)
        assert manifest.version == "1.2.3"
        assert manifest.tier_minimum.value == "premium"
        assert manifest.invocation_mode.value == "async"
        assert manifest.streaming_enabled is True
        assert manifest.estimated_duration_seconds == 42
        assert manifest.deployment.type == "container"
        assert manifest.llm.model_id == "anthropic.claude-sonnet-4-6"
        assert manifest.llm.max_tokens == 2048
        assert manifest.evaluations.threshold == pytest.approx(0.9)
        assert manifest.evaluations.evaluation_region == "eu-west-1"
