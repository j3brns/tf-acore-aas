# =============================================================================
# PLATFORM MAKEFILE
# Run `make help` to see all targets
# =============================================================================

.PHONY: help bootstrap ensure-tools validate-local validate-local-full
.PHONY: validate-local-prereqs validate-python validate-cdk validate-cdk-ts validate-cdk-ts-push validate-cdk-synth
.PHONY: validate-pre-push validate-secrets-diff validate-secrets-push validate-secrets-full
.PHONY: docs-sync-audit docs-sync-stamp
.PHONY: dev dev-stop dev-logs dev-invoke
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
.PHONY: task-next task-list task-start task-resume task-finish task-prompt
.PHONY: worktree issue-queue worktree-next-issue worktree-create-issue worktree-resume-issue
.PHONY: preflight-session pre-validate-session worktree-push-issue finish-worktree-summary finish-worktree-close
.PHONY: issues-audit issues-reconcile agent-handoff install-git-hooks hooks-status

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

## ensure-tools: Install missing dev tools (idempotent — safe to run repeatedly)
ensure-tools:
	@bash scripts/install-dev-tools.sh

## validate-local: Run local validation checks before commit (fast path)
## Uses diff-only secret detection for speed. Run `make validate-local-full` for full repo secret scan.
validate-local: validate-local-prereqs
	@echo "==> Running local validation (fast)"
	@$(MAKE) --no-print-directory validate-python
	@$(MAKE) --no-print-directory validate-cdk
	@$(MAKE) --no-print-directory validate-secrets-diff
	@echo "==> Validation passed"

## validate-local-full: Full local validation including full-repo secret scan
validate-local-full: validate-local-prereqs
	@echo "==> Running local validation (full)"
	@$(MAKE) --no-print-directory validate-python
	@$(MAKE) --no-print-directory validate-cdk
	@$(MAKE) --no-print-directory validate-secrets-full
	@echo "==> Validation passed"

## docs-sync-audit: Check docs/code semver sync and drift heuristics
## Usage: make docs-sync-audit [JSON=1]
docs-sync-audit:
	uv run python scripts/docs_sync_audit.py check \
		$(if $(JSON),--json,)

## docs-sync-stamp: Refresh docs/DOCS_SYNC.json to current semver + commit
docs-sync-stamp:
	uv run python scripts/docs_sync_audit.py stamp

## validate-pre-push: Pre-push validation (skips cdk synth; repo should already synth clean)
validate-pre-push: validate-local-prereqs
	@echo "==> Running pre-push validation (no cdk synth)"
	@$(MAKE) --no-print-directory validate-python
	@$(MAKE) --no-print-directory validate-cdk-ts-push
	@$(MAKE) --no-print-directory validate-secrets-push
	@echo "==> Pre-push validation passed"

## validate-local-prereqs: Minimal local tool checks (no auto-install)
validate-local-prereqs:
	@command -v uv >/dev/null 2>&1 || (echo "ERROR: uv not found. Run: make ensure-tools" && exit 1)
	@command -v node >/dev/null 2>&1 || (echo "ERROR: node not found. Run: make ensure-tools" && exit 1)
	@cd infra/cdk && npx --no-install pyright --version >/dev/null 2>&1 || \
		(echo "ERROR: pyright not installed in infra/cdk. Run: make ensure-tools" && exit 1)

## validate-python: Python lint/format/type checks
validate-python:
	uv run ruff check .
	uv run ruff format --check .
	cd infra/cdk && npx --no-install pyright --project ../../pyrightconfig.json

## validate-cdk: TypeScript compile and CDK synth
validate-cdk:
	@$(MAKE) --no-print-directory validate-cdk-ts
	@$(MAKE) --no-print-directory validate-cdk-synth

## validate-cdk-ts: TypeScript compile only (no synth)
validate-cdk-ts:
	cd infra/cdk && npx --no-install tsc --noEmit

