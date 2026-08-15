# Amazon Cognito — the OIDC identity provider for this deployment, validated
# by the backend's new provider-neutral `OidcBearerScheme` (ADR 0026
# amendment) instead of the Azure-only `fastapi_azure_auth` path. No code
# change needed here beyond what already shipped: this stack only needs to
# set OIDC_ISSUER + OIDC_AUDIENCE (backend) and the DATAQ_AUTH_* runtime
# contract (frontend, already generic per ADR 0028).
#
# Known open item (tracked, not silently assumed correct): Cognito's OAuth
# ACCESS token carries `client_id`, not the standard `aud` claim.
# OidcBearerScheme already accommodates this — see its docstring — but the
# accommodation needs confirming against a REAL token from this pool once
# it exists (this codebase's "#953 rule": driver/third-party-boundary claims
# need a live check, not just a spec read).

resource "aws_cognito_user_pool" "app" {
  name = "dataq-app"

  auto_verified_attributes = ["email"]

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

# Domain prefix must be globally unique across all of Cognito — the random
# suffix (shared with other globally-unique-name resources in this stack)
# avoids a name collision on a common word like "dataq".
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

  callback_urls = ["${local.frontend_url}${var.cognito_callback_path}"]
  logout_urls   = [local.frontend_url]

  # Access tokens are short-lived (1h) by default; the SPA relies on
  # oidc-client-ts's silent-renew via the refresh token, same pattern Azure
  # uses (authClient.ts requests offline_access).
  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}

locals {
  # The standard OIDC issuer URL Cognito publishes discovery + JWKS under —
  # exactly what OidcBearerScheme's `discover_jwks_uri` GETs
  # `{issuer}/.well-known/openid-configuration` from.
  cognito_issuer = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.app.id}"
}
