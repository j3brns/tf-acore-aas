#!/usr/bin/env python3
"""
docs_sync_audit.py

Lightweight docs/code consistency audit and semver stamp utility.

Why:
- A single semver line appended on every write is noisy and not actionable.
- Better: keep one machine-readable sync stamp + objective drift checks.

Commands:
  check   Validate docs sync stamp and drift heuristics
  stamp   Refresh docs/DOCS_SYNC.json to current semver + commit
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CDK_PACKAGE = ROOT / "infra" / "cdk" / "package.json"
SPA_PACKAGE = ROOT / "spa" / "package.json"
TASKS_MD = ROOT / "docs" / "TASKS.md"
OPS_PY = ROOT / "scripts" / "ops.py"
STAMP_FILE = ROOT / "docs" / "DOCS_SYNC.json"


SEMVER_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
TASK_PATH_RE = re.compile(r"\b(src/[A-Za-z0-9._/-]+/handler\.py)\b")


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=ROOT).stdout.strip()


def read_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = SEMVER_RE.search(text)
    if not match:
        raise RuntimeError("Could not parse version from pyproject.toml")
    return match.group(1)


def read_package_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError(f"Could not parse version from {path}")
    return version


def current_head() -> str:
    return _run(["git", "rev-parse", "HEAD"])


def current_head_short() -> str:
    return _run(["git", "rev-parse", "--short", "HEAD"])


def collect_versions() -> dict[str, str]:
    return {
        "python": read_pyproject_version(),
        "cdk": read_package_version(CDK_PACKAGE),
        "spa": read_package_version(SPA_PACKAGE),
    }


def detect_task_handler_path_drift() -> list[str]:
    """
    Detect task doc paths that refer to non-existent handler files.
    """
    findings: list[str] = []
    text = TASKS_MD.read_text(encoding="utf-8")
    paths = sorted(set(TASK_PATH_RE.findall(text)))
    for rel in paths:
        path = ROOT / rel
        if not path.exists():
            findings.append(f"docs/TASKS.md references missing path: {rel}")
    return findings


def detect_ops_cli_stub_drift() -> list[str]:
    """
    Heuristic: Makefile exposes many ops commands but scripts/ops.py is a stub.
    """
    findings: list[str] = []
    text = OPS_PY.read_text(encoding="utf-8")
    has_argparse = "argparse" in text
    has_main = "if __name__ == \"__main__\"" in text
    if not (has_argparse and has_main):
        findings.append(
            "scripts/ops.py appears to be a stub (missing argparse/main) "
            "while Makefile exposes operational commands."
        )
    return findings


def load_stamp() -> dict[str, Any]:
    if not STAMP_FILE.exists():
        return {}
    try:
        return json.loads(STAMP_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {STAMP_FILE}") from exc


def build_stamp() -> dict[str, Any]:
    versions = collect_versions()
    # canonical product semver: must be uniform across tracked components
    canonical = versions["python"]
    return {
        "semver": canonical,
        "components": versions,
        "commit": current_head(),
        "commit_short": current_head_short(),
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "strategy": (
            "Release-oriented docs sync stamp. Update when behavior/docs are intentionally "
            "aligned for a release checkpoint; do not append on every file write."
        ),
    }


def cmd_stamp() -> int:
    stamp = build_stamp()
    STAMP_FILE.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print(
        "Updated "
        f"{STAMP_FILE.relative_to(ROOT)} "
        f"semver={stamp['semver']} "
        f"commit={stamp['commit_short']}"
    )
    return 0


def cmd_check(json_output: bool = False) -> int:
    errors: list[str] = []
    warnings: list[str] = []

    versions = collect_versions()
    if len(set(versions.values())) != 1:
        errors.append(f"component version mismatch: {versions}")

    stamp = load_stamp()
    if not stamp:
        warnings.append("docs/DOCS_SYNC.json missing; run `make docs-sync-stamp`")
    else:
        stamp_semver = str(stamp.get("semver", ""))
        if stamp_semver != versions["python"]:
            errors.append(
                f"docs/DOCS_SYNC.json semver ({stamp_semver}) != code semver ({versions['python']})"
            )
        components = stamp.get("components")
        if isinstance(components, dict):
            for key, val in versions.items():
                if str(components.get(key, "")) != val:
                    errors.append(
                        f"docs/DOCS_SYNC.json components.{key} ({components.get(key)}) != {val}"
                    )

    warnings.extend(detect_task_handler_path_drift())
    warnings.extend(detect_ops_cli_stub_drift())

    result = {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "versions": versions,
    }

    if json_output:
        print(json.dumps(result, indent=2))
    else:
        print("Docs sync audit:", "PASS" if result["ok"] else "FAILED")
        print(f"  versions: {versions}")
        for item in errors:
            print(f"  ERROR: {item}")
        for item in warnings:
            print(f"  WARN:  {item}")

    return 0 if len(errors) == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Audit docs/code consistency")
    check.add_argument("--json", action="store_true", help="Emit JSON output")

    sub.add_parser("stamp", help="Refresh docs/DOCS_SYNC.json")

    args = parser.parse_args()
    if args.command == "check":
        return cmd_check(json_output=args.json)
    if args.command == "stamp":
        return cmd_stamp()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
