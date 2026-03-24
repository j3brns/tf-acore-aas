from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _load_docs_sync_audit_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "docs_sync_audit", repo_root / "scripts" / "docs_sync_audit.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


docs_sync_audit = _load_docs_sync_audit_module()


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_detect_branch_deploy_flow_drift_reports_old_feature_branch_story(
    monkeypatch, tmp_path: Path
) -> None:
    ci_file = _write(
        tmp_path / ".gitlab-ci.yml",
        """
deploy-dev:
  stage: deploy-dev
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
""".lstrip(),
    )
    agent_guide = _write(
        tmp_path / "docs" / "development" / "AGENT-DEVELOPER-GUIDE.md",
        """
Pipeline Promotion

Pushing to a feature branch triggers: validate → test → push-dev (auto).
Merge to main triggers: promote-staging (manual gate, requires evaluation score).
""".lstrip(),
    )

    monkeypatch.setattr(docs_sync_audit, "CI_FILE", ci_file)
    monkeypatch.setattr(docs_sync_audit, "AGENT_GUIDE_MD", agent_guide)

    warnings = docs_sync_audit.detect_branch_deploy_flow_drift()

    assert warnings == [
        "docs/development/AGENT-DEVELOPER-GUIDE.md still describes a "
        "feature-branch push-dev / promote-staging flow, but .gitlab-ci.yml "
        "only deploys dev on main and keeps staging/prod gated."
    ]


def test_detect_local_env_test_key_drift_reports_makefile_and_docs_mismatch(
    monkeypatch, tmp_path: Path
) -> None:
    makefile = _write(
        tmp_path / "Makefile",
        """
dev-invoke:
\t@TENANT=$$(grep BASIC_TENANT_ID .env.test); \\
\tJWT=$$(grep BASIC_TENANT_JWT .env.test)
""".lstrip(),
    )
    bootstrap = _write(
        tmp_path / "scripts" / "dev-bootstrap.py",
        """
def write_env_test(tokens, env_test_path):
    lines = [
        "TEST_JWT_BASIC=",
        "TEST_JWT_PREMIUM=",
        "TEST_JWT_ADMIN=",
    ]
""".lstrip(),
    )
    local_setup = _write(
        tmp_path / "docs" / "development" / "LOCAL-SETUP.md",
        """
| BASIC_TENANT_JWT      | t-test-001 | basic    |
| PREMIUM_TENANT_JWT    | t-test-002 | premium  |
| ADMIN_JWT             | admin-001  | Platform.Admin |
""".lstrip(),
    )
    agent_guide = _write(
        tmp_path / "docs" / "development" / "AGENT-DEVELOPER-GUIDE.md",
        "Use make agent-invoke AGENT=my-agent ENV=local\n",
    )
    runbook = _write(
        tmp_path / "docs" / "operations" / "RUNBOOK-008-developer-onboarding.md",
        "make dev-invoke\n",
    )

    monkeypatch.setattr(docs_sync_audit, "MAKEFILE", makefile)
    monkeypatch.setattr(docs_sync_audit, "DEV_BOOTSTRAP_PY", bootstrap)
    monkeypatch.setattr(docs_sync_audit, "LOCAL_SETUP_MD", local_setup)
    monkeypatch.setattr(docs_sync_audit, "AGENT_GUIDE_MD", agent_guide)
    monkeypatch.setattr(docs_sync_audit, "RUNBOOK_008_MD", runbook)

    warnings = docs_sync_audit.detect_local_env_test_key_drift()

    assert any("make dev-invoke reads BASIC_TENANT_ID/BASIC_TENANT_JWT" in msg for msg in warnings)
    assert any("TEST_JWT_BASIC/TEST_JWT_PREMIUM/TEST_JWT_ADMIN" in msg for msg in warnings)


