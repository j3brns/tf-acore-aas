"""Platform operations CLI backed by the Admin REST API.

Implemented in TASK-029.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

DEFAULT_ENV = "dev"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_TOKEN_TTL_SECONDS = 3600
_TOKEN_ENV_NAMES = ("OPS_ACCESS_TOKEN", "PLATFORM_ACCESS_TOKEN", "BEARER_TOKEN")


@dataclass(frozen=True)
class ApiOperation:
    method: str
    path: str
    query: dict[str, str] | None = None
    body: dict[str, Any] | None = None


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    payload: Any


class OpsCliError(RuntimeError):
    """Domain error for operator CLI failures."""


class ApiRequestError(OpsCliError):
    """Raised when an API call fails."""

    def __init__(
        self,
        *,
        message: str,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _credentials_path() -> Path:
    override = os.environ.get("PLATFORM_CREDENTIALS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".platform" / "credentials"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for filename in (".env.local", ".env"):
        path = _repo_root() / filename
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw = stripped.split("=", 1)
            key = key.strip()
            value = raw.strip().strip('"').strip("'")
            if key:
                values[key] = value
    return values


def _load_credentials_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "profiles": {}}
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise OpsCliError(f"Invalid credentials file format: {path}")
    profiles = parsed.get("profiles")
    if not isinstance(profiles, dict):
        parsed["profiles"] = {}
    return parsed


def _save_credentials_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _profile_for_env(store: dict[str, Any], env_name: str) -> dict[str, Any]:
    profiles = store.get("profiles", {})
    if not isinstance(profiles, dict):
        return {}
    profile = profiles.get(env_name, {})
    return profile if isinstance(profile, dict) else {}


def _save_profile_for_env(path: Path, env_name: str, profile: dict[str, Any]) -> None:
    store = _load_credentials_store(path)
    profiles = store.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        raise OpsCliError(f"Invalid credentials file format: {path}")
    profiles[env_name] = profile
    _save_credentials_store(path, store)


def _decode_json(raw: bytes | None) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _build_url(base_url: str, path: str, query: dict[str, str] | None) -> str:
    merged = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if query:
        return f"{merged}?{urlencode(query)}"
    return merged


def _request_api(
    *,
    base_url: str,
    token: str,
    operation: ApiOperation,
    timeout_seconds: int,
) -> ApiResponse:
    url = _build_url(base_url, operation.path, operation.query)
    data = None
    if operation.body is not None:
        data = json.dumps(operation.body).encode("utf-8")

    request = Request(url=url, data=data, method=operation.method)
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = _decode_json(response.read())
            return ApiResponse(status_code=int(response.status), payload=payload)
    except HTTPError as exc:
        payload = _decode_json(exc.read())
        message = f"API request failed with HTTP {exc.code}: {operation.method} {operation.path}"
        raise ApiRequestError(message=message, status_code=exc.code, payload=payload) from exc
    except URLError as exc:
        raise ApiRequestError(
            message=f"API request failed: {operation.method} {operation.path} ({exc.reason})"
        ) from exc


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + ("=" * ((4 - len(parts[1]) % 4) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _token_subject(claims: dict[str, Any]) -> str:
    for key in ("preferred_username", "upn", "email", "sub"):
        raw = claims.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return "unknown"


def _token_roles(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("roles", [])
    if isinstance(raw, str):
        return [role for role in raw.replace(",", " ").split() if role]
    if isinstance(raw, list):
        return [str(role).strip() for role in raw if str(role).strip()]
    return []


def _resolve_expires_at(token_claims: dict[str, Any], fallback_ttl: int) -> str:
    exp = token_claims.get("exp")
    if isinstance(exp, (int, float)):
        return _format_iso(datetime.fromtimestamp(float(exp), tz=UTC))
    return _format_iso(datetime.now(tz=UTC) + timedelta(seconds=fallback_ttl))


def _resolve_api_base_url(
    *,
    explicit: str | None,
    profile: dict[str, Any],
) -> str:
    if explicit and explicit.strip():
        return explicit.strip()

    for key in ("API_BASE_URL", "VITE_API_BASE_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            return value

    dotenv_values = _load_dotenv_values()
    for key in ("API_BASE_URL", "VITE_API_BASE_URL"):
        value = dotenv_values.get(key, "").strip()
        if value:
            return value

    from_profile = str(profile.get("apiBaseUrl", "")).strip()
    if from_profile:
        return from_profile

    raise OpsCliError("API base URL not set. Use --api-base-url or API_BASE_URL.")


def _resolve_token(cli_token: str | None, profile: dict[str, Any]) -> str:
    if cli_token and cli_token.strip():
        return cli_token.strip()

    for env_name in _TOKEN_ENV_NAMES:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value

    from_profile = str(profile.get("accessToken", "")).strip()
    if from_profile:
        expires_at = str(profile.get("expiresAt", "")).strip()
        if expires_at:
            try:
                expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expires < datetime.now(tz=UTC):
                    raise OpsCliError(
                        "Stored token is expired. Run `make ops-login` to refresh credentials."
                    )
            except ValueError:
                pass
        return from_profile

    raise OpsCliError("No access token found. Run `make ops-login` first.")


def _print_payload(payload: Any, *, stream: Any = sys.stdout) -> None:
    if payload is None:
        return
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, indent=2, sort_keys=True), file=stream)
        return
    print(payload, file=stream)


def _handle_login(args: argparse.Namespace) -> int:
    token = _resolve_token(args.token, {})
    claims = _jwt_payload(token)
    roles = _token_roles(claims)
    subject = _token_subject(claims)
    api_base_url = _resolve_api_base_url(explicit=args.api_base_url, profile={})
    expires_at = _resolve_expires_at(claims, args.ttl_seconds)

    profile = {
        "accessToken": token,
        "tokenType": "Bearer",
        "issuedAt": _format_iso(datetime.now(tz=UTC)),
        "expiresAt": expires_at,
        "subject": subject,
        "roles": roles,
        "apiBaseUrl": api_base_url,
    }
    creds_path = _credentials_path()
    _save_profile_for_env(creds_path, args.env, profile)

    role_text = ", ".join(roles) if roles else "none"
    print(f"Logged in as {subject} with roles: {role_text}")
    print(f"Credentials saved: {creds_path}")
    return 0


def _command_to_operation(args: argparse.Namespace) -> ApiOperation:
    if args.command == "top-tenants":
        return ApiOperation(
            method="GET",
            path="/v1/platform/ops/top-tenants",
            query={"n": str(args.n)},
        )
    if args.command == "tenant-sessions":
        tenant = quote(args.tenant, safe="")
        return ApiOperation(method="GET", path=f"/v1/platform/ops/tenants/{tenant}/sessions")
    if args.command == "suspend-tenant":
        tenant = quote(args.tenant, safe="")
        return ApiOperation(
            method="POST",
            path=f"/v1/platform/ops/tenants/{tenant}/suspend",
            body={"reason": args.reason},
        )
    if args.command == "reinstate-tenant":
        tenant = quote(args.tenant, safe="")
        return ApiOperation(method="POST", path=f"/v1/platform/ops/tenants/{tenant}/reinstate")
    if args.command == "quota-report":
        return ApiOperation(method="GET", path="/v1/platform/quota")
    if args.command == "invocation-report":
        tenant = quote(args.tenant, safe="")
        return ApiOperation(
            method="GET",
            path=f"/v1/platform/ops/tenants/{tenant}/invocations",
            query={"days": str(args.days)},
        )
    if args.command == "security-events":
        return ApiOperation(
            method="GET",
            path="/v1/platform/ops/security-events",
            query={"hours": str(args.hours)},
        )
    if args.command == "dlq-inspect":
        queue = quote(args.queue, safe="")
        return ApiOperation(method="GET", path=f"/v1/platform/ops/dlq/{queue}")
    if args.command == "dlq-redrive":
        queue = quote(args.queue, safe="")
        return ApiOperation(method="POST", path=f"/v1/platform/ops/dlq/{queue}/redrive")
    if args.command == "error-rate":
        return ApiOperation(
            method="GET",
            path="/v1/platform/ops/error-rate",
            query={"minutes": str(args.minutes)},
        )
    if args.command == "failover-lock-acquire":
        return ApiOperation(method="POST", path="/v1/platform/failover/lock/acquire")
    if args.command == "failover-lock-release":
        return ApiOperation(method="POST", path="/v1/platform/failover/lock/release")
    if args.command == "set-runtime-region":
        return ApiOperation(
            method="POST",
            path="/v1/platform/failover",
            body={"runtimeRegion": args.region},
        )
    if args.command == "notify-tenant":
        tenant = quote(args.tenant, safe="")
        return ApiOperation(
            method="POST",
            path=f"/v1/platform/ops/tenants/{tenant}/notify",
            body={"template": args.template},
        )
    if args.command == "service-health":
        return ApiOperation(method="GET", path="/v1/platform/service-health")
    if args.command == "billing-status":
        return ApiOperation(method="GET", path="/v1/platform/billing/status")
    if args.command == "update-tenant-budget":
        tenant = quote(args.tenant, safe="")
        return ApiOperation(
            method="PATCH",
            path=f"/v1/tenants/{tenant}",
            body={"monthlyBudgetUsd": args.budget},
        )
    if args.command == "fail-job":
        job = quote(args.job, safe="")
        return ApiOperation(
            method="POST",
            path=f"/v1/platform/ops/jobs/{job}/fail",
            body={"reason": args.reason},
        )
    if args.command == "audit-export":
        tenant = quote(args.tenant, safe="")
        query: dict[str, str] = {}
        if args.start:
            query["start"] = args.start
        if args.end:
            query["end"] = args.end
        return ApiOperation(
            method="GET",
            path=f"/v1/tenants/{tenant}/audit-export",
            query=query if query else None,
        )
    if args.command == "page-security":
        return ApiOperation(
            method="POST",
            path="/v1/platform/ops/security/page",
            body={"incident": args.incident, "tenantId": args.tenant},
        )
    raise OpsCliError(f"Unsupported command: {args.command}")


def _add_api_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env", default=DEFAULT_ENV)
    parser.add_argument("--api-base-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops.py",
        description="Platform operations CLI (Admin REST API only).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Store operator access token for API calls.")
    login.add_argument("--env", default=DEFAULT_ENV)
    login.add_argument("--api-base-url", default=None)
    login.add_argument("--token", default=None)
    login.add_argument("--ttl-seconds", type=int, default=DEFAULT_TOKEN_TTL_SECONDS)

    top_tenants = subparsers.add_parser("top-tenants", help="List top tenants by token usage.")
    _add_api_common_arguments(top_tenants)
    top_tenants.add_argument("--n", type=int, default=10)

    tenant_sessions = subparsers.add_parser(
        "tenant-sessions",
        help="List active sessions for a tenant.",
    )
    _add_api_common_arguments(tenant_sessions)
    tenant_sessions.add_argument("--tenant", required=True)

    suspend_tenant = subparsers.add_parser("suspend-tenant", help="Suspend a tenant.")
    _add_api_common_arguments(suspend_tenant)
    suspend_tenant.add_argument("--tenant", required=True)
    suspend_tenant.add_argument("--reason", required=True)

    reinstate_tenant = subparsers.add_parser("reinstate-tenant", help="Reinstate a tenant.")
    _add_api_common_arguments(reinstate_tenant)
    reinstate_tenant.add_argument("--tenant", required=True)

    quota_report = subparsers.add_parser("quota-report", help="Get AgentCore quota report.")
    _add_api_common_arguments(quota_report)

    invocation_report = subparsers.add_parser(
        "invocation-report",
        help="Get tenant invocation report.",
    )
    _add_api_common_arguments(invocation_report)
    invocation_report.add_argument("--tenant", required=True)
    invocation_report.add_argument("--days", type=int, default=7)

    security_events = subparsers.add_parser(
        "security-events",
        help="List tenant access violation events.",
    )
    _add_api_common_arguments(security_events)
    security_events.add_argument("--hours", type=int, default=24)

    dlq_inspect = subparsers.add_parser("dlq-inspect", help="Inspect a DLQ.")
    _add_api_common_arguments(dlq_inspect)
    dlq_inspect.add_argument("--queue", required=True)

    dlq_redrive = subparsers.add_parser("dlq-redrive", help="Redrive a DLQ.")
    _add_api_common_arguments(dlq_redrive)
    dlq_redrive.add_argument("--queue", required=True)

    error_rate = subparsers.add_parser("error-rate", help="Get error rate.")
    _add_api_common_arguments(error_rate)
    error_rate.add_argument("--minutes", type=int, default=5)

    failover_lock_acquire = subparsers.add_parser(
        "failover-lock-acquire",
        help="Acquire failover lock.",
    )
    _add_api_common_arguments(failover_lock_acquire)

    failover_lock_release = subparsers.add_parser(
        "failover-lock-release",
        help="Release failover lock.",
    )
    _add_api_common_arguments(failover_lock_release)

    set_runtime_region = subparsers.add_parser(
        "set-runtime-region",
        help="Set active runtime region.",
    )
    _add_api_common_arguments(set_runtime_region)
    set_runtime_region.add_argument("--region", required=True)

    notify_tenant = subparsers.add_parser("notify-tenant", help="Notify tenant owner.")
    _add_api_common_arguments(notify_tenant)
    notify_tenant.add_argument("--tenant", required=True)
    notify_tenant.add_argument("--template", required=True)

    service_health = subparsers.add_parser("service-health", help="Check service health.")
    _add_api_common_arguments(service_health)

    billing_status = subparsers.add_parser("billing-status", help="Get billing status.")
    _add_api_common_arguments(billing_status)

    update_tenant_budget = subparsers.add_parser(
        "update-tenant-budget",
        help="Update tenant monthly budget.",
    )
    _add_api_common_arguments(update_tenant_budget)
    update_tenant_budget.add_argument("--tenant", required=True)
    update_tenant_budget.add_argument("--budget", type=float, required=True)

    fail_job = subparsers.add_parser("fail-job", help="Mark async job as failed.")
    _add_api_common_arguments(fail_job)
    fail_job.add_argument("--job", required=True)
    fail_job.add_argument("--reason", required=True)

    audit_export = subparsers.add_parser("audit-export", help="Get tenant audit export URL.")
    _add_api_common_arguments(audit_export)
    audit_export.add_argument("--tenant", required=True)
    audit_export.add_argument("--start", default=None)
    audit_export.add_argument("--end", default=None)

    page_security = subparsers.add_parser("page-security", help="Page security team.")
    _add_api_common_arguments(page_security)
    page_security.add_argument("--incident", required=True)
    page_security.add_argument("--tenant", required=True)

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _run_api_command(args: argparse.Namespace) -> int:
    creds_store = _load_credentials_store(_credentials_path())
    profile = _profile_for_env(creds_store, args.env)
    api_base_url = _resolve_api_base_url(explicit=args.api_base_url, profile=profile)
    token = _resolve_token(args.token, profile)
    operation = _command_to_operation(args)

    response = _request_api(
        base_url=api_base_url,
        token=token,
        operation=operation,
        timeout_seconds=args.timeout_seconds,
    )
    _print_payload(response.payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "login":
            return _handle_login(args)
        return _run_api_command(args)
    except ApiRequestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        _print_payload(exc.payload, stream=sys.stderr)
        return 1
    except OpsCliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
