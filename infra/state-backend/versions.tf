terraform {
  required_version = "= 1.17.2"

  backend "s3" {
    bucket       = "ne-triage-poc-terraform-state"
    key          = "bootstrap/terraform.tfstate"
    region       = "eu-west-2"
    encrypt      = true
    kms_key_id   = "alias/aws/s3"
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.73.0"
    }
  }
}
