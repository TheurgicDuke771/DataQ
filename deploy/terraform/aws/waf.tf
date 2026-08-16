# AWS WAF on the CloudFront distribution (#1387).
#
# WHAT THIS IS FOR, precisely: the app's own rate limiter (ADR 0035) counts in
# Redis, INSIDE the request path — every request it rejects has already crossed
# CloudFront, the ALB, nginx and FastAPI, and cost a Redis round trip. It is also
# deliberately fail-OPEN: when the store is unavailable it allows everything and
# logs once, so the exact pressure most likely to hurt Redis is the pressure that
# switches enforcement off. That is the right trade for a data-quality API (a
# Redis blip must not black-hole the product), but it means there is no layer
# that sheds load *before* it reaches the origin.
#
# This ACL is that layer. It is not a replacement for the app limiter — the app
# limiter is per-token and per-provider and understands DataQ's classes; this is
# a blunt per-IP ceiling an order of magnitude above it, sized to catch floods,
# not to shape normal traffic.
#
# COST: a Web ACL is ~$5/month + ~$1/rule/month + ~$0.60 per million requests.
# That is real money on a deployment whose whole point is being cheap, so this
# stack keeps exactly two rules and no managed rule groups (those are ~$10-20/mo
# each). `waf_enabled = false` removes the ACL entirely.

locals {
  # Deliberately far above the app limiter's per-IP unauthenticated class
  # (RATE_LIMIT_UNAUTHENTICATED_PER_MINUTE = 120/min): WAF's window is 5 minutes
  # and its job is to stop a flood, not to duplicate policy. 120/min of legitimate
  # traffic = 600 per 5 min, so the default leaves >3x headroom before WAF ever
  # sees a request the app would have allowed.
  waf_rate_limit_5min = var.waf_rate_limit_per_5min
}

resource "aws_wafv2_web_acl" "app" {
  count    = var.waf_enabled ? 1 : 0
  provider = aws.us_east_1 # CLOUDFRONT scope is us-east-1-only (providers.tf)

  name        = "dataq-app"
  description = "DataQ CloudFront edge protection - per-IP rate ceiling + oversized-body guard"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # ── Rule 1: per-IP rate ceiling ────────────────────────────────────────────
  # Counts requests per source IP over a trailing 5-minute window and blocks the
  # offender until it drops back under. `FORWARDED_IP` is NOT used: the viewer
  # address CloudFront sees is already the true client, and trusting a forwarded
  # header at the edge would let an attacker pick their own bucket by sending a
  # fabricated X-Forwarded-For — the same spoofing concern the app limiter
  # handles with a fixed trusted-hop depth, except here there are no trusted hops
  # in front of us at all.
  rule {
    name     = "per-ip-rate-limit"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = local.waf_rate_limit_5min
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "dataq-per-ip-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  # ── Rule 2: oversized request bodies ───────────────────────────────────────
  # DataQ's write surface is JSON — suites, checks, connection configs, custom
  # SQL. None of it is megabytes. An 8KB+ body is either a mistake or someone
  # probing for a parser limit, and rejecting it at the edge keeps it away from
  # FastAPI's validation entirely.
  #
  # 8192 is WAF's own smallest SizeConstraint-friendly threshold to reason about
  # and sits far above the largest legitimate payload observed (a long custom-SQL
  # check is ~1-2KB). Raise `waf_max_body_bytes` if an import ever needs it —
  # suite import is the one endpoint that could plausibly grow.
  rule {
    name     = "oversized-body"
    priority = 2

    action {
      block {}
    }

    statement {
      size_constraint_statement {
        comparison_operator = "GT"
        size                = var.waf_max_body_bytes

        field_to_match {
          body {
            # A body larger than WAF can inspect is CONTINUEd rather than
            # silently treated as empty — combined with GT this means an
            # over-limit body still matches the rule instead of sailing past it.
            oversize_handling = "CONTINUE"
          }
        }

        text_transformation {
          priority = 0
          type     = "NONE"
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "dataq-oversized-body"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "dataq-app"
    sampled_requests_enabled   = true
  }

  tags = { Name = "dataq-app-waf" }
}
