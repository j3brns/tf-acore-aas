output "account_id" {
  description = "AWS account ID of the vended account"
  value       = aws_organizations_account.this.id
}

output "execution_role_arn" {
  description = "ARN of the tenant execution role in the vended account"
  value       = aws_iam_role.tenant_execution.arn
}
