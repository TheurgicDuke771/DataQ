# CloudFront — the HTTPS front for the ALB (#1345).
#
# Cognito requires redirect URIs to be HTTPS (http:// is allowed only for
# localhost), so the original HTTP-only-ALB design could never complete an
# OIDC sign-in. With no custom domain chosen (that decision stands), the
# cheapest valid HTTPS origin is CloudFront's own *.cloudfront.net domain with
# the default certificate — additive to swap out later: pick a domain, add an
# ACM cert + alias here, and the ALB/ECS layers don't change.
#
# This is a pass-through, not a CDN in anger: the default behavior disables
# caching entirely (the app is a dynamic SPA + API; index.html must never be
# stale, /api must never be cached) and forwards the full request via the
# managed AllViewer policy. Static-asset caching can be added as a dedicated
# /assets/* behavior later if it ever matters — nginx already serves
# fingerprinted files.

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

resource "aws_cloudfront_distribution" "app" {
  enabled         = true
  comment         = "DataQ frontend — HTTPS front for the ALB (no custom domain)"
  is_ipv6_enabled = true
  # Cheapest edge-location class (NA + EU) — this is an HTTPS terminator, not
  # a latency play.
  price_class = "PriceClass_100"

  origin {
    domain_name = aws_lb.app.dns_name
    origin_id   = "alb"

    # Origin secret (#1355): stamped on every origin fetch and verified by the
    # frontend nginx (DATAQ_ORIGIN_SECRET) — closes the residual gap alb.tf
    # documents, where a third party's OWN distribution could origin-point at
    # this ALB's discoverable DNS name through the shared CloudFront
    # origin-facing ranges. CloudFront overwrites any viewer-sent header of
    # the same name, so the value can't be probed through this distribution.
    # (Visible in the distribution config to anyone with CloudFront read
    # access — an origin-authentication token, not a user credential.)
    #
    # Residual, stated honestly (#1378 review): the edge→origin hop is plain
    # HTTP (origin_protocol_policy http-only — no custom domain means no cert
    # the ALB could serve), so the header transits that leg in cleartext. An
    # on-path observer of AWS's edge→ALB path could read it; that adversary
    # class already sees the whole session traffic on the same hop. Closing
    # it = custom domain + ACM on the ALB + https-only, the documented
    # follow-up in the README's known-gaps list.
    custom_header {
      name  = "X-DataQ-Origin-Secret"
      value = random_password.origin_secret.result
    }

    custom_origin_config {
      http_port  = 80
      https_port = 443
      # The ALB listener is plain HTTP :80; TLS terminates at CloudFront. The
      # ALB's security group admits only CloudFront's origin-facing address
      # ranges (alb.tf) — see that file's note on what that does and does not
      # guarantee (#1355).
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      # Long warehouse-bound API calls (column profiler, dry-run, test-
      # connection against a cold warehouse) routinely exceed CloudFront's 30s
      # default; match the ALB's 60s idle timeout so CloudFront isn't the
      # tightest hop (/code-review finding).
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "dataq-app-cloudfront" }
}

# Alphanumeric only (`special = false`): the nginx guard's map key uses ":" as
# its delimiter and the value must stay free of nginx map metacharacters.
resource "random_password" "origin_secret" {
  length  = 40
  special = false
}

# Injected into the frontend task via the `secrets` block (execution-role read,
# same pattern as database-url/redis-url) rather than plaintext task-def env.
resource "aws_secretsmanager_secret" "origin_secret" {
  name                    = "dataq-app-infra/origin-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "origin_secret" {
  secret_id     = aws_secretsmanager_secret.origin_secret.id
  secret_string = random_password.origin_secret.result
}
