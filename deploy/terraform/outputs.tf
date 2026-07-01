# Post-apply coordinates. The GitHub-config values feed the repo Secrets/Variables
# the Deploy workflow reads (see deploy/terraform/README.md for the gh commands).
# Sensitive values are marked so `terraform output -raw <name>` is needed to read.

output "api_url" {
  description = "Public API base URL (the frontend nginx proxies /api + /mcp to it same-origin)."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "frontend_url" {
  description = "Frontend Container App URL — the public product surface (ADR 0028 §5). Proxies /api + /mcp same-origin to the api app."
  value       = local.frontend_url
}

output "key_vault_url" {
  description = "Key Vault URI (AZURE_KEY_VAULT_URL the app's SecretStore uses)."
  value       = azurerm_key_vault.app.vault_uri
}

output "postgres_fqdn" {
  description = "Shared Postgres host (app uses the `dataq` database on it)."
  value       = data.azurerm_postgresql_flexible_server.shared.fqdn
}

# ── Container Apps names -> Deploy workflow VARIABLES ─────────────────────────
output "resource_group" {
  description = "-> repo var AZURE_RESOURCE_GROUP"
  value       = data.azurerm_resource_group.dataq.name
}

output "api_app_name" {
  description = "-> repo var API_APP_NAME"
  value       = azurerm_container_app.api.name
}

output "worker_app_name" {
  description = "-> repo var WORKER_APP_NAME"
  value       = azurerm_container_app.worker.name
}

output "frontend_app_name" {
  description = "-> repo var FRONTEND_APP_NAME (the Deploy workflow rolls this Container App)."
  value       = azurerm_container_app.frontend.name
}

output "migrate_job_name" {
  description = "-> repo var MIGRATE_JOB_NAME"
  value       = azurerm_container_app_job.migrate.name
}

output "backend_image" {
  description = "GHCR image ref currently deployed."
  value       = local.backend_image
}

# ── GitHub OIDC -> Deploy workflow SECRETS ───────────────────────────────────
output "github_actions_client_id" {
  description = "-> repo secret AZURE_CLIENT_ID (the github-deploy app registration)."
  value       = azuread_application.github_deploy.client_id
}

output "azure_tenant_id" {
  description = "-> repo secret AZURE_TENANT_ID"
  value       = data.azurerm_client_config.current.tenant_id
}

output "azure_subscription_id" {
  description = "-> repo secret AZURE_SUBSCRIPTION_ID"
  value       = data.azurerm_subscription.current.subscription_id
}

# ── Azure AD SSO (sso.tf) — informational / app-registration coordinates ──────
# No longer build-time VITE_* vars: since the ADR 0028 §5 cutover the frontend is
# configured at RUNTIME (the DATAQ_AUTH_* env on the frontend Container App is
# wired straight from these same resources in frontend.tf), so nothing needs to
# copy them into repo vars. Kept as outputs for manual app-registration checks.
output "azure_api_client_id" {
  description = "API app-registration client id (also the backend AZURE_API_CLIENT_ID)."
  value       = azuread_application.api.client_id
}

output "azure_spa_client_id" {
  description = "SPA app-registration client id (public OIDC client — DATAQ_AUTH_CLIENT_ID)."
  value       = azuread_application.spa.client_id
}

output "azure_api_scope" {
  description = "API scope value (the frontend requests api://<api-client-id>/<this>)."
  value       = var.azure_api_scope
}

# ── Sensitive ────────────────────────────────────────────────────────────────
output "appinsights_connection_string" {
  description = "App Insights connection string (injected into the apps; surfaced for reference)."
  value       = azurerm_application_insights.app.connection_string
  sensitive   = true
}
