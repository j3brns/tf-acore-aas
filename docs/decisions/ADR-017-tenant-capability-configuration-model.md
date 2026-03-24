# ADR-017: Tenant Capability Configuration Model

## Status: Accepted
## Date: 2026-03-21

## Context
The platform currently uses DynamoDB tenant records and SSM parameters for most
configuration reads. That is sufficient for resource inventory and static runtime
parameters, but it is a poor fit for dynamic capability policy such as:
- per-tier feature enablement
- emergency kill switches
- controlled rollout of tool or model access
- progressive enablement of new tenant-facing capabilities

Those concerns need staged rollout, validation, and rollback semantics. At the
same time, AppConfig is not a substitute for transactional tenant data or
resource inventory.

## Decision
The platform splits configuration ownership across three stores:

1. **AWS AppConfig** owns dynamic tenant capability policy only.
   This includes:
   - tier feature enablement
   - capability flags
   - kill switches
   - model availability policy
   - tool availability policy
   - rollout controls

2. **AWS SSM Parameter Store** remains the store for platform and runtime
   parameters whose values are operational inputs rather than tenant policy.
   This includes:
   - active runtime region and failover parameters
   - stable service endpoint and bootstrap parameters
   - AppConfig bootstrap identifiers when needed by Lambdas

3. **DynamoDB** remains the source of truth for tenant state, resource metadata,
   and transactional records.
   This includes:
   - tenant identity and lifecycle status
   - execution-role ARNs
   - memory-store ARNs
   - API key secret references
   - budget contracts and billing state
   - invocation, job, and session records

## Capability Policy Semantics
- Capability policy is evaluated with **deny-by-default** fallback semantics.
- If an AppConfig fetch fails, the control plane must use the last known good
  cached document when available; otherwise it falls back to an empty policy that
  enables nothing.
- Emergency kill switches must override all rollout or allow-list rules.
- Percentage rollout must be deterministic per tenant identifier so rollout
  membership stays stable across repeated reads.

## Safe Defaults And Failure Boundaries
- AppConfig failure is **fail-closed** for tenant capability policy. The control
  plane must not rebuild capability decisions from DynamoDB tenant records or SSM
  parameters.
- SSM parameters are operational inputs, not tenant feature policy. Missing or
  invalid SSM values must never widen tenant capability access. Where code uses a
  built-in default for an SSM-backed parameter, that default must be an explicit,
  approved operational default within existing ADR constraints.
- DynamoDB remains authoritative for tenant metadata and transactional state. If
  a required tenant or resource record is missing, the operation fails rather
  than synthesizing tenant state from AppConfig or SSM.

## Rollout and Rollback
- Capability changes are published through AppConfig deployment workflows.
- Validators should reject malformed capability documents before rollout.
- Rollout should start with bounded percentage exposure or explicit allow-lists.
- Rollback uses AppConfig environment version history; reverting to the previous
  known good configuration must not require DynamoDB record edits.

## Consequences
- Dynamic tenant capability policy gains first-class rollout and rollback support.
- Resource inventory stays out of AppConfig, preserving DynamoDB as the source of
  truth for tenant metadata.
- Operational runtime parameters stay in SSM, avoiding unnecessary AppConfig
  coupling for non-policy values.
- Control-plane Lambdas keep simple public-endpoint reads consistent with ADR-014.

## Alternatives Rejected
- **Keep all configuration in DynamoDB and SSM**: simple at first, but weak for
  staged rollout, validation, and rollback of dynamic capability policy.
- **Move tenant metadata into AppConfig**: breaks source-of-truth boundaries and
  treats resource inventory like mutable feature policy.
- **Use AppConfig for every parameter**: adds deployment workflow overhead to
  static operational parameters that are better served by SSM.
