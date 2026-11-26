resource "aws_lb" "internal" {
  name               = "ne-triage-poc"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.alb_subnet_ids
  idle_timeout       = 120

  tags = {
    Name = "ne-triage-poc"
  }
}

resource "aws_lb_target_group" "ui" {
  name        = "ne-triage-poc-ui"
  port        = 8501
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.provided.id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200"
    path                = "/_stcore/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 2
  }

  tags = {
    Name = "ne-triage-poc-ui"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.internal.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = var.alb_certificate_arn == null ? "forward" : "redirect"
    target_group_arn = var.alb_certificate_arn == null ? aws_lb_target_group.ui.arn : null

    dynamic "redirect" {
      for_each = var.alb_certificate_arn == null ? [] : [1]
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count = var.alb_certificate_arn == null ? 0 : 1

  load_balancer_arn = aws_lb.internal.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.alb_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ui.arn
  }
}
