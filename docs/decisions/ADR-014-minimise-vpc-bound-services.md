# ADR-014: Minimise VPC-Bound Service Dependencies for the Platform Control Plane

## Status: Accepted
## Date: 2026-03-14

## Context
The platform control plane currently uses many AWS managed services that are reachable
through regional public endpoints: DynamoDB, S3, SSM Parameter Store, Secrets Manager,
STS, EventBridge, CloudWatch, and AgentCore control-plane APIs.

Attaching shared control-plane Lambdas to a VPC by default creates extra operational
surface area:
- interface and gateway endpoint inventory must stay complete
- Lambda and endpoint security groups must stay aligned
- isolated-subnet routing failures can break otherwise simple control-plane calls
- each new AWS SDK dependency becomes a networking decision, not just an application change

Some dependencies genuinely require VPC placement: private RDS clusters, ElastiCache,
private ALBs/NLBs, private SaaS via PrivateLink, or explicit source-network controls.
Those are exceptions, not the default shape of the platform control plane.

This ADR does not change ADR-009's runtime-region policy. It governs how platform
services decide whether they should be VPC-attached at all.

See also: [logical decision diagram](../images/tf_acore_aas_vpc_dependency_policy.drawio.png)

## Decision
The platform adopts an exception-first VPC policy:

1. Shared control-plane Lambdas default to **non-VPC** deployment.
2. A service may be VPC-attached only when it depends on a resource that is reachable
   only through VPC networking or when policy explicitly requires subnet/security-group
   enforcement.
3. Public AWS managed services available via regional endpoints do **not** justify VPC
   attachment by themselves.
4. When only one part of a workflow needs private connectivity, isolate that concern in
   a narrow adapter or worker service rather than pulling the entire northbound/control
   plane into the VPC.
5. Every VPC exception must document:
   - the exact private dependency
   - why public regional endpoints or existing AWS service integrations are insufficient
   - the endpoint, routing, and security-group requirements
   - the rollback path back to non-VPC operation if the private dependency is removed

### Default Non-VPC Class
These remain outside the VPC unless a separate ADR or approved design says otherwise:
- Authoriser Lambda
- Tenant API Lambda
- BFF Lambda
- Billing Lambda
- Gateway interceptors
- Bridge Lambda when it only needs AWS public control-plane APIs and AgentCore regional endpoints

### Allowed VPC Exception Class
These may be VPC-attached when backed by a concrete private dependency:
- adapters for private databases or caches
- connectors to private tenant systems
- workloads requiring source subnet or security-group policy
- dedicated components that must use PrivateLink-only reachability

## Consequences
- The default platform path becomes simpler to deploy and reason about.
- Networking drift stops being a hidden dependency of every control-plane code change.
- VPC endpoint, route-table, and security-group complexity is contained to the small set
  of services that truly need it.
- Private dependencies remain supported, but behind explicit architectural boundaries.
- Some workflows may require an additional adapter component instead of direct access from
  the main control-plane Lambda.

## Alternatives Rejected
- **Put all platform Lambdas in the VPC by default**: maximises endpoint/security-group
  complexity and couples routine control-plane code to private networking.
- **Keep isolated-subnet Lambdas and add endpoints for every AWS service call**: workable,
  but turns normal SDK usage into a broad network-operations burden.
- **Use NAT for all control-plane Lambdas**: reintroduces broad egress, extra cost, and a
  larger blast radius than needed for control-plane APIs.
