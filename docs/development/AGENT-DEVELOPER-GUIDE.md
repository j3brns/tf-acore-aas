# Agent Developer Guide

## What You Are Building

An agent is a Python module that processes a prompt using an LLM (Claude via Bedrock)
and optional tools, then returns a result. AgentCore Runtime hosts the agent in an
isolated arm64 Firecracker microVM. The platform handles auth, routing, memory,
tool access, and observability. You write business logic — not infrastructure.

## Quick Start

```bash
# 1. Copy the reference implementation
cp -r agents/echo-agent agents/my-agent

# 2. Edit the manifest (name, owner_team, invocation_mode)
vim agents/my-agent/pyproject.toml

# 3. Develop logic locally (fast, no AWS required)
make test-agent AGENT=my-agent               # Run unit + golden tests

# 4. (Optional) Test platform integration locally (requires Docker)
make dev                                    # Start local environment (LocalStack + mocks)
make agent-invoke AGENT=my-agent ENV=local  # Invoke via local bridge (canned mock response)

# 5. Validate locally, then push to AWS dev (real compute)
make agent-push AGENT=my-agent ENV=dev      # Package, run agent tests, deploy to Runtime, and register
make agent-invoke AGENT=my-agent ENV=dev    # Invoke your agent on real AWS
```

## Local vs. AWS Development

| Phase | Tooling | Environment | Purpose |
|-------|---------|-------------|---------|
| **Logic** | `pytest` | Local | Rapidly iterate on prompt engineering, tools, and business logic. |
| **Integration** | `make dev` | Local (Docker) | Verify that headers, auth, and platform-level routing are correct. |
| **Validation** | `make agent-push` | AWS (dev) | Final end-to-end verification on real AgentCore Runtime compute. |

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
tier_minimum = "standard"      # basic | standard | premium
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

Modes are declared in `pyproject.toml` and enforced by the platform Bridge.

### sync — up to 15 minutes, client waits for full response
Use for: interactive queries, tool lookups, classification

```python
from bedrock_agentcore import BedrockAgentCoreApp, RequestContext
from strands import Agent

app = BedrockAgentCoreApp()
agent = Agent()

@app.entrypoint
def invoke(payload: dict, context: RequestContext):
    # Payload contains prompt, appid, tenantId
    prompt = payload.get("prompt")
    result = agent(prompt)
    return {"result": result.message}
```

### streaming — up to 15 minutes, chunks arrive as generated
Use for: chat interfaces, narrated reasoning

```python
@app.entrypoint
def invoke(payload: dict, context: RequestContext):
    prompt = payload.get("prompt")
    # Yield individual chunk dicts
    for event in agent.stream(prompt):
        yield {"chunk": event.text, "index": event.index}
    # Optional: final sentinel
    yield {"done": True}
```

### async — up to 8 hours, 202 returned immediately
Use for: research agents, batch processing, multi-step workflows

```python
import threading
from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload: dict, context: RequestContext):
    # 1. Register background task (sets /ping to HealthyBusy)
    task_id = app.add_async_task("my-task", {"prompt": payload.get("prompt")})

    def background_work():
        try:
            # 2. Perform long-running work
            do_long_research(payload.get("prompt"))
        finally:
            # 3. Complete task (resets /ping to Healthy)
            app.complete_async_task(task_id)

    # 4. Start background thread and return acknowledgment immediately
    threading.Thread(target=background_work, daemon=True).start()
    return {"accepted": True, "task_id": str(task_id)}
```

**Important for async agents**:
- The session stays alive as long as `/ping` returns `HealthyBusy`.
- After `app.complete_async_task`, `/ping` returns `Healthy`.
- The session will be destroyed 15 minutes after returning `Healthy` (idle timeout).

## Dependency Management

Dependencies are cross-compiled for arm64 (AgentCore Runtime requirement).
The platform hashes `[project.dependencies]` **and** `uv.lock` to detect changes:

- **Warm push** (deps and lockfile unchanged): <30 seconds — zip code only.
- **Cold push** (deps or lockfile changed): <2 minutes — `uv` cross-compiles arm64 deps.

To add a dependency:
```bash
uv add some-package                # Adds to [project.dependencies], updates uv.lock
make agent-push AGENT=my-agent    # Detects hash change, triggers cold push automatically
```

**Constraints**:
- Use `--only-binary=:all:` (enforced by build script) — source-only packages may not compile for arm64.
- If a package fails arm64 cross-compilation, use `deployment.type = "container"` instead.
- Total deployment package must remain under 250MB (AgentCore limit).

## Writing Tests

### Unit tests (pytest)
Test your logic by calling your handler functions directly with mocked inputs.

```python
from unittest.mock import patch, MagicMock
from handler import invoke

def test_invoke_returns_result():
    payload = {"prompt": "Hello"}
    context = MagicMock() # Mock RequestContext
    
    with patch("handler.agent") as mock_agent:
        mock_agent.return_value.message = "Hello back"
        result = invoke(payload, context)
        assert result["result"] == "Hello back"
```

### Golden tests
Create `tests/golden/invoke_cases.json` with at least 3 test cases per mode.
This dataset is used by the evaluation gate during pipeline promotion.

```json
{
  "sync": [
    {
      "id": "basic-greeting",
      "input": {"prompt": "Hello", "tenantId": "t-test-001"},
      "expected": {"result": "Hello back"}
    }
  ]
}
```

Run: `make agent-test AGENT=my-agent` — executes unit tests + verifies golden schemas.

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

Pushing any branch triggers validate and test.
Merge requests also run the plan stage.
Merge to `main` triggers `deploy-dev` automatically.
`deploy-staging` is a manual gate on `main` and keeps the evaluation score check.
`deploy-prod` is a manual gate on `main` and requires two-reviewer approval in GitLab.

Production deploys fail closed unless the GitLab project protects the `prod`
environment and requires at least two approvals. CI verifies that state by
calling the Protected Environments API before any prod deploy step runs.

Required GitLab project setup:
- Protect environment `prod`.
- Require at least 2 approvals on that protected environment.
- Add protected masked CI/CD variable `GITLAB_PROTECTED_ENV_API_TOKEN` with `read_api` scope.

Operator verification command:
```bash
curl --silent --header "PRIVATE-TOKEN: $GITLAB_PROTECTED_ENV_API_TOKEN" \
  "$CI_API_V4_URL/projects/$CI_PROJECT_ID/protected_environments/prod" | \
  jq '{name, required_approval_count, approval_rules}'
```

Failure mode when misconfigured:
- `deploy-prod` exits before `make infra-deploy-prod-ci` if the token is missing,
  the `prod` environment is not protected, or the API reports fewer than 2 approvals.

The evaluation gate (promote-staging) runs your golden test cases against the real
AgentCore Evaluations service in Frankfurt. Your agent will not promote if the
evaluation score is below the threshold in [tool.agentcore.evaluations].

## What You Cannot Do

- Access another tenant's data (the platform will raise TenantAccessViolation)
- Invoke tools above your tier_minimum (Gateway interceptor returns 403)
- Write to S3 outside /tenants/{your-tenantId}/ prefix
- Call external services not registered as Gateway tools (egress controlled by VPC)
- Store secrets in agent code or pyproject.toml
