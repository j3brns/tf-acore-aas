"""Repo structure regression tests.

Ensures the repository structure matches what documentation advertises.
Prevents the repo from claiming directories or surfaces that do not exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestAdvertisedDirectories:
    """Directories that README, ARCHITECTURE.md, or ADRs claim exist must exist."""

    @pytest.mark.parametrize(
        "directory,description",
        [
            ("infra/cdk", "CDK stacks (platform IaC)"),
            ("infra/terraform", "Account vending Terraform (ADR-007)"),
            ("infra/guard", "cfn-guard security rules"),
            ("src", "Platform Lambda functions"),
            ("agents", "Agent implementations"),
            ("gateway", "AgentCore Gateway interceptor Lambdas"),
            ("tests", "Test suites"),
            ("docs", "Documentation"),
            ("scripts", "Ops and bootstrap scripts"),
        ],
    )
    def test_advertised_directory_exists(self, directory: str, description: str) -> None:
        path = REPO_ROOT / directory
        assert path.is_dir(), (
            f"Directory '{directory}' ({description}) is advertised in repo docs "
            f"but does not exist. Either create it or remove the documentation claim."
        )


class TestTerraformAccountVending:
    """The Terraform account-vending surface must have the expected structure."""

    tf_root = REPO_ROOT / "infra" / "terraform"

    def test_root_module_files_exist(self) -> None:
        for name in ("main.tf", "variables.tf", "outputs.tf", "versions.tf"):
            assert (self.tf_root / name).is_file(), f"Missing root module file: {name}"

    def test_vended_account_module_exists(self) -> None:
        module_dir = self.tf_root / "modules" / "vended-account"
        assert module_dir.is_dir(), "Missing vended-account module directory"
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            assert (module_dir / name).is_file(), f"Missing module file: {name}"

    def test_env_directories_exist(self) -> None:
        for env in ("staging", "prod"):
            env_dir = self.tf_root / "envs" / env
            assert env_dir.is_dir(), f"Missing env directory: {env}"
            assert (env_dir / "terraform.tfvars").is_file(), f"Missing terraform.tfvars in {env}"
