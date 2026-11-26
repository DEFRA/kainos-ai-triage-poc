data "aws_iam_policy_document" "deployer_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = tolist(var.allowed_deployer_principal_arns)
    }
  }
}

resource "aws_iam_role" "deployer" {
  name               = "ne-triage-poc-terraform-deployer"
  description        = "PoC application state access; workload deployment permissions are reviewed separately."
  assume_role_policy = data.aws_iam_policy_document.deployer_trust.json

  lifecycle {
    prevent_destroy = true
  }
}

data "aws_iam_policy_document" "deployer_state" {
  statement {
    sid       = "ListApplicationState"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["ne-triage-poc/*"]
    }
  }

  statement {
    sid       = "ReadWriteApplicationState"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${local.bucket_arn}/ne-triage-poc/terraform.tfstate"]
  }

  statement {
    sid       = "ManageApplicationStateLock"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.bucket_arn}/ne-triage-poc/terraform.tfstate.tflock"]
  }

  statement {
    sid    = "UseApplicationStateKmsKey"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]
    resources = [data.aws_kms_alias.s3.target_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.eu-west-2.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:aws:s3:arn"
      values = [
        local.bucket_arn,
        "${local.bucket_arn}/ne-triage-poc/terraform.tfstate",
        "${local.bucket_arn}/ne-triage-poc/terraform.tfstate.tflock",
      ]
    }
  }
}

resource "aws_iam_role_policy" "deployer_state" {
  name   = "ne-triage-poc-terraform-state"
  role   = aws_iam_role.deployer.id
  policy = data.aws_iam_policy_document.deployer_state.json

  lifecycle {
    prevent_destroy = true
  }
}

data "aws_iam_policy_document" "deployer_legacy_s3_read" {
  statement {
    sid    = "ReadLegacyBucketForAdoption"
    effect = "Allow"
    actions = [
      "s3:GetAccelerateConfiguration",
      "s3:GetBucketAcl",
      "s3:GetBucketCORS",
      "s3:GetBucketLocation",
      "s3:GetBucketLogging",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetBucketOwnershipControls",
      "s3:GetBucketPolicy",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetBucketRequestPayment",
      "s3:GetBucketTagging",
      "s3:GetBucketVersioning",
      "s3:GetBucketWebsite",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:ListBucket",
      "s3:ListTagsForResource",
    ]
    resources = ["arn:aws:s3:::ai-triage-test"]
  }
}

resource "aws_iam_role_policy" "deployer_legacy_s3_read" {
  name   = "ne-triage-poc-legacy-s3-read"
  role   = aws_iam_role.deployer.id
  policy = data.aws_iam_policy_document.deployer_legacy_s3_read.json

  lifecycle {
    prevent_destroy = true
  }
}
