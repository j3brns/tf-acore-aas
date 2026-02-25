# CLAUDE.md — Rules for AI Coding Assistants
# Read this at the start of every session. No exceptions.

## What This Platform Is

A production multi-tenant Agent as a Service platform on Amazon Bedrock AgentCore.
B2B tenants invoke AI agents via REST API. The platform manages isolation, identity,
memory, tool access, billing, and observability. This is a production system — not
a prototype — with real tenants, real data, and real compliance obligations.

## Priority Order

When trade-offs arise, resolve in this order:
1. Security — a security flaw ships last, regardless of schedule
2. Operability — ops must run this at 3am without a developer on call
3. Correctness — wrong behaviour is worse than slow behaviour
4. Performance — optimise only after correctness is proven
5. Developer experience — the inner loop matters, but it is last

## Absolute Constraints (non-negotiable)

If any implementation path violates these, stop, state the conflict, propose an
alternative. Never silently work around them.

1. No Cognito anywhere. Auth is Entra ID OIDC/JWT for humans, SigV4 for machines.
2. No hardcoded credentials, ARNs, account IDs, secrets, or region strings.
   Exception: CDK stack definitions may declare the home region (eu-west-2) as an
   architectural constant (e.g. const HOME_REGION = 'eu-west-2'). This constraint
   applies to application code — Lambda handlers and scripts that call AWS APIs at
   runtime must always read the region from os.environ['AWS_REGION'].
3. No IAM policies with wildcard Action or wildcard Resource.
4. No public S3 buckets.
5. No long-lived AWS access keys. Bootstrap IAM user deleted after first deploy.
6. No secrets in GitLab CI/CD variables — Secrets Manager only.
7. Every Lambda: X-Ray tracing, DLQ, structured JSON logging with appid+tenantid.
8. Every DynamoDB table: PITR, KMS encryption, deletion protection in staging/prod.
9. AgentCore Runtime is arm64 only. Dependencies cross-compiled aarch64-manylinux2014.
   Sync limit 15 minutes. Async uses app.add_async_task / app.complete_async_task.
10. No impersonation — act-on-behalf only. Original JWT never reaches tool Lambdas.
11. appid and tenantid on every log line, metric dimension, and trace annotation.
12. data-access-lib is the only permitted way to access DynamoDB from Lambda handlers.
13. No superuser IAM roles in normal operation.
14. All data remains in the EU at all times.

## How To Work

Before writing any code:
1. Read this file
2. Read docs/ARCHITECTURE.md
3. Read the ADR(s) linked to the current task in docs/TASKS.md
4. In local WSL, confirm you are in a task worktree on a task branch (not `main` in the primary repo working tree)
5. If not, start via `make task-start` unless the operator explicitly instructs in-place work
6. If you are in local WSL with the repo checked out, run `make validate-local` — confirm it passes
   (use `make validate-local-full` when a full-repo secret scan is required)
7. State which task you are working on explicitly

Before marking any task complete:
1. All tests pass
2. `make validate-local` passes
3. Senior engineer review completed (code review mindset: bugs, regressions, risks, missing tests)
4. Review recommendations are actioned
5. Senior engineer review re-run and clear (or remaining risks explicitly accepted by operator)
6. New infrastructure passes cfn-guard
7. State "TASK-NNN complete. Tests passing."

When uncertain about a security decision — stop and ask. Do not guess.

### Execution Loop (Drive To Completion)

The agent should drive the task to completion without stopping at the first error.
Use failure output and operational signals to diagnose and fix the next issue until
the closure criteria are met.

Preferred signals (use what is available in the current environment):
- Test failures and stack traces (`pytest`, Jest, `make test-*`)
- Validation output (`make validate-local`, `make validate-local-full`)
- Lint/typecheck output (Ruff, Pyright, TypeScript)
- CDK synth/deploy error output
- Local runtime logs (`make dev-logs`, `docker compose logs`)
- Platform logs (`make logs-*`, `aws logs tail ...`)
- Git state (`git status`, diff, merge conflicts)

Do not stop just because one command failed. Investigate the error, form a hypothesis,
apply a fix, and re-run the smallest relevant check. Only stop for the explicit
"stop and ask" conditions, gate tasks, or when the operator redirects you.

## When To Stop And Ask

- Any change to DynamoDB partition key or GSI design
- Any change to IAM policies or trust relationships
- Any change to authoriser Lambda validation logic
- Any new dependency adding >10MB to the deployment package
- Any change affecting tenant isolation in data-access-lib
- Any change to KMS key policy
- Any operation touching production data

## Naming Conventions

- AWS resources: platform-{resource}-{environment}
- Python: snake_case everywhere — this includes source directory names.
  Lambda source dirs must be snake_case (src/async_runner/, not src/async-runner/)
  because hyphenated names cannot be Python package names and break static type checking.
- Every Python source directory must contain an __init__.py so Pyright resolves
  identically-named modules (e.g. handler.py) as distinct packages.
- TypeScript: camelCase properties, PascalCase classes
- Environment variables: SCREAMING_SNAKE_CASE
- SSM: /platform/{category}/{name}
- DynamoDB keys: {ENTITY}#{id}

## Forbidden Patterns

