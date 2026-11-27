resource "aws_sns_topic" "alerts" {
  name = "ne-triage-poc-alerts"

  tags = {
    Name = "ne-triage-poc-alerts"
  }
}

# Each recipient must click the AWS confirmation email before alerts are delivered.
resource "aws_sns_topic_subscription" "alerts_email" {
  for_each = var.alert_emails

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  ui_service_alarms = {
    memory = "MemoryUtilization"
    cpu    = "CPUUtilization"
  }
}

resource "aws_cloudwatch_metric_alarm" "ui_utilisation" {
  for_each = local.ui_service_alarms

  alarm_name          = "ne-triage-poc-ui-${each.key}-high"
  alarm_description   = "UI task ${each.key} above 75% for 5 minutes; consider resizing the task definition."
  namespace           = "AWS/ECS"
  metric_name         = each.value
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 75
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    # Literal names avoid pulling the service's dependencies into targeted plans.
    ClusterName = "ne-triage-poc"
    ServiceName = "ne-triage-poc-ui"
  }

  tags = {
    Name = "ne-triage-poc-ui-${each.key}-high"
  }
}

# Notifies when a new UI image is pushed, so the operator knows to run plan/apply.
resource "aws_cloudwatch_event_rule" "ui_image_push" {
  name        = "ne-triage-poc-ui-image-push"
  description = "Successful pushes of the UI image tag to ECR."

  event_pattern = jsonencode({
    source      = ["aws.ecr"]
    detail-type = ["ECR Image Action"]
    detail = {
      action-type     = ["PUSH"]
      result          = ["SUCCESS"]
      repository-name = [var.ui_image_repository]
      image-tag       = [var.ui_image_tag]
    }
  })

  tags = {
    Name = "ne-triage-poc-ui-image-push"
  }
}

resource "aws_cloudwatch_event_target" "ui_image_push" {
  rule = aws_cloudwatch_event_rule.ui_image_push.name
  arn  = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      repo   = "$.detail.repository-name"
      tag    = "$.detail.image-tag"
      digest = "$.detail.image-digest"
    }
    input_template = "\"New UI image pushed: <repo>:<tag> (<digest>). Run terraform plan in infra/ and apply after review to deploy it.\""
  }
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AllowCloudWatchAlarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [for a in aws_cloudwatch_metric_alarm.ui_utilisation : a.arn]
    }
  }

  statement {
    sid       = "AllowImagePushRule"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.ui_image_push.arn]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}
