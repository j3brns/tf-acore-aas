# RUNBOOK-007: Deployment Rollback

## Normal Agent Promotion
Agent promotion is a control-plane metadata change against an already registered
version. Do not rebuild or repackage the agent as part of promotion.

1. Register the candidate version. In `prod` it must remain `pending`.
2. Verify staging/integration evidence and evaluation results for that exact version.
3. Promote the version to `released`.
4. Record release notes or ticket evidence with the promotion action.

Operational rule:
- Promotion changes agent status metadata only. The active default version is the
  highest `released` semver in the agent registry.

## Auto-Rollback (handled by pipeline)
The canary deployment monitors error_rate_high alarm. If it fires within 30 minutes
of a deployment, the Lambda alias automatically rolls back to the previous version.
No manual action needed if auto-rollback triggers cleanly.

## Confirming Auto-Rollback
```bash
make logs-bridge ENV=prod MINUTES=10 | grep "Lambda version"
# Should show the previous version number being served
make ops-error-rate ENV=prod MINUTES=5
# Should be recovering to <1%
```

## Manual Lambda Rollback
If auto-rollback did not trigger or did not complete:
```bash
make infra-rollback-lambda FUNCTION=bridge ENV=prod
# Rolls back the Lambda alias to the previous version
# Other functions: authoriser, tenant-api, bff, request-interceptor, response-interceptor
```

## Manual CDK Stack Rollback
If a CDK stack deployment caused the issue (infrastructure change, not Lambda code):
```bash
cd infra/cdk
npx cdk deploy --all --context env=prod --rollback
# CloudFormation will revert to the previous stack state
# This may take 10–20 minutes
```

## Agent Rollback
```bash
make agent-rollback AGENT={agentName} ENV=prod
# Marks the current released version as rollback and re-points runtime metadata
# to the next-highest released version in the agent registry
# Does not require a rebuild or pipeline run
```

Operational rule:
- Never delete a bad agent version from the registry. Rollback is a forward metadata
  transition that preserves audit history.

## Post-Rollback
1. Verify error rate is recovering: make ops-error-rate ENV=prod MINUTES=5
2. Identify root cause of the issue before re-deploying
3. Write a brief post-mortem note in the GitLab issue
4. Fix the issue in a new MR — do not re-deploy the same broken commit
