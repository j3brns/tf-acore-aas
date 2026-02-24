# RUNBOOK-009: Operator Onboarding

## Purpose
Steps for a platform engineer to follow when a new operator joins.

## Prerequisites (platform engineer actions)
1. Add operator to Entra group: platform-operators
2. Confirm the operator has: uv, AWS CLI v2, platform repo access (read)
3. Verify operator does NOT have direct AWS console write access (read-only is acceptable)

## Day 1 Steps

### 1. Get access verified
```bash
# Operator logs in via Entra on the SPA admin view
# Should see: Platform.Operator role, platform health dashboard
# Should NOT see: tenant data, individual invocation content
```

### 2. Install CLI tools
```bash
git clone {repo-url}
cd platform
make bootstrap
# Only needs: uv and AWS CLI — not Docker or Node
```

### 3. Configure ops CLI
```bash
cp .env.example .env.local
# Set: API_BASE_URL, ENTRA_CLIENT_ID
make ops-login
# Fetches Entra JWT with Platform.Operator scope
# Stores in ~/.platform/credentials (TTL 1 hour)
```

### 4. Verify ops access
```bash
make ops-quota-report ENV=prod        # Should return quota data
make ops-top-tenants ENV=prod N=5     # Should return top 5 tenants
make ops-error-rate ENV=prod          # Should return current error rate
```

### 5. Read all runbooks (in order)
RUNBOOK-000 through RUNBOOK-009. Understand each trigger and response.
Complete a dry-run of RUNBOOK-001 (failover) in the dev environment.

## Success Criteria
Operator is considered onboarded when:
- They can complete RUNBOOK-001 (failover) in dev without assistance
- They can answer: "How do I find out which tenant is consuming the most quota?"
- They have NOT needed direct AWS console access for any of the above

## What Operators Cannot Do
Operators cannot:
- Access AWS console for write operations (read-only is acceptable for investigation)
- Directly modify DynamoDB records (use ops.py commands only)
- Delete tenants (Platform.Admin role required)
- Access agent invocation content (privacy boundary)
