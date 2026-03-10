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
    "prompt":      str        — text to echo (required)
    "mode":        str        — "sync" | "streaming" | "async" (default: "sync")
    "appid":       str        — caller app ID for logging (optional)
    "tenantId":    str        — caller tenant ID for logging (optional)
    "a2aTargets":  list[str]  — optional downstream A2A targets for sync orchestration
  }
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Generator
from typing import Any

from aws_lambda_powertools import Logger
from bedrock_agentcore import BedrockAgentCoreApp, RequestContext

logger = Logger(service="echo-agent")

# ASGI application — referenced by pyproject.toml handler = "handler:invoke"
invoke = BedrockAgentCoreApp()

A2A_ENDPOINTS_ENV = "A2A_AGENT_ENDPOINTS_JSON"
A2A_HTTP_TIMEOUT_SECONDS = float(os.environ.get("A2A_HTTP_TIMEOUT_SECONDS", "8.0"))


@invoke.entrypoint
def handler(payload: dict[str, Any], context: RequestContext) -> Any:
    """Dispatch to the correct invocation mode based on payload['mode'].

    Supported modes:
      sync (default) — echo the prompt as a JSON response
      streaming      — echo the prompt word-by-word as SSE chunks
      async          — echo in a background task; /ping returns HealthyBusy
    """
    mode = str(payload.get("mode", "sync")).strip().lower() or "sync"
    prompt = str(payload.get("prompt", ""))
    appid = str(payload.get("appid", "platform"))
    tenant_id = str(payload.get("tenantId", "unknown"))
    a2a_targets = _parse_a2a_targets(payload.get("a2aTargets"))

    logger.append_keys(appid=appid, tenantid=tenant_id, mode=mode)
    logger.info(
        "echo-agent invoked",
        extra={"prompt_len": len(prompt), "a2a_target_count": len(a2a_targets)},
    )

    if a2a_targets and mode != "sync":
        return {
            "error": "A2A orchestration supports sync mode only",
            "code": "INVALID_A2A_CONFIGURATION",
            "mode": mode,
            "appid": appid,
            "tenantId": tenant_id,
        }

    if mode == "streaming":
        return _stream_echo(prompt, appid, tenant_id)
    if mode == "async":
        return _async_echo(prompt, appid, tenant_id)
    return _sync_echo(prompt, appid, tenant_id, a2a_targets=a2a_targets)


# ---------------------------------------------------------------------------
# Sync mode
# ---------------------------------------------------------------------------


def _sync_echo(
    prompt: str,
    appid: str,
    tenant_id: str,
    a2a_targets: list[str] | None = None,
) -> dict[str, Any]:
    """Synchronous echo — returns the prompt as a JSON response immediately.

    Bridge Lambda invocation path: invoke and wait for response body.
    pyproject.toml: invocation_mode = "sync"
    """
    if a2a_targets:
        try:
            return _sync_a2a_orchestration(prompt, appid, tenant_id, a2a_targets)
        except Exception as exc:
            logger.exception("a2a orchestration failed", extra={"target_count": len(a2a_targets)})
            return {
                "error": "A2A orchestration failed",
                "code": "A2A_ORCHESTRATION_FAILED",
                "details": str(exc),
                "mode": "sync",
                "appid": appid,
                "tenantId": tenant_id,
            }

    logger.info("sync echo complete", extra={"prompt_len": len(prompt)})
    return {
        "echo": prompt,
        "mode": "sync",
        "appid": appid,
        "tenantId": tenant_id,
    }


def _parse_a2a_targets(value: Any) -> list[str]:
    """Parse and normalize optional A2A target list from payload."""
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        target = str(item).strip()
        if not target or target in seen:
            continue
        seen.add(target)
        normalized.append(target)
    return normalized


