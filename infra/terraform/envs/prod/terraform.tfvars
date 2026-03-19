# Production account vending configuration
#
# To vend a new account, add an entry to vended_accounts and run:
#   make tf-plan ENV=prod
#   make tf-apply ENV=prod  (requires operator approval)
#
# See RUNBOOK-002 and RUNBOOK-004 for when to escalate to Option B/C.

environment = "prod"

# Set these from your Organizations configuration.
# organizations_ou_id  = "ou-xxxx-xxxxxxxx"
# platform_account_id  = "123456789012"

# Example: Option B tier-split
# vended_accounts = {
#   premium = {
#     email       = "platform+prod-premium@example.com"
#     name_suffix = "premium"
#     tier        = "premium"
#   }
# }

vended_accounts = {}
