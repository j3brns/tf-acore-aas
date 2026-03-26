"""
agent_manifest.py — Canonical agent manifest loader and validator.

The platform's agent contract lives in agents/<name>/pyproject.toml and is split
across the [project] table for packaging metadata and the [tool.agentcore] tables
for platform-owned metadata.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from data_access.models import InvocationMode, TenantTier

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVALUATION_REGION = "eu-central-1"
VALID_DEPLOYMENT_TYPES = frozenset({"zip", "container"})

_MANIFEST_KEYS = frozenset(
    {
        "name",
        "owner_team",
        "tier_minimum",
        "handler",
        "invocation_mode",
        "streaming_enabled",
        "estimated_duration_seconds",
        "llm",
        "deployment",
        "evaluations",
    }
)
_LLM_KEYS = frozenset({"model_id", "max_tokens"})
_DEPLOYMENT_KEYS = frozenset({"type"})
_EVALUATIONS_KEYS = frozenset({"threshold", "evaluation_region"})


class ManifestValidationError(ValueError):
    """Raised when an agent manifest violates the contract."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class AgentDeploymentConfig:
    type: str = "zip"


@dataclass(frozen=True)
class AgentLlmConfig:
    model_id: str | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class AgentEvaluationsConfig:
    threshold: float = 0.8
    evaluation_region: str = DEFAULT_EVALUATION_REGION


@dataclass(frozen=True)
class AgentManifest:
    project_name: str
    version: str
    name: str
    owner_team: str
    tier_minimum: TenantTier
    handler: str
    invocation_mode: InvocationMode
    streaming_enabled: bool = False
    estimated_duration_seconds: int = 5
    deployment: AgentDeploymentConfig = AgentDeploymentConfig()
    llm: AgentLlmConfig = AgentLlmConfig()
    evaluations: AgentEvaluationsConfig = AgentEvaluationsConfig()


def load_agent_manifest(agent_name: str, repo_root: Path | None = None) -> AgentManifest:
    if repo_root is None:
        repo_root = REPO_ROOT
    toml_path = repo_root / "agents" / agent_name / "pyproject.toml"
    if not toml_path.exists():
        raise ManifestValidationError(
            [f"pyproject.toml not found for agent '{agent_name}' at {toml_path}"]
        )

    try:
        with toml_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestValidationError([f"Failed to parse {toml_path}: {exc}"]) from exc

    errors: list[str] = []

    project_raw = _require_table(data, "project", errors)
    tool_raw = _require_table(data, "tool", errors)
    manifest_raw = _require_table(tool_raw, "agentcore", errors, label="[tool.agentcore]")

    project_name = _required_string(project_raw, "name", errors, table="[project]")
    version = _required_string(project_raw, "version", errors, table="[project]")
    _check_unknown_keys(manifest_raw, _MANIFEST_KEYS, errors, table="[tool.agentcore]")

    name = _required_string(manifest_raw, "name", errors, table="[tool.agentcore]")
    owner_team = _required_string(manifest_raw, "owner_team", errors, table="[tool.agentcore]")
    handler = _required_string(manifest_raw, "handler", errors, table="[tool.agentcore]")
    tier_minimum = _required_enum(
        manifest_raw, "tier_minimum", TenantTier, errors, table="[tool.agentcore]"
    )
    invocation_mode = _required_enum(
        manifest_raw, "invocation_mode", InvocationMode, errors, table="[tool.agentcore]"
    )
    streaming_enabled = _optional_bool(
        manifest_raw,
        "streaming_enabled",
        default=False,
        errors=errors,
        table="[tool.agentcore]",
    )
    estimated_duration_seconds = _optional_int(
        manifest_raw,
        "estimated_duration_seconds",
        default=5,
        errors=errors,
        table="[tool.agentcore]",
    )

    if project_name and project_name != agent_name:
        errors.append(
            f"Project name mismatch: [project].name '{project_name}' does not match directory "
            f"name '{agent_name}'"
        )
    if name and name != agent_name:
        errors.append(
            f"Agent name mismatch: manifest name '{name}' does not match directory name "
            f"'{agent_name}'"
        )
    if project_name and name and project_name != name:
        errors.append(
            f"Name mismatch: [project].name '{project_name}' does not match "
            f"[tool.agentcore].name '{name}'"
        )
    if handler and ":" not in handler:
        errors.append("Invalid handler: [tool.agentcore].handler must be in 'module:function' form")
    if estimated_duration_seconds is not None and estimated_duration_seconds <= 0:
        errors.append(
            "Invalid estimated_duration_seconds: [tool.agentcore].estimated_duration_seconds "
            "must be greater than 0"
        )

    llm_raw = _optional_table(manifest_raw, "llm", errors, table="[tool.agentcore.llm]")
    _check_unknown_keys(llm_raw, _LLM_KEYS, errors, table="[tool.agentcore.llm]")
    model_id = _optional_string(llm_raw, "model_id", errors=errors, table="[tool.agentcore.llm]")
    max_tokens = _optional_int(
        llm_raw,
        "max_tokens",
        default=None,
        errors=errors,
        table="[tool.agentcore.llm]",
    )
    if max_tokens is not None and max_tokens <= 0:
        errors.append("Invalid max_tokens: [tool.agentcore.llm].max_tokens must be greater than 0")

    deployment_raw = _optional_table(
        manifest_raw,
        "deployment",
        errors,
        table="[tool.agentcore.deployment]",
    )
    _check_unknown_keys(
        deployment_raw,
        _DEPLOYMENT_KEYS,
        errors,
        table="[tool.agentcore.deployment]",
    )
    deployment_type = _optional_string(
        deployment_raw,
        "type",
        default="zip",
        errors=errors,
        table="[tool.agentcore.deployment]",
    )
    if deployment_type not in VALID_DEPLOYMENT_TYPES:
        options = ", ".join(sorted(VALID_DEPLOYMENT_TYPES))
        errors.append(f"Invalid deployment type: '{deployment_type}'. Must be one of: {options}")

    evaluations_raw = _optional_table(
        manifest_raw,
        "evaluations",
        errors,
        table="[tool.agentcore.evaluations]",
    )
    _check_unknown_keys(
        evaluations_raw,
        _EVALUATIONS_KEYS,
        errors,
        table="[tool.agentcore.evaluations]",
    )
    threshold = _optional_float(
        evaluations_raw,
        "threshold",
        default=0.8,
        errors=errors,
        table="[tool.agentcore.evaluations]",
    )
    evaluation_region = _optional_string(
        evaluations_raw,
        "evaluation_region",
        default=DEFAULT_EVALUATION_REGION,
        errors=errors,
        table="[tool.agentcore.evaluations]",
    )
    if threshold is not None and not 0.0 <= threshold <= 1.0:
        errors.append("Invalid threshold: [tool.agentcore.evaluations].threshold must be 0.0-1.0")

    if errors:
        raise ManifestValidationError(errors)

    return AgentManifest(
        project_name=project_name,
        version=version,
        name=name,
        owner_team=owner_team,
        tier_minimum=cast(TenantTier, tier_minimum),
        handler=handler,
        invocation_mode=cast(InvocationMode, invocation_mode),
        streaming_enabled=streaming_enabled,
        estimated_duration_seconds=estimated_duration_seconds or 5,
        deployment=AgentDeploymentConfig(type=deployment_type or "zip"),
        llm=AgentLlmConfig(model_id=model_id, max_tokens=max_tokens),
        evaluations=AgentEvaluationsConfig(
            threshold=threshold,
            evaluation_region=evaluation_region or DEFAULT_EVALUATION_REGION,
        ),
    )