def test_detect_local_fixture_name_drift_reports_docs_and_bootstrap_mismatch(
    monkeypatch, tmp_path: Path
) -> None:
    local_setup = _write(
        tmp_path / "docs" / "development" / "LOCAL-SETUP.md",
        """
After make dev, two test tenants are available:
t-test-001
t-test-002
""".lstrip(),
    )
    agent_guide = _write(
        tmp_path / "docs" / "development" / "AGENT-DEVELOPER-GUIDE.md",
        'tenantId": "t-test-001"\n',
    )
    runbook = _write(
        tmp_path / "docs" / "operations" / "RUNBOOK-008-developer-onboarding.md",
        "make agent-invoke AGENT=my-first-agent TENANT=t-test-001\n",
    )
    bootstrap = _write(
        tmp_path / "scripts" / "dev-bootstrap.py",
        """
TENANT_FIXTURES = [
    {"tenant_id": "t-basic-001"},
    {"tenant_id": "t-premium-001"},
]
""".lstrip(),
    )

    monkeypatch.setattr(docs_sync_audit, "LOCAL_SETUP_MD", local_setup)
    monkeypatch.setattr(docs_sync_audit, "AGENT_GUIDE_MD", agent_guide)
    monkeypatch.setattr(docs_sync_audit, "RUNBOOK_008_MD", runbook)
    monkeypatch.setattr(docs_sync_audit, "DEV_BOOTSTRAP_PY", bootstrap)

    warnings = docs_sync_audit.detect_local_fixture_name_drift()

    assert warnings == [
        "Local docs reference tenant fixtures ['t-test-001', 't-test-002'], "
        "but scripts/dev-bootstrap.py seeds ['t-basic-001', 't-premium-001']."
    ]


def test_cmd_check_surfaces_local_dev_drift_warnings(monkeypatch, tmp_path: Path, capsys) -> None:
    makefile = _write(
        tmp_path / "Makefile",
        (
            "dev-invoke:\n\t@TENANT=$$(grep BASIC_TENANT_ID .env.test)\n"
            "\tJWT=$$(grep BASIC_TENANT_JWT .env.test)\n"
        ),
    )
    bootstrap = _write(
        tmp_path / "scripts" / "dev-bootstrap.py",
        """
TENANT_FIXTURES = [
    {"tenant_id": "t-basic-001"},
    {"tenant_id": "t-premium-001"},
]
print("TEST_JWT_BASIC")
print("TEST_JWT_PREMIUM")
print("TEST_JWT_ADMIN")
""".lstrip(),
    )
    local_setup = _write(
        tmp_path / "docs" / "development" / "LOCAL-SETUP.md",
        "BASIC_TENANT_JWT | t-test-001\nPREMIUM_TENANT_JWT | t-test-002\n",
    )
    agent_guide = _write(
        tmp_path / "docs" / "development" / "AGENT-DEVELOPER-GUIDE.md",
        'tenantId": "t-test-001"\n',
    )
    runbook = _write(
        tmp_path / "docs" / "operations" / "RUNBOOK-008-developer-onboarding.md",
        "make dev-invoke\n",
    )

    monkeypatch.setattr(docs_sync_audit, "MAKEFILE", makefile)
    monkeypatch.setattr(docs_sync_audit, "DEV_BOOTSTRAP_PY", bootstrap)
    monkeypatch.setattr(docs_sync_audit, "LOCAL_SETUP_MD", local_setup)
    monkeypatch.setattr(docs_sync_audit, "AGENT_GUIDE_MD", agent_guide)
    monkeypatch.setattr(docs_sync_audit, "RUNBOOK_008_MD", runbook)
    monkeypatch.setattr(
        docs_sync_audit,
        "collect_versions",
        lambda: {"python": "1.0.0", "cdk": "1.0.0", "spa": "1.0.0"},
    )
    monkeypatch.setattr(
        docs_sync_audit,
        "load_stamp",
        lambda: {
            "semver": "1.0.0",
            "components": {"python": "1.0.0", "cdk": "1.0.0", "spa": "1.0.0"},
        },
    )

    rc = docs_sync_audit.cmd_check(json_output=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "Docs sync audit: PASS" in out
    assert "make dev-invoke reads BASIC_TENANT_ID/BASIC_TENANT_JWT" in out
    assert "Local docs reference tenant fixtures" in out


def test_cmd_check_passes_on_repository_local_contract(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        docs_sync_audit,
        "collect_versions",
        lambda: {"python": "1.0.0", "cdk": "1.0.0", "spa": "1.0.0"},
    )
    monkeypatch.setattr(
        docs_sync_audit,
        "load_stamp",
        lambda: {
            "semver": "1.0.0",
            "components": {"python": "1.0.0", "cdk": "1.0.0", "spa": "1.0.0"},
        },
    )

    rc = docs_sync_audit.cmd_check(json_output=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "Docs sync audit: PASS" in out
    assert "WARN:" not in out
