# AWS WAF on the CloudFront distribution (#1388).

locals {
  # Deliberately far above the app limiter's per-IP unauthenticated class
  # (RATE_LIMIT_UNAUTHENTICATED_PER_MINUTE = 120/min): WAF's window is 5 minutes and its job is to
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

  # ── Rule 1: per-IP rate ceiling ──────────────────────────────────────────── Counts requests per
  # source IP over a trailing 5-minute window and blocks the offender until it drops back under.
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

  # ── Rule 2: oversized request bodies ─────────────────────────────────────── DataQ's write
  # surface is JSON — suites, checks, connection configs, custom SQL.
  rule {
    name     = "oversized-body"
    priority = 2

    action {
      block {}
    }

    statement {
      and_statement {
        statement {
          size_constraint_statement {
            comparison_operator = "GT"
            size                = var.waf_max_body_bytes

            field_to_match {
              body {
                # A body larger than WAF can inspect is CONTINUEd rather than silently treated as
                # empty.
                oversize_handling = "CONTINUE"
              }
            }

            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }

        # ... and NOT the suite-import path (see note 2 above).
        statement {
          not_statement {
            statement {
              byte_match_statement {
                positional_constraint = "STARTS_WITH"
                search_string         = "/api/v1/suites/import"

                field_to_match {
                  uri_path {}
                }

                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
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
