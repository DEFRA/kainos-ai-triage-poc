data "aws_ecr_image" "ui" {
  repository_name = var.ui_image_repository
  image_tag       = var.ui_image_tag
}

locals {
  ui_container_image = "${data.aws_ecr_image.ui.registry_id}.dkr.ecr.eu-west-2.amazonaws.com/${var.ui_image_repository}@${data.aws_ecr_image.ui.image_digest}"
}

resource "aws_ecs_cluster" "poc" {
  name = "ne-triage-poc"

  tags = {
    Name = "ne-triage-poc"
  }
}

resource "aws_cloudwatch_log_group" "ui" {
  name              = "/ecs/ne-triage-poc-ui"
  retention_in_days = var.ui_log_retention_days

  tags = {
    Name = "/ecs/ne-triage-poc-ui"
  }
}

resource "aws_ecs_task_definition" "ui" {
  family                   = "ne-triage-poc-ui"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ui_execution.arn
  task_role_arn            = aws_iam_role.ui_task.arn

  container_definitions = jsonencode([
    {
      name      = "ui"
      image     = local.ui_container_image
      essential = true

      # ECS reads the RDS-managed password at task start; restart the service after any rotation.
      secrets = var.enable_frontend_rds ? [
        {
          name      = "DB_PASSWORD"
          valueFrom = "${aws_db_instance.frontend[0].master_user_secret[0].secret_arn}:password::"
        },
      ] : []

      environment = var.enable_frontend_rds ? [
        { name = "DB_HOST", value = aws_db_instance.frontend[0].address },
        { name = "DB_PORT", value = tostring(aws_db_instance.frontend[0].port) },
        { name = "DB_NAME", value = aws_db_instance.frontend[0].db_name },
        { name = "DB_USER", value = aws_db_instance.frontend[0].username },
      ] : []

      portMappings = [
        {
          containerPort = 8501
          hostPort      = 8501
          protocol      = "tcp"
          appProtocol   = "http"
        }
      ]

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python3 -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=5)\"",
        ]
        interval    = 30
        timeout     = 10
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ui.name
          "awslogs-region"        = "eu-west-2"
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Name = "ne-triage-poc-ui"
  }
}

resource "aws_ecs_service" "ui" {
  name                               = "ne-triage-poc-ui"
  cluster                            = aws_ecs_cluster.poc.id
  task_definition                    = aws_ecs_task_definition.ui.arn
  desired_count                      = 1
  launch_type                        = "FARGATE"
  platform_version                   = "LATEST"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 120
  enable_execute_command             = false

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = [var.ecs_subnet_id]
    security_groups  = [aws_security_group.ui_tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.ui.arn
    container_name   = "ui"
    container_port   = 8501
  }

  depends_on = [
    aws_lb_listener.http,
    aws_iam_role_policy_attachment.ui_execution_ecs,
    aws_iam_role_policy_attachment.ui_execution_logs,
    aws_iam_role_policy.ui_execution_db_secret,
    aws_iam_role_policy_attachment.ui_task_bedrock,
    aws_vpc_endpoint.ui_interface,
  ]

  tags = {
    Name = "ne-triage-poc-ui"
  }
}
