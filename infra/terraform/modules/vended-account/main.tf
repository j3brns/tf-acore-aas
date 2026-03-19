# Vended Account Module
#
# Creates an AWS account via Organizations and provisions the minimal
# cross-account execution role that the platform Bridge Lambda assumes
# for tenant-scoped AgentCore Runtime invocation.

resource "aws_organizations_account" "this" {
  name      = var.account_name
  email     = var.account_email
  parent_id = var.parent_ou_id

  role_name = "OrganizationAccountAccessRole"

  tags = merge(var.tags, {
    "platform:tier"        = var.tier
    "platform:environment" = var.environment
    "platform:managed-by"  = "terraform-account-vending"
  })

  lifecycle {
    # Accounts cannot be deleted via API — only removed from Organization.
    prevent_destroy = true
  }
}

# Cross-account execution role assumed by the platform Bridge Lambda.
# This role is provisioned in the vended account via the Organizations
# bootstrap role, then used by Bridge for tenant-scoped Runtime invocation.
#
# The trust policy scopes to the platform home account only.
# The permissions policy scopes to AgentCore Runtime invocation in the
# approved runtime regions (eu-west-1 primary, eu-central-1 failover).
resource "aws_iam_role" "tenant_execution" {
  provider = aws

  name = "platform-tenant-execution-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${var.platform_account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "platform-${var.environment}"
          }
        }
      }
    ]
  })

  tags = merge(var.tags, {
    "platform:tier"        = var.tier
    "platform:environment" = var.environment
  })
}

resource "aws_iam_role_policy" "tenant_execution" {
  provider = aws

  name = "platform-tenant-execution-policy"
  role = aws_iam_role.tenant_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAgentCoreRuntimeInvocation"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeAgent",
          "bedrock-agentcore:InvokeAgentWithResponseStream"
        ]
        Resource = "arn:aws:bedrock-agentcore:*:${aws_organizations_account.this.id}:runtime/*"
        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = ["eu-west-1", "eu-central-1"]
          }
        }
      }
    ]
  })
}
