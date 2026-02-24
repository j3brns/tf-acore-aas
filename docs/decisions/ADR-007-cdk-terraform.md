# ADR-007: CDK TypeScript for Platform IaC, Terraform for Account Vending

## Status: Accepted
## Date: 2026-02-24

## Context
Infrastructure as code must support per-tenant provisioning (TenantStack), complex
conditional logic, and multi-stack dependencies. Account vending requires AWS
Organizations API calls.

## Decision
CDK TypeScript (strict mode) for all application infrastructure stacks.
Terraform HCL only for account vending via AWS Organizations.

## Consequences
- CDK enables programmatic tenant stack instantiation with type-safe props
- CloudFormation state management — no separate state backend for platform infra
- TypeScript strict mode catches type errors at compile time
- Terraform used only where CDK cannot natively manage Organizations resources
- Two IaC tools in the project — justified by clear separation of concern
- CDK Aspects enforce security policy across all constructs at synth time

## Alternatives Rejected
- Pure Terraform: poor imperative logic for dynamic tenant stack generation;
  count/for_each limited compared to CDK programmatic approach
- Pure CDK: CDK support for AWS Organizations account creation is limited;
  Terraform has better native support for this use case
- AWS CloudFormation directly: too verbose, no programmatic constructs
