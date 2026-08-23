# CloudFront — the HTTPS front for the ALB (#1345).

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# For the fingerprinted build output only (#1388). Everything else stays
# uncached — see the header comment.
data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

locals {
  # Extensions Vite emits into dist/assets/, mirroring the nginx `location ~*` list
  # (frontend/nginx.conf.template) so the two layers agree on what a static asset is.
  cacheable_asset_extensions = [
    "js", "mjs", "css", "map",
    "png", "jpg", "jpeg", "gif", "svg", "ico", "webp", "avif",
    "woff", "woff2", "ttf", "eot",
  ]

  # Extension-matched DELIBERATELY: a bare /assets/* prefix would re-collide with the
  # SPA's own /assets/:assetId route and edge-cache HTML as an asset (#1388 review).
  cacheable_asset_patterns = [for ext in local.cacheable_asset_extensions : "/assets/*.${ext}"]
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

    # Origin secret (#1355): stamped on every origin fetch and verified by the frontend nginx
    # (DATAQ_ORIGIN_SECRET) — closes the residual gap alb.tf documents.
    custom_header {
      name  = "X-DataQ-Origin-Secret"
      value = random_password.origin_secret.result
    }

    custom_origin_config {
      http_port  = 80
      https_port = 443
      # The ALB listener is plain HTTP :80; TLS terminates at CloudFront.
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      # Long warehouse-bound API calls (column profiler, dry-run, test- connection against a cold
      # warehouse) routinely exceed CloudFront's 30s default.
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

  # Fingerprinted build output — the ONE thing here that is safe to cache at the edge, and the only
  # reason this distribution can absorb any load at all (#1388).
  dynamic "ordered_cache_behavior" {
    for_each = local.cacheable_asset_patterns
    content {
      path_pattern           = ordered_cache_behavior.value
      target_origin_id       = "alb"
      viewer_protocol_policy = "redirect-to-https"
      allowed_methods        = ["GET", "HEAD", "OPTIONS"]
      cached_methods         = ["GET", "HEAD"]
      compress               = true

      # No origin_request_policy: AllViewer would forward every header and make the cache key so
      # specific that almost nothing would hit.
      cache_policy_id = data.aws_cloudfront_cache_policy.caching_optimized.id
    }
  }

  # Edge rate limiting + oversized-body rejection (#1388, waf.tf). Null when
  # waf_enabled = false, which detaches the ACL without touching anything else.
  web_acl_id = var.waf_enabled ? aws_wafv2_web_acl.app[0].arn : null

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
