# Internet-facing ALB — the sole public surface (ADR 0028 §5's api-goes-
# internal cutover, mirrored here from the start rather than retrofitted).
# Only the frontend target group exists; api/worker have no ALB listener and
# are reached only via ECS Service Connect internal DNS (ecs.tf).
#
# HTTP-only for now (decision: no custom domain yet — see the approved plan).
# Add an ACM cert + HTTPS listener + Route53 record once a domain is chosen;
# that's additive, not a rework of this file.

resource "aws_security_group" "alb" {
  name        = "dataq-app-alb"
  description = "Public ALB — inbound HTTP from the internet"
  vpc_id      = aws_vpc.app.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "dataq-app-alb" }
}

resource "aws_lb" "app" {
  name               = "dataq-app"
  internal           = false
  load_balancer_type = "application"
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]

  tags = { Name = "dataq-app-alb" }
}

resource "aws_lb_target_group" "frontend" {
  name        = "dataq-app-frontend"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.app.id
  target_type = "ip" # required for Fargate tasks (no EC2 instance id)

  health_check {
    path                = "/healthz"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }

  tags = { Name = "dataq-app-frontend" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

locals {
  # No custom domain yet — the ALB's own DNS name is the deployment's URL,
  # matching the earlier decision to start HTTP-only. Computed here (not a
  # frontend-resource reference) so nothing later in the graph needs to wait
  # on the ECS service itself, mirroring the Azure stack's deterministic-FQDN
  # trick that breaks the frontend<->api circular dependency.
  frontend_url     = "http://${aws_lb.app.dns_name}"
  api_internal_url = "http://api.dataq.local:8000"
}
