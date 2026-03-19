variable "environment" {
  description = "Deployment environment (staging, prod)"
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "Environment must be staging or prod."
  }
}

variable "home_region" {
  description = "Platform home region"
  type        = string
  default     = "eu-west-2"
}

variable "organizations_ou_id" {
  description = "AWS Organizations OU ID under which vended accounts are placed"
  type        = string
}

variable "platform_account_id" {
  description = "AWS account ID of the platform home account (for cross-account trust)"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.platform_account_id))
    error_message = "Platform account ID must be a 12-digit AWS account ID."
  }
}

variable "vended_accounts" {
  description = "Map of accounts to vend (key = logical name)"
  type = map(object({
    email       = string
    name_suffix = string
    tier        = string
  }))
  default = {}

  validation {
    condition     = alltrue([for k, v in var.vended_accounts : contains(["basic", "standard", "premium", "dedicated"], v.tier)])
    error_message = "Each vended account tier must be one of: basic, standard, premium, dedicated."
  }
}

variable "tags" {
  description = "Default tags applied to all resources"
  type        = map(string)
  default     = {}
}
