# Agent Developer Guide

## What You Are Building

An agent is a Python module that processes a prompt using an LLM (Claude via Bedrock)
and optional tools, then returns a result. AgentCore Runtime hosts the agent in an
isolated arm64 Firecracker microVM. The platform handles auth, routing, memory,
tool access, and observability. You write business logic — not infrastructure.

## Quick Start

```bash
# Copy the reference implementation
cp -r agents/echo-agent agents/my-agent

# Edit the manifest
vim agents/my-agent/pyproject.toml

# Develop locally
make dev                                    # Start local environment
make agent-push AGENT=my-agent ENV=dev      # Deploy to local mock Runtime
make agent-invoke AGENT=my-agent PROMPT="hello"

# Run tests
make agent-test AGENT=my-agent
```

## Project Structure (per agent)

```
agents/my-agent/
├── pyproject.toml          # Dependencies + agent manifest [tool.agentcore]
├── uv.lock                 # Locked dependencies (commit this)
├── handler.py              # Entry point with @app.entrypoint
├── tools/                  # Tool implementations
│   └── my_tool.py
└── tests/
    ├── test_handler.py     # Unit tests with mocked AgentCore
    └── golden/
        └── invoke_cases.json   # Golden test dataset (3+ cases per mode)
```

## The Agent Manifest (pyproject.toml)

```toml
[project]
name = "my-agent"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = [
    "bedrock-agentcore>=0.1.4",
    "strands-agents>=0.4.1",
    "boto3>=1.37.0",
    "aws-lambda-powertools>=3.3.0",
]

[dependency-groups]
dev = [
    "bedrock-agentcore-starter-toolkit>=0.2.5",
    "pytest>=8.0.0",
]

[tool.agentcore]
name = "my-agent"
owner_team = "team-commerce"
tier_minimum = "standard"      # basic | standard | premium | enterprise
handler = "handler:invoke"
invocation_mode = "sync"       # sync | streaming | async
estimated_duration_seconds = 30

[tool.agentcore.llm]
model_id = "anthropic.claude-sonnet-4-6"
max_tokens = 4096

[tool.agentcore.deployment]
type = "zip"                   # zip (default) | container
```

## Invocation Modes

### sync — up to 15 minutes, client waits for full response
Use for: interactive queries, tool lookups, classification

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent

app = BedrockAgentCoreApp()
agent = Agent()

@app.entrypoint
def invoke(payload):
    result = agent(payload.get("prompt"))
    return {"result": result.message}

if __name__ == "__main__":
    app.run()
```

### streaming — up to 15 minutes, chunks arrive as generated
Use for: chat interfaces, narrated reasoning

```python
@app.entrypoint
async def invoke(payload):
    async for event in agent.stream_async(payload.get("prompt")):
        yield event
```

### async — up to 8 hours, 202 returned immediately
Use for: research agents, batch processing, multi-step workflows

```python
import threading
from bedrock_agentcore.runtime import BedrockAgentCoreApp, PingStatus

app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload):
    task_id = app.add_async_task("research_task", {"prompt": payload.get("prompt")})

    def background_work():
        # Long-running work here — can take up to 8 hours
        result = do_long_research(payload.get("prompt"))
        # Write result to S3 for the platform to retrieve
        store_result(result, payload.get("job_id"))
        app.complete_async_task(task_id)

    threading.Thread(target=background_work, daemon=True).start()
    return {"status": "accepted", "task_id": task_id}

# Optional: customise ping behaviour
@app.ping
def health_check():
    if any_background_tasks_running():
        return PingStatus.HEALTHY_BUSY
    return PingStatus.HEALTHY

if __name__ == "__main__":
    app.run()
```

**Important for async agents**:
- The session stays alive as long as /ping returns HealthyBusy
- After app.complete_async_task, /ping returns Healthy
- The session will be destroyed 15 minutes after returning Healthy
- Result must be written to S3 — the platform polls for it

## Dependency Management

Dependencies are cross-compiled for arm64 (AgentCore Runtime requirement).
The platform hashes [project.dependencies] to detect changes:

- **Warm push** (deps unchanged): <30 seconds — zip code only
- **Cold push** (deps changed): <2 minutes — uv cross-compiles arm64 deps

To add a dependency:
```bash
uv add some-package                # Adds to [project.dependencies], updates uv.lock
make agent-push AGENT=my-agent    # Detects hash change, triggers cold push automatically
```

**Constraints**:
- Use `--only-binary=:all:` (enforced by build script) — source-only packages may not compile for arm64
- If a package fails arm64 cross-compilation, use `deployment.type = "container"` instead
- Total deployment package must remain under 250MB (AgentCore limit)

## Writing Tests

### Unit tests (pytest)
```python
from unittest.mock import patch, MagicMock

def test_invoke_returns_result():
    with patch("handler.agent") as mock_agent:
        mock_agent.return_value.message = "Hello back"
        from handler import invoke
        result = invoke({"prompt": "Hello"})
        assert result["result"] == "Hello back"
```

### Golden tests
Create tests/golden/invoke_cases.json with at least 3 test cases:
```json
[
  {
    "name": "basic_greeting",
    "input": {"prompt": "Hello"},
    "expected_contains": ["hello", "help"],
    "mode": "sync"
  }
]
```

Run: `make agent-test AGENT=my-agent` — executes unit tests + golden tests.

## Tool Access via Gateway

Tools are registered in AgentCore Gateway by the platform team. Your agent accesses
them via the Strands tools() decorator or MCP client — the interceptors handle
auth and tier filtering transparently.

```python
from strands import Agent
from bedrock_agentcore.gateway import get_gateway_tools

# Tools are automatically filtered by your agent's tier_minimum
tools = get_gateway_tools()
agent = Agent(tools=tools)
```

If a tool requires a higher tier than your agent's tier_minimum, the REQUEST interceptor
returns 403 before the tool Lambda is invoked. The agent sees a tool error, not a security error.

## Pipeline Promotion

Pushing to a feature branch triggers: validate → test → push-dev (auto).
Merge to main triggers: promote-staging (manual gate, requires evaluation score).
Staging → prod: two-reviewer approval in GitLab.

The evaluation gate (promote-staging) runs your golden test cases against the real
AgentCore Evaluations service in Frankfurt. Your agent will not promote if the
evaluation score is below the threshold in [tool.agentcore.evaluations].

## What You Cannot Do

- Access another tenant's data (the platform will raise TenantAccessViolation)
- Invoke tools above your tier_minimum (Gateway interceptor returns 403)
- Write to S3 outside /tenants/{your-tenantId}/ prefix
- Call external services not registered as Gateway tools (egress controlled by VPC)
- Store secrets in agent code or pyproject.toml
