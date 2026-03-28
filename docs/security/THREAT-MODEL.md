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
cfn-guard validates all IaC before deploy, protected `prod` environment audited
via GitLab API before deployment, two-reviewer approval for production

### 8. Session Hijacking
Threat: attacker intercepts active AgentCore Runtime session
Mitigation: HTTPS everywhere, scoped tokens per session, session ID is UUID4 (not guessable),
Runtime microVM isolation prevents cross-session access at compute level

### 8a. Runtime Public Network Drift
Threat: AgentCore Runtime remains internet-addressable by silent default even after the
platform claims a stronger network posture
Mitigation: the runtime stack now records `PUBLIC` as an explicit exception tied to
ADR-009 and the absence of eu-west-1 runtime VPC infrastructure. CDK tests and
cfn-guard fail if `PUBLIC` is present without the documented exception metadata and
revisit trigger. Migration to `VPC` is deferred until runtime-region subnets, security
groups, endpoints, and egress controls are designed and approved.

### 9. Data Exfiltration via Agent
Threat: agent accumulates and exfiltrates tenant data
Mitigation: RESPONSE interceptor PII redaction, tool access filtered by tier,
Bedrock Guardrails on Runtime, AgentCore Memory scoped per tenant

### 10. Insider Threat (Operator)
Threat: operator reads tenant invocation content
Mitigation: operators use ops.py — which calls Admin REST API — not direct DynamoDB;
invocation content not exposed via Admin API (only metadata); audit log on all API calls;
CloudTrail records all AWS API calls including Secrets Manager reads

### 11. Abuse of Reserved Platform Tenant
Threat: a reserved internal tenant (`platform`) is treated as a hidden super-tenant or
authorization bypass, allowing cross-tenant actions outside explicit control-plane paths
Mitigation: `platform` is a reserved internal tenant, not a bypass mode; platform-agent
routes still require explicit platform RBAC; target-tenant actions must go through
documented control-plane APIs or workflows; audit records must capture acting tenant,
acting principal, target tenant, operation type, and outcome

## Controls Summary

| Control                        | Threat(s) Addressed          | Where Implemented         |
|--------------------------------|------------------------------|---------------------------|
| JWT validation (sig, exp, aud) | 1, 3                         | Authoriser Lambda         |
| TenantScopedDynamoDB           | 2                            | data-access-lib           |
| Act-on-behalf scoped tokens    | 4                            | REQUEST interceptor        |
| Encryption at rest            | 2, 10                        | AWS-managed encryption on DynamoDB + S3 |
| HTTPS everywhere               | 8                            | CloudFront, API GW, VPC   |
| detect-secrets in CI           | 6, 7                         | GitLab CI validate stage  |
| Two-reviewer prod approval     | 7                            | GitLab protected env + CI API audit |
| cfn-guard IaC policy           | 7                            | GitLab CI validate stage  |
| CloudTrail + VPC Flow Logs     | 10                           | ObservabilityStack        |
| PII redaction (RESPONSE)       | 9                            | RESPONSE interceptor      |
| Explicit runtime posture gate  | 8a                           | AgentCoreStack + cfn-guard + CDK tests |
| Reserved platform-tenant guardrails | 11                      | ADR-016 + control-plane APIs + audit model |

## Data Classification

| Data Type               | Classification  | Encryption       | Retention    |
|-------------------------|-----------------|------------------|--------------|
| Agent invocation content| Confidential    | DynamoDB AWS managed at rest + TLS | 90 days      |
| Tenant metadata         | Internal        | DynamoDB AWS managed at rest + TLS | Lifetime     |
| Audit logs              | Internal        | AWS managed at rest + TLS | 7 years      |
| CloudTrail logs         | Internal        | Default+KMS      | 7 years      |
| Platform secrets        | Secret          | Secrets Manager  | Rotated 30d  |
| Agent code (ZIP/image)  | Internal        | S3 SSE-S3        | Per version  |

## Residual Risks

1. Entra identity provider outage: platform cannot authenticate human users.
   Mitigation: SigV4 path still works for machine consumers; monitor Entra SLA.

2. Misconfiguration of AWS-managed encryption or service defaults.
   Mitigation: keep infrastructure aligned to documented AWS-managed encryption defaults;
   verify service assumptions against AWS docs before changing encryption posture.

3. AgentCore Runtime escape: microVM isolation breach at hypervisor level.
   Mitigation: AWS responsibility under shared responsibility model; Firecracker
   has a strong security track record; monitor AWS security bulletins.

## Reserved Platform Tenant Threat Detail

### Description
The platform may define a reserved internal tenant, `platform`, for operator-controlled
agents and control-plane automation. This creates a concentrated privilege boundary:
if the `platform` tenant is treated as an implicit super-tenant, tenant isolation can
be weakened or bypassed.

### Threat Actors
- malicious operator with a valid platform role
- compromised operator session or Entra token
- compromised internal agent prompt/tool chain
- implementation error that grants `platform` broad direct tenant-data access

### Attack Paths
1. Platform agent directly reads or mutates customer-tenant data without using explicit
   admin/control-plane APIs.
2. Authorization logic treats `tenantid=platform` as a bypass condition.
3. Internal automation omits `targetTenantId`, making cross-tenant actions difficult to
   audit.
4. IAM or application logic grants broad direct resource access because the actor is
   "internal".
5. Prompt injection or tool misuse causes a platform-owned agent to perform an
   unauthorized target-tenant action.

### Impact
- cross-tenant confidentiality breach
- cross-tenant integrity breach
- weakly attributable admin actions
- reduced trust in tenant isolation guarantees
- compliance and audit failure

### Required Mitigations
- `platform` is a reserved internal tenant, not a super-tenant
- `tenantid=platform` must never act as an implicit authorization bypass
- cross-tenant actions must go through explicit control-plane APIs or workflows
- all such actions must record:
  - acting principal
  - acting tenant (`platform`)
  - target tenant
  - operation type
  - outcome
- `data-access-lib` remains the only permitted DynamoDB interface in handlers
- no wildcard IAM permissions introduced for platform-agent flows
- platform-agent routes require explicit platform RBAC
- tests must prove platform-agent flows cannot bypass tenant isolation

### Detection
- audit events containing `tenantid=platform`
- audit events containing `targetTenantId`
- alerts on unexpected volume or unusual target-tenant breadth for platform-agent actions
- traces linking operator identity to internal-agent activity

### Residual Risk
The `platform` tenant concentrates control-plane authority. Residual risk remains if
operator credentials are compromised or platform-agent tooling is allowed to invoke
unsafe workflows. This risk is accepted only if target-tenant actions remain explicit,
audited, and bounded by RBAC and control-plane validation.