def _sync_a2a_orchestration(
    prompt: str,
    appid: str,
    tenant_id: str,
    a2a_targets: list[str],
) -> dict[str, Any]:
    """Execute sync cross-agent orchestration using A2A task calls."""
    endpoint_map = _load_a2a_endpoint_map()
    delegate_results: list[dict[str, str]] = []

    for target in a2a_targets:
        endpoint = endpoint_map.get(target)
        if endpoint is None:
            raise ValueError(f"Missing A2A endpoint for target '{target}'")

        agent_card = _fetch_agent_card(endpoint)
        card_name = str(agent_card.get("name", target))
        output = _invoke_a2a_target(endpoint, prompt)
        delegate_results.append(
            {
                "target": target,
                "agentCardName": card_name,
                "output": output,
            }
        )

    # Deterministic aggregate output for contract-style tests and simple clients.
    aggregate_output = " | ".join(f"{item['target']}={item['output']}" for item in delegate_results)
    logger.info(
        "sync a2a orchestration complete",
        extra={"target_count": len(delegate_results)},
    )
    return {
        "echo": prompt,
        "mode": "sync",
        "appid": appid,
        "tenantId": tenant_id,
        "orchestration": "a2a",
        "delegates": delegate_results,
        "output": aggregate_output,
    }


def _load_a2a_endpoint_map() -> dict[str, str]:
    """Load A2A endpoint mapping from environment.

    Expected env var:
      A2A_AGENT_ENDPOINTS_JSON='{"planner":"https://planner.example","retriever":"https://..."}'
    """
    raw_value = os.environ.get(A2A_ENDPOINTS_ENV, "").strip()
    if not raw_value:
        return {}

    try:
        decoded = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{A2A_ENDPOINTS_ENV} must be valid JSON") from exc

    if not isinstance(decoded, dict):
        raise ValueError(f"{A2A_ENDPOINTS_ENV} must be a JSON object")

    endpoint_map: dict[str, str] = {}
    for key, value in decoded.items():
        target = str(key).strip()
        endpoint = str(value).strip().rstrip("/")
        if not target or not endpoint:
            continue
        endpoint_map[target] = endpoint
    return endpoint_map


def _fetch_agent_card(endpoint: str) -> dict[str, Any]:
    card_url = f"{endpoint}/.well-known/agent-card.json"
    return _http_json_request(card_url, method="GET")


def _invoke_a2a_target(endpoint: str, prompt: str) -> str:
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tasks/send",
        "params": {
            "id": str(uuid.uuid4()),
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": prompt}],
            },
        },
    }
    rpc_response = _http_json_request(f"{endpoint}/", method="POST", payload=rpc_payload)
    return _extract_a2a_output_text(rpc_response)


def _http_json_request(
    url: str, method: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url=url, data=data, method=method.upper())
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=A2A_HTTP_TIMEOUT_SECONDS) as response:
            body_bytes = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc

    body_text = body_bytes.decode("utf-8") if body_bytes else "{}"
    try:
        decoded = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Response from {url} is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"Response from {url} must be a JSON object")
    return decoded


def _extract_a2a_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("error"), dict):
        message = str(response["error"].get("message", "Unknown A2A error"))
        raise RuntimeError(message)

    result = response.get("result")
    if not isinstance(result, dict):
        return json.dumps(response, sort_keys=True)

    artifacts = result.get("artifacts")
    if isinstance(artifacts, list):
        text_parts: list[str] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            parts = artifact.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if text is not None:
                    text_parts.append(str(text))
        if text_parts:
            return " ".join(text_parts)

    message = result.get("message")
    if isinstance(message, dict):
        parts = message.get("parts")
        if isinstance(parts, list):
            text_parts = [
                str(part.get("text"))
                for part in parts
                if isinstance(part, dict) and part.get("text") is not None
            ]
            if text_parts:
                return " ".join(text_parts)

    output = result.get("output")
    if output is not None:
        return str(output)
    return json.dumps(result, sort_keys=True)


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
