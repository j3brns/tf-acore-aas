# =============================================================================
# PLATFORM MAKEFILE
# Run `make help` to see all targets
# =============================================================================

.PHONY: help bootstrap validate-local dev dev-stop dev-logs dev-invoke
.PHONY: test-unit test-int test-agent test-all
.PHONY: worktree-create worktree-list worktree-clean
.PHONY: infra-synth infra-diff infra-deploy infra-destroy
.PHONY: infra-rollback-lambda infra-set-runtime-region
.PHONY: failover-lock-acquire failover-lock-release
.PHONY: bootstrap-cdk bootstrap-secrets bootstrap-gitlab-oidc
.PHONY: bootstrap-post-deploy bootstrap-verify bootstrap-delete-iam-user
.PHONY: agent-push agent-invoke agent-logs agent-test agent-rollback
.PHONY: spa-dev spa-build spa-deploy
.PHONY: ops-login ops-top-tenants ops-tenant-sessions ops-suspend-tenant
.PHONY: ops-reinstate-tenant ops-quota-report ops-invocation-report
.PHONY: ops-security-events ops-dlq-inspect ops-dlq-redrive ops-error-rate
.PHONY: ops-notify-tenant ops-service-health ops-billing-status
.PHONY: ops-update-tenant-budget ops-fail-job ops-audit-export ops-page-security
.PHONY: logs-bridge logs-authoriser logs-tenant-api logs-async-runner logs-bff
.PHONY: plan-dev

ENV ?= dev

# Default target
all: help

## help: Print available targets
help:
	@echo "Platform Makefile"
	@echo ""
	@echo "Usage: make <target> [ENV=dev|staging|prod] [AGENT=name] [TENANT=id]"
	@echo ""
	@grep -E '^## ' Makefile | sed 's/^## /  /'

# =============================================================================
# BOOTSTRAP AND SETUP
# =============================================================================

## bootstrap: First-time setup — install tools, check prerequisites
bootstrap:
	@echo "==> Checking prerequisites"
	@command -v uv >/dev/null 2>&1 || (echo "ERROR: uv not installed. Run: curl -Ls https://astral.sh/uv/install.sh | sh" && exit 1)
	@command -v docker >/dev/null 2>&1 || (echo "ERROR: docker not installed" && exit 1)
	@command -v aws >/dev/null 2>&1 || (echo "ERROR: aws cli not installed" && exit 1)
	@command -v node >/dev/null 2>&1 || (echo "ERROR: node not installed" && exit 1)
	uv sync
	cd infra/cdk && npm install
	cd spa && npm install
	@[ -f .env.local ] || cp .env.example .env.local
	@echo "==> Bootstrap complete. Edit .env.local if needed, then run: make dev"

## bootstrap-cdk: CDK bootstrap all three regions (requires bootstrap IAM user)
bootstrap-cdk:
	@test -n "$(ENV)" || (echo "ERROR: ENV required" && exit 1)
	uv run python scripts/bootstrap.py --step cdk-bootstrap --env $(ENV)

## bootstrap-secrets: Seed initial secrets into Secrets Manager
bootstrap-secrets:
	@test -n "$(ENV)" || (echo "ERROR: ENV required" && exit 1)
	uv run python scripts/bootstrap.py --step seed-secrets --env $(ENV)

## bootstrap-gitlab-oidc: Create GitLab OIDC provider and pipeline roles
bootstrap-gitlab-oidc:
	@test -n "$(ENV)" || (echo "ERROR: ENV required" && exit 1)
	uv run python scripts/bootstrap.py --step gitlab-oidc --env $(ENV)
	@echo "==> MANUAL STEP: Add the printed role ARNs to GitLab CI/CD variables"

## bootstrap-post-deploy: Seed first admin, SSM params, register echo-agent
bootstrap-post-deploy:
	@test -n "$(ENV)" || (echo "ERROR: ENV required" && exit 1)
	uv run python scripts/bootstrap.py --step post-deploy --env $(ENV)

## bootstrap-verify: Smoke test the bootstrapped environment
bootstrap-verify:
	@test -n "$(ENV)" || (echo "ERROR: ENV required" && exit 1)
	uv run python scripts/bootstrap.py --step verify --env $(ENV)

