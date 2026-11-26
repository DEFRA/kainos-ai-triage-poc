output "provided_vpc_cidr_block" {
  description = "CIDR block of the existing PoC VPC."
  value       = data.aws_vpc.provided.cidr_block
}

output "alb_subnet_availability_zones" {
  description = "Availability Zones containing the internal ALB subnets."
  value       = sort([for subnet in data.aws_subnet.alb : subnet.availability_zone])

  precondition {
    condition     = length(distinct([for subnet in data.aws_subnet.alb : subnet.availability_zone])) >= 2
    error_message = "Select ALB subnets in at least two different Availability Zones."
  }

  precondition {
    condition     = alltrue([for subnet in data.aws_subnet.alb : subnet.vpc_id == data.aws_vpc.provided.id])
    error_message = "All ALB subnets must belong to the provided VPC."
  }
}

output "alb_dns_name" {
  description = "Internal DNS name of the ALB; the central WAF record is managed by CCoE."
  value       = aws_lb.internal.dns_name
}

output "alb_arn" {
  description = "ARN of the internal ALB."
  value       = aws_lb.internal.arn
}

output "ui_target_group_arn" {
  description = "ARN of the Streamlit target group."
  value       = aws_lb_target_group.ui.arn
}

output "ui_task_security_group_id" {
  description = "Security group ID to attach to the replacement UI tasks."
  value       = aws_security_group.ui_tasks.id
}

output "ui_ecs_cluster_name" {
  description = "Name of the Terraform-managed ECS cluster for the replacement UI service."
  value       = aws_ecs_cluster.poc.name
}

output "ui_ecs_service_name" {
  description = "Name of the replacement UI ECS service."
  value       = aws_ecs_service.ui.name
}

output "ecs_subnet_availability_zone" {
  description = "Availability Zone used by the single-AZ ECS UI service."
  value       = data.aws_subnet.ecs.availability_zone

  precondition {
    condition     = data.aws_subnet.ecs.vpc_id == data.aws_vpc.provided.id
    error_message = "The ECS subnet must belong to the provided VPC."
  }
}

output "frontend_rds_address" {
  description = "Private address of the frontend database, when enabled."
  value       = var.enable_frontend_rds ? aws_db_instance.frontend[0].address : null
}

output "nlb_arn_suffix" {
  description = "Use to find the NLB private IPs for CCoE: aws ec2 describe-network-interfaces --filters Name=description,Values='ELB <suffix>'."
  value       = aws_lb.nlb.arn_suffix
}

output "app_url" {
  description = "Internal URL once CCoE networking and DNS are in place."
  value       = "https://${var.app_hostname}"
}
