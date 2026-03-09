import re
from pathlib import Path

CI_FILE = Path(__file__).resolve().parents[2] / ".gitlab-ci.yml"


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
    assert 'PROD_APPROVAL_MODE: "protected-environment-two-reviewer"' in content


def test_ci_test_matrix_covers_unit_integration_and_cdk() -> None:
    content = CI_FILE.read_text(encoding="utf-8")
    for name in ("test-unit", "test-integration", "test-cdk"):
        block = _job_block(name, content)
        assert "extends: .test_job_base" in block


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
    assert 'test "${PROD_APPROVAL_MODE}" = "protected-environment-two-reviewer"' in prod

    prod_window = _job_block("prod-rollout-window", content)
    assert "when: delayed" in prod_window
    assert "start_in: 15 minutes" in prod_window
    assert 'needs: ["deploy-prod"]' in prod_window
