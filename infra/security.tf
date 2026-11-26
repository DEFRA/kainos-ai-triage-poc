resource "aws_security_group" "alb" {
  name        = "ne-triage-poc-alb"
  description = "Internal ALB ingress from the platform NLB when configured"
  vpc_id      = data.aws_vpc.provided.id

  tags = {
    Name = "ne-triage-poc-alb"
  }
}

resource "aws_security_group" "ui_tasks" {
  name        = "ne-triage-poc-ui"
  description = "Streamlit tasks reachable only from the internal ALB"
  vpc_id      = data.aws_vpc.provided.id

  tags = {
    Name = "ne-triage-poc-ui"
  }
}

resource "aws_security_group" "vpc_endpoints" {
  name        = "ne-triage-poc-vpce"
  description = "HTTPS access to required interface VPC endpoints from UI tasks"
  vpc_id      = data.aws_vpc.provided.id

  tags = {
    Name = "ne-triage-poc-vpce"
  }
}

resource "aws_security_group" "frontend_rds" {
  count = var.enable_frontend_rds ? 1 : 0

  name        = "ai-triage-rds-db-frontend"
  description = "PostgreSQL access from the replacement UI tasks only"
  vpc_id      = data.aws_vpc.provided.id
  egress      = []

  tags = {
    Name = "ai-triage-rds-db-frontend"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https_from_nlb" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.nlb.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
  description                  = "HTTPS from the project NLB"
}

resource "aws_vpc_security_group_ingress_rule" "frontend_rds_from_defra_laptops" {
  for_each = var.enable_frontend_rds ? var.rds_admin_cidrs : toset([])

  security_group_id = aws_security_group.frontend_rds[0].id
  cidr_ipv4         = each.value
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
  description       = "PostgreSQL from Defra laptops (CCoE-supplied range)"
}

resource "aws_vpc_security_group_ingress_rule" "ui_from_alb" {
  security_group_id            = aws_security_group.ui_tasks.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8501
  to_port                      = 8501
  ip_protocol                  = "tcp"
  description                  = "Streamlit traffic from the internal ALB only"
}

resource "aws_vpc_security_group_ingress_rule" "frontend_rds_from_ui" {
  count = var.enable_frontend_rds ? 1 : 0

  security_group_id            = aws_security_group.frontend_rds[0].id
  referenced_security_group_id = aws_security_group.ui_tasks.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from the replacement UI tasks"
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_ui" {
  security_group_id            = aws_security_group.vpc_endpoints.id
  referenced_security_group_id = aws_security_group.ui_tasks.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
  description                  = "HTTPS from the replacement UI tasks"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_ui" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.ui_tasks.id
  from_port                    = 8501
  to_port                      = 8501
  ip_protocol                  = "tcp"
  description                  = "Forward UI requests and health checks"
}

resource "aws_vpc_security_group_egress_rule" "ui_to_vpc_endpoints" {
  security_group_id            = aws_security_group.ui_tasks.id
  referenced_security_group_id = aws_security_group.vpc_endpoints.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
  description                  = "HTTPS to ECR and CloudWatch Logs endpoints"
}

resource "aws_vpc_security_group_egress_rule" "ui_to_frontend_rds" {
  count = var.enable_frontend_rds ? 1 : 0

  security_group_id            = aws_security_group.ui_tasks.id
  referenced_security_group_id = aws_security_group.frontend_rds[0].id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL to the frontend database"
}

resource "aws_vpc_security_group_egress_rule" "ui_to_s3" {
  security_group_id = aws_security_group.ui_tasks.id
  prefix_list_id    = data.aws_prefix_list.s3.id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS to S3 for image layers and case-intake objects"
}