## bootstrap-delete-iam-user: Delete the temporary bootstrap IAM user (MANDATORY)
bootstrap-delete-iam-user:
	@echo "WARNING: This deletes the bootstrap IAM user. Ensure you can access the platform via Entra first."
	@read -p "Type 'delete' to confirm: " confirm && [ "$$confirm" = "delete" ]
	uv run python scripts/bootstrap.py --step delete-bootstrap-user --env $(ENV)

# =============================================================================
# LOCAL DEVELOPMENT
# =============================================================================

## validate-local: Run all local validation checks before commit
validate-local:
	@echo "==> Running local validation"
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src/ gateway/ scripts/ --ignore-missing-imports
	cd infra/cdk && npx tsc --noEmit
	cd infra/cdk && npx cdk synth --context env=dev --quiet > /dev/null
	uv run detect-secrets scan --baseline .secrets.baseline
	@echo "==> Validation passed"

## dev: Start local development environment
dev:
	@echo "==> Starting local development environment"
	docker compose up -d
	@echo "==> Waiting for LocalStack to be ready..."
	@until aws --endpoint-url=http://localhost:4566 s3 ls >/dev/null 2>&1; do sleep 2; done
	uv run python scripts/dev-bootstrap.py
	@echo ""
	@echo "==> Local environment ready"
	@echo "    Try: make dev-invoke"

## dev-stop: Stop local development environment
dev-stop:
	docker compose down

## dev-logs: Stream logs from all local services
dev-logs:
	docker compose logs -f

## dev-invoke: Invoke echo agent locally with test tenant
dev-invoke:
	@TENANT=$$(grep BASIC_TENANT_ID .env.test 2>/dev/null | cut -d= -f2); \
	JWT=$$(grep BASIC_TENANT_JWT .env.test 2>/dev/null | cut -d= -f2); \
	uv run python scripts/dev-invoke.py \
		--agent echo-agent \
		--tenant "$$TENANT" \
		--jwt "$$JWT" \
		--prompt "$(or $(PROMPT),Hello from local environment)" \
		--mode "$(or $(MODE),sync)"

# =============================================================================
# TESTING
# =============================================================================

## test-unit: Run all unit tests against LocalStack
test-unit:
	uv run pytest tests/unit/ src/ -v --tb=short

## test-int: Run integration tests (requires make dev running)
test-int:
	uv run pytest tests/integration/ -v --tb=short

## test-agent: Run tests for a specific agent (AGENT required)
test-agent:
	@test -n "$(AGENT)" || (echo "ERROR: AGENT required. Usage: make test-agent AGENT=echo-agent" && exit 1)
	uv run pytest agents/$(AGENT)/tests/ -v --tb=short

## test-all: Run unit and integration tests
test-all: test-unit test-int

# =============================================================================
# GIT WORKTREES
# =============================================================================

## worktree-create: Create a worktree for parallel feature development
## Usage: make worktree-create NAME=feature-my-feature
worktree-create:
	@test -n "$(NAME)" || (echo "ERROR: NAME required. Usage: make worktree-create NAME=feature-xyz" && exit 1)
	git worktree add ../platform-$(NAME) -b $(NAME)
	@echo "==> Worktree created at ../platform-$(NAME)"
	@echo "    cd ../platform-$(NAME) && make bootstrap"

## worktree-list: List all active worktrees
worktree-list:
	git worktree list

## worktree-clean: Prune stale worktree references
worktree-clean:
	git worktree prune
	@echo "==> Stale worktree references pruned"

# =============================================================================
# INFRASTRUCTURE
# =============================================================================

## infra-synth: Synthesise all CDK stacks (validation only)
infra-synth:
	cd infra/cdk && npx cdk synth --context env=$(ENV)

## infra-diff: Show CDK diff before deployment
infra-diff:
	cd infra/cdk && npx cdk diff --context env=$(ENV)

## infra-deploy: Deploy all CDK stacks to an environment
infra-deploy:
	@test "$(ENV)" != "prod" || (echo "ERROR: Use GitLab pipeline for prod deploys" && exit 1)
	cd infra/cdk && npx cdk deploy --all --context env=$(ENV) --require-approval never

