from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

try:
    import handler as shared
except ImportError:  # pragma: no cover - local package import path
    from src.tenant_api import handler as shared


@shared.logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    deps = shared._dependencies()
    detail_type = shared._str_or_none(event.get("detail-type"))
    source = shared._str_or_none(event.get("source"))
    if detail_type and source == "platform.tenant_provisioner":
        detail = event.get("detail") or {}
        tenant_id = (
            shared._str_or_none(detail.get("tenantId")) if isinstance(detail, dict) else None
        )
        app_id = shared._str_or_none(detail.get("appId")) if isinstance(detail, dict) else None
        shared.logger.append_keys(appid=app_id or "unknown", tenantid=tenant_id or "unknown")
        try:
            return shared._handle_tenant_provisioning_event(event, deps)
        except ValueError as exc:
            return shared._error(400, "BAD_REQUEST", str(exc))
        except ClientError as exc:
            shared.logger.exception("AWS client error in tenant provisioning event handler")
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            return shared._error(502, "AWS_CLIENT_ERROR", error_code)

    caller = shared._caller_identity(event)
    shared.logger.append_keys(
        appid=caller.app_id or "unknown",
        tenantid=caller.tenant_id or "unknown",
    )

    method = shared._http_method(event)
    path = shared._request_path(event)

    try:
        tenant_id = shared._validated_path_tenant_id(event)
        if path == "/v1/health" and method == "GET":
            return shared._handle_health(deps)
        if path == "/v1/sessions" and method == "GET":
            return shared._handle_sessions(event, caller)

        response = shared._dispatch_tenant_routes(path, method, event, caller, deps, tenant_id)
        if response:
            return response

        return shared._error(405, "METHOD_NOT_ALLOWED", "Unsupported tenant management route")
    except PermissionError as exc:
        return shared._error(403, "FORBIDDEN", str(exc))
    except ValueError as exc:
        return shared._error(400, "BAD_REQUEST", str(exc))
    except ClientError as exc:
        shared.logger.exception("AWS client error in tenant management handler")
        return shared._error(
            502, "AWS_CLIENT_ERROR", exc.response.get("Error", {}).get("Code", "Unknown")
        )
    except Exception:
        shared.logger.exception("Unhandled tenant management handler error")
        return shared._error(500, "INTERNAL_ERROR", "Internal server error")
