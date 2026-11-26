locals {
  ui_interface_endpoint_services = toset([
    "ecr.api",
    "ecr.dkr",
    "logs",
    "secretsmanager",
  ])
}

resource "aws_vpc_endpoint" "ui_interface" {
  for_each = local.ui_interface_endpoint_services

  vpc_id              = data.aws_vpc.provided.id
  service_name        = "com.amazonaws.eu-west-2.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [var.ecs_subnet_id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "ne-triage-poc-${replace(each.value, ".", "-")}-endpoint"
  }
}

resource "aws_route_table" "ecs" {
  vpc_id = data.aws_vpc.provided.id

  tags = {
    Name = "ne-triage-poc-ecs"
  }
}

resource "aws_route_table_association" "ecs" {
  subnet_id      = var.ecs_subnet_id
  route_table_id = aws_route_table.ecs.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = data.aws_vpc.provided.id
  service_name      = "com.amazonaws.eu-west-2.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.ecs.id]

  tags = {
    Name = "ne-triage-poc-s3-endpoint"
  }
}
