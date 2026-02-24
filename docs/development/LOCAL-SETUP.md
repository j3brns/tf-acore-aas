# Local Development Setup

## Prerequisites

| Tool         | Version    | Install                                          |
|--------------|------------|--------------------------------------------------|
| uv           | latest     | curl -Ls https://astral.sh/uv/install.sh | sh    |
| Docker       | 24+        | https://docs.docker.com/get-docker/              |
| AWS CLI      | v2         | https://docs.aws.amazon.com/cli/latest/userguide |
| Node         | 20 LTS     | https://nodejs.org/ or nvm                       |
| Git          | 2.30+      | system package manager                           |

## .env.local Values

Copy `.env.example` to `.env.local` and fill in these values:

| Variable              | Where to Find It                                        |
|-----------------------|----------------------------------------------------------|
| VITE_ENTRA_CLIENT_ID  | Entra portal → App Registrations → platform-{env}       |
| VITE_ENTRA_TENANT_ID  | Entra portal → Overview → Directory (tenant) ID         |
| VITE_API_BASE_URL     | CDK outputs after infra-deploy, or team-platform Slack   |
| GITLAB_PROJECT_ID     | GitLab project settings → General → Project ID           |

For local development only (no real AWS needed):
```bash
VITE_API_BASE_URL=http://localhost:8080
MOCK_RUNTIME=true
```

## Starting the Local Environment

```bash
make dev
```

This starts:
- **LocalStack** on :4566 — mocks S3, DynamoDB, SSM, Secrets Manager, SQS
- **Mock AgentCore Runtime** on :8765 — returns canned streaming responses
- **Mock JWKS endpoint** on :8766 — issues test JWTs

Then seeds LocalStack with two test tenants and all SSM parameters.

## Verifying the Setup

```bash
make dev-invoke
# Expected: {"result": "Echo: Hello from local environment"}
```

If this works, your local environment is healthy.

## Test Tenants (seeded by dev-bootstrap.py)

After `make dev`, two test tenants are available. Their JWTs are in `.env.test`:

| Variable              | Tenant     | Tier     |
|-----------------------|------------|----------|
| BASIC_TENANT_JWT      | t-test-001 | basic    |
| PREMIUM_TENANT_JWT    | t-test-002 | premium  |
| ADMIN_JWT             | admin-001  | Platform.Admin |

Use these in `make agent-invoke --tenant t-test-001` or in your tests via conftest.py.

## Running Tests

```bash
make test-unit      # All Lambda unit tests against LocalStack
make test-int       # Integration tests (requires make dev running)
make agent-test AGENT=echo-agent    # Tests for a specific agent
```

## Common Issues

**LocalStack not starting**: ensure Docker is running (`docker ps` should work).

**make dev-invoke fails with 401**: LocalStack may not have finished seeding.
Wait 10 seconds and retry, or check `docker compose logs localstack`.

**uv: command not found**: run `source ~/.bashrc` or open a new terminal after install.

**CDK synth fails**: run `cd infra/cdk && npm install` first.
