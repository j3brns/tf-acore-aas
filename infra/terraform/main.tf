# Account Vending — Root Module
#
# Vends AWS accounts via Organizations for Option B/C account topology.
# See ADR-007 for the CDK/Terraform split rationale.

provider "aws" {
  region = var.home_region

  default_tags {
    tags = merge(var.tags, {
      "platform:managed-by" = "terraform-account-vending"
      "platform:environment" = var.environment
    })
  }
}

# Look up the Organizations OU where vended accounts are placed.
data "aws_organizations_organization" "current" {}

module "vended_account" {
  source   = "./modules/vended-account"
  for_each = var.vended_accounts

  account_name         = "platform-${var.environment}-${each.value.name_suffix}"
  account_email        = each.value.email
  parent_ou_id         = var.organizations_ou_id
  tier                 = each.value.tier
  platform_account_id  = var.platform_account_id
  home_region          = var.home_region
  environment          = var.environment
  tags                 = var.tags
}
