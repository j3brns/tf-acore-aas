from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

try:
    import agent_registry
    import handler as shared
except ImportError:  # pragma: no cover - local package import path
    from src.tenant_api import agent_registry
    from src.tenant_api import handler as shared


@shared.logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = shared._http_method(event)
    path = shared._request_path(event)

    try:
        if not path.startswith("/v1/platform/agents"):
            return shared._error(405, "METHOD_NOT_ALLOWED", "Unsupported agent registry route")
        deps = shared._dependencies()
        caller = shared._caller_identity(event)
        shared.logger.append_keys(
            appid=caller.app_id or "unknown",
            tenantid=caller.tenant_id or "unknown",
        )
        response = agent_registry.dispatch_routes(path, method, event, caller, deps)
        if response:
            return response
        return shared._error(405, "METHOD_NOT_ALLOWED", "Unsupported agent registry route")
    except PermissionError as exc:
        return shared._error(403, "FORBIDDEN", str(exc))
    except ValueError as exc:
        return shared._error(400, "BAD_REQUEST", str(exc))
    except ClientError as exc:
        shared.logger.exception("AWS client error in agent registry handler")
        return shared._error(
            502, "AWS_CLIENT_ERROR", exc.response.get("Error", {}).get("Code", "Unknown")
        )
    except Exception:
        shared.logger.exception("Unhandled agent registry handler error")
        return shared._error(500, "INTERNAL_ERROR", "Internal server error")