## infra-destroy: Destroy all CDK stacks (dev only)
infra-destroy:
	@test "$(ENV)" = "dev" || (echo "ERROR: infra-destroy only permitted in dev" && exit 1)
	@read -p "Type 'destroy-dev' to confirm: " confirm && [ "$$confirm" = "destroy-dev" ]
	cd infra/cdk && npx cdk destroy --all --context env=$(ENV)

## infra-rollback-lambda: Roll back a Lambda to previous alias version
## Usage: make infra-rollback-lambda FUNCTION=bridge ENV=prod
infra-rollback-lambda:
	@test -n "$(FUNCTION)" || (echo "ERROR: FUNCTION required" && exit 1)
	uv run python scripts/rollback_lambda.py $(FUNCTION) $(ENV)

## infra-set-runtime-region: Update active runtime region (use with failover lock)
## Usage: make infra-set-runtime-region REGION=eu-central-1 ENV=prod
infra-set-runtime-region:
	@test -n "$(REGION)" || (echo "ERROR: REGION required" && exit 1)
	aws ssm put-parameter \
		--name /platform/config/runtime-region \
		--value $(REGION) \
		--type String \
		--overwrite
	@echo "==> Runtime region set to $(REGION)"
	@echo "    Allow 90 seconds for all bridge Lambda instances to pick up the change"

## failover-lock-acquire: Acquire distributed lock before region failover
failover-lock-acquire:
	uv run python scripts/failover_lock.py acquire --env $(ENV)

## failover-lock-release: Release distributed lock after region failover
failover-lock-release:
	uv run python scripts/failover_lock.py release --env $(ENV)

# =============================================================================
# AGENT DEVELOPER COMMANDS
# =============================================================================

## agent-push: Package and deploy an agent
## Usage: make agent-push AGENT=my-agent [ENV=dev]
agent-push:
	@test -n "$(AGENT)" || (echo "ERROR: AGENT required. Usage: make agent-push AGENT=my-agent" && exit 1)
	@echo "==> Checking dependency hash for $(AGENT)"
	@if uv run python scripts/hash_layer.py $(AGENT) --env $(ENV); then \
		echo "==> Dependencies unchanged (fast path ~15s)"; \
	else \
		echo "==> Dependencies changed (cold path ~90s)"; \
		uv run python scripts/build_layer.py $(AGENT) --env $(ENV); \
	fi
	@echo "==> Packaging agent code"
	uv run python scripts/package_agent.py $(AGENT)
	@echo "==> Deploying to AgentCore Runtime"
	uv run python scripts/deploy_agent.py $(AGENT) --env $(ENV)
	@echo "==> Running agent tests"
	$(MAKE) test-agent AGENT=$(AGENT)
	@echo "==> Registering agent"
	uv run python scripts/register_agent.py $(AGENT) --env $(ENV)
	@echo "==> Agent $(AGENT) deployed successfully to $(ENV)"

## agent-invoke: Invoke a deployed agent
## Usage: make agent-invoke AGENT=my-agent TENANT=t-abc123 [PROMPT="hello"] [MODE=sync]
agent-invoke:
	@test -n "$(AGENT)" || (echo "ERROR: AGENT required" && exit 1)
	@test -n "$(TENANT)" || (echo "ERROR: TENANT required" && exit 1)
	uv run python scripts/dev-invoke.py \
		--agent $(AGENT) \
		--tenant $(TENANT) \
		--env $(ENV) \
		--prompt "$(or $(PROMPT),Hello)" \
		--mode "$(or $(MODE),sync)"

## agent-logs: Stream CloudWatch logs for an agent
## Usage: make agent-logs AGENT=my-agent [ENV=dev]
agent-logs:
	@test -n "$(AGENT)" || (echo "ERROR: AGENT required" && exit 1)
	aws logs tail /platform/agentcore/$(AGENT)/$(ENV) --follow

## agent-test: Run tests for a specific agent
agent-test: test-agent