## validate-cdk-ts-push: Run CDK TypeScript compile only when CDK paths changed in commits-to-push
validate-cdk-ts-push:
	@files="$$( \
		upstream="$$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"; \
		if [ -n "$$upstream" ]; then \
			git diff --name-only --diff-filter=ACMR "$$upstream...HEAD"; \
		elif git show-ref --verify --quiet refs/remotes/origin/main; then \
			git diff --name-only --diff-filter=ACMR origin/main...HEAD; \
		elif git rev-parse --verify --quiet HEAD~1 >/dev/null; then \
			git diff --name-only --diff-filter=ACMR HEAD~1...HEAD; \
		else \
			git diff --name-only --diff-filter=ACMR; \
		fi \
	)"; \
	if ! printf '%s\n' "$$files" | grep -Eq '^(infra/cdk/|pyrightconfig\.json$$|tsconfig\.json$$|package\.json$$|package-lock\.json$$|pnpm-lock\.yaml$$|yarn\.lock$$)'; then \
		echo "==> validate-cdk-ts: skipped (no CDK/TS files in commits-to-push)"; \
		exit 0; \
	fi; \
	echo "==> validate-cdk-ts: running (CDK/TS files changed)"; \
	$(MAKE) --no-print-directory validate-cdk-ts

## validate-cdk-synth: CDK synth only
validate-cdk-synth:
	cd infra/cdk && npx --no-install cdk synth --context env=dev --quiet > /dev/null

## validate-secrets-diff: detect-secrets on changed files only (staged, unstaged, untracked)
validate-secrets-diff:
	@echo "==> detect-secrets (changed files only)"
	@files="$$( \
		{ \
			git diff --name-only --diff-filter=ACMR; \
			git diff --cached --name-only --diff-filter=ACMR; \
			git ls-files -o --exclude-standard; \
		} | sort -u | grep -Ev '(^|/)(package-lock\.json)$$|\.(lock|log)$$' || true \
	)"; \
	if [ -z "$$files" ]; then \
		echo "==> detect-secrets: no changed files to scan"; \
		exit 0; \
	fi; \
	printf '%s\n' "$$files" | while IFS= read -r f; do \
		[ -f "$$f" ] && printf '%s\0' "$$f"; \
	done | xargs -0 -r uv run detect-secrets-hook --baseline .secrets.baseline; \
	echo "==> detect-secrets diff scan passed"

## validate-secrets-push: detect-secrets on files in commits that will be pushed (fast pre-push path)
validate-secrets-push:
	@echo "==> detect-secrets (files in commits-to-push)"
	@files="$$( \
		upstream="$$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"; \
		if [ -n "$$upstream" ]; then \
			git diff --name-only --diff-filter=ACMR "$$upstream...HEAD"; \
		elif git show-ref --verify --quiet refs/remotes/origin/main; then \
			git diff --name-only --diff-filter=ACMR origin/main...HEAD; \
		elif git rev-parse --verify --quiet HEAD~1 >/dev/null; then \
			git diff --name-only --diff-filter=ACMR HEAD~1...HEAD; \
		fi; \
	)"; \
	files="$$(printf '%s\n' "$$files" | sort -u | grep -Ev '(^|/)(package-lock\.json)$$|\.(lock|log)$$' || true)"; \
	if [ -z "$$files" ]; then \
		echo "==> detect-secrets: no commits-to-push files detected; falling back to changed-files scan"; \
		$(MAKE) --no-print-directory validate-secrets-diff; \
		exit 0; \
	fi; \
	printf '%s\n' "$$files" | while IFS= read -r f; do \
		[ -f "$$f" ] && printf '%s\0' "$$f"; \
	done | xargs -0 -r uv run detect-secrets-hook --baseline .secrets.baseline; \
	echo "==> detect-secrets push scan passed"

## validate-secrets-full: detect-secrets on all tracked + untracked files
validate-secrets-full:
	@echo "==> detect-secrets (full repo)"
	@(git ls-files -o --exclude-standard; git ls-files) | sort -u | \
		grep -Ev '(^|/)(package-lock\.json)$$|\.(lock|log)$$' | \
		while IFS= read -r f; do [ -f "$$f" ] && printf '%s\0' "$$f"; done | \
		xargs -0 -r uv run detect-secrets-hook --baseline .secrets.baseline
	@echo "==> detect-secrets full scan passed"

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
	PYTHONPATH=. uv run pytest tests/unit/ src/ -v --tb=short

