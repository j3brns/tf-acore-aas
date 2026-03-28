from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

try:
    import handler as shared
    import ops_control
except ImportError:  # pragma: no cover - local package import path
    from src.tenant_api import handler as shared
    from src.tenant_api import ops_control


@shared.logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = shared._http_method(event)
    path = shared._request_path(event)

    try:
        platform_admin_paths = ops_control.PLATFORM_ADMIN_PATHS

        if not path.startswith("/v1/platform/ops/") and path not in platform_admin_paths:
            return shared._error(405, "METHOD_NOT_ALLOWED", "Unsupported admin ops route")

        deps = shared._dependencies()
        caller = shared._caller_identity(event)
        shared.logger.append_keys(
            appid=caller.app_id or "unknown",
            tenantid=caller.tenant_id or "unknown",
        )

        if path.startswith("/v1/platform/ops/"):
            response = ops_control.dispatch_ops_routes(path, method, event, caller, deps)
            if response:
                return response

        if path in platform_admin_paths:
            response = ops_control.dispatch_platform_admin_routes(path, method, event, caller, deps)
            if response:
                return response

        return shared._error(405, "METHOD_NOT_ALLOWED", "Unsupported admin ops route")
    except PermissionError as exc:
        return shared._error(403, "FORBIDDEN", str(exc))
    except ValueError as exc:
        return shared._error(400, "BAD_REQUEST", str(exc))
    except ClientError as exc:
        shared.logger.exception("AWS client error in admin ops handler")
        return shared._error(
            502, "AWS_CLIENT_ERROR", exc.response.get("Error", {}).get("Code", "Unknown")
        )
    except Exception:
        shared.logger.exception("Unhandled admin ops handler error")
        return shared._error(500, "INTERNAL_ERROR", "Internal server error")
