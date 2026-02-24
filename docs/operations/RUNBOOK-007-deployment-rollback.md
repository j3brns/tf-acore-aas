# RUNBOOK-007: Deployment Rollback

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
# Redeploys the previous agent version from the platform-agents DynamoDB registry
# Does not require a pipeline run
```

## Post-Rollback
1. Verify error rate is recovering: make ops-error-rate ENV=prod MINUTES=5
2. Identify root cause of the issue before re-deploying
3. Write a brief post-mortem note in the GitLab issue
4. Fix the issue in a new MR — do not re-deploy the same broken commit
