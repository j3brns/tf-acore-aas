terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # Configured per-environment via -backend-config or envs/<env>/backend.hcl
    # key            = "account-vending/terraform.tfstate"
    # bucket         = (set by backend config)
    # dynamodb_table = (set by backend config)
    # region         = "eu-west-2"
    # encrypt        = true
  }
}
