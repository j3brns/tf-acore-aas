# ADR-014: Minimise VPC-Bound Service Dependencies for the Platform Control Plane

## Status: Accepted — partial implementation gap (see Issue #225)
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

### Security Group Contract (mandatory for all VPC-attached services)

`NetworkStack` creates two related security groups:
- `LambdaSecurityGroup` — assigned to VPC-attached platform Lambdas
- `InterfaceEndpointSecurityGroup` — assigned to all VPC interface endpoints;
  allows inbound 443 **only** from `LambdaSecurityGroup`

Any Lambda deployed into isolated subnets **must** be explicitly assigned
`LambdaSecurityGroup`. The default auto-generated Lambda SG is not trusted by
the interface endpoints and will silently break calls to SSM, Secrets Manager,
STS, and AgentCore.

CDK code must pass `securityGroups: [props.lambdaSecurityGroup]` when calling
`createPythonLambda` or equivalent. Jest construct tests must assert that every
VPC-attached Lambda references `LambdaSecurityGroup`, not a self-referencing or
auto-generated group.

> **Known gap:** `infra/cdk/lib/platform-stack.ts` attaches Lambdas to isolated
> subnets but omits `securityGroups`, causing them to use an auto-generated SG
> that the interface endpoint SG does not trust. Tracked in **Issue #225**.
> The VPC has no NAT gateways; without this fix, control-plane calls to SSM,
> Secrets Manager, and AgentCore will fail at runtime.

## Consequences
- The default platform path becomes simpler to deploy and reason about.
- Networking drift stops being a hidden dependency of every control-plane code change.
- VPC endpoint, route-table, and security-group complexity is contained to the small set
  of services that truly need it.
- Private dependencies remain supported, but behind explicit architectural boundaries.
- Some workflows may require an additional adapter component instead of direct access from
  the main control-plane Lambda.
- Any service that is legitimately VPC-attached must participate in the
  `LambdaSecurityGroup` / `InterfaceEndpointSecurityGroup` contract or interface
  endpoint calls will fail silently.

## Implementation History
- Issue #259 (closed): removed forced VPC attachment from shared control-plane Lambdas
  that had no private dependency — aligned the deployment with this ADR's default.
- Issue #292 (closed): aligned cfn-guard Lambda networking rules with this ADR.
- Issue #225 (open): platform Lambdas still deployed into isolated subnets are not
  assigned `LambdaSecurityGroup`, breaking interface endpoint reachability. Must be
  resolved before platform is production-ready.

## Alternatives Rejected
- **Put all platform Lambdas in the VPC by default**: maximises endpoint/security-group
  complexity and couples routine control-plane code to private networking.
- **Keep isolated-subnet Lambdas and add endpoints for every AWS service call**: workable,
  but turns normal SDK usage into a broad network-operations burden.
- **Use NAT for all control-plane Lambdas**: reintroduces broad egress, extra cost, and a
  larger blast radius than needed for control-plane APIs.
