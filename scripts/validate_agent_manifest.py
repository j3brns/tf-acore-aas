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
from pathlib import Path

from data_access.models import InvocationMode, TenantTier

try:
    from agent_manifest import ManifestValidationError, load_agent_manifest
except ImportError:
    from scripts.agent_manifest import ManifestValidationError, load_agent_manifest

logger = logging.getLogger("validate_manifest")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

VALID_TIERS = {tier.value for tier in TenantTier}
VALID_INVOCATION_MODES = {mode.value for mode in InvocationMode}
REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate agent manifest")
    parser.add_argument("agent_name", help="Name of the agent directory")
    return parser.parse_args()


def validate_manifest(agent_name: str) -> bool:
    try:
        load_agent_manifest(agent_name, REPO_ROOT)
    except ManifestValidationError as exc:
        for error in exc.errors:
            logger.error(error)
        return False

    logger.info("Manifest for agent '%s' is valid.", agent_name)
    return True


if __name__ == "__main__":
    args = parse_args()
    if not validate_manifest(args.agent_name):
        sys.exit(1)
    sys.exit(0)
