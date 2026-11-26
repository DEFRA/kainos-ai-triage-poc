# Project-owned NLB. CCoE routes transit-gateway traffic to its two private IPs;
# the NLB forwards TCP 443 to the internal ALB, which terminates TLS.

resource "aws_security_group" "nlb" {
  name        = "ne-triage-poc-nlb"
  description = "Defra internal HTTPS to the project NLB"
  vpc_id      = data.aws_vpc.provided.id

  tags = {
    Name = "ne-triage-poc-nlb"
  }
}

resource "aws_vpc_security_group_ingress_rule" "nlb_https" {
  security_group_id = aws_security_group.nlb.id
  cidr_ipv4         = var.nlb_cidr
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS from Defra internal networks"
}

resource "aws_vpc_security_group_egress_rule" "nlb_https" {
  security_group_id = aws_security_group.nlb.id
  cidr_ipv4         = var.nlb_cidr
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS to the internal ALB (inside the Defra range)"
}

resource "aws_lb" "nlb" {
  name                             = "ne-triage-poc-nlb"
  internal                         = true
  load_balancer_type               = "network"
  security_groups                  = [aws_security_group.nlb.id]
  subnets                          = var.alb_subnet_ids
  enable_cross_zone_load_balancing = true

  tags = {
    Name = "ne-triage-poc-nlb"
  }
}

resource "aws_lb_target_group" "nlb_to_alb" {
  name        = "ne-triage-poc-nlb-alb"
  port        = 443
  protocol    = "TCP"
  target_type = "alb"
  vpc_id      = data.aws_vpc.provided.id

  health_check {
    enabled  = true
    protocol = "HTTPS"
    path     = "/"
    matcher  = "200-399"
  }

  tags = {
    Name = "ne-triage-poc-nlb-alb"
  }
}

# An ALB target needs a matching ALB listener, so attach only once the HTTPS listener exists.
resource "aws_lb_target_group_attachment" "nlb_to_alb" {
  count = var.alb_certificate_arn == null ? 0 : 1

  target_group_arn = aws_lb_target_group.nlb_to_alb.arn
  target_id        = aws_lb.internal.arn
  port             = 443

  depends_on = [aws_lb_listener.https]
}

resource "aws_lb_listener" "nlb_https" {
  load_balancer_arn = aws_lb.nlb.arn
  port              = 443
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.nlb_to_alb.arn
  }
}
