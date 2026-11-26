provider "aws" {
  region = "eu-west-2"
}

data "aws_caller_identity" "current" {}

data "aws_kms_alias" "s3" {
  name = "alias/aws/s3"
}

locals {
  bucket_name = "ne-triage-poc-terraform-state"
  state_keys = [
    "ne-triage-poc/terraform.tfstate",
    "ne-triage-poc/terraform.tfstate.tflock",
    "bootstrap/terraform.tfstate",
    "bootstrap/terraform.tfstate.tflock",
    "iam-access/terraform.tfstate",
    "iam-access/terraform.tfstate.tflock",
  ]
  bucket_arn = "arn:aws:s3:::${local.bucket_name}"
  root_arn   = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
  bucket_objects = [
    for key in local.state_keys : "${local.bucket_arn}/${key}"
  ]
  permitted_principals = concat(
    tolist(var.allowed_state_principal_arns),
    [local.root_arn, aws_iam_role.deployer.arn],
  )
}

resource "aws_s3_bucket" "state" {
  bucket        = local.bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = data.aws_kms_alias.s3.target_key_arn
    }

    bucket_key_enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }

  lifecycle {
    prevent_destroy = true
  }
}

data "aws_iam_policy_document" "state" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [local.bucket_arn, "${local.bucket_arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid    = "DenyUnapprovedPrincipals"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [local.bucket_arn, "${local.bucket_arn}/*"]

    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = local.permitted_principals
    }
  }

  statement {
    sid    = "AllowStateListing"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = tolist(var.allowed_state_principal_arns)
    }

    actions   = ["s3:ListBucket"]
    resources = [local.bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["ne-triage-poc/*", "bootstrap/*", "iam-access/*"]
    }
  }

  statement {
    sid    = "AllowStateAndLockObjects"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = tolist(var.allowed_state_principal_arns)
    }

    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = local.bucket_objects
  }

  statement {
    sid    = "AllowAccountRootRecovery"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = [local.root_arn]
    }

    actions   = ["s3:*"]
    resources = [local.bucket_arn, "${local.bucket_arn}/*"]
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state.json

  lifecycle {
    prevent_destroy = true
  }
}
