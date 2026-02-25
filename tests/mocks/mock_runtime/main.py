"""Mock AgentCore Runtime — FastAPI on :8765.

Endpoints:
    GET  /ping         Health check, returns {"status": "Healthy"}
    POST /invocations  Returns a canned SSE streaming response.
                       Logs tenant context headers from the Bridge Lambda.
"""

import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="mock-agentcore-runtime")

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mock-runtime")

# Headers injected by Bridge Lambda carrying tenant context
_TENANT_HEADERS = [
    "x-tenant-id",
    "x-app-id",
    "x-tier",
    "x-session-id",
    "x-invocation-id",
    "x-agent-name",
]

# Canned streaming chunks returned for every /invocations request
_CANNED_CHUNKS = [
    "Hello from mock AgentCore Runtime.",
    " This is a canned streaming response.",
    " Tenant context has been logged.",
]


@app.get("/ping")
def ping() -> dict[str, str]:
    """AgentCore Runtime health check."""
    return {"status": "Healthy"}


@app.post("/invocations")
async def invocations(request: Request) -> StreamingResponse:
    """Return a canned SSE streaming response and log tenant context headers."""
    tenant_context: dict[str, str] = {}
    for header in _TENANT_HEADERS:
        value = request.headers.get(header)
        if value:
            tenant_context[header] = value

    body = await request.body()
    logger.info(
        "Invocation received | tenant_context=%s body_bytes=%d",
        json.dumps(tenant_context),
        len(body),
    )

    async def _stream():
        for chunk in _CANNED_CHUNKS:
            event = json.dumps({"type": "text", "content": chunk})
            yield f"data: {event}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