## test-int: Run integration tests (requires make dev running)
test-int:
	PYTHONPATH=. uv run pytest tests/integration/ -v --tb=short

## test-agent: Run tests for a specific agent (AGENT required)
test-agent:
	@test -n "$(AGENT)" || (echo "ERROR: AGENT required. Usage: make test-agent AGENT=echo-agent" && exit 1)
	PYTHONPATH=. uv run pytest agents/$(AGENT)/tests/ -v --tb=short

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
	@test -n "$$AWS_REGION" || (echo "ERROR: AWS_REGION environment variable not set" && exit 1)
	aws ssm put-parameter \
		--region $$AWS_REGION \
		--name /platform/config/runtime-region \
		--value $(REGION) \
		--type String \
		--overwrite
	@echo "==> Runtime region set to $(REGION) (via SSM in $$AWS_REGION)"
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

## logs-bridge: Tail bridge Lambda logs (recent) or stream live
## Usage: make logs-bridge [ENV=prod] [MINUTES=30]
logs-bridge:
	@if [ -n "$(MINUTES)" ]; then \
		aws logs tail /platform/bridge/$(ENV) --since $(MINUTES)m; \
	else \
		aws logs tail /platform/bridge/$(ENV) --follow; \
	fi

## logs-authoriser: Tail authoriser Lambda logs
## Usage: make logs-authoriser [ENV=prod] [MINUTES=30]
logs-authoriser:
	@if [ -n "$(MINUTES)" ]; then \
		aws logs tail /platform/authoriser/$(ENV) --since $(MINUTES)m; \
	else \
		aws logs tail /platform/authoriser/$(ENV) --follow; \
	fi

## logs-tenant-api: Tail tenant API Lambda logs
## Usage: make logs-tenant-api [ENV=prod] [MINUTES=30]
logs-tenant-api:
	@if [ -n "$(MINUTES)" ]; then \
		aws logs tail /platform/tenant-api/$(ENV) --since $(MINUTES)m; \
	else \
		aws logs tail /platform/tenant-api/$(ENV) --follow; \
	fi

## logs-async-runner: Tail async runner Lambda logs
## Usage: make logs-async-runner [ENV=prod] [MINUTES=30]
logs-async-runner:
	@if [ -n "$(MINUTES)" ]; then \
		aws logs tail /platform/async-runner/$(ENV) --since $(MINUTES)m; \
	else \
		aws logs tail /platform/async-runner/$(ENV) --follow; \
	fi

## logs-bff: Tail BFF Lambda logs
## Usage: make logs-bff [ENV=prod] [MINUTES=30]
logs-bff:
	@if [ -n "$(MINUTES)" ]; then \
		aws logs tail /platform/bff/$(ENV) --since $(MINUTES)m; \
	else \
		aws logs tail /platform/bff/$(ENV) --follow; \
	fi

# =============================================================================
# PLAN DEV
# =============================================================================

## plan-dev: Generate a structured implementation plan for a task
## Usage: make plan-dev TASK="Implement the billing metering pipeline"
plan-dev:
	@test -n "$(TASK)" || (echo "ERROR: TASK required. Usage: make plan-dev TASK=\"describe your task\"" && exit 1)
	uv run python scripts/plan_dev.py "$(TASK)"

# =============================================================================
# TASK LIFECYCLE (worktree-based agent sessions)
# =============================================================================

## task-next: Print the next not-started task from docs/TASKS.md
task-next:
	uv run python scripts/task.py next

## task-list: List all tasks with their status
task-list:
	uv run python scripts/task.py list

## task-start: Create worktree, mark [~], run validate-local, launch Claude Code
## Usage: make task-start              # auto-selects next [ ] task
##        make task-start TASK=TASK-011
task-start:
	uv run python scripts/task.py start $(TASK)

## task-resume: Resume an existing worktree session for a task
## Usage: make task-resume             # auto-selects first [~] task with worktree
##        make task-resume TASK=TASK-011
task-resume:
	uv run python scripts/task.py resume $(TASK)

## task-finish: Print finish checklist and git/gh commands for a task
## Usage: make task-finish TASK=TASK-011
task-finish:
	@test -n "$(TASK)" || (echo "ERROR: TASK required. Usage: make task-finish TASK=TASK-011" && exit 1)
	uv run python scripts/task.py finish $(TASK)