```python
# FORBIDDEN: raw boto3 DynamoDB in handlers
dynamodb.Table('platform-tenants').get_item(...)

# REQUIRED: data-access-lib only
from data_access import TenantScopedDynamoDB
db = TenantScopedDynamoDB(tenant_context)

# FORBIDDEN: hardcoded region
boto3.client('ssm', region_name='eu-west-2')

# REQUIRED: from environment
boto3.client('ssm', region_name=os.environ['AWS_REGION'])

# FORBIDDEN: bare exception silencing
try:
    do_something()
except Exception:
    pass

# REQUIRED: log and handle
try:
    do_something()
except TenantAccessViolation as e:
    logger.error("Tenant access violation", extra={"tenant_id": tenant_id})
    return error_response(403, "UNAUTHORISED")
```

## Task Workflow (Worktree Protocol)

Every task runs in its own git branch and for local dev (WSL) a worktree. This is so main stays clean and multiple tasks
can be in flight at the same time without conflicts. When operating in Claude Code mobile / remote prompt mode, worktrees are not required.

### Selecting a task

```bash
make task-next            # show the next not-started task
make task-list            # list all tasks and their status
```

### Starting a task

```bash
make task-start              # auto-selects the next [ ] task
make task-start TASK=TASK-011  # explicit task
```

This will (local WSL mode / default when WSL is detected):
1. Auto-select the next `[ ]` task (or use the explicit TASK argument)
2. Create a git worktree at `../worktrees/TASK-NNN-<slug>/`
3. Create branch `task/NNN-<slug>` from `origin/main`
4. Update `docs/TASKS.md` in the worktree: mark the task `[~]` and commit it
5. Run `make validate-local` in the worktree — abort if it fails
6. Launch Claude Code: `claude --dangerously-skip-permissions <prompt>`

In remote/mobile mode (`make task-start ... -- --env remote`, or when not running in WSL):
1. Auto-select the task (same rules)
2. Generate and print the structured prompt for copy/paste into Claude Code mobile
3. Do not create a worktree
4. Do not mark `docs/TASKS.md` `[~]` automatically

The prompt instructs the agent to read CLAUDE.md, ARCHITECTURE.md, the task's
ADRs, state the task name, and work the loop. In local WSL mode it also requires
`make validate-local`; in remote/mobile mode it first confirms repo path and tool availability.

If the worktree already exists, use `make task-resume` instead.

### Local WSL Safety Rule (mandatory)

In local WSL mode, do not implement task changes directly on `main` in the
primary repo working tree. Use the worktree protocol (`make task-start` /
`make task-resume`) by default.

Only work in-place if the operator explicitly instructs that exception in
writing.

Before implementation in local WSL mode, state:
- current branch
- whether you are in a task worktree (or remote/mobile prompt mode)

If task implementation has already started in the wrong location (for example,
on `main` in the primary repo working tree):
- Stop creating new edits
- Create a task branch immediately from the current state
- Continue on the task branch (or move to a worktree if practical)
- State the deviation and correction in your session output

### Resuming a task

```bash
make task-resume              # auto-selects first [~] task with an existing worktree
make task-resume TASK=TASK-011  # explicit task
```

Relaunches Claude Code in the existing worktree with the same structured prompt (local WSL mode).
In remote/mobile mode, it prints the prompt for copy/paste and does not require a worktree.

### Finishing a task

```bash
make task-finish TASK=TASK-011
```

Prints the finish checklist and the exact `git push` / `gh pr create` commands.
The agent is responsible for:
1. Running `make validate-local` — must pass clean
   - Use `make validate-local-full` when you need a full-repo secret scan (the default is diff-only secrets)
2. Running a senior engineer review (bugs/regressions/risks/missing tests first)
3. Actioning review findings and re-running relevant tests/validation
4. Re-running senior engineer review until findings are cleared (or explicitly accepted)
5. Committing all changes with a message referencing `TASK-NNN`
6. Updating `docs/TASKS.md`: mark `[x]` with today's date and commit SHA
7. Closing only when errors are cleared, then pushing and opening a PR titled `TASK-NNN: <title>`

### Gate tasks

Some tasks have a Gate field (see docs/TASKS.md). When a gate is present:
- The agent stops at the gate and presents findings
- The operator reviews and gives written sign-off.
- Only then does the agent proceed (or close if that was the final step)
- Never advance past a gate unilaterally

### Naming conventions for worktrees

| Item       | Pattern                              | Example                              |
|------------|--------------------------------------|--------------------------------------|
| Directory  | `../worktrees/TASK-NNN-<slug>/`     | `../worktrees/TASK-011-dynamo-schema/` |
| Branch     | `task/NNN-<slug>`                    | `task/011-dynamo-schema`             |

The slug is derived from the task title: lowercase, non-alphanumeric → `-`,
max 50 chars.

### After merge

```bash
git worktree remove ../worktrees/TASK-NNN-<slug>
git branch -d task/NNN-<slug>
git worktree prune
```

## Technology Stack

| Concern            | Technology                      |
|--------------------|---------------------------------|
| Agent runtime      | AgentCore Runtime eu-west-1     |
| Human auth         | Microsoft Entra ID OIDC         |
| Machine auth       | AWS SigV4                       |
| IaC platform       | CDK TypeScript strict           |
| IaC account vend   | Terraform HCL                   |
| Python packaging   | uv + pyproject.toml             |
| Logging            | aws_lambda_powertools Logger    |
| Testing CDK        | Jest + cdk assertions           |
| Testing Python     | pytest + LocalStack             |
| Secrets            | AWS Secrets Manager             |
| Config             | SSM Parameter Store             |
| Async agents       | AgentCore add_async_task SDK    |
| Observability      | AgentCore Observability + CW    |
