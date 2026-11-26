resource "aws_s3_bucket" "legacy" {
  provider = aws.legacy
  bucket   = "ai-triage-test"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "legacy" {
  provider = aws.legacy
  bucket   = aws_s3_bucket.legacy.id

  rule {
    bucket_key_enabled       = true
    blocked_encryption_types = ["SSE-C"]

    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "legacy" {
  provider = aws.legacy
  bucket   = aws_s3_bucket.legacy.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "legacy" {
  provider = aws.legacy
  bucket   = aws_s3_bucket.legacy.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = aws_s3_bucket.legacy
  id = "ai-triage-test"
}

import {
  to = aws_s3_bucket_server_side_encryption_configuration.legacy
  id = "ai-triage-test"
}

import {
  to = aws_s3_bucket_public_access_block.legacy
  id = "ai-triage-test"
}

import {
  to = aws_s3_bucket_ownership_controls.legacy
  id = "ai-triage-test"
}

resource "aws_s3_bucket_versioning" "legacy" {
  count    = var.enable_legacy_bucket_versioning ? 1 : 0
  provider = aws.legacy
  bucket   = aws_s3_bucket.legacy.id

  versioning_configuration {
    status = "Enabled"
  }

  lifecycle {
    prevent_destroy = true
  }
}

data "aws_iam_policy_document" "legacy_https_only" {
  count = var.enforce_legacy_bucket_https ? 1 : 0

  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [aws_s3_bucket.legacy.arn, "${aws_s3_bucket.legacy.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }

    condition {
      test     = "Bool"
      variable = "aws:PrincipalIsAWSService"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "legacy_https_only" {
  count    = var.enforce_legacy_bucket_https ? 1 : 0
  provider = aws.legacy
  bucket   = aws_s3_bucket.legacy.id
  policy   = data.aws_iam_policy_document.legacy_https_only[0].json

  lifecycle {
    prevent_destroy = true
  }
}
