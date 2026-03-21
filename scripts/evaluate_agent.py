"""
evaluate_agent.py — Run golden test cases against AgentCore Evaluations service.

This script implements the evaluation gate for agent promotion. It reads
golden test cases from the agent's tests/golden/invoke_cases.json and sends
them to the AgentCore Evaluations service in Frankfurt (eu-central-1).

The agent will only promote if the aggregate evaluation score is above the
threshold defined in its pyproject.toml [tool.agentcore.evaluations].

Usage:
    uv run python scripts/evaluate_agent.py <agent_name> --env <env>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import boto3

try:
    from agent_manifest import ManifestValidationError, load_agent_manifest
except ImportError:
    from scripts.agent_manifest import ManifestValidationError, load_agent_manifest

logger = logging.getLogger("evaluate_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate agent performance")
    parser.add_argument("agent_name", help="Name of the agent")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"])
    return parser.parse_args()


def load_golden_cases(agent_dir: Path) -> list[dict[str, Any]]:
    golden_path = agent_dir / "tests" / "golden" / "invoke_cases.json"
    if not golden_path.exists():
        logger.error(f"Golden cases not found at {golden_path}")
        return []

    with open(golden_path) as f:
        data = json.load(f)

    cases: list[dict[str, Any]] = []
    # Collect all cases from all modes (sync, streaming, async)
    for mode in ("sync", "streaming", "async"):
        if mode in data:
            cases.extend(data[mode])

    return cases


def evaluate_agent(agent_name: str, env: str) -> bool:
    del env
    agent_dir = REPO_ROOT / "agents" / agent_name
    try:
        manifest = load_agent_manifest(agent_name, REPO_ROOT)
    except ManifestValidationError as exc:
        for error in exc.errors:
            logger.error(error)
        return False

    golden_cases = load_golden_cases(agent_dir)
    if not golden_cases:
        logger.error(f"No golden cases found for agent '{agent_name}'")
        return False

    logger.info(
        "Evaluating agent '%s' in %s with %s cases",
        agent_name,
        manifest.evaluations.evaluation_region,
        len(golden_cases),
    )
    logger.info("Threshold: %s", manifest.evaluations.threshold)

    try:
        # AgentCore Evaluations service is always in eu-central-1 (Frankfurt)
        acore = boto3.client(
            "bedrock-agentcore",
            region_name=manifest.evaluations.evaluation_region,
        )

        # Call the evaluation service
        # ADR-009: AgentCore Evaluations is the source of truth for promotion quality
        response = acore.evaluate_agent(
            agentName=agent_name,
            dataset=golden_cases,
        )

        score = float(response.get("score", 0.0))
        passed = score >= manifest.evaluations.threshold

        if passed:
            logger.info(
                "Evaluation PASSED: score=%.2f (threshold=%.2f)",
                score,
                manifest.evaluations.threshold,
            )
        else:
            logger.error(
                "Evaluation FAILED: score=%.2f (threshold=%.2f)",
                score,
                manifest.evaluations.threshold,
            )
            return False

        return True

    except Exception as e:
        logger.error(f"Evaluation service call failed: {e}")
        # In non-CI environments, we might want to continue for debugging,
        # but the gate MUST fail closed in CI.
        return False


if __name__ == "__main__":
    args = parse_args()
    if not evaluate_agent(args.agent_name, args.env):
        sys.exit(1)
    sys.exit(0)
