# DataQ frontend on Container Apps (ADR 0028 §5 cutover — replaces the Static Web
# App, swa.tf). ONE generic nginx image (ghcr.io/theurgicduke771/dataq-frontend),
# pulled anonymously (public GHCR package, ADR 0023). Nothing is baked in: the SPA
# auth config + the /api proxy upstream are injected at RUNTIME via the DATAQ_*
# env below (nginx envsubst → /config.js + the proxy_pass upstream). This is why
# SWA couldn't host it — a static host can't inject runtime config.
#
# External ingress on 8080. The nginx conf reverse-proxies /api + /mcp to the api
# Container App (same-origin, no CORS — the api keeps CORS_ALLOW_ORIGINS empty),
# so this app is the **single public product surface**. The api is INTERNAL-only
# (containerapps.tf), reached over the in-environment endpoint; external
# orchestrator webhooks POST to this frontend (PUBLIC_BASE_URL) and are proxied in.

resource "azurerm_container_app" "frontend" {
  name                         = "dataq-app-frontend"
  container_app_environment_id = data.azurerm_container_app_environment.shared.id
  resource_group_name          = data.azurerm_resource_group.dataq.name
  revision_mode                = "Single"

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 2
    container {
      name   = "frontend"
      image  = "${var.frontend_image_repo}:${var.frontend_image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      # Proxy upstream — the api Container App's INTERNAL in-environment FQDN over
      # plain HTTP (ACA's documented internal pattern; see local.api_internal_url).
      # nginx forwards the upstream host as Host so Envoy routes it to the api.
      # Internal = no public round-trip, and traffic never leaves the environment.
      env {
        name  = "DATAQ_API_UPSTREAM"
        value = local.api_internal_url
      }

      # Runtime auth config (generic DATAQ_AUTH_* contract, ADR 0028). Real OIDC
      # against Azure AD: mode=oidc + the SPA client + the tenant v2 authority +
      # the full API scope. Validated end-to-end against this exact tenant in the
      # #504 stage-3 pass. `mode=oidc` (anything != "bypass") keeps bypass OFF.
      env {
        name  = "DATAQ_AUTH_MODE"
        value = "oidc"
      }
      env {
        name  = "DATAQ_AUTH_AUTHORITY"
        value = "https://login.microsoftonline.com/${local.azure_tenant_id}/v2.0"
      }
      env {
        name  = "DATAQ_AUTH_CLIENT_ID"
        value = azuread_application.spa.client_id
      }
      # Full scope string the SPA requests for the API access token
      # (api://<api-client-id>/<scope>), per the config.ts apiScope contract.
      env {
        name  = "DATAQ_AUTH_API_SCOPE"
        value = "api://${azuread_application.api.client_id}/${var.azure_api_scope}"
      }
    }
  }

  # The Deploy workflow rolls the frontend image out-of-band (`az containerapp
  # update --image <sha>`), same as the backend apps — ignore the image so an apply
  # never resets it to var.frontend_image_tag. The first create still uses that tag.
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  tags = local.common_tags
}
