from __future__ import annotations

from typing import Any

from .runtime_invoker import RuntimeInvoker


def build_runtime_orchestrator(
    *,
    get_config: Any,
    invoke_mock_runtime: Any,
    invoke_real_runtime: Any,
    is_runtime_unavailable_error: Any,
    trigger_failover: Any,
    runtime_failure_response: Any,
    log_warning: Any,
    log_exception: Any,
) -> RuntimeInvoker:
    return RuntimeInvoker(
        get_config=get_config,
        invoke_mock_runtime=invoke_mock_runtime,
        invoke_real_runtime=invoke_real_runtime,
        is_runtime_unavailable_error=is_runtime_unavailable_error,
        trigger_failover=trigger_failover,
        runtime_failure_response=runtime_failure_response,
        log_warning=log_warning,
        log_exception=log_exception,
    )
