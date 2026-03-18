import re
from pathlib import Path

CI_FILE = Path(__file__).resolve().parents[2] / ".gitlab-ci.yml"
TASKS_FILE = Path(__file__).resolve().parents[2] / "docs" / "TASKS.md"


def _job_block(name: str, content: str) -> str:
    pattern = rf"(?ms)^{re.escape(name)}:\n(.*?)(?=^[A-Za-z0-9_.-]+:\n|\Z)"
    match = re.search(pattern, content)
    assert match is not None, f"Missing job block: {name}"
    return match.group(1)


def test_canary_policy_variables_are_explicit_per_environment() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    assert 'CANARY_POLICY_DEV: "all-at-once"' in content
    assert 'CANARY_POLICY_STAGING: "canary-10%-30m"' in content
    assert 'CANARY_POLICY_PROD: "canary-10%-15m"' in content
    assert 'STAGING_ROLLOUT_WINDOW_MINUTES: "30"' in content
    assert 'PROD_ROLLOUT_WINDOW_MINUTES: "15"' in content
    assert 'GITLAB_PROTECTED_ENVIRONMENT_NAME: "prod"' in content
    assert 'GITLAB_PROTECTED_ENV_REQUIRED_APPROVALS: "2"' in content


def test_ci_test_matrix_covers_unit_integration_and_cdk() -> None:
    content = CI_FILE.read_text(encoding="utf-8")
    for name in ("test-unit", "test-integration", "test-cdk"):
        block = _job_block(name, content)
        assert "extends: .test_job_base" in block


def test_validate_pipeline_policy_runs_ci_contract_and_protection_script_tests() -> None:
    content = CI_FILE.read_text(encoding="utf-8")
    validate = _job_block("validate-pipeline-policy", content)
    assert "tests/unit/test_issue_110_ci_policy.py" in validate
    assert "tests/unit/test_check_gitlab_protected_environment.py" in validate


def test_task_044_plan_stage_claim_matches_artifact_only_pipeline() -> None:
    tasks = TASKS_FILE.read_text(encoding="utf-8")
    content = CI_FILE.read_text(encoding="utf-8")
    plan = _job_block("plan-infra", content)

    assert "plan: cdk diff stored as artifacts for review" in tasks
    assert "plan: cdk diff posted as MR comment" not in tasks
    assert "dev-diff.txt" in plan
    assert "staging-diff.txt" in plan
    assert "prod-diff.txt" in plan


def test_staging_and_prod_gates_have_manual_approvals_and_rollout_windows() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    staging = _job_block("deploy-staging", content)
    assert "when: manual" in staging
    assert "deployment_tier: staging" in staging

    staging_window = _job_block("staging-rollout-window", content)
    assert "when: delayed" in staging_window
    assert "start_in: 30 minutes" in staging_window
    assert 'needs: ["deploy-staging"]' in staging_window

    prod = _job_block("deploy-prod", content)
    assert 'needs: ["staging-rollout-window"]' in prod
    assert "when: manual" in prod
    assert "deployment_tier: production" in prod
    assert "uv run python scripts/check_gitlab_protected_environment.py" in prod
    assert '--environment "${GITLAB_PROTECTED_ENVIRONMENT_NAME}"' in prod
    assert '--min-approvals "${GITLAB_PROTECTED_ENV_REQUIRED_APPROVALS}"' in prod
    assert 'test "${PROD_APPROVAL_MODE}"' not in prod

    prod_window = _job_block("prod-rollout-window", content)
    assert "when: delayed" in prod_window
    assert "start_in: 15 minutes" in prod_window
    assert 'needs: ["deploy-prod"]' in prod_window
