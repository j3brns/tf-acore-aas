"""
build_layer.py — Build and upload arm64 dependency layer for an agent.

Cross-compiles Python dependencies for aarch64-manylinux2014 (AgentCore Runtime).
Uploads to S3 and updates SSM hash and s3-key.

Command:
    uv pip install \\
        --python-platform aarch64-manylinux2014 \\
        --python-version 3.12 \\
        --target=.build/deps \\
        --only-binary=:all:

--only-binary=:all: ensures no source packages are compiled on the wrong arch.

Usage:
    uv run python scripts/build_layer.py <agent_name> --env <env>

Implemented in TASK-034.
ADRs: ADR-006
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import boto3

logger = logging.getLogger("build_layer")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO_ROOT / ".build"
HASH_LENGTH = 16
PYTHON_PLATFORM = "aarch64-manylinux2014"
PYTHON_VERSION = "3.12"
ARM64_TOKENS = ("aarch64", "arm64")
FORBIDDEN_ARCH_TOKENS = ("x86_64", "amd64", "i686")
BINARY_SUFFIXES = (".so", ".pyd", ".dylib")
BUCKET_PARAM_CANDIDATES = (
    "/platform/core/{env}/agent-artifacts-bucket",
    "/platform/core/{env}/runtime-artifact-bucket",
    "/platform/core/{env}/artifacts-bucket",
)


def require_aws_region() -> str:
    """Read AWS_REGION from environment and fail fast if missing."""
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Build and upload arm64 dependency layer for an agent"
    )
    parser.add_argument(
        "agent_name",
        help="Agent name (must match agents/<agent_name>/pyproject.toml)",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["dev", "staging", "prod"],
        help="Target environment",
    )
    return parser.parse_args(argv)


def read_agent_deps(agent_name: str) -> list[str]:
    """Read [project.dependencies] from agents/{agent_name}/pyproject.toml."""
    toml_path = REPO_ROOT / "agents" / agent_name / "pyproject.toml"
    if not toml_path.exists():
        raise FileNotFoundError(f"pyproject.toml not found: {toml_path}")

    with toml_path.open("rb") as fh:
        data = tomllib.load(fh)

    deps = data.get("project", {}).get("dependencies", [])
    if not isinstance(deps, list):
        raise ValueError(f"[project.dependencies] must be a list in {toml_path}")
    return [str(dep) for dep in deps]


def read_agent_lockfile(agent_name: str) -> str | None:
    """Read uv.lock from agents/{agent_name}/uv.lock if it exists.

    Returns the file content as a string, or None if the lockfile is absent.
    """
    lock_path = REPO_ROOT / "agents" / agent_name / "uv.lock"
    if not lock_path.exists():
        return None
    return lock_path.read_text(encoding="utf-8")


def compute_dependency_hash(deps: list[str], lockfile_content: str | None = None) -> str:
    """Return canonical dependency hash used for S3 key and SSM metadata.

    Includes lockfile content when present to track transitive dependency changes.
    """
    canonical = "\n".join(sorted(dep.strip() for dep in deps))
    if lockfile_content is not None:
        canonical = canonical + "\n---lockfile---\n" + lockfile_content
    return hashlib.sha256(canonical.encode()).hexdigest()[:HASH_LENGTH]


def build_dependencies(dependencies: list[str], target_dir: Path) -> None:
    """Cross-compile dependencies for arm64 using uv."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    command = [
        "uv",
        "pip",
        "install",
        "--python-platform",
        PYTHON_PLATFORM,
        "--python-version",
        PYTHON_VERSION,
        "--target",
        str(target_dir),
        "--only-binary=:all:",
        *dependencies,
    ]
    logger.info("Building arm64 dependencies for %d packages", len(dependencies))
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def _extract_wheel_tags(wheel_text: str) -> list[str]:
    tags: list[str] = []
    for line in wheel_text.splitlines():
        line = line.strip()
        if line.startswith("Tag:"):
            tags.append(line.split(":", 1)[1].strip())
    return tags


def _validate_wheel_tags(tags: list[str], source: str) -> None:
    for tag in tags:
        tag_lower = tag.lower()
        if any(token in tag_lower for token in FORBIDDEN_ARCH_TOKENS):
            raise RuntimeError(f"Non-arm64 wheel tag detected in {source}: {tag}")
        is_linux_tag = "linux" in tag_lower or "manylinux" in tag_lower
        is_pure_python = "any" in tag_lower
        if (
            is_linux_tag
            and not is_pure_python
            and not any(token in tag_lower for token in ARM64_TOKENS)
        ):
            raise RuntimeError(f"Wheel tag is not arm64 in {source}: {tag}")


