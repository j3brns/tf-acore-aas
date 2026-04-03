from __future__ import annotations

import re
from pathlib import Path

RUNTIME_FILES = [
    "src/authoriser/handler.py",
    "src/billing/handler.py",
    "src/bridge/discovery_service.py",
    "src/bridge/handler.py",
    "src/bridge/lock_manager.py",
    "gateway/interceptors/request_interceptor.py",
]


def test_runtime_files_do_not_use_raw_dynamodb_resource() -> None:
    pattern = re.compile(r'boto3\.resource\(\s*["\']dynamodb["\']')
    repo_root = Path(__file__).resolve().parents[2]

    offenders: list[str] = []
    for relative_path in RUNTIME_FILES:
        content = (repo_root / relative_path).read_text(encoding="utf-8")
        if pattern.search(content):
            offenders.append(relative_path)

    assert offenders == [], f"raw boto3 DynamoDB resource forbidden in runtime files: {offenders}"