## agent-rollback: Roll back agent to previous version
## Usage: make agent-rollback AGENT=my-agent [ENV=prod]
agent-rollback:
	@test -n "$(AGENT)" || (echo "ERROR: AGENT required" && exit 1)
	uv run python scripts/rollback_agent.py $(AGENT) --env $(ENV)

# =============================================================================
# SPA FRONTEND
# =============================================================================

## spa-dev: Start SPA development server against local mock API
spa-dev:
	cd spa && npm run dev

## spa-build: Build SPA for production
spa-build:
	cd spa && npm run build

## spa-deploy: Deploy SPA to S3 and invalidate CloudFront
## Usage: make spa-deploy ENV=staging
spa-deploy:
	@test -n "$(ENV)" || (echo "ERROR: ENV required" && exit 1)
	$(MAKE) spa-build
	uv run python scripts/deploy_frontend.py --env $(ENV)

# =============================================================================
# OPERATIONS
# =============================================================================

## ops-login: Authenticate as operator via Entra
ops-login:
	uv run python scripts/ops.py login --env $(ENV)

## ops-top-tenants: List top N tenants by token consumption
## Usage: make ops-top-tenants [ENV=prod] [N=10]
ops-top-tenants:
	uv run python scripts/ops.py top-tenants --env $(ENV) --n $(or $(N),10)

## ops-tenant-sessions: Show active sessions for a tenant
## Usage: make ops-tenant-sessions TENANT=t-abc123 [ENV=prod]
ops-tenant-sessions:
	@test -n "$(TENANT)" || (echo "ERROR: TENANT required" && exit 1)
	uv run python scripts/ops.py tenant-sessions --tenant $(TENANT) --env $(ENV)

## ops-suspend-tenant: Suspend a tenant immediately
## Usage: make ops-suspend-tenant TENANT=t-abc123 REASON="quota_protection" [ENV=prod]
ops-suspend-tenant:
	@test -n "$(TENANT)" || (echo "ERROR: TENANT required" && exit 1)
	@test -n "$(REASON)" || (echo "ERROR: REASON required" && exit 1)
	uv run python scripts/ops.py suspend-tenant --tenant $(TENANT) --reason "$(REASON)" --env $(ENV)

## ops-reinstate-tenant: Reinstate a suspended tenant
## Usage: make ops-reinstate-tenant TENANT=t-abc123 [ENV=prod]
ops-reinstate-tenant:
	@test -n "$(TENANT)" || (echo "ERROR: TENANT required" && exit 1)
	uv run python scripts/ops.py reinstate-tenant --tenant $(TENANT) --env $(ENV)

## ops-quota-report: Show AgentCore quota utilisation
ops-quota-report:
	uv run python scripts/ops.py quota-report --env $(ENV)

## ops-invocation-report: Show invocation report for a tenant
## Usage: make ops-invocation-report TENANT=t-abc123 [DAYS=7] [ENV=prod]
ops-invocation-report:
	@test -n "$(TENANT)" || (echo "ERROR: TENANT required" && exit 1)
	uv run python scripts/ops.py invocation-report --tenant $(TENANT) --days $(or $(DAYS),7) --env $(ENV)

## ops-security-events: Show tenant access violation events
## Usage: make ops-security-events [HOURS=24] [ENV=prod]
ops-security-events:
	uv run python scripts/ops.py security-events --env $(ENV) --hours $(or $(HOURS),24)

## ops-dlq-inspect: Inspect messages in a DLQ
## Usage: make ops-dlq-inspect QUEUE=platform-bridge-dlq-prod [ENV=prod]
ops-dlq-inspect:
	@test -n "$(QUEUE)" || (echo "ERROR: QUEUE required" && exit 1)
	uv run python scripts/ops.py dlq-inspect --queue $(QUEUE) --env $(ENV)

## ops-dlq-redrive: Redrive messages from DLQ back to main queue
## Usage: make ops-dlq-redrive QUEUE=platform-bridge-dlq-prod [ENV=prod]
ops-dlq-redrive:
	@test -n "$(QUEUE)" || (echo "ERROR: QUEUE required" && exit 1)
	uv run python scripts/ops.py dlq-redrive --queue $(QUEUE) --env $(ENV)

