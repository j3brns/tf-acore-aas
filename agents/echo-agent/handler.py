"""
echo-agent handler — Reference implementation for all three invocation modes.

Demonstrates the correct pattern for sync, streaming, and async agents.
Used for platform smoke tests and as the starting point for new agents.

See docs/development/AGENT-DEVELOPER-GUIDE.md for full documentation.

Implemented in TASK-020.
ADRs: ADR-005, ADR-008

Invocation modes (declared in pyproject.toml [tool.agentcore.invocation_mode]):
  sync      — handler returns a dict; bridge Lambda waits for the response.
  streaming — handler returns a generator; bridge Lambda relays SSE chunks.
  async     — handler returns immediately; background work tracked via
               app.add_async_task / app.complete_async_task; /ping returns
               HealthyBusy until complete_async_task() is called.

Request payload schema:
  {
    "prompt":   str   — text to echo (required)
    "mode":     str   — "sync" | "streaming" | "async" (default: "sync")
    "appid":    str   — caller app ID for logging (optional)
    "tenantId": str   — caller tenant ID for logging (optional)
  }
"""

import threading
import time
from collections.abc import Generator
from typing import Any

from aws_lambda_powertools import Logger
from bedrock_agentcore import BedrockAgentCoreApp, RequestContext

logger = Logger(service="echo-agent")

# ASGI application — referenced by pyproject.toml handler = "handler:invoke"
invoke = BedrockAgentCoreApp()


@invoke.entrypoint
def handler(payload: dict[str, Any], context: RequestContext) -> Any:
    """Dispatch to the correct invocation mode based on payload['mode'].

    Supported modes:
      sync (default) — echo the prompt as a JSON response
      streaming      — echo the prompt word-by-word as SSE chunks
      async          — echo in a background task; /ping returns HealthyBusy
    """
    mode = payload.get("mode", "sync")
    prompt = str(payload.get("prompt", ""))
    appid = str(payload.get("appid", "platform"))
    tenant_id = str(payload.get("tenantId", "unknown"))

    logger.append_keys(appid=appid, tenantid=tenant_id, mode=mode)
    logger.info("echo-agent invoked", extra={"prompt_len": len(prompt)})

    if mode == "streaming":
        return _stream_echo(prompt, appid, tenant_id)
    if mode == "async":
        return _async_echo(prompt, appid, tenant_id)
    return _sync_echo(prompt, appid, tenant_id)


# ---------------------------------------------------------------------------
# Sync mode
# ---------------------------------------------------------------------------


def _sync_echo(prompt: str, appid: str, tenant_id: str) -> dict[str, Any]:
    """Synchronous echo — returns the prompt as a JSON response immediately.

    Bridge Lambda invocation path: invoke and wait for response body.
    pyproject.toml: invocation_mode = "sync"
    """
    logger.info("sync echo complete", extra={"prompt_len": len(prompt)})
    return {
        "echo": prompt,
        "mode": "sync",
        "appid": appid,
        "tenantId": tenant_id,
    }


# ---------------------------------------------------------------------------
# Streaming mode
# ---------------------------------------------------------------------------


def _stream_echo(prompt: str, appid: str, tenant_id: str) -> Generator[dict[str, Any], None, None]:
    """Streaming echo — yields each word as an SSE chunk.

    Bridge Lambda invocation path: invoke with streaming; relay SSE chunks.
    pyproject.toml: invocation_mode = "streaming"

    The BedrockAgentCoreApp SDK converts each yielded dict to SSE format:
      data: {"chunk": "word", "index": 0, ...}

    Clients consume the stream and reassemble chunks into the full response.
    """
    words = prompt.split() if prompt.strip() else [prompt]
    for index, word in enumerate(words):
        logger.debug("streaming chunk", extra={"index": index})
        yield {
            "chunk": word,
            "index": index,
            "mode": "streaming",
            "appid": appid,
            "tenantId": tenant_id,
        }
    yield {
        "done": True,
        "total_chunks": len(words),
        "mode": "streaming",
        "appid": appid,
        "tenantId": tenant_id,
    }


# ---------------------------------------------------------------------------
# Async mode
# ---------------------------------------------------------------------------


def _async_echo(prompt: str, appid: str, tenant_id: str) -> dict[str, Any]:
    """Async echo — registers a background task; /ping returns HealthyBusy.

    Bridge Lambda invocation path: invoke; receive 202 Accepted immediately;
    client polls GET /v1/jobs/{jobId} or registers a webhook.

    Pattern (mandatory for async agents):
      1. Call app.add_async_task(name) — registers task, sets HealthyBusy
      2. Start a daemon thread to perform background work
      3. Return an acknowledgement dict immediately
      4. Background thread calls app.complete_async_task(task_id) when done
         — this resets /ping to Healthy so the session can be reclaimed.

    pyproject.toml: invocation_mode = "async"
    """
    task_id = invoke.add_async_task(
        "echo-background",
        metadata={"prompt_len": len(prompt)},
    )
    logger.info(
        "async task registered",
        extra={"task_id": str(task_id), "prompt_len": len(prompt)},
    )

    def _background() -> None:
        try:
            # Simulate proportional work (capped at 2 s for this reference agent).
            delay = min(0.05 * max(len(prompt.split()), 1), 2.0)
            time.sleep(delay)
            logger.info("async echo background complete", extra={"task_id": str(task_id)})
        finally:
            invoke.complete_async_task(task_id)

    thread = threading.Thread(target=_background, daemon=True, name=f"echo-async-{task_id}")
    thread.start()

    return {
        "accepted": True,
        "task_id": str(task_id),
        "echo": prompt,
        "mode": "async",
        "appid": appid,
        "tenantId": tenant_id,
    }
