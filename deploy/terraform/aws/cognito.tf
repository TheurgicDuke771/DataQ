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

  # NO self-service sign-up (#1386). This is the load-bearing line in this file.
  #
  # A Cognito pool allows self-registration by DEFAULT, and the hosted UI serves
  # a working /signup form to anyone who finds the domain. DataQ auto-provisions
  # a user row for any identity the configured issuer vouches for
  # (`core/auth.py`'s `_upsert_user`), so the pool's registration policy IS the
  # product's access policy: with sign-up open, any stranger could register,
  # verify their own email, and land an authenticated account inside the
  # workspace. That also dissolved the "single-tenant, workspace-trusted"
  # premise that issue #1118 (arbitrary `*_secret_name` resolution -> warehouse-
  # credential exfiltration) is deliberately accepted under, turning a known
  # internal risk into a remotely reachable one.
  #
  # With this set, operators are created by an admin
  # (`aws cognito-idp admin-create-user`) and the hosted /signup endpoint stops
  # serving a form. Existing users are unaffected.
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

  # Root-with-trailing-slash, matching what the SPA actually registers:
  # authClient.ts uses `redirect_uri: ${window.location.origin}/` (and the same
  # for post-logout) — it has no /auth/callback route. Cognito matches redirect
  # URIs exactly, so anything else is an instant redirect_mismatch (#1345).
  # HTTPS is mandatory here (Cognito rejects non-localhost http://) — satisfied
  # by fronting the ALB with CloudFront (cloudfront.tf).
  callback_urls = ["${local.frontend_url}/"]
  logout_urls   = ["${local.frontend_url}/"]

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