def verify_arm64_artifacts(deps_dir: Path) -> None:
    """Verify built artifacts do not include x86 wheel tags or binary names."""
    for wheel_file in sorted(deps_dir.rglob("*.dist-info/WHEEL")):
        tags = _extract_wheel_tags(wheel_file.read_text(encoding="utf-8", errors="replace"))
        _validate_wheel_tags(tags, str(wheel_file))

    for binary_file in sorted(deps_dir.rglob("*")):
        if not binary_file.is_file() or binary_file.suffix.lower() not in BINARY_SUFFIXES:
            continue
        name = binary_file.name.lower()
        if any(token in name for token in FORBIDDEN_ARCH_TOKENS):
            raise RuntimeError(f"Non-arm64 binary detected: {binary_file}")


def create_layer_zip(deps_dir: Path, zip_path: Path) -> None:
    """Zip dependency directory contents into zip_path."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(deps_dir.rglob("*")):
            if item.is_file():
                archive.write(item, arcname=item.relative_to(deps_dir).as_posix())


def verify_arm64_zip(zip_path: Path) -> None:
    """Verify the packaged zip does not contain x86 artifacts."""
    with zipfile.ZipFile(zip_path, mode="r") as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            filename = info.filename
            lower_name = Path(filename).name.lower()
            if filename.endswith(".dist-info/WHEEL"):
                text = archive.read(info).decode("utf-8", errors="replace")
                tags = _extract_wheel_tags(text)
                _validate_wheel_tags(tags, filename)
            if lower_name.endswith(BINARY_SUFFIXES) and any(
                token in lower_name for token in FORBIDDEN_ARCH_TOKENS
            ):
                raise RuntimeError(f"Non-arm64 binary in zip: {filename}")


def resolve_layer_bucket(env: str, aws_region: str) -> str:
    """Resolve layer artifact bucket from env var first, then SSM parameter."""
    for env_var in ("PLATFORM_LAYER_BUCKET", "AGENT_LAYER_BUCKET", "LAYER_ARTIFACT_BUCKET"):
        value = os.environ.get(env_var, "").strip()
        if value:
            return value

    ssm = boto3.client("ssm", region_name=aws_region)
    names = [template.format(env=env) for template in BUCKET_PARAM_CANDIDATES]
    response = ssm.get_parameters(Names=names)
    by_name: dict[str, str] = {}
    for item in response.get("Parameters", []):
        name = item.get("Name")
        value = item.get("Value")
        if not name or not value:
            continue
        by_name[str(name)] = str(value)
    for name in names:
        value = by_name.get(name, "").strip()
        if value:
            return value

    joined = ", ".join(names)
    raise RuntimeError(
        "Layer artifact bucket not configured. "
        f"Set PLATFORM_LAYER_BUCKET or one of SSM params: {joined}"
    )


def upload_layer_zip(zip_path: Path, *, bucket: str, key: str, aws_region: str) -> None:
    """Upload layer zip to S3."""
    s3 = boto3.client("s3", region_name=aws_region)
    s3.upload_file(str(zip_path), bucket, key)


def put_layer_metadata(agent_name: str, env: str, dep_hash: str, key: str, aws_region: str) -> None:
    """Write hash and S3 key metadata to SSM."""
    ssm = boto3.client("ssm", region_name=aws_region)
    ssm.put_parameter(
        Name=f"/platform/layers/{env}/{agent_name}/hash",
        Value=dep_hash,
        Type="String",
        Overwrite=True,
    )
    ssm.put_parameter(
        Name=f"/platform/layers/{env}/{agent_name}/s3-key",
        Value=key,
        Type="String",
        Overwrite=True,
    )


def run(agent_name: str, env: str) -> int:
    """Run dependency layer build and publish flow."""
    aws_region = require_aws_region()
    deps = read_agent_deps(agent_name)
    lockfile = read_agent_lockfile(agent_name)
    dep_hash = compute_dependency_hash(deps, lockfile_content=lockfile)

    deps_dir = BUILD_DIR / "deps"
    zip_path = BUILD_DIR / f"{agent_name}-deps-{dep_hash}.zip"
    build_dependencies(deps, deps_dir)
    verify_arm64_artifacts(deps_dir)
    create_layer_zip(deps_dir, zip_path)
    verify_arm64_zip(zip_path)

    bucket = resolve_layer_bucket(env, aws_region)
    key = f"layers/{zip_path.name}"
    upload_layer_zip(zip_path, bucket=bucket, key=key, aws_region=aws_region)
    put_layer_metadata(agent_name, env, dep_hash, key, aws_region)

    logger.info("Layer published for %s: s3://%s/%s", agent_name, bucket, key)
    print(f"LAYER_BUILT agent={agent_name} hash={dep_hash} s3=s3://{bucket}/{key}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    try:
        return run(agent_name=args.agent_name, env=args.env)
    except Exception as exc:
        logger.error("build_layer failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