## task-prompt: Print the agent prompt for a task without creating a worktree
## Usage: make task-prompt TASK=TASK-011
task-prompt:
	@test -n "$(TASK)" || (echo "ERROR: TASK required. Usage: make task-prompt TASK=TASK-011" && exit 1)
	uv run python scripts/task.py prompt $(TASK)

# =============================================================================
# ISSUE-DRIVEN WORKTREE FLOW (GitHub Issues SoT)
# =============================================================================

## issue-queue: Show issue queue ordered by Seq (with dependency blocking)
## Usage: make issue-queue [QUEUE_MODE=auto|ready|open-task] [STREAM=a] [LIMIT=20]
issue-queue:
	uv run python scripts/worktree_issues.py issue-queue \
		--mode "$(if $(QUEUE_MODE),$(QUEUE_MODE),auto)" \
		$(if $(STREAM),--stream-label "$(STREAM)",) \
		$(if $(LIMIT),--limit $(LIMIT),)

## issues-audit: Objective issue-state/queue invariants check (fails on drift)
## Usage: make issues-audit [JSON=1]
issues-audit:
	uv run python scripts/worktree_issues.py issues-audit \
		$(if $(JSON),--json,)

## issues-reconcile: Repair task issue labels to lifecycle rules
## Usage: make issues-reconcile [DRY_RUN=1]
issues-reconcile:
	uv run python scripts/worktree_issues.py issues-reconcile \
		$(if $(DRY_RUN),--dry-run,)

## worktree: Interactive issue-driven worktree menu (Seq/Depends on aware)
## Usage: make worktree [QUEUE_MODE=auto|ready|open-task] [STREAM=a]
worktree:
	uv run python scripts/worktree_issues.py menu \
		--mode "$(if $(QUEUE_MODE),$(QUEUE_MODE),auto)" \
		$(if $(STREAM),--stream-label "$(STREAM)",)

## worktree-next-issue: Create a worktree for the next runnable issue in the queue
## Usage: make worktree-next-issue [QUEUE_MODE=auto|ready|open-task] [DRY_RUN=1] [OPEN_SHELL=1]
worktree-next-issue:
	uv run python scripts/worktree_issues.py worktree-next \
		--mode "$(if $(QUEUE_MODE),$(QUEUE_MODE),auto)" \
		$(if $(STREAM),--stream-label "$(STREAM)",) \
		$(if $(DRY_RUN),--dry-run,) \
		$(if $(OPEN_SHELL),--open-shell,) \
		$(if $(NO_CLAIM),--no-claim,) \
		$(if $(NO_PREFLIGHT),--no-preflight,) \
		$(if $(ALLOW_BLOCKED),--allow-blocked,) \
		$(if $(AGENT),--agent "$(AGENT)",) \
		$(if $(AGENT_MODE),--agent-mode "$(AGENT_MODE)",) \
		$(if $(HANDOFF),--handoff "$(HANDOFF)",) \
		$(if $(PRINT_ONLY),--print-only,)

## worktree-create-issue: Create a worktree for a specific issue number
## Usage: make worktree-create-issue ISSUE=23 [DRY_RUN=1] [OPEN_SHELL=1]
worktree-create-issue:
	@test -n "$(ISSUE)" || (echo "ERROR: ISSUE required. Usage: make worktree-create-issue ISSUE=23" && exit 1)
	uv run python scripts/worktree_issues.py worktree-create \
		--issue $(ISSUE) \
		--mode "$(if $(QUEUE_MODE),$(QUEUE_MODE),auto)" \
		$(if $(STREAM),--stream-label "$(STREAM)",) \
		$(if $(DRY_RUN),--dry-run,) \
		$(if $(OPEN_SHELL),--open-shell,) \
		$(if $(NO_CLAIM),--no-claim,) \
		$(if $(NO_PREFLIGHT),--no-preflight,) \
		$(if $(ALLOW_BLOCKED),--allow-blocked,) \
		$(if $(AGENT),--agent "$(AGENT)",) \
		$(if $(AGENT_MODE),--agent-mode "$(AGENT_MODE)",) \
		$(if $(HANDOFF),--handoff "$(HANDOFF)",) \
		$(if $(PRINT_ONLY),--print-only,) \
		$(if $(SCOPE),--scope "$(SCOPE)",) \
		$(if $(SLUG),--slug "$(SLUG)",) \
		$(if $(NAME),--name "$(NAME)",) \
		$(if $(BASE_REF),--base-ref "$(BASE_REF)",) \
		$(if $(BASE_DIR),--base-dir "$(BASE_DIR)",)

