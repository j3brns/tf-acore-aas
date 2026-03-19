variable "account_name" {
  description = "Name for the vended AWS account"
  type        = string
}

variable "account_email" {
  description = "Root email for the vended AWS account"
  type        = string
}

variable "parent_ou_id" {
  description = "Organizations OU ID to place the account under"
  type        = string
}

variable "tier" {
  description = "Platform tier for this account (basic, standard, premium, dedicated)"
  type        = string
}

variable "platform_account_id" {
  description = "Home platform account ID (for cross-account trust)"
  type        = string
}

variable "home_region" {
  description = "Platform home region"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}
