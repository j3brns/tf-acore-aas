# Entra App Registration Setup

## Purpose

The platform uses Microsoft Entra ID for human authentication. This document
covers the one-time app registration setup required before bootstrapping.

Requires: Azure Portal access with Application Administrator role.

## Step 1: Create App Registration

1. Azure Portal → Entra ID → App Registrations → New Registration
2. Name: `platform-{env}` (e.g. `platform-prod`)
3. Supported account types: Accounts in this organisational directory only
4. Redirect URI: Single-page application → `https://{cloudfront-domain}/auth/callback`
   (Use a placeholder for now — update after CDK deploy outputs the domain)
5. Register

Note the **Application (client) ID** and **Directory (tenant) ID**.

## Step 2: Configure API Scopes

App Registration → Expose an API → Set Application ID URI:
`api://{application-client-id}`

Add scopes:
| Scope name         | Description                          | Who can consent |
|--------------------|--------------------------------------|-----------------|
| Agent.Invoke       | Invoke agents on the platform        | Admins and users|
| Agent.Developer    | Push and manage agents               | Admins only     |
| Platform.Admin     | Full administrative access           | Admins only     |
| Platform.Operator  | Operational access                   | Admins only     |

## Step 3: Configure App Roles (for RBAC)

App Registration → App Roles → Create App Role:

| Display Name       | Value              | Allowed Member Types |
|--------------------|--------------------|----------------------|
| Platform Admin     | Platform.Admin     | Users/Groups         |
| Platform Operator  | Platform.Operator  | Users/Groups         |
| Agent Developer    | Agent.Developer    | Users/Groups         |
| Basic Tenant       | Agent.Invoke.basic | Users/Groups         |
| Standard Tenant    | Agent.Invoke.standard | Users/Groups      |
| Premium Tenant     | Agent.Invoke.premium | Users/Groups       |

## Step 4: Create Security Groups and Assign Roles

Entra ID → Groups → New Group for each:
- `platform-admins` → assign Platform.Admin role
- `platform-operators` → assign Platform.Operator role
- `agent-developers` → assign Agent.Developer role

For tenants: create one group per tier, assign the corresponding role.

## Step 5: Configure Token Claims

App Registration → Token Configuration → Add Groups Claim:
- Group Types: Security Groups
- ID Token: Group ID
- Access Token: Group ID

Add Optional Claim → Access Token → `roles` (to include app roles in JWT).

## Step 6: Create Client Secret

App Registration → Certificates & Secrets → New Client Secret
- Description: `platform-{env}-bootstrap`
- Expires: 24 months

**Copy the secret value immediately** — it will not be shown again.
This goes into Secrets Manager during bootstrap step 2 (`make bootstrap-secrets`).

## Step 7: Configure PKCE for SPA

App Registration → Authentication → Platform: Single-page application
- Redirect URIs: `https://{cloudfront-domain}/auth/callback`
- Enable: Access tokens and ID tokens for implicit flow — **NO** (use auth code + PKCE)
- Enable: Allow public client flows — No

## Step 8: On-Behalf-Of for BFF Lambda

App Registration → API Permissions → Add Permission:
- My APIs → platform-{env} → Delegated → Agent.Invoke

This allows the BFF Lambda to request tokens on behalf of users (OBO flow).

## Updating Redirect URIs After Deploy

After `make infra-deploy ENV={env}` completes, CDK outputs the CloudFront domain.
Update the redirect URI in Entra app registration to use the real domain.

## Verifying Setup

After bootstrap is complete:
```bash
make ops-login --env dev
# Should prompt for Entra login
# On success: prints "Logged in as {user} with roles: Platform.Operator"
```
