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


_MINIMAL_TOML = """\
[project]
name = "{name}"
version = "1.0.0"

[tool.agentcore]
name = "{name}"
owner_team = "team-test"
tier_minimum = "basic"
handler = "handler:invoke"
invocation_mode = "sync"
"""


class TestHandlerFormat:
    def test_handler_without_colon_is_rejected(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            "my-agent",
            _MINIMAL_TOML.replace(
                'handler = "handler:invoke"', 'handler = "handler_invoke"'
            ).format(name="my-agent"),
        )
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("my-agent", tmp_path)
        assert any("handler" in e.lower() for e in exc_info.value.errors)

    def test_handler_with_colon_is_accepted(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, "my-agent", _MINIMAL_TOML.format(name="my-agent"))
        manifest = load_agent_manifest("my-agent", tmp_path)
        assert manifest.handler == "handler:invoke"


class TestEstimatedDurationValidation:
    def test_zero_duration_is_rejected(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + "\nestimated_duration_seconds = 0\n"
        _write_manifest(tmp_path, "my-agent", toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("my-agent", tmp_path)
        assert any("estimated_duration_seconds" in e for e in exc_info.value.errors)

    def test_negative_duration_is_rejected(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + "\nestimated_duration_seconds = -5\n"
        _write_manifest(tmp_path, "my-agent", toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("my-agent", tmp_path)
        assert any("estimated_duration_seconds" in e for e in exc_info.value.errors)

    def test_positive_duration_is_accepted(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + "\nestimated_duration_seconds = 30\n"
        _write_manifest(tmp_path, "my-agent", toml)
        manifest = load_agent_manifest("my-agent", tmp_path)
        assert manifest.estimated_duration_seconds == 30


class TestLlmSectionValidation:
    def test_max_tokens_zero_rejected(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            '\n[tool.agentcore.llm]\nmodel_id = "claude-3"\nmax_tokens = 0\n'
        )
        _write_manifest(tmp_path, "my-agent", toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("my-agent", tmp_path)
        assert any("max_tokens" in e for e in exc_info.value.errors)

    def test_max_tokens_positive_accepted(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            '\n[tool.agentcore.llm]\nmodel_id = "claude-3"\nmax_tokens = 1024\n'
        )
        _write_manifest(tmp_path, "my-agent", toml)
        manifest = load_agent_manifest("my-agent", tmp_path)
        assert manifest.llm.max_tokens == 1024

    def test_unknown_llm_key_is_rejected(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            '\n[tool.agentcore.llm]\nmodel_id = "claude-3"\ntemperature = 0.7\n'
        )
        _write_manifest(tmp_path, "my-agent", toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("my-agent", tmp_path)
        assert any("temperature" in e for e in exc_info.value.errors)


class TestDeploymentSectionValidation:
    def test_invalid_deployment_type_rejected(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            '\n[tool.agentcore.deployment]\ntype = "docker"\n'
        )
        _write_manifest(tmp_path, "my-agent", toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("my-agent", tmp_path)
        assert any("deployment type" in e.lower() for e in exc_info.value.errors)

    def test_zip_deployment_type_accepted(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            '\n[tool.agentcore.deployment]\ntype = "zip"\n'
        )
        _write_manifest(tmp_path, "my-agent", toml)
        manifest = load_agent_manifest("my-agent", tmp_path)
        assert manifest.deployment.type == "zip"

    def test_container_deployment_type_accepted(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            '\n[tool.agentcore.deployment]\ntype = "container"\n'
        )
        _write_manifest(tmp_path, "my-agent", toml)
        manifest = load_agent_manifest("my-agent", tmp_path)
        assert manifest.deployment.type == "container"


class TestEvaluationsSectionValidation:
    def test_threshold_above_one_rejected(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            "\n[tool.agentcore.evaluations]\nthreshold = 1.5\n"
        )
        _write_manifest(tmp_path, "my-agent", toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("my-agent", tmp_path)
        assert any("threshold" in e for e in exc_info.value.errors)

    def test_threshold_below_zero_rejected(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            "\n[tool.agentcore.evaluations]\nthreshold = -0.1\n"
        )
        _write_manifest(tmp_path, "my-agent", toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("my-agent", tmp_path)
        assert any("threshold" in e for e in exc_info.value.errors)

    def test_threshold_boundaries_accepted(self, tmp_path: Path) -> None:
        toml = _MINIMAL_TOML.format(name="my-agent") + (
            "\n[tool.agentcore.evaluations]\nthreshold = 0.0\n"
        )
        _write_manifest(tmp_path, "my-agent", toml)
        manifest = load_agent_manifest("my-agent", tmp_path)
        assert manifest.evaluations.threshold == pytest.approx(0.0)

    def test_default_evaluation_region_is_eu_central(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, "my-agent", _MINIMAL_TOML.format(name="my-agent"))
        manifest = load_agent_manifest("my-agent", tmp_path)
        assert manifest.evaluations.evaluation_region == "eu-central-1"


class TestTomlParseError:
    def test_malformed_toml_raises_manifest_validation_error(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agents" / "bad-agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "pyproject.toml").write_text("[[invalid toml\nkey = oops")
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("bad-agent", tmp_path)
        assert any("parse" in e.lower() or "Failed" in e for e in exc_info.value.errors)


class TestMissingProjectSection:
    def test_missing_project_section_accumulates_error(self, tmp_path: Path) -> None:
        toml = """\
[tool.agentcore]
name = "no-project"
owner_team = "team-a"
tier_minimum = "basic"
handler = "handler:invoke"
invocation_mode = "sync"
"""
        agent_dir = tmp_path / "agents" / "no-project"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "pyproject.toml").write_text(toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("no-project", tmp_path)
        assert any("[project]" in e for e in exc_info.value.errors)


class TestManifestValidationErrorStructure:
    def test_errors_attribute_contains_all_issues(self, tmp_path: Path) -> None:
        """Multiple validation failures must all be reported together, not fail-fast."""
        toml = """\
[project]
name = "multi-err"
version = "1.0.0"

[tool.agentcore]
name = "multi-err"
owner_team = "team-test"
tier_minimum = "invalid-tier"
handler = "no_colon_here"
invocation_mode = "invalid-mode"
estimated_duration_seconds = -1
"""
        _write_manifest(tmp_path, "multi-err", toml)
        from scripts.agent_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            load_agent_manifest("multi-err", tmp_path)

        errors = exc_info.value.errors
        assert len(errors) >= 3, f"Expected multiple errors, got: {errors}"
        joined = " ".join(errors)
        assert "tier_minimum" in joined
        assert "invocation_mode" in joined