## worktree-resume-issue: Resume/select a linked issue worktree (preflight + optional shell/command)
## Usage: make worktree-resume-issue [OPEN_SHELL=1] [CMD='make test-unit']
worktree-resume-issue:
	uv run python scripts/worktree_issues.py worktree-resume \
		$(if $(WT_PATH),--path "$(WT_PATH)",) \
		$(if $(NO_PREFLIGHT),--no-preflight,) \
		$(if $(CMD),--command "$(CMD)",) \
		$(if $(OPEN_SHELL),--open-shell,) \
		$(if $(AGENT),--agent "$(AGENT)",) \
		$(if $(AGENT_MODE),--agent-mode "$(AGENT_MODE)",) \
		$(if $(HANDOFF),--handoff "$(HANDOFF)",) \
		$(if $(PRINT_ONLY),--print-only,)

## preflight-session: Run issue-worktree preflight checks for current worktree
preflight-session:
	uv run python scripts/worktree_issues.py preflight

## pre-validate-session: Run enforced pre-push validation (skips cdk synth)
## Usage: make pre-validate-session [WT_PATH=../worktrees/wt23]
pre-validate-session:
	uv run python scripts/worktree_issues.py pre-validate \
		$(if $(WT_PATH),--path "$(WT_PATH)",)

## worktree-push-issue: Push current issue worktree branch (preflight + pre-validate enforced)
## Usage: make worktree-push-issue [WT_PATH=../worktrees/wt23] [DRY_RUN=1]
worktree-push-issue:
	uv run python scripts/worktree_issues.py push-branch \
		$(if $(WT_PATH),--path "$(WT_PATH)",) \
		$(if $(DRY_RUN),--dry-run,)

## finish-worktree-summary: Show guided finish summary for current worktree
## Usage: make finish-worktree-summary [WT_PATH=../worktrees/wt23]
finish-worktree-summary:
	uv run python scripts/worktree_issues.py finish-summary \
		$(if $(WT_PATH),--path "$(WT_PATH)",)

## finish-worktree-close: Close the current worktree issue after merge verification
## Usage: make finish-worktree-close [WT_PATH=../worktrees/wt23] [FORCE=1]
finish-worktree-close:
	uv run python scripts/worktree_issues.py finish-close \
		$(if $(WT_PATH),--path "$(WT_PATH)",) \
		$(if $(FORCE),--force,)

## agent-handoff: Print/launch agent command with agent selection and yolo modes for current path
## Usage: make agent-handoff [AGENT=codex] [AGENT_MODE=yolo] [HANDOFF=print-only]
agent-handoff:
	uv run python scripts/worktree_issues.py agent-handoff \
		$(if $(WT_PATH),--path "$(WT_PATH)",) \
		$(if $(AGENT),--agent "$(AGENT)",) \
		$(if $(AGENT_MODE),--agent-mode "$(AGENT_MODE)",) \
		$(if $(HANDOFF),--handoff "$(HANDOFF)",) \
		$(if $(PRINT_ONLY),--print-only,)

## install-git-hooks: Install repo-local Git hooks (pre-push runs fast pre-validation)
install-git-hooks:
	@git config core.hooksPath .githooks
	@chmod +x .githooks/pre-push
	@echo "==> Installed Git hooks (core.hooksPath=.githooks)"
	@echo "==> pre-push will run: make validate-pre-push (no cdk synth)"

## hooks-status: Show current hooksPath and installed repo hooks
hooks-status:
	@echo "core.hooksPath=$$(git config --get core.hooksPath || echo .git/hooks)"
	@ls -la .githooks 2>/dev/null || echo "No .githooks directory present"
