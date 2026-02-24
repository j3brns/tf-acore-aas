# Threat Model

## Scope
The AgentCore multi-tenant AaaS platform including: REST API, bridge Lambda,
AgentCore Gateway and interceptors, AgentCore Runtime, DynamoDB, S3, Secrets Manager,
SPA frontend, and CI/CD pipeline.

## Threat Actors

| Actor                | Motivation              | Capability              |
|----------------------|-------------------------|-------------------------|
| External attacker    | Data theft, disruption  | Low-moderate            |
| Malicious tenant     | Access other tenant data| Platform-level access   |
| Compromised agent    | Lateral movement        | Tool invocation access  |
| Insider threat       | Data exfiltration       | High (limited by IAM)   |
| Supply chain         | Code injection          | Build pipeline access   |

## Attack Surfaces

### 1. Northbound REST API
Threat: unauthenticated access, JWT forgery, replay attacks
Mitigation: WAF (AWS Managed Rules), authoriser Lambda validates every request,
JWKS cache prevents replay via exp claim validation, usage plans prevent DoS

### 2. Cross-Tenant Data Access
Threat: tenant A reads tenant B's data via API manipulation
Mitigation: four-layer isolation (authoriser, bridge execution role, Gateway interceptor,
data-access-lib TenantScopedDynamoDB). TenantAccessViolation raised and alerted on any breach.

### 3. JWT Manipulation
Threat: attacker forges or modifies JWT to claim different tenant/tier/role
Mitigation: RS256 signature verification against Entra JWKS; signature cannot be forged
without Entra private key; aud/iss claims validated

### 4. Identity Propagation (Confused Deputy)
Threat: agent uses its own elevated permissions to access resources beyond its scope
Mitigation: act-on-behalf model (ADR-004); original JWT never reaches tool Lambdas;
scoped tokens expire in 5 minutes; tool Lambda cannot escalate beyond scoped token

### 5. Supply Chain (Dependencies)
Threat: malicious Python package in agent dependencies
Mitigation: detect-secrets in CI, uv.lock pins all transitive dependencies,
--only-binary=:all: prevents executing setup.py scripts, SBOM generation in pipeline

### 6. Secrets Exposure
Threat: credentials leaked in logs, code, or environment variables
Mitigation: detect-secrets pre-commit scan, forbidden pattern enforcement (CLAUDE.md),
all secrets in Secrets Manager with 30-day rotation, Lambda /tmp caching (not env vars)

### 7. Pipeline Compromise
Threat: malicious code injected via CI/CD pipeline
Mitigation: GitLab WIF OIDC (no long-lived keys), least-privilege pipeline roles,
cfn-guard validates all IaC before deploy, two-reviewer approval for production

### 8. Session Hijacking
Threat: attacker intercepts active AgentCore Runtime session
Mitigation: HTTPS everywhere, scoped tokens per session, session ID is UUID4 (not guessable),
Runtime microVM isolation prevents cross-session access at compute level

### 9. Data Exfiltration via Agent
Threat: agent accumulates and exfiltrates tenant data
Mitigation: RESPONSE interceptor PII redaction, tool access filtered by tier,
Bedrock Guardrails on Runtime, AgentCore Memory scoped per tenant

### 10. Insider Threat (Operator)
Threat: operator reads tenant invocation content
Mitigation: operators use ops.py — which calls Admin REST API — not direct DynamoDB;
invocation content not exposed via Admin API (only metadata); audit log on all API calls;
CloudTrail records all AWS API calls including Secrets Manager reads

## Controls Summary

| Control                        | Threat(s) Addressed          | Where Implemented         |
|--------------------------------|------------------------------|---------------------------|
| JWT validation (sig, exp, aud) | 1, 3                         | Authoriser Lambda         |
| TenantScopedDynamoDB           | 2                            | data-access-lib           |
| Act-on-behalf scoped tokens    | 4                            | REQUEST interceptor        |
| KMS encryption at rest         | 2, 10                        | All DynamoDB + S3          |
| HTTPS everywhere               | 8                            | CloudFront, API GW, VPC   |
| detect-secrets in CI           | 6, 7                         | GitLab CI validate stage  |
| Two-reviewer prod approval     | 7                            | GitLab protected env      |
| cfn-guard IaC policy           | 7                            | GitLab CI validate stage  |
| CloudTrail + VPC Flow Logs     | 10                           | ObservabilityStack        |
| PII redaction (RESPONSE)       | 9                            | RESPONSE interceptor      |

## Data Classification

| Data Type               | Classification  | Encryption       | Retention    |
|-------------------------|-----------------|------------------|--------------|
| Agent invocation content| Confidential    | KMS at rest+TLS  | 90 days      |
| Tenant metadata         | Internal        | KMS at rest+TLS  | Lifetime     |
| Audit logs              | Internal        | KMS at rest+TLS  | 7 years      |
| CloudTrail logs         | Internal        | Default+KMS      | 7 years      |
| Platform secrets        | Secret          | Secrets Manager  | Rotated 30d  |
| Agent code (ZIP/image)  | Internal        | S3 SSE-KMS       | Per version  |

## Residual Risks

1. Entra identity provider outage: platform cannot authenticate human users.
   Mitigation: SigV4 path still works for machine consumers; monitor Entra SLA.

2. KMS key compromise: all encrypted data at risk.
   Mitigation: KMS key policies restrict access; key rotation every 90 days;
   separate keys per data classification.

3. AgentCore Runtime escape: microVM isolation breach at hypervisor level.
   Mitigation: AWS responsibility under shared responsibility model; Firecracker
   has a strong security track record; monitor AWS security bulletins.
