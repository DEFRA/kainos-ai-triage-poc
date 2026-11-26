provider "aws" {
  region = "eu-west-2"

  default_tags {
    tags = merge({
      Project     = "ne-triage-poc"
      Environment = "poc"
      }, var.owner == null ? {} : { Owner = var.owner },
      var.cost_centre == null ? {} : { CostCentre = var.cost_centre }
    )
  }
}

provider "aws" {
  alias  = "legacy"
  region = "eu-west-2"

  assume_role {
    role_arn     = "arn:aws:iam::637806558918:role/ne-triage-poc-terraform-deployer"
    session_name = "ne-triage-poc-legacy-s3"
  }
}
