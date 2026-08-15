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

    custom_origin_config {
      http_port  = 80
      https_port = 443
      # The ALB listener is plain HTTP :80; TLS terminates at CloudFront. The
      # ALB is not internet-reachable in practice — its security group admits
      # only CloudFront's origin-facing address ranges (alb.tf).
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
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
