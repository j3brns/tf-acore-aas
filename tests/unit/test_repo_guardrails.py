from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SECURITY_CRITICAL_FILE_LIMITS = {
    "src/bridge/handler.py": 650,
    "src/authoriser/handler.py": 560,
    "gateway/interceptors/request_interceptor.py": 600,
    "gateway/interceptors/response_interceptor.py": 320,
    "src/tenant_api/handler.py": 380,
    "infra/cdk/lib/platform-stack.ts": 1250,
}

ALLOWED_PRODUCTION_HANDLER_IMPORTS = {
    "src/tenant_api/admin_ops_handler.py",
    "src/tenant_api/agent_registry_handler.py",
    "src/tenant_api/ops_control.py",
    "src/tenant_api/tenant_mgmt_handler.py",
    "src/tenant_api/webhook_registry_handler.py",
}


def test_security_critical_files_stay_under_guardrail_limits() -> None:
    offenders: list[str] = []

    for relative_path, max_lines in SECURITY_CRITICAL_FILE_LIMITS.items():
        line_count = sum(1 for _ in (REPO_ROOT / relative_path).open("r", encoding="utf-8"))
        if line_count > max_lines:
            offenders.append(f"{relative_path} ({line_count} > {max_lines})")

    assert offenders == [], (
        "security-critical modules exceeded size guardrails; split them before they become the "
        f"next monolith: {offenders}"
    )


def test_production_code_does_not_add_new_handler_to_handler_imports() -> None:
    patterns = (
        re.compile(r"^\s*from\s+src\.[\w.]+\.handler\s+import\b", re.MULTILINE),
        re.compile(r"^\s*from\s+src\.[\w.]+\s+import\s+handler\b", re.MULTILINE),
        re.compile(r"^\s*import\s+handler\s+as\s+\w+", re.MULTILINE),
    )

    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        if relative_path in ALLOWED_PRODUCTION_HANDLER_IMPORTS:
            continue
        content = path.read_text(encoding="utf-8")
        if any(pattern.search(content) for pattern in patterns):
            offenders.append(relative_path)

    assert offenders == [], (
        "production handler-to-handler imports are limited to explicit shim allowlist entries: "
        f"{offenders}"
    )