def _require_table(
    data: dict[str, Any],
    key: str,
    errors: list[str],
    *,
    label: str | None = None,
) -> dict[str, Any]:
    table_label = label or f"[{key}]"
    value = data.get(key)
    if not isinstance(value, dict):
        errors.append(f"Missing {table_label} section")
        return {}
    return value


def _optional_table(
    data: dict[str, Any],
    key: str,
    errors: list[str],
    *,
    table: str,
) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"Invalid {table}: expected table, got {type(value).__name__}")
        return {}
    return value


def _required_string(
    data: dict[str, Any],
    key: str,
    errors: list[str],
    *,
    table: str,
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"Missing required field: {table}.{key}")
        return ""
    return value.strip()


def _optional_string(
    data: dict[str, Any],
    key: str,
    *,
    default: str | None = None,
    errors: list[str],
    table: str,
) -> str | None:
    value = data.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"Invalid {table}.{key}: expected non-empty string")
        return default
    return value.strip()


def _optional_bool(
    data: dict[str, Any],
    key: str,
    *,
    default: bool,
    errors: list[str],
    table: str,
) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        errors.append(f"Invalid {table}.{key}: expected bool, got {type(value).__name__}")
        return default
    return value


def _optional_int(
    data: dict[str, Any],
    key: str,
    *,
    default: int | None,
    errors: list[str],
    table: str,
) -> int | None:
    value = data.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"Invalid {table}.{key}: expected int, got {type(value).__name__}")
        return default
    return value


def _optional_float(
    data: dict[str, Any],
    key: str,
    *,
    default: float,
    errors: list[str],
    table: str,
) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        errors.append(f"Invalid {table}.{key}: expected number, got {type(value).__name__}")
        return default
    return float(value)


def _required_enum(
    data: dict[str, Any],
    key: str,
    enum_type: type[TenantTier] | type[InvocationMode],
    errors: list[str],
    *,
    table: str,
) -> TenantTier | InvocationMode:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"Missing required field: {table}.{key}")
        return next(iter(enum_type))
    try:
        return enum_type(value.strip())
    except ValueError:
        options = ", ".join(member.value for member in enum_type)
        errors.append(f"Invalid {table}.{key}: '{value}'. Must be one of: {options}")
        return next(iter(enum_type))


def _check_unknown_keys(
    data: dict[str, Any],
    allowed_keys: frozenset[str],
    errors: list[str],
    *,
    table: str,
) -> None:
    unknown_keys = sorted(set(data) - allowed_keys)
    if unknown_keys:
        errors.append(f"Unknown keys in {table}: {', '.join(unknown_keys)}")
