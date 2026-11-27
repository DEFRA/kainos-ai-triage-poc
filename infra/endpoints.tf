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

# Subnet routing (0.0.0.0/0 -> TGW) and the S3 gateway endpoint (POCLDNDIPS3VPCE001)
# are owned by the platform VPC Terraform on POCLDNDIPINTRTB001/2.
