# Amazon Cognito — the OIDC identity provider for this deployment.

resource "aws_cognito_user_pool" "app" {
  name = "dataq-app"

  auto_verified_attributes = ["email"]

  # NO self-service sign-up (#1386).
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }

  username_attributes = ["email"]

  tags = { Name = "dataq-app-cognito" }
}

# Domain prefix must be globally unique across all of Cognito.
resource "aws_cognito_user_pool_domain" "app" {
  domain       = "dataq-app-${random_string.suffix.result}"
  user_pool_id = aws_cognito_user_pool.app.id
}

# Public SPA client — no secret (a browser can't hold one), matching how the
# frontend's oidc-client-ts uses Authorization Code + PKCE (authClient.ts).
resource "aws_cognito_user_pool_client" "spa" {
  name         = "dataq-app-spa"
  user_pool_id = aws_cognito_user_pool.app.id

  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  # Root-with-trailing-slash, matching what the SPA actually registers: authClient.ts uses
  # `redirect_uri: ${window.location.origin}/` (and the same for post-logout).
  callback_urls = ["${local.frontend_url}/"]
  logout_urls   = ["${local.frontend_url}/"]

  # Access tokens are short-lived (1h) by default; the SPA relies on oidc-client-ts's silent-renew
  # via the refresh token, same pattern Azure uses (authClient.ts requests offline_access).
  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}

locals {
  # The standard OIDC issuer URL Cognito publishes discovery + JWKS under — exactly what
  # OidcBearerScheme's `discover_jwks_uri` GETs `{issuer}/.well-known/openid-configuration` from.
  cognito_issuer = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.app.id}"
}
