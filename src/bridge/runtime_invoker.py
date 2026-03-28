from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from data_access.models import AgentRecord, TenantContext


class RuntimeInvoker:
    """Own runtime selection and failover policy for bridge agent invocation."""

    def __init__(
        self,
        *,
        get_config: Callable[..., dict[str, Any]],
        invoke_mock_runtime: Callable[..., Any],
        invoke_real_runtime: Callable[..., Any],
        is_runtime_unavailable_error: Callable[[Exception], bool],
        trigger_failover: Callable[[str], str],
        runtime_failure_response: Callable[..., dict[str, Any]],
        log_warning: Callable[[str], None] | None = None,
        log_exception: Callable[[str], None] | None = None,
    ) -> None:
        self._get_config = get_config
        self._invoke_mock_runtime = invoke_mock_runtime
        self._invoke_real_runtime = invoke_real_runtime
        self._is_runtime_unavailable_error = is_runtime_unavailable_error
        self._trigger_failover = trigger_failover
        self._runtime_failure_response = runtime_failure_response
        self._log_warning = log_warning
        self._log_exception = log_exception

    def invoke(
        self,
        *,
        agent: AgentRecord,
        tenant_context: TenantContext,
        prompt: str,
        session_id: str | None,
        webhook_id: str | None,
        request_id: str,
        response_stream: Any,
    ) -> Any:
        config = self._get_config()
        mock_url = config.get("mock_runtime_url")
        invocation_id = str(uuid.uuid4())
        start_time = time.time()

        try:
            return self._dispatch(
                region=str(config["runtime_region"]),
                mock_url=mock_url,
                agent=agent,
                tenant_context=tenant_context,
                prompt=prompt,
                session_id=session_id,
                webhook_id=webhook_id,
                request_id=request_id,
                response_stream=response_stream,
                invocation_id=invocation_id,
                start_time=start_time,
            )
        except Exception as exc:
            if self._is_runtime_unavailable_error(exc):
                if self._log_warning is not None:
                    self._log_warning("Runtime unavailable, attempting failover")
                new_region = self._trigger_failover(str(config["runtime_region"]))
                retry_config = self._get_config(force_refresh=True)
                retry_mock_url = retry_config.get("mock_runtime_url")
                try:
                    return self._dispatch(
                        region=new_region,
                        mock_url=retry_mock_url,
                        agent=agent,
                        tenant_context=tenant_context,
                        prompt=prompt,
                        session_id=session_id,
                        webhook_id=webhook_id,
                        request_id=request_id,
                        response_stream=response_stream,
                        invocation_id=invocation_id,
                        start_time=start_time,
                    )
                except Exception as retry_exc:
                    failure = self._runtime_failure_response(
                        tenant_context,
                        agent,
                        invocation_id,
                        start_time,
                        agent.invocation_mode,
                        new_region,
                        request_id,
                        retry_exc,
                        session_id=session_id,
                    )
                    if self._log_exception is not None:
                        self._log_exception("Invocation failed after failover retry")
                    return failure
            if self._log_exception is not None:
                self._log_exception("Invocation failed")
            return self._runtime_failure_response(
                tenant_context,
                agent,
                invocation_id,
                start_time,
                agent.invocation_mode,
                str(config["runtime_region"]),
                request_id,
                exc,
                session_id=session_id,
            )

    def _dispatch(
        self,
        *,
        region: str,
        mock_url: str | None,
        agent: AgentRecord,
        tenant_context: TenantContext,
        prompt: str,
        session_id: str | None,
        webhook_id: str | None,
        request_id: str,
        response_stream: Any,
        invocation_id: str,
        start_time: float,
    ) -> Any:
        if mock_url:
            return self._invoke_mock_runtime(
                mock_url,
                agent,
                tenant_context,
                prompt,
                session_id,
                webhook_id,
                request_id,
                response_stream,
                invocation_id,
                start_time,
            )
        return self._invoke_real_runtime(
            region,
            agent,
            tenant_context,
            prompt,
            session_id,
            webhook_id,
            request_id,
            response_stream,
            invocation_id,
            start_time,
        )
