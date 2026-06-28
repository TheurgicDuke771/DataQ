# Azure AD SSO app registrations (real auth in prod; AUTH_DEV_BYPASS=false). The
# backend's init_auth() requires AZURE_API_CLIENT_ID + AZURE_TENANT_ID at startup
# and validates v2 access tokens; the SPA (MSAL) uses AZURE_SPA_CLIENT_ID with a
# redirect URI of the SWA origin. Two registrations:
#   - API  (dataq-app-api-sso): exposes the `user_impersonation` scope, v2 tokens.
#   - SPA  (dataq-app-spa): public single-page client, redirect = SWA origin,
#                           pre-authorized on the API scope (no consent prompt).

data "azuread_client_config" "current" {}

resource "random_uuid" "api_scope" {}

# ── API app registration ─────────────────────────────────────────────────────
resource "azuread_application" "api" {
  display_name = "dataq-app-api-sso"
  owners       = [data.azuread_client_config.current.object_id]

  api {
    requested_access_token_version = 2 # v2 tokens (login.microsoftonline.com/<tenant>/v2.0)

    oauth2_permission_scope {
      id                         = random_uuid.api_scope.result
      value                      = var.azure_api_scope
      type                       = "User"
      enabled                    = true
      admin_consent_display_name = "Access the DataQ API"
      admin_consent_description  = "Allow the application to access the DataQ API on behalf of the signed-in user."
      user_consent_display_name  = "Access the DataQ API"
      user_consent_description   = "Allow the application to access the DataQ API on your behalf."
    }
  }
}

# api://<client_id> identifier URI (separate resource — needs the client_id known).
resource "azuread_application_identifier_uri" "api" {
  application_id = azuread_application.api.id
  identifier_uri = "api://${azuread_application.api.client_id}"
}

resource "azuread_service_principal" "api" {
  client_id = azuread_application.api.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

# ── SPA app registration ─────────────────────────────────────────────────────
resource "azuread_application" "spa" {
  display_name = "dataq-app-spa"
  owners       = [data.azuread_client_config.current.object_id]

  single_page_application {
    # Azure AD requires a trailing slash when there's no path segment; the SPA's
    # MSAL redirectUri is set to `${window.location.origin}/` to match exactly
    # (frontend/src/auth/msalInstance.ts).
    redirect_uris = ["https://${azurerm_static_web_app.app.default_host_name}/"]
  }

  # Request the API's user_impersonation scope.
  required_resource_access {
    resource_app_id = azuread_application.api.client_id
    resource_access {
      id   = random_uuid.api_scope.result
      type = "Scope"
    }
  }

  # Microsoft Graph User.Read (sign-in + read profile).
  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000"
    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "spa" {
  client_id = azuread_application.spa.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

# Pre-authorize the SPA on the API scope so users aren't prompted to consent to
# the custom API (same-tenant, single product).
resource "azuread_application_pre_authorized" "spa_on_api" {
  application_id       = azuread_application.api.id
  authorized_client_id = azuread_application.spa.client_id
  permission_ids       = [random_uuid.api_scope.result]
}
