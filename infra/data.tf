data "aws_vpc" "provided" {
  id = var.vpc_id
}

data "aws_subnet" "alb" {
  for_each = var.alb_subnet_ids

  id = each.value
}

data "aws_subnet" "ecs" {
  id = var.ecs_subnet_id
}

data "aws_prefix_list" "s3" {
  name = "com.amazonaws.eu-west-2.s3"
}

