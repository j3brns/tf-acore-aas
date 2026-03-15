"""Developer CLI for invoking the contracted agent REST route."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_API_BASE_URL = "http://localhost:8080"
DEFAULT_ENV = "local"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_CREDENTIALS_PATH = Path.home() / ".platform" / "credentials"
_TOKEN_ENV_NAMES = ("DEV_INVOKE_JWT", "PLATFORM_ACCESS_TOKEN", "BEARER_TOKEN")


class DevInvokeError(RuntimeError):
    """Domain error for CLI usage and request failures."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

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


def _load_dotenv_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for filename in (".env.local", ".env", ".env.test"):
        values.update(_load_env_file(_repo_root() / filename))
    return values


def _load_credentials_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "profiles": {}}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise DevInvokeError(f"Invalid credentials file format: {path}")
    profiles = parsed.get("profiles")
    if not isinstance(profiles, dict):
        parsed["profiles"] = {}
    return parsed


def _credentials_path() -> Path:
    override = os.environ.get("PLATFORM_CREDENTIALS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_CREDENTIALS_PATH


def _profile_for_env(store: dict[str, Any], env_name: str) -> dict[str, Any]:
    profiles = store.get("profiles", {})
    if not isinstance(profiles, dict):
        return {}
    profile = profiles.get(env_name, {})
    return profile if isinstance(profile, dict) else {}


def _resolve_api_base_url(explicit: str | None, env_name: str) -> str:
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

    if env_name == DEFAULT_ENV:
        return DEFAULT_API_BASE_URL

    profile = _profile_for_env(_load_credentials_store(_credentials_path()), env_name)
    stored = str(profile.get("apiBaseUrl", "")).strip()
    if stored:
        return stored

    raise DevInvokeError(
        "API base URL not set. Use --api-base-url, API_BASE_URL, or VITE_API_BASE_URL."
    )


def _tenant_token_from_env_test(tenant_id: str, dotenv_values: dict[str, str]) -> str | None:
    mappings = (
        ("BASIC_TENANT_ID", "BASIC_TENANT_JWT"),
        ("PREMIUM_TENANT_ID", "PREMIUM_TENANT_JWT"),
        ("ADMIN_TENANT_ID", "ADMIN_JWT"),
    )
    for tenant_key, token_key in mappings:
        if dotenv_values.get(tenant_key, "").strip() == tenant_id:
            token = dotenv_values.get(token_key, "").strip()
            return token or None

    if tenant_id == "admin-001":
        token = dotenv_values.get("ADMIN_JWT", "").strip()
        return token or None
    return None


def _resolve_token(explicit: str | None, tenant_id: str, env_name: str) -> str:
    if explicit and explicit.strip():
        return explicit.strip()

    for env_key in _TOKEN_ENV_NAMES:
        candidate = os.environ.get(env_key, "").strip()
        if candidate:
            return candidate

    dotenv_values = _load_dotenv_values()
    tenant_token = _tenant_token_from_env_test(tenant_id, dotenv_values)
    if tenant_token:
        return tenant_token

    profile = _profile_for_env(_load_credentials_store(_credentials_path()), env_name)
    stored = str(profile.get("accessToken", "")).strip()
    if stored:
        return stored

    raise DevInvokeError(
        "Bearer token not set. Use --jwt, DEV_INVOKE_JWT, .env.test, or stored credentials."
    )


def _build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {"input": args.prompt}
    if args.session_id:
        payload["sessionId"] = args.session_id
    if args.webhook_id:
        payload["webhookId"] = args.webhook_id
    return payload


def build_request(
    *,
    api_base_url: str,
    token: str,
    args: argparse.Namespace,
) -> Request:
    route = f"/v1/agents/{args.agent}/invoke"
    url = urljoin(api_base_url.rstrip("/") + "/", route.lstrip("/"))
    payload = json.dumps(_build_payload(args)).encode("utf-8")
    request = Request(url=url, data=payload, method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    accept = "text/event-stream" if args.mode == "streaming" else "application/json"
    request.add_header("Accept", accept)
    request.add_header("x-tenant-id", args.tenant)
    return request


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Invoke an agent via the contracted REST route.")
    parser.add_argument("--agent", required=True, help="Agent name registered in the platform")
    parser.add_argument("--tenant", required=True, help="Tenant ID for the request context")
    parser.add_argument(
        "--jwt",
        "--token",
        dest="token",
        help="Bearer token to send in the Authorization header",
    )
    parser.add_argument("--prompt", default="Hello", help="Input prompt/body for the invocation")
    parser.add_argument(
        "--mode",
        choices=("sync", "streaming", "async"),
        default="sync",
        help="Client expectation for response handling; the server still decides actual mode",
    )
    parser.add_argument("--env", default=DEFAULT_ENV, help="Profile/environment name")
    parser.add_argument("--api-base-url", help="Override the API base URL")
    parser.add_argument("--session-id", help="Optional existing session identifier")
    parser.add_argument("--webhook-id", help="Optional webhook registration identifier")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds",
    )
    return parser.parse_args(argv)


def _decode_json(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    return json.loads(text)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _print_streaming_response(raw: bytes) -> None:
    events: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            events.append(data)
            continue
        if payload.get("type") == "text":
            events.append(str(payload.get("content", "")))
    print("".join(events))


def _handle_success_response(*, content_type: str, body: bytes) -> None:
    if "text/event-stream" in content_type:
        _print_streaming_response(body)
        return

    payload = _decode_json(body)
    _print_json(payload)


def _handle_http_error(exc: HTTPError) -> None:
    body = exc.read()
    try:
        payload = _decode_json(body)
    except json.JSONDecodeError:
        payload = body.decode("utf-8", errors="replace")
    print(
        f"Invoke failed with HTTP {exc.code}",
        file=sys.stderr,
    )
    if payload is not None:
        rendered = (
            json.dumps(payload, indent=2, sort_keys=True)
            if not isinstance(payload, str)
            else payload
        )
        print(rendered, file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    api_base_url = _resolve_api_base_url(args.api_base_url, args.env)
    token = _resolve_token(args.token, args.tenant, args.env)
    request = build_request(api_base_url=api_base_url, token=token, args=args)

    try:
        with urlopen(request, timeout=args.timeout_seconds) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "application/json")
    except HTTPError as exc:
        _handle_http_error(exc)
        return 1
    except URLError as exc:
        print(f"Invoke failed: {exc.reason}", file=sys.stderr)
        return 1

    _handle_success_response(content_type=content_type, body=body)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return run(args)
    except DevInvokeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
