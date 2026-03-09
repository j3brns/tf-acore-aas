"""
validate_agent_manifest.py — Validate agent pyproject.toml [tool.agentcore] section.

Ensures that the agent manifest follows the required schema for registration.
Checks for required fields, type correctness, and enum constraints.

Usage:
    uv run python scripts/validate_agent_manifest.py <agent_name>
"""

from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from pathlib import Path
from typing import Any

# Add src to path to import models if needed,
# but for simple validation we can just define the expected fields here
# to avoid dependency issues in the validation stage.

REQUIRED_FIELDS = {
    "name": str,
    "owner_team": str,
    "tier_minimum": str,
    "invocation_mode": str,
}

VALID_TIERS = {"basic", "standard", "premium"}
VALID_INVOCATION_MODES = {"sync", "streaming", "async"}

logger = logging.getLogger("validate_manifest")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate agent manifest")
    parser.add_argument("agent_name", help="Name of the agent directory")
    return parser.parse_args()


def validate_manifest(agent_name: str) -> bool:
    toml_path = REPO_ROOT / "agents" / agent_name / "pyproject.toml"
    if not toml_path.exists():
        logger.error(f"pyproject.toml not found for agent '{agent_name}' at {toml_path}")
        return False

    with open(toml_path, "rb") as f:
        try:
            data = tomllib.load(f)
        except Exception as e:
            logger.error(f"Failed to parse pyproject.toml: {e}")
            return False

    if "tool" not in data or "agentcore" not in data["tool"]:
        logger.error(f"Missing [tool.agentcore] section in {toml_path}")
        return False

    manifest = data["tool"]["agentcore"]
    errors = []

    # Check required fields and types
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in manifest:
            errors.append(f"Missing required field: '{field}'")
        elif not isinstance(manifest[field], expected_type):
            errors.append(
                f"Invalid type for '{field}': expected {expected_type.__name__}, "
                f"got {type(manifest[field]).__name__}"
            )

    # Check enum values
    if "tier_minimum" in manifest and manifest["tier_minimum"] not in VALID_TIERS:
        errors.append(
            f"Invalid tier_minimum: '{manifest['tier_minimum']}'. "
            f"Must be one of: {', '.join(VALID_TIERS)}"
        )

    if "invocation_mode" in manifest and manifest["invocation_mode"] not in VALID_INVOCATION_MODES:
        errors.append(
            f"Invalid invocation_mode: '{manifest['invocation_mode']}'. "
            f"Must be one of: {', '.join(VALID_INVOCATION_MODES)}"
        )

    # Check name match
    if "name" in manifest and manifest["name"] != agent_name:
        errors.append(
            f"Agent name mismatch: manifest name '{manifest['name']}' "
            f"does not match directory name '{agent_name}'"
        )

    if errors:
        for error in errors:
            logger.error(error)
        return False

    logger.info(f"Manifest for agent '{agent_name}' is valid.")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not validate_manifest(args.agent_name):
        sys.exit(1)
    sys.exit(0)
