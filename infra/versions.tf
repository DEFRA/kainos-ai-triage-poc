terraform {
  required_version = "= 1.17.2"

  backend "s3" {
    bucket               = "ne-triage-poc-terraform-state"
    key                  = "ne-triage-poc/terraform.tfstate"
    region               = "eu-west-2"
    encrypt              = true
    kms_key_id           = "alias/aws/s3"
    use_lockfile         = true
    workspace_key_prefix = "ne-triage-poc/workspaces"

    assume_role = {
      role_arn     = "arn:aws:iam::637806558918:role/ne-triage-poc-terraform-deployer"
      session_name = "ne-triage-poc-state"
    }
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.73.0"
    }
  }
}
