output "vended_accounts" {
  description = "Map of vended account details"
  value = {
    for k, v in module.vended_account : k => {
      account_id         = v.account_id
      execution_role_arn = v.execution_role_arn
    }
  }
}
