# ALB behind CloudFront (#1345) — CloudFront is the sole public surface
# (ADR 0028 §5's api-goes-internal posture, one hop further out). Only the
# frontend target group exists; api/worker have no ALB listener and are
# reached only via Cloud Map internal DNS (ecs.tf).
#
# The listener stays plain HTTP :80: TLS terminates at CloudFront (Cognito
# requires an HTTPS redirect URI, and there is no custom domain — see
# cloudfront.tf). The security group admits only CloudFront's origin-facing
# ranges (AWS-managed prefix list), so the ALB is not reachable from an
# arbitrary internet address. Those ranges are shared by ALL CloudFront
# distributions, so a third party could still origin-point their own
# distribution at this ALB's (discoverable) DNS name — which is why the
# frontend nginx additionally verifies the X-DataQ-Origin-Secret header our
# distribution stamps on every origin fetch (#1355, cloudfront.tf): a foreign
# edge doesn't know the secret and gets a 403 for every path except the ALB's
# own local health endpoint.

# AWS-managed, auto-updated list of CloudFront origin-facing IP ranges.
data "aws_ec2_managed_prefix_list" "cloudfront_origin" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "dataq-app-alb"
  description = "ALB - inbound HTTP from CloudFront origin-facing ranges only"
  vpc_id      = aws_vpc.app.id

  ingress {
    description     = "HTTP from CloudFront"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront_origin.id]
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
    # /_alb-health — nginx's own local endpoint, exempt from the #1355
    # origin-secret guard (the ALB probes the container directly, so it can
    # never carry CloudFront's header; probing `/` would 403 and recycle
    # healthy tasks once the guard is on). It supersedes the plain `/` chosen
    # by #1349 while keeping that fix's property: frontend health means
    # "nginx + bundle up", never "the api is up" (nginx PROXIES /healthz to
    # the api). Old images without the location fall through to the SPA
    # index (200), so the path change deploys safely ahead of the new image.
    path                = "/_alb-health"
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
  # No custom domain yet — CloudFront's own domain is the deployment's public
  # HTTPS URL (#1345). Depends only on the distribution (which depends only on
  # the ALB), so nothing later in the graph waits on the ECS service itself —
  # mirroring the Azure stack's deterministic-FQDN trick that breaks the
  # frontend<->api circular dependency.
  frontend_url     = "https://${aws_cloudfront_distribution.app.domain_name}"
  api_internal_url = "http://api.dataq.local:8000"
}