## ops-error-rate: Show error rate for last N minutes
## Usage: make ops-error-rate [MINUTES=5] [ENV=prod]
ops-error-rate:
	uv run python scripts/ops.py error-rate --env $(ENV) --minutes $(or $(MINUTES),5)

## ops-notify-tenant: Send notification to tenant owner
## Usage: make ops-notify-tenant TENANT=t-abc123 TEMPLATE=budget_warning [ENV=prod]
ops-notify-tenant:
	@test -n "$(TENANT)" || (echo "ERROR: TENANT required" && exit 1)
	@test -n "$(TEMPLATE)" || (echo "ERROR: TEMPLATE required" && exit 1)
	uv run python scripts/ops.py notify-tenant --tenant $(TENANT) --template $(TEMPLATE) --env $(ENV)

## ops-service-health: Check AWS service health for AgentCore regions
ops-service-health:
	uv run python scripts/ops.py service-health --env $(ENV)

## ops-billing-status: Check billing Lambda status and last run
ops-billing-status:
	uv run python scripts/ops.py billing-status --env $(ENV)

## ops-update-tenant-budget: Update a tenant's monthly budget
## Usage: make ops-update-tenant-budget TENANT=t-abc123 BUDGET=5000 [ENV=prod]
ops-update-tenant-budget:
	@test -n "$(TENANT)" || (echo "ERROR: TENANT required" && exit 1)
	@test -n "$(BUDGET)" || (echo "ERROR: BUDGET required" && exit 1)
	uv run python scripts/ops.py update-tenant-budget --tenant $(TENANT) --budget $(BUDGET) --env $(ENV)

## ops-fail-job: Manually mark an async job as failed
## Usage: make ops-fail-job JOB=job-uuid REASON="reason" [ENV=prod]
ops-fail-job:
	@test -n "$(JOB)" || (echo "ERROR: JOB required" && exit 1)
	@test -n "$(REASON)" || (echo "ERROR: REASON required" && exit 1)
	uv run python scripts/ops.py fail-job --job $(JOB) --reason "$(REASON)" --env $(ENV)

## ops-audit-export: Export audit trail for a tenant and time window
## Usage: make ops-audit-export TENANT=t-abc123 START=2026-01-01T00:00:00Z END=2026-01-02T00:00:00Z
ops-audit-export:
	@test -n "$(TENANT)" || (echo "ERROR: TENANT required" && exit 1)
	uv run python scripts/ops.py audit-export \
		--tenant $(TENANT) \
		--start "$(or $(START),)" \
		--end "$(or $(END),)" \
		--env $(ENV)

## ops-page-security: Page the security team
## Usage: make ops-page-security INCIDENT="tenant_access_violation" TENANT=t-abc123
ops-page-security:
	@test -n "$(INCIDENT)" || (echo "ERROR: INCIDENT required" && exit 1)
	uv run python scripts/ops.py page-security --incident "$(INCIDENT)" --tenant "$(or $(TENANT),unknown)" --env $(ENV)

# =============================================================================
# LOG STREAMING
# =============================================================================

## logs-bridge: Stream bridge Lambda logs
## Usage: make logs-bridge [ENV=prod] [MINUTES=30]
logs-bridge:
	aws logs tail /platform/bridge/$(ENV) --follow

## logs-authoriser: Stream authoriser Lambda logs
logs-authoriser:
	aws logs tail /platform/authoriser/$(ENV) --follow

## logs-tenant-api: Stream tenant API Lambda logs
logs-tenant-api:
	aws logs tail /platform/tenant-api/$(ENV) --follow

## logs-async-runner: Stream async runner Lambda logs
logs-async-runner:
	aws logs tail /platform/async-runner/$(ENV) --follow

## logs-bff: Stream BFF Lambda logs
logs-bff:
	aws logs tail /platform/bff/$(ENV) --follow

# =============================================================================
# PLAN DEV
# =============================================================================

## plan-dev: Generate a structured implementation plan for a task
## Usage: make plan-dev TASK="Implement the billing metering pipeline"
plan-dev:
	@test -n "$(TASK)" || (echo "ERROR: TASK required. Usage: make plan-dev TASK=\"describe your task\"" && exit 1)
	uv run python scripts/plan_dev.py "$(TASK)"
