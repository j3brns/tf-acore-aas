# ADR-002: Microsoft Entra ID over Amazon Cognito

## Status: Accepted
## Date: 2026-02-24

## Context
The platform serves B2B tenants who are predominantly Microsoft enterprise customers.
Authentication must support OIDC/JWT, role-based access, and enterprise SSO.

## Decision
Microsoft Entra ID for all human authentication. No Cognito anywhere in the codebase.
MSAL.js (@azure/msal-browser) in the SPA. Authoriser Lambda validates Entra JWTs.

## Consequences
- Tenants use their existing corporate Entra credentials — no new account to manage
- Requires Entra app registration (manual Azure Portal step — documented in entra-setup.md)
- Requires custom JWT validation Lambda (JWKS fetch, sig validate, audience check)
- MSAL.js handles PKCE flow client-side — no server-side token exchange for standard auth
- Entra group membership injected as roles claims in JWT — drives platform RBAC

## Alternatives Rejected
- Cognito: adds a dependency that is not native for Microsoft 365 enterprise customers;
  federation to Entra is possible but adds complexity and a second identity system
- Auth0: additional cost, additional vendor, unnecessary when Entra already covers tenants
