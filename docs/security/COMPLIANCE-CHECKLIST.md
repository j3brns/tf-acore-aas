# Compliance Checklist

## GDPR / UK GDPR

| Requirement                               | Implementation                             | Status   |
|-------------------------------------------|--------------------------------------------|----------|
| Lawful basis for processing               | Contract (B2B service agreement)           | Document |
| Data minimisation                         | Only necessary data collected/stored       | By design|
| Purpose limitation                        | Invocation data used only for billing/audit| By design|
| Storage limitation                        | TTL on invocations (90d), sessions (24h)   | Implemented|
| Data subject rights (access/deletion)     | GET /v1/tenants/{id}/audit-export          | Partial  |
| Data residency (UK/EU)                    | eu-west-2 home region, EU-only regions     | Implemented|
| Breach notification (72h to ICO)          | RUNBOOK-003 triggers investigation         | Procedural|
| DPA with AWS                              | Covered by AWS Data Processing Agreement   | Document |
| Encryption at rest                        | KMS on all DynamoDB and S3                 | Implemented|
| Encryption in transit                     | TLS 1.2+ everywhere                        | Implemented|
| Audit logging                             | CloudTrail + platform invocation logs 7yr  | Implemented|

## SOC 2 Type II (reference controls)

| Control Domain         | Implementation                             |
|------------------------|--------------------------------------------|
| Access control         | Entra RBAC, least-privilege IAM            |
| Logical separation     | Four-layer tenant isolation                |
| Encryption             | KMS at rest, TLS in transit                |
| Availability           | Multi-region failover, usage plans         |
| Change management      | Two-reviewer approval, pipeline validation |
| Incident response      | Runbooks, PagerDuty integration            |
| Vendor management      | AWS DPA, Entra DPA                         |
| Audit logging          | CloudTrail 7 years, platform logs 7 years  |

## Actions Required Before Production Launch
- [ ] Legal: Sign customer DPAs before processing any personal data
- [ ] Legal: Confirm data residency requirements per customer contract
- [ ] Security: Penetration test of northbound REST API
- [ ] Security: Review IAM policies for least-privilege compliance
- [ ] Ops: Test data subject deletion procedure end-to-end
- [ ] Ops: Confirm CloudTrail logs are flowing to 7-year retention bucket
- [ ] Ops: Test breach notification procedure (RUNBOOK-003)
