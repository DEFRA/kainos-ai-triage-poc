data "aws_iam_policy_document" "ecs_tasks_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ui_execution" {
  name               = "ne-triage-poc-ui-execution"
  description        = "Pull the approved UI image and write its CloudWatch logs."
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json

  tags = {
    Name = "ne-triage-poc-ui-execution"
  }
}

resource "aws_iam_role_policy_attachment" "ui_execution_ecs" {
  role       = aws_iam_role.ui_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy_attachment" "ui_execution_logs" {
  role       = aws_iam_role.ui_execution.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
}

resource "aws_iam_role" "ui_task" {
  name               = "ne-triage-poc-ui-task"
  description        = "UI task role with temporary permissions matching the existing application role."
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json

  tags = {
    Name = "ne-triage-poc-ui-task"
  }
}

# Broad access retained for the demo; narrow to case-intake/poc_data/ later.
resource "aws_iam_role_policy_attachment" "ui_task_s3" {
  role       = aws_iam_role.ui_task.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

resource "aws_iam_role_policy_attachment" "ui_task_bedrock" {
  role       = aws_iam_role.ui_task.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
}

data "aws_iam_policy_document" "ui_execution_db_secret" {
  count = var.enable_frontend_rds ? 1 : 0

  statement {
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.frontend[0].master_user_secret[0].secret_arn]
  }
}

resource "aws_iam_role_policy" "ui_execution_db_secret" {
  count = var.enable_frontend_rds ? 1 : 0

  name   = "ne-triage-poc-ui-db-secret"
  role   = aws_iam_role.ui_execution.id
  policy = data.aws_iam_policy_document.ui_execution_db_secret[0].json
}
